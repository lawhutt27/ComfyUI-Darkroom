"""
Starting-point recommendations from camera metadata and image statistics.

Everything here is a suggestion shown to the user with its reasoning; it is
only applied when the user clicks "Apply suggestions".  Rules are
conservative and grounded in known characteristics of the two target bodies:

* Nikon D5300: 24 MP APS-C, no optical low-pass filter (crisp, fine detail,
  occasional moiré), clean to ~ISO 1600-3200.
* Canon EOS Rebel XS / 1000D: 10 MP APS-C (2008) with an optical low-pass
  filter (softer at pixel level), noise visible from ~ISO 800.
"""

from __future__ import annotations

import numpy as np

from . import colorspace as cms

CAMERA_TRAITS = {
    "Nikon D5300": {"olpf": False, "nr_iso": 1600, "high_iso": 3200,
                    "note": "No optical low-pass filter: keep sharpening radius small; watch for moiré on fabrics."},
    "Canon EOS Rebel XS / 1000D": {"olpf": True, "nr_iso": 800, "high_iso": 1600,
                                   "note": "Optical low-pass filter and 10 MP sensor: benefits from a little more "
                                           "capture sharpening; shadows get noisy past ISO 800."},
}


def analyse(linear, space):
    Y = cms.apply_matrix(linear, np.tile(space.luma, (3, 1)))[..., 0]
    Yp = cms.srgb_encode(np.clip(Y, 0, None))
    return {"median": float(np.median(Yp)), "p01": float(np.percentile(Yp, 1)),
            "p99": float(np.percentile(Yp, 99)),
            "clipped_pct": float(np.mean(np.any(linear >= 0.999, axis=-1)) * 100),
            "crushed_pct": float(np.mean(Yp <= 0.01) * 100)}


def suggest(info, stats, is_raw):
    rec = {"basic": {}, "detail": {}, "lens": {}}
    why = []
    cam = info.get("known_camera")
    traits = CAMERA_TRAITS.get(cam)
    iso = info.get("iso") or 0
    if cam:
        why.append(f"**{cam}** recognised. {traits['note']}")
    if is_raw:
        if traits and not traits["olpf"]:
            rec["detail"].update(sharpen_amount=25, sharpen_radius=0.8, sharpen_masking=30)
            why.append("RAW files have no in-camera sharpening; light capture sharpening (25, radius 0.8) "
                       "suits a sensor without a low-pass filter.")
        else:
            rec["detail"].update(sharpen_amount=40, sharpen_radius=1.0, sharpen_masking=25)
            why.append("RAW files have no in-camera sharpening; moderate capture sharpening (40, radius 1.0).")
    nr_iso = traits["nr_iso"] if traits else 1600
    if iso and iso >= nr_iso:
        hi = traits["high_iso"] if traits else 3200
        lum = 15 if iso < hi else 30
        rec["detail"].update(nr_luminance=lum, nr_color=25 if is_raw else 15)
        why.append(f"ISO {iso} is at or above where this body shows noise: luminance NR {lum}, colour NR.")
    elif iso and is_raw:
        rec["detail"]["nr_color"] = 10
        why.append(f"ISO {iso}: only light colour-noise reduction for the RAW decode.")
    ap = info.get("aperture")
    if ap and ap <= 2.8:
        rec["lens"]["vignette_correction"] = 15
        why.append(f"Wide aperture f/{ap:g}: lenses vignette most wide open (+15 lens vignetting correction).")
    sh, fl = info.get("shutter_s"), info.get("focal_length_35mm") or info.get("focal_length")
    if sh and fl and sh > 1.0 / max(fl, 1):
        why.append(f"Shutter {info.get('shutter')} is slower than 1/{int(fl)} s at {fl:g} mm (35 mm eq.); "
                   "check for camera shake at 100% zoom (not corrected automatically).")
    med = stats["median"]
    if med < 0.30 and stats["p99"] < 0.85:
        ev = round(min(1.5, np.log2(0.42 / max(med, 0.05))), 2)
        rec["basic"]["exposure"] = ev
        why.append(f"Image is dark (median {med:.2f}); exposure {ev:+.2f} EV as a starting point.")
    elif med > 0.65:
        rec["basic"]["exposure"] = -0.3
        why.append(f"Image is bright (median {med:.2f}); exposure -0.3 EV.")
    if stats["clipped_pct"] > 0.5:
        rec["basic"]["highlights"] = -40
        why.append(f"{stats['clipped_pct']:.1f}% of pixels are at the top of the range: highlights -40"
                   + (" (also try a Reconstruct highlight mode in RAW develop)." if is_raw else "."))
    if stats["crushed_pct"] > 2:
        rec["basic"]["shadows"] = 25
        why.append(f"{stats['crushed_pct']:.1f}% of pixels are near black: shadows +25.")
    if not why:
        why.append("No specific adjustments suggested; the file looks well exposed.")
    return {k: v for k, v in rec.items() if v}, why
