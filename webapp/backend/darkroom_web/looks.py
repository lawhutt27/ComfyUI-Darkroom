"""Quick looks: curated Instagram-ready presets plus the Darkroom Studio presets.

Each look is a *partial* Darkroom recipe. A `look` layer scales the look's
numeric values by its `amount` (0-100 %), so "Golden Hour at 40 %" is a
genuinely gentler version of the look rather than a cross-fade.
"""

from darkroom_studio.presets import BUILTIN_PRESETS

IG_LOOKS = {
    "Golden Hour": {
        "description": "Warm sun, lifted shadows, soft glow in the highlights.",
        "recipe": {"basic": {"temperature": 22, "tint": 4, "shadows": 18, "highlights": -18, "vibrance": 14},
                   "effects": {"halation_amount": 12, "fade": 4},
                   "color_grading": {"highlights_hue": 42, "highlights_saturation": 22,
                                     "shadows_hue": 25, "shadows_saturation": 8}},
    },
    "Clean & Bright": {
        "description": "Airy, bright and crisp with true whites.",
        "recipe": {"basic": {"exposure": 0.25, "contrast": 8, "highlights": -25, "shadows": 25, "whites": 12,
                             "vibrance": 10},
                   "effects": {"clarity": 8}},
    },
    "Moody Matte": {
        "description": "Deep, desaturated, matte blacks and cool shadows.",
        "recipe": {"basic": {"exposure": -0.2, "contrast": 12, "highlights": -30, "saturation": -22},
                   "effects": {"fade": 18, "vignette_amount": -20},
                   "color_grading": {"shadows_hue": 205, "shadows_saturation": 16}},
    },
    "Film Fade": {
        "description": "Faded print colour with gentle grain.",
        "recipe": {"film": {"stock": "Studio / Vintage faded", "strength": 75},
                   "effects": {"fade": 10, "grain_amount": 18, "grain_size": 30}},
    },
    "Teal & Orange": {
        "description": "Cinematic split tone: teal shadows, warm skin.",
        "recipe": {"film": {"stock": "Studio / Cinematic teal & orange", "strength": 70},
                   "basic": {"contrast": 10, "vibrance": 8}},
    },
    "Soft Pastel": {
        "description": "Low contrast, pastel colour, creamy highlights.",
        "recipe": {"basic": {"contrast": -18, "highlights": -15, "shadows": 20, "saturation": -12, "temperature": 6},
                   "effects": {"fade": 12, "clarity": -8},
                   "hsl": {"blue_luminance": 15, "green_saturation": -20}},
    },
    "B&W Punch": {
        "description": "Contrasty black & white with fine grain.",
        "recipe": {"film": {"stock": "Studio / Kodak Tri-X-inspired (B&W)", "strength": 100},
                   "basic": {"contrast": 20, "blacks": -8}, "effects": {"grain_amount": 15, "grain_size": 22}},
    },
    "Portra Glow": {
        "description": "Kodak Portra-inspired skin tones with a warm, gentle finish.",
        "recipe": {"film": {"stock": "Studio / Portra 400-inspired", "strength": 85},
                   "basic": {"temperature": 6, "highlights": -20, "shadows": 12}, "effects": {"fade": 5}},
    },
}


def all_looks():
    out = {}
    for name, v in IG_LOOKS.items():
        out[f"IG / {name}"] = v
    for name, v in BUILTIN_PRESETS.items():
        out[f"Film / {name}"] = v
    return out


def catalogue():
    return [{"name": k, "description": v["description"]} for k, v in all_looks().items()]
