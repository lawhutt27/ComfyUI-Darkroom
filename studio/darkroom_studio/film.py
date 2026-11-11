"""
Film emulation.

These are aesthetic simulations inspired by the look of well-known stocks,
not claims of chemical or spectral reproduction.

Two sources:

1. "Studio" looks (defined here).  Each is a small, readable parameter set:
   a master tone curve, per-channel crossover curves, saturation, highlight
   chroma roll-off, split toning and per-hue shifts, executed in sRGB-encoded
   and OkLab space.  B&W looks use the channel-sensitivity weights published
   in ComfyUI-Darkroom's data/bw_stocks.py (MIT).

2. The ComfyUI-Darkroom parametric stock library (data/color_stocks.py, MIT),
   loaded from the repository this app ships in.  Darkroom renders 56 of its
   stocks through baked Capture One LUTs that are NOT used here; the
   parametric fallback values for several of those crush the shadows, so
   every Darkroom stock is screened on a grey ramp and only well-behaved ones
   are listed.
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys
from dataclasses import dataclass, field

import numpy as np

from . import colorspace as cms

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@dataclass(frozen=True)
class FilmLook:
    name: str
    family: str
    description: str
    master: tuple = ((0.0, 0.0), (1.0, 1.0))
    red: tuple = ((0.0, 0.0), (1.0, 1.0))
    green: tuple = ((0.0, 0.0), (1.0, 1.0))
    blue: tuple = ((0.0, 0.0), (1.0, 1.0))
    saturation: float = 1.0
    highlight_desat: float = 0.0          # 0..1, chroma roll-off above ~70% L
    shadow_tint: tuple = (0.0, 0.0)       # (UI hue deg, amount 0..1)
    highlight_tint: tuple = (0.0, 0.0)
    hue_shift: dict = field(default_factory=dict)    # band -> degrees (OkLab hue)
    band_sat: dict = field(default_factory=dict)     # band -> multiplier
    bw_weights: tuple | None = None       # (r, g, b) for monochrome looks


S = (0.0, 0.0)
E = (1.0, 1.0)

STUDIO_LOOKS = [
    # ---- Kodak Portra-inspired: soft contrast, warm skin, pastel greens, long highlight roll-off
    FilmLook("Portra 160-inspired", "Kodak-inspired colour negative",
             "Very soft, fine-grained portrait look: gentle contrast, creamy highlights, true skin.",
             master=(S, (0.06, 0.075), (0.25, 0.26), (0.5, 0.515), (0.78, 0.77), (0.95, 0.925), E),
             red=(S, (0.5, 0.51), E), blue=(S, (0.5, 0.49), E),
             saturation=0.9, highlight_desat=0.35, shadow_tint=(200, 0.12), highlight_tint=(40, 0.15),
             hue_shift={"green": 10, "yellow": -4}, band_sat={"green": 0.8, "blue": 0.9}),
    FilmLook("Portra 400-inspired", "Kodak-inspired colour negative",
             "The classic warm portrait negative: lifted toe, warm skin, muted olive greens.",
             master=(S, (0.05, 0.07), (0.25, 0.265), (0.5, 0.52), (0.78, 0.775), (0.95, 0.93), E),
             red=(S, (0.5, 0.515), E), green=(S, (0.5, 0.505), E), blue=(S, (0.5, 0.48), E),
             saturation=0.95, highlight_desat=0.3, shadow_tint=(190, 0.1), highlight_tint=(38, 0.25),
             hue_shift={"green": 14, "aqua": -6}, band_sat={"green": 0.78, "orange": 1.05}),
    FilmLook("Portra 800-inspired", "Kodak-inspired colour negative",
             "Warmer, a touch more contrast and saturation than 400, for low light.",
             master=(S, (0.05, 0.065), (0.25, 0.255), (0.5, 0.525), (0.78, 0.79), (0.95, 0.94), E),
             red=(S, (0.5, 0.52), E), blue=(S, (0.5, 0.475), E),
             saturation=1.0, highlight_desat=0.25, shadow_tint=(185, 0.12), highlight_tint=(35, 0.3),
             hue_shift={"green": 12}, band_sat={"green": 0.82, "red": 1.05}),
    # ---- Kodak Gold-inspired: warm yellow-amber, punchy mids
    FilmLook("Gold 200-inspired", "Kodak-inspired colour negative",
             "Sunny consumer film: golden-yellow warmth, saturated reds and yellows, nostalgic.",
             master=(S, (0.05, 0.06), (0.25, 0.25), (0.5, 0.53), (0.78, 0.8), (0.95, 0.945), E),
             red=(S, (0.5, 0.525), E), green=(S, (0.5, 0.51), E), blue=(S, (0.5, 0.455), E),
             saturation=1.08, highlight_desat=0.2, shadow_tint=(30, 0.08), highlight_tint=(48, 0.35),
             hue_shift={"green": 6, "blue": -5}, band_sat={"yellow": 1.08, "red": 1.1, "blue": 0.9, "green": 0.95}),
    # ---- Kodak Ektar-inspired: vivid, contrasty, clean
    FilmLook("Ektar 100-inspired", "Kodak-inspired colour negative",
             "Ultra-vivid landscape negative: deep blues, saturated reds, crisp contrast.",
             master=(S, (0.06, 0.045), (0.25, 0.23), (0.5, 0.52), (0.78, 0.82), (0.95, 0.955), E),
             blue=(S, (0.5, 0.505), E),
             saturation=1.22, highlight_desat=0.1, shadow_tint=(220, 0.06),
             hue_shift={"blue": -4, "red": -3}, band_sat={"blue": 1.15, "red": 1.12, "green": 1.05}),
    # ---- Fujifilm-inspired
    FilmLook("Fuji Pro 400H-inspired", "Fujifilm-inspired colour negative",
             "Airy pastel wedding look: cool mint greens, soft magenta-free skin, bright and gentle.",
             master=(S, (0.05, 0.07), (0.25, 0.27), (0.5, 0.525), (0.78, 0.785), (0.95, 0.935), E),
             green=(S, (0.5, 0.515), E), blue=(S, (0.5, 0.51), E),
             saturation=0.88, highlight_desat=0.35, shadow_tint=(170, 0.12), highlight_tint=(190, 0.08),
             hue_shift={"green": -10, "aqua": -5}, band_sat={"green": 0.85, "red": 0.92}),
    FilmLook("Fuji Superia-inspired", "Fujifilm-inspired colour negative",
             "Everyday Fuji: green-tinted shadows, punchy greens and blues, slight magenta highlights.",
             master=(S, (0.05, 0.05), (0.25, 0.245), (0.5, 0.52), (0.78, 0.8), (0.95, 0.945), E),
             green=(S, (0.25, 0.26), (0.5, 0.505), E), red=(S, (0.5, 0.495), E),
             saturation=1.08, highlight_desat=0.15, shadow_tint=(150, 0.15), highlight_tint=(320, 0.08),
             hue_shift={"green": -6}, band_sat={"green": 1.12, "blue": 1.08}),
    FilmLook("Fuji Velvia-inspired", "Fujifilm-inspired slide",
             "Saturated landscape slide film: dense shadows, intense greens, magenta-leaning skies.",
             master=(S, (0.06, 0.035), (0.25, 0.215), (0.5, 0.515), (0.78, 0.83), (0.95, 0.96), E),
             saturation=1.35, highlight_desat=0.05,
             hue_shift={"blue": 6, "green": -4}, band_sat={"green": 1.2, "blue": 1.15, "purple": 1.1}),
    # ---- Kodak Vision3-inspired cinema
    FilmLook("Vision3 500T-inspired", "Kodak-inspired cinema negative",
             "Tungsten cinema negative printed for a modern feature: low-con, teal shadows, warm skin.",
             master=(S, (0.05, 0.065), (0.25, 0.255), (0.5, 0.505), (0.78, 0.77), (0.95, 0.925), E),
             red=(S, (0.25, 0.24), (0.5, 0.505), E), blue=(S, (0.25, 0.27), (0.5, 0.5), E),
             saturation=0.9, highlight_desat=0.3, shadow_tint=(195, 0.25), highlight_tint=(35, 0.2),
             hue_shift={"green": 8, "aqua": 6}, band_sat={"green": 0.8}),
    FilmLook("Vision3 250D-inspired", "Kodak-inspired cinema negative",
             "Daylight cinema negative: natural, wide latitude, subtle warmth, restrained saturation.",
             master=(S, (0.05, 0.06), (0.25, 0.255), (0.5, 0.51), (0.78, 0.78), (0.95, 0.93), E),
             red=(S, (0.5, 0.505), E), blue=(S, (0.5, 0.495), E),
             saturation=0.93, highlight_desat=0.25, shadow_tint=(200, 0.08), highlight_tint=(40, 0.1),
             band_sat={"green": 0.88}),
    # ---- Cinestill-inspired
    FilmLook("Cinestill 800T-inspired", "Cinestill-inspired",
             "Night-city tungsten stock: cool cyan cast, glowing warm practicals (add Halation for the red bloom).",
             master=(S, (0.05, 0.055), (0.25, 0.25), (0.5, 0.515), (0.78, 0.79), (0.95, 0.94), E),
             red=(S, (0.5, 0.485), E), blue=(S, (0.5, 0.525), E),
             saturation=1.0, highlight_desat=0.15, shadow_tint=(190, 0.3), highlight_tint=(25, 0.2),
             hue_shift={"aqua": 8, "orange": -4}, band_sat={"red": 1.1, "aqua": 1.1}),
    FilmLook("Cinestill 50D-inspired", "Cinestill-inspired",
             "Clean daylight cinema stock: crisp, slightly cool, fine grain.",
             master=(S, (0.05, 0.05), (0.25, 0.245), (0.5, 0.515), (0.78, 0.8), (0.95, 0.945), E),
             blue=(S, (0.5, 0.51), E),
             saturation=1.05, highlight_desat=0.1, shadow_tint=(205, 0.12)),
    # ---- Looks
    FilmLook("Vintage faded", "Looks",
             "Aged print: lifted milky blacks, low contrast, warm-magenta highlights, faded colour.",
             master=(S, (0.0, 0.09), (0.25, 0.3), (0.5, 0.52), (0.85, 0.82), (1.0, 0.92)),
             red=(S, (0.5, 0.52), E), blue=(S, (0.0, 0.03), (0.5, 0.47), E),
             saturation=0.72, highlight_desat=0.3, shadow_tint=(200, 0.15), highlight_tint=(30, 0.3),
             hue_shift={"green": 15}, band_sat={"green": 0.7, "blue": 0.8}),
    FilmLook("1970s warm print", "Looks",
             "Yellow-orange colour shift, faded blacks and muted greens of a 1970s drugstore print.",
             master=(S, (0.0, 0.06), (0.25, 0.28), (0.5, 0.53), (0.8, 0.8), (1.0, 0.95)),
             red=(S, (0.5, 0.53), E), green=(S, (0.5, 0.515), E), blue=(S, (0.0, 0.02), (0.5, 0.43), (1.0, 0.92)),
             saturation=0.82, highlight_desat=0.25, shadow_tint=(30, 0.15), highlight_tint=(45, 0.35),
             hue_shift={"green": 18, "blue": -8}, band_sat={"green": 0.65, "blue": 0.75, "orange": 1.08}),
    FilmLook("Documentary natural", "Looks",
             "Honest reportage rendering: neutral colour, modest contrast, slightly restrained saturation.",
             master=(S, (0.05, 0.05), (0.25, 0.245), (0.5, 0.505), (0.78, 0.785), (0.95, 0.945), E),
             saturation=0.92, highlight_desat=0.15, band_sat={"green": 0.92}),
    FilmLook("Cinematic teal & orange", "Looks",
             "Feature-film grade: teal shadows, warm skin and highlights, firm contrast.",
             master=(S, (0.05, 0.045), (0.25, 0.235), (0.5, 0.515), (0.78, 0.8), (0.95, 0.94), E),
             saturation=0.95, highlight_desat=0.2, shadow_tint=(190, 0.35), highlight_tint=(32, 0.3),
             hue_shift={"green": 20, "blue": -10, "aqua": -5}, band_sat={"green": 0.7, "orange": 1.1, "aqua": 1.1}),
    FilmLook("Bleach bypass", "Looks",
             "Silver-retention look: desaturated, hard contrast, metallic highlights.",
             master=(S, (0.06, 0.03), (0.25, 0.2), (0.5, 0.52), (0.78, 0.84), (0.95, 0.97), E),
             saturation=0.5, highlight_desat=0.3),
    FilmLook("Expired colour film", "Looks",
             "Out-of-date film: magenta-green crossover, faded shadows, unpredictable warmth.",
             master=(S, (0.0, 0.07), (0.25, 0.29), (0.5, 0.53), (0.85, 0.83), (1.0, 0.93)),
             red=(S, (0.25, 0.27), (0.75, 0.77), E), green=(S, (0.25, 0.24), (0.75, 0.76), E),
             blue=(S, (0.0, 0.04), (0.5, 0.47), E),
             saturation=0.8, highlight_desat=0.3, shadow_tint=(300, 0.15), highlight_tint=(60, 0.2)),
    # ---- Ilford-inspired B&W (weights from ComfyUI-Darkroom data/bw_stocks.py)
    FilmLook("Ilford HP5 Plus-inspired (B&W)", "Ilford-inspired black & white",
             "Classic versatile B&W: medium contrast, open shadows.",
             master=(S, (0.05, 0.04), (0.25, 0.235), (0.5, 0.515), (0.78, 0.8), (0.95, 0.95), E),
             bw_weights=(0.289, 0.347, 0.364)),
    FilmLook("Ilford Delta 100-inspired (B&W)", "Ilford-inspired black & white",
             "Fine, smooth tonality with long gradations.",
             master=(S, (0.05, 0.045), (0.25, 0.24), (0.5, 0.51), (0.78, 0.79), (0.95, 0.945), E),
             bw_weights=(0.246, 0.254, 0.501)),
    FilmLook("Ilford FP4 Plus-inspired (B&W)", "Ilford-inspired black & white",
             "Studio favourite: crisp, slightly higher contrast, luminous skies.",
             master=(S, (0.05, 0.035), (0.25, 0.225), (0.5, 0.515), (0.78, 0.81), (0.95, 0.955), E),
             bw_weights=(0.241, 0.221, 0.537)),
    FilmLook("Kodak Tri-X-inspired (B&W)", "Kodak-inspired black & white",
             "Punchy photojournalism B&W: deep blacks, bright highlights.",
             master=(S, (0.06, 0.025), (0.25, 0.205), (0.5, 0.52), (0.78, 0.835), (0.95, 0.965), E),
             bw_weights=(0.30, 0.40, 0.30)),
]


def _load_darkroom_data():
    """Import ComfyUI-Darkroom's data package from the repository root without
    putting a generic 'data' package on sys.path."""
    pkg_dir = os.path.join(REPO_ROOT, "data")
    if not os.path.isfile(os.path.join(pkg_dir, "color_stocks.py")):
        return None, None
    name = "_darkroom_data"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, os.path.join(pkg_dir, "__init__.py"),
                                                      submodule_search_locations=[pkg_dir])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    import importlib as _il
    cs = _il.import_module(f"{name}.color_stocks")
    bw = _il.import_module(f"{name}.bw_stocks")
    return cs, bw


# ComfyUI-Darkroom utils/color.py (MIT): characteristic curve, split tone, saturation
def _characteristic_curve(x, toe_power, shoulder_power, slope, pivot_x=0.18, pivot_y=0.18):
    x = np.clip(x, 0.0, 1.0)
    t_toe = x / (pivot_x + 1e-10)
    toe = pivot_y * np.power(t_toe, toe_power) * slope
    t_sh = np.clip((x - pivot_x) / (1.0 - pivot_x + 1e-10), 0, 1)
    sh = pivot_y * slope + (1.0 - pivot_y * slope) * (1.0 - np.power(1.0 - t_sh, shoulder_power))
    return np.clip(np.where(x <= pivot_x, toe, sh), 0.0, 1.0).astype(np.float32)


def _darkroom_stock_apply(stock, E):
    lin = cms.srgb_decode(np.clip(E, 0, 1))
    out = np.empty_like(lin)
    for i, c in enumerate((stock.r_curve, stock.g_curve, stock.b_curve)):
        out[..., i] = _characteristic_curve(lin[..., i], c.toe_power, c.shoulder_power, c.slope,
                                            c.pivot_x, c.pivot_y)
    if abs(stock.saturation - 1.0) > 1e-3:
        lum = (0.2126 * out[..., 0] + 0.7152 * out[..., 1] + 0.0722 * out[..., 2])[..., None]
        out = np.clip(lum + stock.saturation * (out - lum), 0, 1)
    if any(abs(v) > 1e-3 for v in (*stock.shadow_tint, *stock.highlight_tint)):
        lum = 0.2126 * out[..., 0] + 0.7152 * out[..., 1] + 0.0722 * out[..., 2]
        sw = np.clip(1.0 - lum / 0.5, 0, 1)[..., None]
        hw = np.clip((lum - 0.5) / 0.5, 0, 1)[..., None]
        out = np.clip(out + sw * np.array(stock.shadow_tint, np.float32)
                      + hw * np.array(stock.highlight_tint, np.float32), 0, 1)
    return cms.srgb_encode(out)


def _ramp_ok(stock):
    x = np.linspace(0, 1, 11, dtype=np.float32)
    o = _darkroom_stock_apply(stock, np.stack([x, x, x], -1)[None])[0].mean(-1)
    return bool(o[3] > 0.12 and o[5] > 0.35 and np.all(np.diff(o) >= -1e-4))


class FilmRegistry:
    def __init__(self, include_darkroom=True):
        self.looks = {f"Studio / {lk.name}": lk for lk in STUDIO_LOOKS}
        self.darkroom = {}
        self.darkroom_excluded = []
        self.notes = []
        if include_darkroom:
            try:
                cs, _bw = _load_darkroom_data()
                if cs is None:
                    self.notes.append("ComfyUI-Darkroom data/ not found next to studio/; Darkroom stocks unavailable.")
                else:
                    for name, stock in cs.COLOR_STOCKS.items():
                        if _ramp_ok(stock):
                            self.darkroom[f"Darkroom / {name}"] = stock
                        else:
                            self.darkroom_excluded.append(name)
            except Exception as e:  # pragma: no cover
                self.notes.append(f"Darkroom stock import failed: {e}")
        self._lab = None

    def names(self):
        return ["None"] + list(self.looks) + list(self.darkroom)

    def describe(self, name):
        if name in self.looks:
            lk = self.looks[name]
            return f"**{lk.name}** · {lk.family}  \n{lk.description}"
        if name in self.darkroom:
            st = self.darkroom[name]
            return f"**{st.name}** · ComfyUI-Darkroom parametric stock (MIT)  \n{st.description}"
        return ""

    def apply(self, name, E, strength):
        if name in self.darkroom:
            out = _darkroom_stock_apply(self.darkroom[name], E)
        elif name in self.looks:
            out = apply_look(self.looks[name], E)
        else:
            return E
        if strength >= 1.0:
            return out
        return (E + (out - E) * np.float32(strength)).astype(np.float32)


def _curve(E, pts):
    if tuple(map(tuple, pts)) == (S, E_ID):
        return E
    from .engine import apply_lut1d, pchip_lut
    xs, ys = pchip_lut([list(p) for p in pts], n=1024)
    return apply_lut1d(E, xs, ys)


E_ID = (1.0, 1.0)


def apply_look(lk: FilmLook, E):
    from .engine import OkLab, BAND_CENTRES, _hsv_hue_to_oklab_angle, hue_lookup
    from .recipe import HSL_BANDS
    if lk.bw_weights is not None:
        w = np.array(lk.bw_weights, np.float32)
        w = w / w.sum()
        mono = cms.srgb_decode(E) @ w
        mono_e = _curve(cms.srgb_encode(mono), lk.master)
        return np.repeat(mono_e[..., None], 3, axis=2).astype(np.float32)
    out = _curve(E, lk.master)
    if any(tuple(map(tuple, c)) != (S, E_ID) for c in (lk.red, lk.green, lk.blue)):
        out = out.copy()
        for i, c in enumerate((lk.red, lk.green, lk.blue)):
            out[..., i] = _curve(out[..., i], c)
    ok = OkLab(cms.LINEAR_SRGB)
    lab = ok.from_linear(cms.srgb_decode(out))
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    C = np.sqrt(a * a + b * b)
    h = np.degrees(np.arctan2(b, a)) % 360.0
    if lk.hue_shift or lk.band_sat:
        centres = np.array([BAND_CENTRES[n] for n in HSL_BANDS], np.float32)
        dh = np.array([lk.hue_shift.get(n, 0.0) for n in HSL_BANDS], np.float32)
        ds = np.array([lk.band_sat.get(n, 1.0) for n in HSL_BANDS], np.float32)
        cw = np.clip(C / 0.05, 0, 1)
        h = h + hue_lookup(h, centres, dh) * cw
        C = C * hue_lookup(h % 360.0, centres, ds)
    C = C * lk.saturation
    if lk.highlight_desat:
        C = C * (1.0 - lk.highlight_desat * np.clip((L - 0.7) / 0.3, 0, 1))
    hr = np.radians(h)
    a, b = C * np.cos(hr), C * np.sin(hr)
    for (hue, amt), mask in ((lk.shadow_tint, np.clip(1.0 - L / 0.55, 0, 1) ** 1.5),
                             (lk.highlight_tint, np.clip((L - 0.45) / 0.55, 0, 1) ** 1.5)):
        if amt:
            ang = _hsv_hue_to_oklab_angle(hue)
            a = a + mask * amt * 0.06 * math.cos(ang)
            b = b + mask * amt * 0.06 * math.sin(ang)
    lab = np.stack([L, a, b], axis=-1).astype(np.float32)
    return cms.srgb_encode(ok.to_linear(lab))
