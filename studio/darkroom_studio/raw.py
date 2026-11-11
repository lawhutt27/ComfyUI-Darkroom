"""
RAW development stage (rawpy / LibRaw).

This is a separate stage from grading: it turns the sensor mosaic into a
full-resolution, linear, float32 RGB image ("developed image").  The RAW file
itself is never modified; it stays in the originals library and can be
re-developed with different settings at any time.

Defaults are chosen for fidelity, not punch:
  * AHD demosaic (available in every LibRaw build; the GPL demosaic packs are
    not in PyPI wheels), camera ("as shot") white balance, highlight clip.
  * LibRaw auto-brightening is OFF; exposure is an explicit user control.
  * No LibRaw noise reduction, median filtering or sharpening.
  * Linear 16-bit output from LibRaw (gamma 1,1), converted to float32.
  * Base tone curve: the empirical "Camera Raw default" curve from
    ComfyUI-Darkroom (utils/raw_loader.py, MIT) so a neutral develop looks
    like a normal camera rendering instead of a dark linear image.  Choose
    "Linear (scene-referred)" to skip it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from . import colorspace as cms

try:
    import rawpy
    HAS_RAWPY = True
    RAWPY_VERSION = rawpy.__version__
    LIBRAW_VERSION = ".".join(str(x) for x in rawpy.libraw_version)
except Exception:  # pragma: no cover
    rawpy = None
    HAS_RAWPY = False
    RAWPY_VERSION = LIBRAW_VERSION = None

# ComfyUI-Darkroom, utils/raw_loader.py: CAMERA_RAW_DEFAULT_CURVE (MIT, J. Louvaert)
CAMERA_RAW_DEFAULT_CURVE = [(0.000, 0.000), (0.040, 0.105), (0.100, 0.335), (0.200, 0.540),
                            (0.350, 0.790), (0.500, 0.890), (0.700, 0.955), (1.000, 1.000)]

WHITE_BALANCE_OPTIONS = ["As shot (camera)", "Auto (LibRaw grey-world)", "Daylight (camera preset)"]
HIGHLIGHT_OPTIONS = ["Clip", "Unclip (keep raw values)", "Blend", "Reconstruct (3)", "Reconstruct (5)",
                     "Reconstruct (8)"]
DEMOSAIC_OPTIONS = ["AHD", "AAHD", "DHT", "DCB", "VNG", "PPG", "LINEAR"]
COLORSPACE_OPTIONS = ["sRGB", "Adobe RGB (1998)", "Display P3", "ProPhoto RGB"]
BASE_CURVE_OPTIONS = ["Camera-like (Darkroom ACR-matched)", "Linear (scene-referred)"]


@dataclass
class RawSettings:
    white_balance: str = WHITE_BALANCE_OPTIONS[0]
    exposure_ev: float = 0.0          # -2 .. +3, applied by LibRaw (exp_shift) before clipping
    highlight_mode: str = "Clip"
    demosaic: str = "AHD"
    color_space: str = "sRGB"
    base_curve: str = BASE_CURVE_OPTIONS[0]

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        s = cls()
        for k, v in (d or {}).items():
            if hasattr(s, k):
                setattr(s, k, v)
        s.exposure_ev = float(np.clip(float(s.exposure_ev), -2.0, 3.0))
        if s.white_balance not in WHITE_BALANCE_OPTIONS:
            s.white_balance = WHITE_BALANCE_OPTIONS[0]
        if s.highlight_mode not in HIGHLIGHT_OPTIONS:
            s.highlight_mode = "Clip"
        if s.demosaic not in DEMOSAIC_OPTIONS:
            s.demosaic = "AHD"
        if s.color_space not in COLORSPACE_OPTIONS:
            s.color_space = "sRGB"
        if s.base_curve not in BASE_CURVE_OPTIONS:
            s.base_curve = BASE_CURVE_OPTIONS[0]
        return s


def _working_space(name):
    base = {"sRGB": cms.SRGB, "Adobe RGB (1998)": cms.ADOBE_RGB, "Display P3": cms.DISPLAY_P3,
            "ProPhoto RGB": cms.PROPHOTO}[name]
    return cms.ColorSpace(base.name, base.to_xyz_d50, base.transfer, icc=None,
                          source=f"RAW develop (LibRaw output: {name}, linear)")


def _rawpy_colorspace(name):
    cs = rawpy.ColorSpace
    return {"sRGB": cs.sRGB, "Adobe RGB (1998)": cs.Adobe, "Display P3": cs.P3D65,
            "ProPhoto RGB": cs.ProPhoto}[name]


def _rawpy_highlight(name):
    hm = rawpy.HighlightMode
    if name == "Clip":
        return hm.Clip
    if name.startswith("Unclip"):
        return hm.Ignore
    if name == "Blend":
        return hm.Blend
    n = int(name.split("(")[1].rstrip(")"))
    return hm.Reconstruct(n)


def _flip_to_orientation(flip):
    # LibRaw sizes.flip: 0 none, 3 rotate 180, 5 rotate 90 CCW, 6 rotate 90 CW
    return {0: 1, 3: 3, 5: 8, 6: 6}.get(int(flip), 1)


def base_curve(img):
    from scipy.interpolate import PchipInterpolator
    pts = np.array(CAMERA_RAW_DEFAULT_CURVE, dtype=np.float64)
    f = PchipInterpolator(pts[:, 0], pts[:, 1], extrapolate=False)
    from .engine import apply_lut1d
    x = np.linspace(0, 1, 4097)
    lut = f(x).astype(np.float32)
    return apply_lut1d(img, x.astype(np.float32), lut)   # continues linearly above 1 ("Unclip")


def develop_raw(path, settings: RawSettings | None = None):
    """Develop a RAW file. Returns (linear float32 HxWx3, ColorSpace, meta)."""
    if not HAS_RAWPY:
        raise RuntimeError("rawpy is not installed (pip install rawpy). RAW files cannot be developed.")
    s = RawSettings.from_dict(settings.to_dict() if settings else {})
    notes = []
    with rawpy.imread(path) as raw:
        is_bayer = tuple(raw.raw_pattern.shape) == (2, 2) if raw.raw_pattern is not None else False
        kwargs = dict(output_color=_rawpy_colorspace(s.color_space), gamma=(1, 1), no_auto_bright=True,
                      output_bps=16, highlight_mode=_rawpy_highlight(s.highlight_mode),
                      user_flip=None)
        if abs(s.exposure_ev) > 1e-4:
            kwargs["exp_shift"] = float(2.0 ** s.exposure_ev)
            kwargs["exp_preserve_highlights"] = 0.0
        if is_bayer:
            kwargs["demosaic_algorithm"] = getattr(rawpy.DemosaicAlgorithm, s.demosaic)
        else:
            notes.append("Non-Bayer sensor: LibRaw's default demosaic is used.")
        wb = s.white_balance
        if wb.startswith("As shot"):
            kwargs["use_camera_wb"] = True
        elif wb.startswith("Auto"):
            kwargs["use_auto_wb"] = True
        else:
            kwargs["user_wb"] = [float(x) for x in raw.daylight_whitebalance]
        img16 = raw.postprocess(**kwargs)
        sizes = raw.sizes
        meta = {
            "rawpy_version": RAWPY_VERSION, "libraw_version": LIBRAW_VERSION,
            "raw_width": int(sizes.raw_width), "raw_height": int(sizes.raw_height),
            "visible_width": int(sizes.width), "visible_height": int(sizes.height),
            "margins": {"top": int(sizes.top_margin), "left": int(sizes.left_margin)},
            "camera_crop_hint": {"left": int(sizes.crop_left_margin), "top": int(sizes.crop_top_margin),
                                 "width": int(sizes.crop_width), "height": int(sizes.crop_height)},
            "flip": int(sizes.flip),
            "flip_orientation": _flip_to_orientation(sizes.flip),
            "color_desc": raw.color_desc.decode(errors="replace"),
            "raw_pattern": raw.raw_pattern.tolist() if raw.raw_pattern is not None else None,
            "black_level_per_channel": [int(x) for x in raw.black_level_per_channel],
            "white_level": int(raw.white_level),
            "bits_per_sample": int(raw.white_level).bit_length(),
            "camera_whitebalance": [float(x) for x in raw.camera_whitebalance],
            "daylight_whitebalance": [float(x) for x in raw.daylight_whitebalance],
            "rgb_xyz_matrix": np.asarray(raw.rgb_xyz_matrix).round(6).tolist(),
            "postprocess": {"demosaic": s.demosaic if is_bayer else "LibRaw default",
                            "output_color": s.color_space, "gamma": [1, 1], "output_bps": 16,
                            "no_auto_bright": True, "highlight_mode": s.highlight_mode,
                            "white_balance": s.white_balance, "exp_shift": kwargs.get("exp_shift", 1.0),
                            "noise_reduction": "off", "median_filter": "off"},
            "base_curve": s.base_curve,
        }
    if meta["camera_crop_hint"]["width"]:
        notes.append(f"LibRaw output {img16.shape[1]}×{img16.shape[0]} is kept as-is; the camera's own "
                     f"JPEG crop ({meta['camera_crop_hint']['width']}×{meta['camera_crop_hint']['height']}) "
                     "is recorded in the sidecar but NOT applied.")
    linear = img16.astype(np.float32) / 65535.0
    clipped = float(np.mean(np.any(img16 >= 65535, axis=-1)))
    meta["clipped_fraction_after_develop"] = round(clipped, 6)
    if clipped > 0.001:
        notes.append(f"{clipped * 100:.2f}% of pixels are at the 16-bit ceiling after develop "
                     "(clipped highlights). Try a Reconstruct highlight mode or negative RAW exposure.")
    if s.base_curve.startswith("Camera-like"):
        linear = base_curve(linear)
    meta["notes"] = notes
    return np.ascontiguousarray(linear), _working_space(s.color_space), meta


def raw_capabilities():
    if not HAS_RAWPY:
        return {"available": False}
    return {"available": True, "rawpy": RAWPY_VERSION, "libraw": LIBRAW_VERSION,
            "demosaic": [d.name for d in rawpy.DemosaicAlgorithm if d.isSupported],
            "flags": dict(rawpy.flags or {})}
