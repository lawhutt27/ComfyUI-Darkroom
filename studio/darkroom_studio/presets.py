"""Built-in looks (partial recipes, validated on load)."""

BUILTIN_PRESETS = {
    "Kodak Portra-inspired portrait": {
        "description": "Soft contrast, warm skin, muted olive greens, gentle highlight roll-off.",
        "recipe": {"film": {"stock": "Studio / Portra 400-inspired", "strength": 85},
                   "basic": {"temperature": 6, "highlights": -20, "shadows": 12, "vibrance": -5},
                   "effects": {"fade": 6}},
    },
    "1970s independent film": {
        "description": "Warm, faded print with muted greens, soft contrast and visible grain.",
        "recipe": {"film": {"stock": "Studio / 1970s warm print", "strength": 85},
                   "basic": {"temperature": 12, "contrast": -12, "highlights": -25, "saturation": -10},
                   "effects": {"fade": 15, "grain_amount": 30, "grain_size": 40, "vignette_amount": -18}},
    },
    "2000s music video": {
        "description": "Warm, glowing, saturated highlights with soft bloom and lifted blacks.",
        "recipe": {"film": {"stock": "Studio / Gold 200-inspired", "strength": 70},
                   "basic": {"temperature": 18, "tint": 6, "contrast": 10, "vibrance": 15},
                   "effects": {"halation_amount": 25, "halation_radius": 45, "fade": 8, "clarity": -10},
                   "color_grading": {"highlights_hue": 40, "highlights_saturation": 25,
                                     "shadows_hue": 330, "shadows_saturation": 12}},
    },
    "Dark, earthy and cinematic": {
        "description": "Lower exposure, firm contrast, desaturated greens and blues, warm earth tones.",
        "recipe": {"film": {"stock": "Studio / Vision3 500T-inspired", "strength": 70},
                   "basic": {"exposure": -0.4, "contrast": 18, "highlights": -30, "blacks": -10,
                             "temperature": 8, "saturation": -12},
                   "hsl": {"green_saturation": -35, "green_hue": 25, "blue_saturation": -30,
                           "orange_saturation": 8},
                   "effects": {"vignette_amount": -25}},
    },
    "Natural documentary": {
        "description": "Neutral, honest colour with modest contrast and restrained saturation.",
        "recipe": {"film": {"stock": "Studio / Documentary natural", "strength": 100},
                   "basic": {"contrast": 6, "highlights": -10, "shadows": 8, "vibrance": 5}},
    },
    "Moody, atmospheric, desaturated": {
        "description": "Cool, low-saturation, deep shadows and soft highlights.",
        "recipe": {"basic": {"exposure": -0.3, "contrast": 10, "highlights": -35, "shadows": -5,
                             "temperature": -10, "saturation": -35, "vibrance": -10},
                   "effects": {"fade": 10, "vignette_amount": -25, "dehaze": -8},
                   "color_grading": {"shadows_hue": 210, "shadows_saturation": 18}},
    },
    "Ilford HP5-inspired B&W": {
        "description": "Classic black & white with open shadows and fine grain.",
        "recipe": {"film": {"stock": "Studio / Ilford HP5 Plus-inspired (B&W)", "strength": 100},
                   "basic": {"contrast": 10}, "effects": {"grain_amount": 20, "grain_size": 30}},
    },
}
