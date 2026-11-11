"""
Deterministic, non-generative image-processing engine.

`render()` executes a validated recipe on a float32 linear-light image.  It
never changes the frame size: every operation maps an H x W input to an
H x W output.  The same code renders the preview proxy, a 1:1 crop and the
full-resolution export; only the "level" (pixels per full-resolution pixel)
differs, and every spatial radius is defined in full-resolution pixels and
scaled by that level, so the preview is a faithful (if softer) proxy.

Processing order (each stage is skipped when its controls are neutral, so an
all-default recipe returns the input unchanged):

  linear light   lens geometry (distortion, lateral CA) -> lens vignetting ->
                 white balance (Bradford) -> exposure -> tone (highlights /
                 shadows / whites / blacks / contrast, luminance-ratio) ->
                 dehaze -> halation
  OkLab          reference match -> clarity / texture -> HSL -> vibrance /
                 saturation -> colour-grading wheels
  sRGB-encoded   tone curves -> lift/gamma/gain -> film stock -> LUT -> fade
  OkLab          noise reduction -> sharpening
  linear light   vignette -> creative chromatic aberration
  sRGB-encoded   grain

Algorithms adapted from ComfyUI-Darkroom (MIT, J. Louvaert) are marked.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import colorspace as cms
from .recipe import HSL_BANDS, IDENTITY_CURVE, ZONES

cv2.setUseOptimized(True)

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / max(e1 - e0, 1e-6), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def gblur(img, sigma):
    """Gaussian blur; large sigmas run on a downsampled copy (exactness is not
    needed for glow/haze maps and it keeps 24 MP renders fast)."""
    if sigma <= 0.05:
        return img
    if sigma > 8.0:
        f = sigma / 4.0
        h, w = img.shape[:2]
        sw, sh = max(1, int(round(w / f))), max(1, int(round(h / f)))
        small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), 4.0, borderType=cv2.BORDER_REFLECT)
        return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    return cv2.GaussianBlur(img, (0, 0), sigma, borderType=cv2.BORDER_REFLECT)


def box(img, r):
    k = 2 * int(r) + 1
    return cv2.boxFilter(img, -1, (k, k), borderType=cv2.BORDER_REFLECT)


def guided_filter(I, p, r, eps):
    """He et al. guided filter (edge-preserving smoothing) with O(1) box filters."""
    r = max(1, int(round(r)))
    mI, mp = box(I, r), box(p, r)
    vI = box(I * I, r) - mI * mI
    cIp = box(I * p, r) - mI * mp
    a = cIp / (vI + eps)
    b = mp - a * mI
    return (box(a, r) * I + box(b, r)).astype(np.float32)


def pchip_lut(points, n=1024):
    from scipy.interpolate import PchipInterpolator
    pts = np.asarray(sorted(points, key=lambda q: q[0]), dtype=np.float64)
    keep = np.append(np.diff(pts[:, 0]) > 1e-6, True)      # duplicate x: the later point wins
    pts = pts[keep]
    f = PchipInterpolator(pts[:, 0], pts[:, 1], extrapolate=True)
    xs = np.linspace(0, 1, n)
    return xs.astype(np.float32), np.clip(f(xs), 0.0, 1.0).astype(np.float32)


_LUT_N = 1 << 16


def apply_lut1d(x, xs, ys):
    """Evaluate a 1-D curve sampled at uniform xs on [0, 1].  The curve is
    resampled to 65537 entries and looked up by index (error < 1e-5, ~10x
    faster than np.interp on 24 MP); values outside [0, 1] continue linearly."""
    x = np.asarray(x, dtype=np.float32)
    fine = np.interp(np.linspace(0, 1, _LUT_N + 1), xs, ys).astype(np.float32)
    idx = x * np.float32(_LUT_N)
    idx += np.float32(0.5)
    np.clip(idx, 0, _LUT_N, out=idx)
    out = fine[idx.astype(np.int32)]
    lo_v, hi_v = float(x.min()) if x.size else 0.0, float(x.max()) if x.size else 0.0
    if hi_v > 1.0:
        slope = float((ys[-1] - ys[-2]) / (xs[-1] - xs[-2]))
        out = np.where(x > 1, ys[-1] + (x - 1) * slope, out)
    if lo_v < 0.0:
        lo_slope = float((ys[1] - ys[0]) / (xs[1] - xs[0]))
        out = np.where(x < 0, ys[0] + x * lo_slope, out)
    return out.astype(np.float32, copy=False)


def hue_lookup(h, centres, vals, res=10):
    """Periodic piecewise-linear function of hue (degrees) via a 0.1 deg table."""
    grid = np.arange(360 * res, dtype=np.float32) / res
    table = np.interp(grid, centres, vals, period=360.0).astype(np.float32)
    idx = (h * np.float32(res)).astype(np.int32)
    idx %= 360 * res
    return table[idx]


# --------------------------------------------------------------------------
# OkLab (Björn Ottosson, public domain formulas)
# --------------------------------------------------------------------------

_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]], dtype=np.float64)
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]], dtype=np.float64)


class OkLab:
    def __init__(self, space: cms.ColorSpace):
        to_srgb = np.linalg.inv(cms.LINEAR_SRGB.to_xyz_d50) @ space.to_xyz_d50
        self.m1 = (_M1 @ to_srgb).astype(np.float32)
        self.m1_inv = np.linalg.inv(_M1 @ to_srgb).astype(np.float32)
        self.m2 = _M2.astype(np.float32)
        self.m2_inv = np.linalg.inv(_M2).astype(np.float32)

    def from_linear(self, rgb):
        lms = cms.apply_matrix(rgb, self.m1)
        return cms.apply_matrix(np.cbrt(lms), self.m2)

    def to_linear(self, lab):
        lms = cms.apply_matrix(lab, self.m2_inv)
        return cms.apply_matrix(lms * lms * lms, self.m1_inv)


def _hsv_hue_to_oklab_angle(h_deg):
    """Map a UI colour-wheel hue (HSV convention: 0 red, 120 green, 240 blue)
    to the OkLab hue angle of that fully saturated sRGB colour."""
    import colorsys
    r, g, b = colorsys.hsv_to_rgb((h_deg % 360) / 360.0, 1.0, 1.0)
    lin = cms.srgb_decode(np.array([[r, g, b]], dtype=np.float32))
    lab = OkLab(cms.LINEAR_SRGB).from_linear(lin)[0]
    return math.atan2(float(lab[2]), float(lab[1]))


# HSL band centres in OkLab hue degrees (measured from sRGB primaries/secondaries)
BAND_CENTRES = {"red": 25.0, "orange": 55.0, "yellow": 100.0, "green": 140.0,
                "aqua": 195.0, "blue": 260.0, "purple": 295.0, "magenta": 335.0}


# --------------------------------------------------------------------------
# Render context
# --------------------------------------------------------------------------


@dataclass
class Context:
    space: cms.ColorSpace
    full_size: tuple                 # (W, H) of the full-resolution source
    films: object | None = None      # film.FilmRegistry
    luts: object | None = None       # lut.LUTRegistry
    analysis: np.ndarray | None = None   # small full-frame linear image (for dehaze airlight)
    cache: dict = field(default_factory=dict)


@dataclass
class Frame:
    """Where the processed region sits in the full frame at this level."""
    level_w: int
    level_h: int
    x0: int
    y0: int
    level: float                    # level pixels per full-resolution pixel

    def full_long_edge(self):
        return max(self.level_w, self.level_h) / self.level

    def px(self, full_px):
        """Convert a size in full-resolution pixels to level pixels."""
        return full_px * self.level

    def radial(self, h, w):
        ys = (np.arange(h, dtype=np.float32) + self.y0 + 0.5)[:, None]
        xs = (np.arange(w, dtype=np.float32) + self.x0 + 0.5)[None, :]
        cx, cy = self.level_w / 2.0, self.level_h / 2.0
        dx, dy = xs - cx, ys - cy
        half_diag = math.hypot(cx, cy)
        r_circ = np.sqrt(dx * dx + dy * dy) / half_diag
        r_ell = np.sqrt((dx / cx) ** 2 + (dy / cy) ** 2) / math.sqrt(2.0)
        return r_circ.astype(np.float32), r_ell.astype(np.float32)


def _rel(full_frac, frame):
    """A radius given as a fraction of the full-resolution long edge, in level px."""
    return frame.px(full_frac * frame.full_long_edge())


# --------------------------------------------------------------------------
# Linear-light stages
# --------------------------------------------------------------------------


def lens_maps(recipe, frame, h, w, creative=False):
    """Per-channel sampling maps for distortion and lateral CA (or None)."""
    lens, fx = recipe["lens"], recipe["effects"]
    if creative:
        a = fx["chromatic_aberration"] / 100.0
        if a == 0:
            return None
        k1, sr, sb = 0.0, 1.0 + a * 0.004, 1.0 - a * 0.004
    else:
        if lens["distortion"] == 0 and lens["ca_red"] == 0 and lens["ca_blue"] == 0:
            return None
        k1 = -lens["distortion"] / 100.0 * 0.15
        sr = 1.0 - lens["ca_red"] / 100.0 * 0.003
        sb = 1.0 - lens["ca_blue"] / 100.0 * 0.003
    ys = (np.arange(h, dtype=np.float32) + frame.y0 + 0.5)[:, None]
    xs = (np.arange(w, dtype=np.float32) + frame.x0 + 0.5)[None, :]
    cx, cy = frame.level_w / 2.0, frame.level_h / 2.0
    hd = math.hypot(cx, cy)
    dx, dy = (xs - cx) / hd, (ys - cy) / hd
    r2 = dx * dx + dy * dy
    base = 1.0 + k1 * r2
    maps = []
    for s in (sr, 1.0, sb):
        f = base * s
        mx = (cx + dx * f * hd - 0.5).astype(np.float32)
        my = (cy + dy * f * hd - 0.5).astype(np.float32)
        maps.append((np.broadcast_to(mx, (h, w)).copy(), np.broadcast_to(my, (h, w)).copy()))
    return maps


def remap_channels(src, maps, border):
    out = np.empty(maps[0][0].shape + (3,), dtype=np.float32)
    for c in range(3):
        out[..., c] = cv2.remap(np.ascontiguousarray(src[..., c]), maps[c][0], maps[c][1],
                                interpolation=cv2.INTER_CUBIC, borderMode=border)
    return out


def _planck_xy(T):
    """CIE xy of a blackbody (Kim et al. cubic approximation, 1667-25000 K)."""
    if T <= 4000:
        x = -0.2661239e9 / T ** 3 - 0.2343589e6 / T ** 2 + 0.8776956e3 / T + 0.179910
    else:
        x = -3.0258469e9 / T ** 3 + 2.1070379e6 / T ** 2 + 0.2226347e3 / T + 0.240390
    if T <= 2222:
        y = -1.1063814 * x ** 3 - 1.34811020 * x ** 2 + 2.18555832 * x - 0.20219683
    elif T <= 4000:
        y = -0.9549476 * x ** 3 - 1.37418593 * x ** 2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x ** 3 - 5.87338670 * x ** 2 + 3.75112997 * x - 0.37001483
    return x, y


def _daylight_xy(T):
    T = min(max(T, 4000.0), 25000.0)
    if T <= 7000:
        x = -4.6070e9 / T ** 3 + 2.9678e6 / T ** 2 + 0.09911e3 / T + 0.244063
    else:
        x = -2.0064e9 / T ** 3 + 1.9018e6 / T ** 2 + 0.24748e3 / T + 0.237040
    y = -3.000 * x * x + 2.870 * x - 0.275
    return x, y


def _illuminant_xy(T):
    return _daylight_xy(T) if T >= 4000 else _planck_xy(T)


def white_balance_matrix(temperature, tint, space):
    """Relative temperature/tint as a Bradford adaptation in working primaries.
    +temperature = warmer rendering (as if the scene light were cooler)."""
    if temperature == 0 and tint == 0:
        return None
    base_mired = 1e6 / 6504.0
    T_src = 1e6 / max(base_mired - temperature * 0.9, 25.0)
    xs, ys = _illuminant_xy(6504.0)
    xt, yt = _illuminant_xy(T_src)
    # tint: offset perpendicular to the locus in uv (approx. along +/- green)
    W_dst = cms.xy_to_XYZ(xs, ys)
    W_src = cms.xy_to_XYZ(xt, yt)
    A = cms.bradford_adapt(W_src, W_dst)
    M = space.to_xyz_d50
    # work in XYZ adapted from D50 PCS to D65 for the adaptation itself
    to65 = cms.bradford_adapt(cms.D50, cms.xy_to_XYZ(*cms.D65_XY))
    full = np.linalg.inv(M) @ np.linalg.inv(to65) @ A @ to65 @ M
    if tint:
        g = 2.0 ** (-tint / 100.0 * 0.35)
        full = np.diag([1.0, g, 1.0]) @ full
    # preserve luminance of neutral grey
    luma = space.luma.astype(np.float64)
    full = full / float(luma @ (full @ np.ones(3)))
    return full.astype(np.float32)


def tone_lut(basic):
    """1-D tone function on perceptual (sRGB-encoded) luminance."""
    hl, sh = basic["highlights"] / 100.0, basic["shadows"] / 100.0
    wh, bl = basic["whites"] / 100.0, basic["blacks"] / 100.0
    c = basic["contrast"] / 100.0
    x = np.linspace(0.0, 1.0, 2049, dtype=np.float64)
    y = x.copy()
    y += sh * 0.20 * np.exp(-((x - 0.22) / 0.16) ** 2) * (1 - x)
    y += hl * 0.20 * np.exp(-((x - 0.78) / 0.16) ** 2) * x
    y += bl * 0.10 * (1 - x) ** 5
    y += wh * 0.10 * x ** 5
    if c:
        k = c * 0.7 if c > 0 else c * 0.5
        p = 0.46
        y = np.clip(y, 0, None)
        lo = y <= p
        y2 = np.empty_like(y)
        y2[lo] = p * (y[lo] / p) ** (1 + k)
        yy = np.clip((1 - y[~lo]) / (1 - p), 0, None)
        y2[~lo] = 1 - (1 - p) * yy ** (1 + k)
        y = y2
    y = np.maximum.accumulate(y)       # enforce monotonic (no tonal inversions)
    return x.astype(np.float32), y.astype(np.float32)


def apply_tone(img, basic, space):
    if not any(basic[k] for k in ("highlights", "shadows", "whites", "blacks", "contrast")):
        return img
    xs, ys = tone_lut(basic)
    Y = cms.apply_matrix(img, np.tile(space.luma, (3, 1)))[..., 0]
    Yp = cms.srgb_encode(Y)
    Y2 = cms.srgb_decode(apply_lut1d(Yp, xs, ys))
    eps = 1e-5
    ratio = np.where(Y > eps, Y2 / np.maximum(Y, eps), 1.0).astype(np.float32)
    out = img * ratio[..., None]
    small = Y <= eps
    if np.any(small):
        out[small] = img[small] + (Y2[small] - Y[small])[:, None]
    return out


def dehaze(img, amount, frame, ctx, airlight):
    d = amount / 100.0
    if d == 0:
        return img
    A = np.asarray(airlight, dtype=np.float32)
    if d < 0:
        k = -d * 0.55
        return (img * (1 - k) + A[None, None, :] * k).astype(np.float32)
    patch = max(1, int(round(_rel(0.004, frame))))
    norm = np.clip(img / np.maximum(A, 1e-3), 0, None)
    dark = cv2.erode(np.min(norm, axis=-1), np.ones((2 * patch + 1, 2 * patch + 1), np.uint8),
                     borderType=cv2.BORDER_REFLECT)
    t = 1.0 - d * 0.92 * dark
    lum = cms.apply_matrix(img, np.tile(ctx.space.luma, (3, 1)))[..., 0]
    t = guided_filter(np.clip(lum, 0, 1), t.astype(np.float32), patch * 3, 1e-3)
    t = np.clip(t, 0.15, 1.0)[..., None]
    return ((img - A) / t + A).astype(np.float32)


def estimate_airlight(img):
    """Brightest 0.1% of the dark channel (He et al.). img: small linear image."""
    dark = cv2.erode(np.min(img, axis=-1), np.ones((7, 7), np.uint8))
    n = max(1, dark.size // 1000)
    idx = np.argpartition(dark.ravel(), -n)[-n:]
    A = img.reshape(-1, 3)[idx].mean(axis=0)
    return np.clip(A, 0.05, 4.0)


def halation(img, fx, frame, space):
    """Highlight glow bleeding back through the emulsion, red-weighted.
    Simplified from ComfyUI-Darkroom's physically derived halation node:
    a Gaussian scatter of the over-threshold luminance, tinted red-orange and
    composited additively in linear light."""
    amt = fx["halation_amount"] / 100.0
    if amt == 0:
        return img
    thr = float(cms.srgb_decode(np.float32(0.35 + fx["halation_threshold"] / 100.0 * 0.6)))
    Y = cms.apply_matrix(img, np.tile(space.luma, (3, 1)))[..., 0]
    excess = np.clip(Y - thr, 0, None).astype(np.float32)
    sigma = _rel(0.002 + fx["halation_radius"] / 100.0 * 0.02, frame)
    glow = gblur(excess, sigma)
    tint = np.array([1.0, 0.30, 0.08], dtype=np.float32)
    return (img + amt * 1.2 * glow[..., None] * tint[None, None, :]).astype(np.float32)


def vignette(img, fx, frame):
    a = fx["vignette_amount"] / 100.0
    if a == 0:
        return img
    h, w = img.shape[:2]
    r_circ, r_ell = frame.radial(h, w)
    t = (fx["vignette_roundness"] + 100.0) / 200.0
    r = r_ell * (1 - t) + r_circ * t
    mid = 0.15 + fx["vignette_midpoint"] / 100.0 * 0.75
    f = 0.05 + fx["vignette_feather"] / 100.0 * 0.9
    m = smoothstep(mid - f / 2, mid + f / 2, r)
    return (img * np.exp2(a * 1.6 * m)[..., None]).astype(np.float32)


def lens_vignetting(img, lens, frame):
    v = lens["vignette_correction"] / 100.0
    if v == 0:
        return img
    h, w = img.shape[:2]
    r_circ, _ = frame.radial(h, w)
    return (img * np.exp2(v * r_circ * r_circ)[..., None]).astype(np.float32)


# --------------------------------------------------------------------------
# OkLab stages
# --------------------------------------------------------------------------


def apply_match(lab, match):
    s = match["strength"] / 100.0
    data = match.get("data")
    if s == 0 or not data:
        return lab
    out = lab.copy()
    sm, ss = np.array(data["src_mean"], np.float32), np.array(data["src_std"], np.float32)
    rm, rs = np.array(data["ref_mean"], np.float32), np.array(data["ref_std"], np.float32)
    for c in (1, 2):
        out[..., c] = (lab[..., c] - sm[c]) * (rs[c] / ss[c]) + rm[c]
    tl = data.get("tone_lut") or []
    if len(tl) >= 2:
        xs = np.linspace(0, 1, len(tl), dtype=np.float32)
        out[..., 0] = apply_lut1d(lab[..., 0], xs, np.asarray(tl, np.float32))
    else:
        out[..., 0] = (lab[..., 0] - sm[0]) * (rs[0] / ss[0]) + rm[0]
    return (lab + (out - lab) * s).astype(np.float32)


def clarity_texture(lab, fx, frame):
    c, t = fx["clarity"] / 100.0, fx["texture"] / 100.0
    if c == 0 and t == 0:
        return lab
    L = np.ascontiguousarray(lab[..., 0])
    out = L.copy()
    if c:
        r = max(1.0, _rel(0.006, frame))
        base = guided_filter(L, L, r, 4e-3)
        mid = 0.35 + 0.65 * (1.0 - np.clip(2 * L - 1, -1, 1) ** 2)
        out += c * 0.9 * (L - base) * mid
    if t:
        r = max(1.0, _rel(0.0012, frame))
        base = guided_filter(L, L, r, 2.5e-4)
        out += t * 0.9 * (L - base)
    lab = lab.copy()
    lab[..., 0] = out
    return lab


def hsl_and_saturation(lab, recipe):
    hsl, basic = recipe["hsl"], recipe["basic"]
    bands_active = any(hsl[f"{b}_{k}"] for b in HSL_BANDS for k in ("hue", "saturation", "luminance"))
    vib, sat = basic["vibrance"] / 100.0, basic["saturation"] / 100.0
    if not bands_active and vib == 0 and sat == 0:
        return lab
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    C = np.sqrt(a * a + b * b)
    h = np.degrees(np.arctan2(b, a)) % 360.0
    newC, newh, newL = C, h, L
    if bands_active:
        centres = np.array([BAND_CENTRES[n] for n in HSL_BANDS], dtype=np.float32)
        vals = {k: np.array([hsl[f"{n}_{k}"] for n in HSL_BANDS], dtype=np.float32) / 100.0
                for k in ("hue", "saturation", "luminance")}
        cw = np.clip(C / 0.05, 0, 1)
        dh = hue_lookup(h, centres, vals["hue"])
        ds = hue_lookup(h, centres, vals["saturation"])
        dl = hue_lookup(h, centres, vals["luminance"])
        newh = h + dh * 30.0 * cw
        newC = C * np.clip(1.0 + ds, 0.0, None)
        newL = L * (1.0 + dl * 0.35 * cw)
    if vib:
        skin = np.exp(-(((h - 55.0 + 180.0) % 360.0 - 180.0) / 20.0) ** 2)
        muted = np.clip(1.0 - newC / 0.22, 0, 1)
        prot = 1.0 - 0.65 * skin if vib > 0 else 1.0
        newC = newC * (1.0 + vib * muted * prot)
    if sat:
        newC = newC * max(0.0, 1.0 + sat)
    hr = np.radians(newh)
    out = np.empty_like(lab)
    out[..., 0] = newL
    out[..., 1] = newC * np.cos(hr)
    out[..., 2] = newC * np.sin(hr)
    return out


def color_grading(lab, cg):
    active = [z for z in ZONES if cg[f"{z}_saturation"] or cg[f"{z}_luminance"]]
    if not active:
        return lab
    L = np.clip(lab[..., 0], 0, 1)
    mid = 0.5 - cg["balance"] / 100.0 * 0.25
    w = 0.12 + cg["blending"] / 100.0 * 0.35
    masks = {"shadows": 1.0 - smoothstep(mid - w, mid + w * 0.3, L),
             "highlights": smoothstep(mid - w * 0.3, mid + w, L),
             "midtones": np.exp(-((L - mid) / (w * 1.1)) ** 2),
             "global": np.ones_like(L)}
    out = lab.copy()
    for z in active:
        m = masks[z]
        s = cg[f"{z}_saturation"] / 100.0 * 0.05
        ang = _hsv_hue_to_oklab_angle(cg[f"{z}_hue"])
        out[..., 1] += m * s * math.cos(ang)
        out[..., 2] += m * s * math.sin(ang)
        out[..., 0] += m * cg[f"{z}_luminance"] / 100.0 * 0.12
    return out


def noise_reduction(lab, det, frame):
    nl, nc = det["nr_luminance"] / 100.0, det["nr_color"] / 100.0
    if nl == 0 and nc == 0:
        return lab
    out = lab.copy()
    L = np.ascontiguousarray(lab[..., 0])
    if nl:
        r = max(1.0, frame.px(1.0 + nl * 3.0))
        out[..., 0] = guided_filter(L, L, r, (0.004 + 0.03 * nl) ** 2)
    if nc:
        r = max(1.0, frame.px(2.0 + nc * 10.0))
        for c in (1, 2):
            ch = np.ascontiguousarray(lab[..., c])
            out[..., c] = guided_filter(L, ch, r, 1e-3 + nc * 5e-3)
    return out


def sharpen(lab, det, frame):
    amt = det["sharpen_amount"] / 100.0
    if amt == 0:
        return lab
    L = np.ascontiguousarray(lab[..., 0])
    sigma = max(0.3, frame.px(det["sharpen_radius"]))
    blur = cv2.GaussianBlur(L, (0, 0), sigma, borderType=cv2.BORDER_REFLECT)
    detail = L - blur
    if det["sharpen_masking"]:
        g = cv2.GaussianBlur(L, (0, 0), max(0.7, sigma))
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        mag = np.sqrt(gx * gx + gy * gy)
        thr = det["sharpen_masking"] / 100.0 * 0.25
        detail = detail * smoothstep(thr * 0.4, thr, mag)
    out = lab.copy()
    out[..., 0] = L + amt * detail
    return out


# --------------------------------------------------------------------------
# Encoded-domain stages
# --------------------------------------------------------------------------


def tone_curves(E, curves):
    m = curves["master"]
    if m != IDENTITY_CURVE:
        xs, ys = pchip_lut(m)
        E = apply_lut1d(E, xs, ys)
    if any(curves[ch] != IDENTITY_CURVE for ch in ("red", "green", "blue")):
        E = E.copy()
    for i, ch in enumerate(("red", "green", "blue")):
        pts = curves[ch]
        if pts != IDENTITY_CURVE:
            xs, ys = pchip_lut(pts)
            E[..., i] = apply_lut1d(E[..., i], xs, ys)
    return E


def lift_gamma_gain(E, lgg):
    if not any(lgg.values()):
        return E
    out = np.empty_like(E)
    for i, c in enumerate(("r", "g", "b")):
        lift = (lgg["lift_m"] + lgg[f"lift_{c}"]) / 100.0 * 0.2
        gamma = 2.0 ** (-(lgg["gamma_m"] + lgg[f"gamma_{c}"]) / 100.0)
        gain = 1.0 + (lgg["gain_m"] + lgg[f"gain_{c}"]) / 100.0 * 0.5
        x = E[..., i]
        v = x + lift * (1.0 - x)
        out[..., i] = gain * np.sign(v) * np.abs(v) ** gamma
    return out


def fade(E, amount):
    f = amount / 100.0
    if f == 0:
        return E
    lo, hi = 0.08 * f, 1.0 - 0.05 * f
    return (lo + E * (hi - lo)).astype(np.float32)


_TILE = 256


def tiled_noise(x0, y0, h, w, n, seed, stream):
    """Unit normal noise that depends only on ABSOLUTE pixel position, so a crop,
    a strip and the whole frame see identical grain at the same pixel."""
    out = np.empty((h, w, n), np.float32)
    tx0, ty0 = x0 // _TILE, y0 // _TILE
    tx1, ty1 = (x0 + w - 1) // _TILE, (y0 + h - 1) // _TILE
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            rng = np.random.default_rng([int(seed), int(stream), int(tx), int(ty)])
            tile = rng.standard_normal((_TILE, _TILE, n), dtype=np.float32)
            gx0, gy0 = tx * _TILE, ty * _TILE
            sx0, sy0 = max(x0, gx0), max(y0, gy0)
            sx1, sy1 = min(x0 + w, gx0 + _TILE), min(y0 + h, gy0 + _TILE)
            out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = tile[sy0 - gy0:sy1 - gy0, sx0 - gx0:sx1 - gx0]
    return out


def _blur_std(sigma):
    k = int(round(sigma * 8 + 1)) | 1
    g = cv2.getGaussianKernel(k, sigma, cv2.CV_64F).ravel()
    return float((g * g).sum())      # std of 2-D blurred unit noise = (sum g^2)^(2/2)


def grain(E, fx, frame):
    """Luminance-weighted film grain in the encoded domain.  Grain size is
    defined relative to the frame (like ComfyUI-Darkroom's resolution-aware
    grain), so the look does not change with megapixels."""
    amt = fx["grain_amount"] / 100.0
    if amt == 0:
        return E
    h, w = E.shape[:2]
    sigma_full = (0.6 + fx["grain_size"] / 100.0 * 2.4) * frame.full_long_edge() / 6000.0
    sigma = sigma_full * frame.level
    atten = min(1.0, sigma / 0.5)
    sigma = max(sigma, 0.5)
    seed = int(fx["grain_seed"])
    rough = fx["grain_roughness"] / 100.0
    lvl = int(round(frame.level * 1000))

    def field_(n, stream):
        a = tiled_noise(frame.x0, frame.y0, h, w, n, seed, stream * 100000 + lvl)
        a = cv2.GaussianBlur(a, (0, 0), sigma).reshape(h, w, n) / _blur_std(sigma)
        if rough:
            s2 = max(0.5, sigma * 0.5)
            b = tiled_noise(frame.x0, frame.y0, h, w, n, seed, stream * 100000 + lvl + 50000)
            b = cv2.GaussianBlur(b, (0, 0), s2).reshape(h, w, n) / _blur_std(s2)
            wa, wb = 1 - rough * 0.5, rough * 0.5
            a = (a * wa + b * wb) / math.hypot(wa, wb)
        return a

    Y = np.clip(E.mean(axis=-1, keepdims=True), 0, 1)
    mask = 0.25 + 0.75 * 4.0 * Y * (1.0 - Y)
    out = E + amt * 0.045 * atten * field_(1, 1) * mask
    col = fx["grain_color"] / 100.0
    if col:
        out = out + amt * 0.025 * col * atten * field_(3, 2) * mask
    return out.astype(np.float32)


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def margin_for(recipe, level, full_long):
    """Extra pixels needed around a crop so spatial filters see real context."""
    fx, det = recipe["effects"], recipe["detail"]
    m = 2.0
    if fx["clarity"]:
        m = max(m, 3 * 0.006 * full_long)
    if fx["texture"]:
        m = max(m, 3 * 0.0012 * full_long)
    if fx["halation_amount"]:
        m = max(m, 3 * (0.002 + fx["halation_radius"] / 100 * 0.02) * full_long)
    if fx["dehaze"] > 0:
        m = max(m, 12 * 0.004 * full_long)
    if det["nr_color"] or det["nr_luminance"]:
        m = max(m, 30)
    if det["sharpen_amount"]:
        m = max(m, 4 * det["sharpen_radius"] + 4)
    if fx["grain_amount"]:
        m = max(m, 4 * 3.0 * full_long / 6000.0 + 4)
    return int(min(math.ceil(m * level), 600))


def _airlight(recipe, ctx):
    key = ("airlight", recipe["basic"]["exposure"], recipe["basic"]["temperature"], recipe["basic"]["tint"])
    if key in ctx.cache:
        return ctx.cache[key]
    small = ctx.analysis
    if small is None:
        A = np.array([1.0, 1.0, 1.0], np.float32)
    else:
        img = small
        M = white_balance_matrix(recipe["basic"]["temperature"], recipe["basic"]["tint"], ctx.space)
        if M is not None:
            img = cms.apply_matrix(img, M)
        img = img * np.float32(2.0 ** recipe["basic"]["exposure"])
        A = estimate_airlight(img)
    ctx.cache[key] = A
    return A


def to_srgb_encoded(lin, space):
    if not space.same_primaries(cms.SRGB, tol=1e-5):
        lin = cms.convert_linear(lin, space, cms.SRGB)
    return cms.srgb_encode(lin)


def from_srgb_encoded(E, space):
    lin = cms.srgb_decode(E)
    if not space.same_primaries(cms.SRGB, tol=1e-5):
        lin = cms.convert_linear(lin, cms.SRGB, space)
    return lin


def render(src, recipe, ctx: Context, region=None, level=None, stop=None):
    """Render `recipe` on `src` (a linear image at some level of the full frame).

    region: (x, y, w, h) in src pixels, or None for the whole image.
    level:  src pixels per full-resolution pixel (default from ctx.full_size).
    stop:   "pre_match" returns the linear image right before the OkLab stage
            (used to measure the photo for reference matching).
    Returns a float32 (h, w, 3) linear image in the working space.
    """
    H, W = src.shape[:2]
    if level is None:
        level = W / float(ctx.full_size[0])
    if region is None:
        region = (0, 0, W, H)
    rx, ry, rw, rh = [int(v) for v in region]
    full_long = max(ctx.full_size)
    m = margin_for(recipe, level, full_long)
    ex0, ey0 = max(0, rx - m), max(0, ry - m)
    ex1, ey1 = min(W, rx + rw + m), min(H, ry + rh + m)
    frame = Frame(W, H, ex0, ey0, level)
    eh, ew = ey1 - ey0, ex1 - ex0

    lens = recipe["lens"]
    border = (cv2.BORDER_REPLICATE if lens["fill_mode"].endswith("repeat edge pixels")
              else cv2.BORDER_CONSTANT)
    maps = lens_maps(recipe, frame, eh, ew)
    if maps is not None:
        img = remap_channels(src, maps, border)
    else:
        img = np.array(src[ey0:ey1, ex0:ex1], dtype=np.float32, copy=True)

    space = ctx.space
    basic, fx, det = recipe["basic"], recipe["effects"], recipe["detail"]

    # ---- linear light ----
    img = lens_vignetting(img, lens, frame)
    M = white_balance_matrix(basic["temperature"], basic["tint"], space)
    if M is not None:
        img = cms.apply_matrix(img, M)
    if basic["exposure"]:
        img = img * np.float32(2.0 ** basic["exposure"])
    img = apply_tone(img, basic, space)
    if fx["dehaze"]:
        img = dehaze(img, fx["dehaze"], frame, ctx, _airlight(recipe, ctx))
    img = halation(img, fx, frame, space)
    if stop == "pre_match":
        return np.ascontiguousarray(img[ry - ey0:ry - ey0 + rh, rx - ex0:rx - ex0 + rw])

    # ---- OkLab A ----
    cg = recipe["color_grading"]
    needs_lab = (recipe["match"]["strength"] and recipe["match"].get("data")) or fx["clarity"] or fx["texture"] \
        or basic["vibrance"] or basic["saturation"] \
        or any(v for k, v in recipe["hsl"].items()) \
        or any(cg[f"{z}_saturation"] or cg[f"{z}_luminance"] for z in ZONES)
    ok = None
    if needs_lab:
        ok = OkLab(space)
        lab = ok.from_linear(img)
        lab = apply_match(lab, recipe["match"])
        lab = clarity_texture(lab, fx, frame)
        lab = hsl_and_saturation(lab, recipe)
        lab = color_grading(lab, cg)
        img = ok.to_linear(lab)

    # ---- sRGB-encoded creative stage ----
    curves_active = any(recipe["tone_curve"][c] != IDENTITY_CURVE for c in recipe["tone_curve"])
    lgg_active = any(recipe["lgg"].values())
    film_active = recipe["film"]["stock"] != "None" and recipe["film"]["strength"] > 0 and ctx.films is not None
    lut_active = recipe["lut"]["name"] != "None" and recipe["lut"]["strength"] > 0 and ctx.luts is not None
    if curves_active or lgg_active or film_active or lut_active or fx["fade"]:
        E = to_srgb_encoded(img, space)
        if curves_active:
            E = tone_curves(E, recipe["tone_curve"])
        if lgg_active:
            E = lift_gamma_gain(E, recipe["lgg"])
        if film_active:
            E = ctx.films.apply(recipe["film"]["stock"], E, recipe["film"]["strength"] / 100.0)
        if lut_active:
            E = ctx.luts.apply(recipe["lut"]["name"], E, recipe["lut"]["strength"] / 100.0)
        E = fade(E, fx["fade"])
        img = from_srgb_encoded(E, space)

    # ---- OkLab B: detail ----
    if det["nr_luminance"] or det["nr_color"] or det["sharpen_amount"]:
        ok = ok or OkLab(space)
        lab = ok.from_linear(img)
        lab = noise_reduction(lab, det, frame)
        lab = sharpen(lab, det, frame)
        img = ok.to_linear(lab)

    # ---- linear: optics effects ----
    img = vignette(img, fx, frame)
    cmaps = lens_maps(recipe, frame, eh, ew, creative=True)   # radial about the full-frame centre
    if cmaps is not None:
        shifted = []
        for c in range(3):
            mx, my = cmaps[c]
            shifted.append((mx - ex0, my - ey0))
        img = remap_channels(img, shifted, cv2.BORDER_REPLICATE)

    # ---- grain ----
    if fx["grain_amount"]:
        E = to_srgb_encoded(img, space)
        E = grain(E, fx, frame)
        img = from_srgb_encoded(E, space)

    # trim margins
    tx, ty = rx - ex0, ry - ey0
    out = img[ty:ty + rh, tx:tx + rw]
    assert out.shape[:2] == (rh, rw), (out.shape, rh, rw)
    return np.ascontiguousarray(out, dtype=np.float32)


def render_tiled(src, recipe, ctx, level=1.0, strip=512, workers=None):
    """Full-frame render in horizontal strips (with overlap margins) on a thread
    pool.  NumPy/OpenCV release the GIL, so this uses all cores, and peak memory
    is a few strips instead of a dozen full-frame temporaries.  The result is
    identical to a single-pass render (see tests)."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    H, W = src.shape[:2]
    out = np.empty((H, W, 3), np.float32)
    rows = list(range(0, H, strip))

    def job(y):
        h = min(strip, H - y)
        out[y:y + h] = render(src, recipe, ctx, region=(0, y, W, h), level=level)

    if recipe["effects"]["dehaze"]:
        _airlight(recipe, ctx)            # compute once before threads start
    n = workers or max(1, min(2, os.cpu_count() or 1))   # memory-bandwidth bound: 2 is the sweet spot
    with ThreadPoolExecutor(n) as ex:
        list(ex.map(job, rows))
    return out


def render_alpha(alpha, recipe, ctx, region=None, level=None):
    """Alpha follows lens geometry only; everything else leaves it untouched."""
    if alpha is None:
        return None
    H, W = alpha.shape[:2]
    if level is None:
        level = W / float(ctx.full_size[0])
    rx, ry, rw, rh = region or (0, 0, W, H)
    frame = Frame(W, H, rx, ry, level)
    maps = lens_maps(recipe, frame, rh, rw)
    if maps is None:
        return alpha[ry:ry + rh, rx:rx + rw]
    return cv2.remap(np.ascontiguousarray(alpha, dtype=np.float32), maps[1][0], maps[1][1],
                     interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)


def to_display(linear, space):
    """Working linear -> sRGB-encoded uint8 for the browser preview ONLY."""
    lin = cms.convert_linear(linear, space, cms.SRGB)
    E = cms.srgb_encode(np.clip(lin, 0.0, 1.0))
    return (E * 255.0 + 0.5).astype(np.uint8)
