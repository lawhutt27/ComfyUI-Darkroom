"""Scopes rendered as small images from the (display-encoded) preview."""

from __future__ import annotations

import cv2
import numpy as np

BG = np.array([22, 22, 24], np.uint8)


def histogram(disp, w=512, h=150):
    canvas = np.zeros((h, w, 3), np.float32)
    small = disp[::2, ::2].reshape(-1, 3)
    cols = [(1.0, 0.25, 0.25), (0.3, 1.0, 0.35), (0.3, 0.5, 1.0)]
    hists = [np.bincount(small[:, c], minlength=256).astype(np.float32) for c in range(3)]
    peak = max(np.percentile(np.concatenate(hists), 99.5), 1.0)
    xs = (np.arange(256) * (w - 1) / 255).astype(int)
    for c in range(3):
        hh = np.clip(hists[c] / peak, 0, 1) * (h - 4)
        for i in range(256):
            x0 = xs[i]
            x1 = xs[i + 1] if i < 255 else w
            canvas[h - int(hh[i]):, x0:x1] += np.array(cols[c], np.float32) * 0.55
    out = (np.clip(canvas, 0, 1) * 230).astype(np.uint8)
    out = np.maximum(out, BG)
    for q in (0.25, 0.5, 0.75):
        out[:, int(q * (w - 1))] = (60, 60, 64)
    clip_hi = np.mean(np.any(small >= 255, axis=1)) * 100
    clip_lo = np.mean(np.all(small <= 0, axis=1)) * 100
    if clip_hi > 0.05:
        cv2.rectangle(out, (w - 14, 4), (w - 4, 14), (255, 80, 80), -1)
    if clip_lo > 0.05:
        cv2.rectangle(out, (4, 4), (14, 14), (80, 140, 255), -1)
    return out


def waveform(disp, w=512, h=150):
    Y = (0.2126 * disp[..., 0] + 0.7152 * disp[..., 1] + 0.0722 * disp[..., 2]).astype(np.float32)
    Y = cv2.resize(Y, (w, max(1, int(Y.shape[0] * w / Y.shape[1]))), interpolation=cv2.INTER_AREA)
    acc = np.zeros((h, w), np.float32)
    rows = np.clip(((255 - Y) / 255 * (h - 1)).astype(int), 0, h - 1)
    cols = np.broadcast_to(np.arange(w), rows.shape)
    np.add.at(acc, (rows.ravel(), cols.ravel()), 1.0)
    acc = np.sqrt(acc / max(acc.max(), 1))
    out = np.empty((h, w, 3), np.uint8)
    out[...] = BG
    g = (acc * 220).astype(np.uint8)
    out[..., 0] = np.maximum(out[..., 0], g // 2)
    out[..., 1] = np.maximum(out[..., 1], g)
    out[..., 2] = np.maximum(out[..., 2], g // 2)
    for q in (0.1, 0.5, 0.9):
        out[int(q * (h - 1)), :] = (60, 60, 64)
    return out


def vectorscope(disp, size=220):
    f = disp[::3, ::3].reshape(-1, 3).astype(np.float32) / 255.0
    cb = -0.1146 * f[:, 0] - 0.3854 * f[:, 1] + 0.5 * f[:, 2]
    cr = 0.5 * f[:, 0] - 0.4542 * f[:, 1] - 0.0458 * f[:, 2]
    c = size // 2
    xs = np.clip((c + cb * size * 0.9).astype(int), 0, size - 1)
    ys = np.clip((c - cr * size * 0.9).astype(int), 0, size - 1)
    acc = np.zeros((size, size), np.float32)
    np.add.at(acc, (ys, xs), 1.0)
    acc = np.sqrt(acc / max(acc.max(), 1))
    out = np.empty((size, size, 3), np.uint8)
    out[...] = BG
    cv2.circle(out, (c, c), int(size * 0.45), (55, 55, 60), 1)
    cv2.circle(out, (c, c), int(size * 0.225), (45, 45, 50), 1)
    # skin-tone line (~123 degrees on a standard vectorscope)
    ang = np.radians(123.0)
    cv2.line(out, (c, c), (int(c + np.cos(ang) * size * 0.45), int(c - np.sin(ang) * size * 0.45)),
             (120, 90, 60), 1)
    lit = (acc * 235).astype(np.uint8)
    col = np.stack([lit, lit, lit], -1)
    return np.maximum(out, col)


def curve_svg(points_by_channel, size=200):
    """Small SVG drawing of the four tone curves."""
    from .engine import pchip_lut
    colors = {"master": "#e6e6e6", "red": "#ff6b6b", "green": "#5fd38d", "blue": "#6aa8ff"}
    parts = [f'<svg viewBox="0 0 {size} {size}" width="100%" style="background:#161618;border-radius:6px">']
    for q in (0.25, 0.5, 0.75):
        p = q * size
        parts.append(f'<line x1="{p}" y1="0" x2="{p}" y2="{size}" stroke="#2c2c30"/>'
                     f'<line x1="0" y1="{p}" x2="{size}" y2="{p}" stroke="#2c2c30"/>')
    for ch, pts in points_by_channel.items():
        xs, ys = pchip_lut(pts, n=64)
        poly = " ".join(f"{x * size:.1f},{(1 - y) * size:.1f}" for x, y in zip(xs, ys))
        parts.append(f'<polyline points="{poly}" fill="none" stroke="{colors[ch]}" stroke-width="1.6"/>')
        for x, y in pts:
            parts.append(f'<circle cx="{x * size:.1f}" cy="{(1 - y) * size:.1f}" r="2.5" fill="{colors[ch]}"/>')
    parts.append("</svg>")
    return "".join(parts)


def wheels_svg(cg, size=74):
    """Colour-wheel indicators for the colour-grading zones."""
    import colorsys
    out = ['<div style="display:flex;gap:10px;justify-content:space-between">']
    for z in ("shadows", "midtones", "highlights", "global"):
        h, s = cg[f"{z}_hue"], cg[f"{z}_saturation"] / 100.0
        r = size / 2
        x = r + np.cos(np.radians(h)) * s * (r - 6)
        y = r - np.sin(np.radians(h)) * s * (r - 6)
        rr, gg, bb = (int(v * 255) for v in colorsys.hsv_to_rgb(h / 360, max(s, 0.15), 1))
        grad = ",".join(f"hsl({a},70%,45%)" for a in range(0, 361, 30))
        out.append(f'<div style="text-align:center;font-size:10px;color:#9a9aa2;text-transform:uppercase;'
                   f'letter-spacing:.06em"><svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
                   f'<foreignObject width="{size}" height="{size}"><div xmlns="http://www.w3.org/1999/xhtml" '
                   f'style="width:{size}px;height:{size}px;border-radius:50%;background:conic-gradient(from 90deg,'
                   f'{grad});transform:scaleX(-1);opacity:.55"></div></foreignObject>'
                   f'<circle cx="{r}" cy="{r}" r="{r - 1}" fill="none" stroke="#3a3a40"/>'
                   f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="rgb({rr},{gg},{bb})" stroke="#111" '
                   f'stroke-width="1.5"/></svg><div>{z}</div></div>')
    out.append("</div>")
    return "".join(out)
