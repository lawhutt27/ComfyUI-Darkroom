"""Bring a provider's low-resolution edit back onto the full-resolution image.

`prepare_edit` runs once, when the AI layer is created: it aligns the
provider output to the input it was given and stores both. `composite`
runs on every render, at any level (proxy or master), and is pure.

See ARCHITECTURE.md §4 for the rules. In short: outside the mask the image
is returned bit-identical; inside it, small changes are transferred as a
delta over the full-resolution pixels (keeping sensor detail) and large
changes replace them (so removed objects leave no ghost).
"""

from __future__ import annotations

import cv2
import numpy as np

from darkroom_studio import engine


def _smooth(e0, e1, x):
    t = np.clip((x - e0) / max(e1 - e0, 1e-6), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def to_u8_srgb(linear, space):
    return engine.to_display(linear, space)


def align(output_u8, input_u8):
    """Resize the provider output to the input size and correct small
    shifts/zoom (ECC, similarity-ish via affine with low iterations).
    Returns (aligned_u8, info)."""
    h, w = input_u8.shape[:2]
    out = output_u8
    info = {"provider_size": [int(out.shape[1]), int(out.shape[0])]}
    if out.shape[:2] != (h, w):
        out = cv2.resize(out, (w, h), interpolation=cv2.INTER_LANCZOS4 if out.shape[1] < w else cv2.INTER_AREA)
    a = cv2.cvtColor(input_u8, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    b = cv2.cvtColor(out, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    scale = 512.0 / max(h, w)
    if scale < 1:
        a_s = cv2.resize(a, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        b_s = cv2.resize(b, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        a_s, b_s, scale = a, b, 1.0
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5)
    max_shift = 0.06 * max(h, w)

    def plausible(wp):
        return (np.abs(wp[:, :2] - np.eye(2)).max() <= 0.06
                and np.abs(wp[:, 2] / scale).max() <= max_shift)

    warp, cc = None, None
    try:      # translation first: robust even when large areas were edited
        cc, wt = cv2.findTransformECC(a_s, b_s, np.eye(2, 3, dtype=np.float32), cv2.MOTION_TRANSLATION,
                                      crit, None, 5)
        if plausible(wt):
            warp = wt
            try:  # then refine to a small zoom/rotation if the model reframed slightly
                cc2, wa = cv2.findTransformECC(a_s, b_s, wt.copy(), cv2.MOTION_AFFINE, crit, None, 5)
                if plausible(wa) and cc2 >= cc:
                    warp, cc = wa, cc2
            except cv2.error:
                pass
    except cv2.error:
        pass
    if warp is None:
        info["aligned"] = "not aligned (no reliable match; used as returned)"
        return out, info
    lin = warp[:, :2]
    shift = warp[:, 2] / scale
    info["ecc"] = round(float(cc), 4)
    if np.abs(lin - np.eye(2)).max() > 1e-3 or np.abs(shift).max() > 0.5:
        warp_full = warp.copy()
        warp_full[:, 2] = shift
        out = cv2.warpAffine(out, warp_full, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                             borderMode=cv2.BORDER_REPLICATE)
        info["aligned"] = {"shift_px": [round(float(v), 2) for v in shift],
                           "matrix": [[round(float(v), 4) for v in r] for r in lin]}
    else:
        info["aligned"] = "already aligned"
    return out, info


def change_map(input_u8, output_u8):
    """Per-pixel change magnitude (0-1-ish) in a perceptual space, lightly blurred."""
    a = cv2.cvtColor(input_u8, cv2.COLOR_RGB2LAB).astype(np.float32)
    b = cv2.cvtColor(output_u8, cv2.COLOR_RGB2LAB).astype(np.float32)
    d = np.sqrt(((a - b) ** 2).sum(-1)) / 100.0
    sigma = max(1.0, max(input_u8.shape[:2]) / 400.0)
    return cv2.GaussianBlur(d, (0, 0), sigma)


def structure_change(input_u8, output_u8):
    """How much local STRUCTURE (edges, texture visible at the provider's
    resolution) changed, as opposed to smooth tone/colour shifts. High where
    an object was removed or added, low where the model only relit or
    recoloured an area."""
    a = cv2.cvtColor(input_u8, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    b = cv2.cvtColor(output_u8, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    s = max(1.0, max(a.shape) / 512.0)
    hp_a = a - cv2.GaussianBlur(a, (0, 0), 2 * s)
    hp_b = b - cv2.GaussianBlur(b, (0, 0), 2 * s)
    return cv2.GaussianBlur(np.abs(hp_a - hp_b), (0, 0), 3 * s)


def _feathered_box_mask(h, w, box_px, feather):
    m = np.zeros((h, w), np.float32)
    x, y, bw, bh = box_px
    m[max(0, y):min(h, y + bh), max(0, x):min(w, x + bw)] = 1.0
    if feather > 0.5:
        m = cv2.GaussianBlur(m, (0, 0), feather / 2.0)
        # keep the box interior solid, feather only outward/edge
    return m


def _crop_px(params, W, H):
    c = params["crop"]
    x0, y0 = int(round(c["x"] * W)), int(round(c["y"] * H))
    x1, y1 = int(round((c["x"] + c["w"]) * W)), int(round((c["y"] + c["h"]) * H))
    return max(0, x0), max(0, y0), min(W, x1), min(H, y1)


def build_mask(params, W, H, change, full_long):
    """Composite mask for the crop at a level of size (W, H). `change` is the
    change map already resized to the crop. Values 0..1 (before strength)."""
    x0, y0, x1, y1 = _crop_px(params, W, H)
    cw, ch = x1 - x0, y1 - y0
    feather = params["feather"] * max(W, H) / float(full_long)   # feather is in full-res px
    if params["box"]:
        b = params["box"]
        bx = int(round(b["x"] * W)) - x0
        by = int(round(b["y"] * H)) - y0
        bw, bh = int(round(b["w"] * W)), int(round(b["h"] * H))
        region = _feathered_box_mask(ch, cw, (bx, by, bw, bh), feather)
    else:
        region = np.ones((ch, cw), np.float32)
    if not params["preserve"]:
        return region
    # sensitivity 0..100 -> threshold 0.12 .. 0.015 (higher = picks up subtler changes)
    t = 0.12 - (params["sensitivity"] / 100.0) * 0.105
    sel = (change > t).astype(np.uint8)
    k = max(1, int(round(max(cw, ch) / 300)))
    sel = cv2.morphologyEx(sel, cv2.MORPH_OPEN, np.ones((2 * k + 1, 2 * k + 1), np.uint8))
    sel = cv2.dilate(sel, np.ones((4 * k + 1, 4 * k + 1), np.uint8)).astype(np.float32)
    if feather > 0.5:
        sel = cv2.GaussianBlur(sel, (0, 0), feather / 2.0)
    return region * np.clip(sel * 1.5, 0, 1)


def composite(img, space, params, input_u8, output_u8, full_long):
    """Composite one prepared AI edit onto `img` (linear, working space, at any
    level of a frame whose long edge is `full_long` at 100 %), in place."""
    H, W = img.shape[:2]
    s = params["strength"] / 100.0
    if s <= 0:
        return img
    x0, y0, x1, y1 = _crop_px(params, W, H)
    cw, ch = x1 - x0, y1 - y0
    if cw < 2 or ch < 2:
        return img
    interp = cv2.INTER_CUBIC if cw > input_u8.shape[1] else cv2.INTER_AREA
    inp = cv2.resize(input_u8, (cw, ch), interpolation=interp).astype(np.float32) / 255.0
    out = cv2.resize(output_u8, (cw, ch), interpolation=interp).astype(np.float32) / 255.0
    change = cv2.resize(change_map(input_u8, output_u8), (cw, ch), interpolation=cv2.INTER_LINEAR)
    struct = cv2.resize(structure_change(input_u8, output_u8), (cw, ch), interpolation=cv2.INTER_LINEAR)
    mask = build_mask(params, W, H, change, full_long)
    mask = np.where(mask > 2e-3, mask * s, 0).astype(np.float32)
    if not mask.any():
        return img

    # blend in sRGB-encoded space, which is what the provider saw
    sl = (slice(y0, y1), slice(x0, x1))
    base_lin = img[sl]
    base_E = engine.to_srgb_encoded(base_lin, space)
    delta_E = base_E + (out - inp)                         # keeps full-res texture
    # structure changed (object removed/added) or a drastic recolour -> take the model's pixels
    replace_w = np.maximum(_smooth(0.012, 0.035, struct), _smooth(0.25, 0.4, change))[..., None]
    cand_E = delta_E * (1 - replace_w) + out * replace_w
    cand_lin = engine.from_srgb_encoded(np.clip(cand_E, 0, 1), space)
    m = mask[..., None]
    img[sl] = np.where(m > 0, base_lin + (cand_lin - base_lin) * m, base_lin)
    return img


def mask_overlay(params, input_u8, output_u8, W, H, full_long):
    """Full-frame (H, W) mask at preview size, for the UI overlay."""
    x0, y0, x1, y1 = _crop_px(params, W, H)
    full = np.zeros((H, W), np.float32)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return full
    change = cv2.resize(change_map(input_u8, output_u8), (x1 - x0, y1 - y0), interpolation=cv2.INTER_LINEAR)
    full[y0:y1, x0:x1] = build_mask(params, W, H, change, full_long)
    return full
