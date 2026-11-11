"""
The editing recipe: a strict, JSON-serialisable description of every edit.

Sliders, presets, the rule-based prompt interpreter and the optional LLM all
produce the SAME structure, and everything goes through `validate()` before
it can reach the engine.  Values are clamped to documented limits, unknown
keys are dropped, enums must match exactly.  Nothing in a recipe is ever
executed as code.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass

SCHEMA_VERSION = 1

HSL_BANDS = ["red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta"]
ZONES = ["shadows", "midtones", "highlights", "global"]


@dataclass(frozen=True)
class P:
    path: str            # "group.key"
    lo: float
    hi: float
    default: float
    step: float = 1.0
    label: str = ""
    help: str = ""


def _hsl_params():
    out = []
    for b in HSL_BANDS:
        out += [P(f"hsl.{b}_hue", -100, 100, 0, 1, f"{b.title()} hue"),
                P(f"hsl.{b}_saturation", -100, 100, 0, 1, f"{b.title()} saturation"),
                P(f"hsl.{b}_luminance", -100, 100, 0, 1, f"{b.title()} luminance")]
    return out


def _grading_params():
    out = []
    for z in ZONES:
        out += [P(f"color_grading.{z}_hue", 0, 360, 0, 1, f"{z.title()} hue"),
                P(f"color_grading.{z}_saturation", 0, 100, 0, 1, f"{z.title()} saturation"),
                P(f"color_grading.{z}_luminance", -100, 100, 0, 1, f"{z.title()} luminance")]
    out += [P("color_grading.blending", 0, 100, 50, 1, "Blending"),
            P("color_grading.balance", -100, 100, 0, 1, "Balance")]
    return out


def _lgg_params():
    out = []
    for w in ("lift", "gamma", "gain"):
        for c, n in (("m", "master"), ("r", "red"), ("g", "green"), ("b", "blue")):
            out.append(P(f"lgg.{w}_{c}", -100, 100, 0, 1, f"{w.title()} {n}"))
    return out


NUMERIC = [
    # Basic
    P("basic.exposure", -5, 5, 0, 0.05, "Exposure", "EV stops, applied in linear light"),
    P("basic.contrast", -100, 100, 0, 1, "Contrast"),
    P("basic.highlights", -100, 100, 0, 1, "Highlights"),
    P("basic.shadows", -100, 100, 0, 1, "Shadows"),
    P("basic.whites", -100, 100, 0, 1, "Whites"),
    P("basic.blacks", -100, 100, 0, 1, "Blacks"),
    P("basic.temperature", -100, 100, 0, 1, "Temperature", "relative; + warmer, - cooler"),
    P("basic.tint", -100, 100, 0, 1, "Tint", "+ magenta, - green"),
    P("basic.vibrance", -100, 100, 0, 1, "Vibrance"),
    P("basic.saturation", -100, 100, 0, 1, "Saturation"),
    # Presence / effects
    P("effects.clarity", -100, 100, 0, 1, "Clarity"),
    P("effects.texture", -100, 100, 0, 1, "Texture"),
    P("effects.dehaze", -100, 100, 0, 1, "Dehaze"),
    P("effects.fade", 0, 100, 0, 1, "Fade", "lifts blacks / lowers whites (matte film look)"),
    P("effects.grain_amount", 0, 100, 0, 1, "Grain amount"),
    P("effects.grain_size", 0, 100, 25, 1, "Grain size"),
    P("effects.grain_roughness", 0, 100, 50, 1, "Grain roughness"),
    P("effects.grain_color", 0, 100, 0, 1, "Grain colour"),
    P("effects.grain_seed", 0, 9999, 1, 1, "Grain seed"),
    P("effects.vignette_amount", -100, 100, 0, 1, "Vignette amount", "- darkens corners"),
    P("effects.vignette_midpoint", 0, 100, 50, 1, "Vignette midpoint"),
    P("effects.vignette_roundness", -100, 100, 0, 1, "Vignette roundness"),
    P("effects.vignette_feather", 0, 100, 50, 1, "Vignette feather"),
    P("effects.halation_amount", 0, 100, 0, 1, "Halation amount"),
    P("effects.halation_radius", 0, 100, 30, 1, "Halation radius"),
    P("effects.halation_threshold", 0, 100, 75, 1, "Halation threshold"),
    P("effects.chromatic_aberration", 0, 100, 0, 1, "Add chromatic aberration", "creative fringing"),
    # Detail
    P("detail.sharpen_amount", 0, 150, 0, 1, "Sharpening amount"),
    P("detail.sharpen_radius", 0.5, 3.0, 1.0, 0.1, "Sharpening radius", "px at full resolution"),
    P("detail.sharpen_masking", 0, 100, 0, 1, "Sharpening masking"),
    P("detail.nr_luminance", 0, 100, 0, 1, "Luminance noise reduction"),
    P("detail.nr_color", 0, 100, 0, 1, "Colour noise reduction"),
    # Lens
    P("lens.distortion", -100, 100, 0, 1, "Distortion", "+ corrects barrel, - corrects pincushion"),
    P("lens.ca_red", -100, 100, 0, 1, "CA red/cyan"),
    P("lens.ca_blue", -100, 100, 0, 1, "CA blue/yellow"),
    P("lens.vignette_correction", -100, 100, 0, 1, "Lens vignetting", "+ brightens corners"),
    # Film / LUT / match strengths
    P("film.strength", 0, 100, 100, 1, "Film strength"),
    P("lut.strength", 0, 100, 100, 1, "LUT strength"),
    P("match.strength", 0, 100, 0, 1, "Reference match strength"),
] + _hsl_params() + _grading_params() + _lgg_params()

NUMERIC_BY_PATH = {p.path: p for p in NUMERIC}

ENUMS = {
    "lens.fill_mode": (["Keep size, repeat edge pixels", "Keep size, transparent-black edges"],
                       "Keep size, repeat edge pixels"),
}
# Film stock / LUT names are validated against live registries injected by the app.
REGISTRIES = {"film.stock": ["None"], "lut.name": ["None"]}
CURVE_CHANNELS = ["master", "red", "green", "blue"]
IDENTITY_CURVE = [[0.0, 0.0], [1.0, 1.0]]


def default_recipe():
    r = {"version": SCHEMA_VERSION, "name": "", "notes": ""}
    for p in NUMERIC:
        g, k = p.path.split(".")
        r.setdefault(g, {})[k] = p.default
    for path, (_, d) in ENUMS.items():
        g, k = path.split(".")
        r.setdefault(g, {})[k] = d
    r["film"]["stock"] = "None"
    r["lut"]["name"] = "None"
    r["tone_curve"] = {c: copy.deepcopy(IDENTITY_CURVE) for c in CURVE_CHANNELS}
    r["match"]["data"] = None
    return r


def get(recipe, path):
    g, k = path.split(".")
    return recipe[g][k]


def set_(recipe, path, value):
    g, k = path.split(".")
    recipe[g][k] = value


def _validate_curve(pts, warnings, name):
    if not isinstance(pts, (list, tuple)) or len(pts) < 2:
        warnings.append(f"tone_curve.{name}: needs at least 2 points; reset to identity")
        return copy.deepcopy(IDENTITY_CURVE)
    clean = []
    for p in pts[:16]:
        try:
            x, y = float(p[0]), float(p[1])
        except Exception:
            warnings.append(f"tone_curve.{name}: dropped invalid point {p!r}")
            continue
        clean.append([min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)])
    clean.sort(key=lambda q: q[0])
    dedup = []
    for q in clean:
        if dedup and q[0] - dedup[-1][0] < 1e-3:
            continue
        dedup.append(q)
    if len(dedup) < 2:
        warnings.append(f"tone_curve.{name}: too few distinct points; reset to identity")
        return copy.deepcopy(IDENTITY_CURVE)
    if len(pts) > 16:
        warnings.append(f"tone_curve.{name}: truncated to 16 points")
    return dedup


def _validate_match(data, warnings):
    """Frozen colour-transfer parameters produced by match.py."""
    if data is None:
        return None
    try:
        out = {
            "method": str(data["method"]),
            "src_mean": [float(v) for v in data["src_mean"]][:3],
            "src_std": [float(v) for v in data["src_std"]][:3],
            "ref_mean": [float(v) for v in data["ref_mean"]][:3],
            "ref_std": [float(v) for v in data["ref_std"]][:3],
            "tone_lut": [min(max(float(v), 0.0), 1.5) for v in data.get("tone_lut", [])][:1024],
            "reference_name": str(data.get("reference_name", ""))[:200],
        }
        if out["method"] not in ("lab_stats", "lab_stats+tone"):
            raise ValueError("unknown method")
        if len(out["src_mean"]) != 3 or len(out["ref_std"]) != 3:
            raise ValueError("bad stats length")
        out["src_std"] = [max(v, 1e-4) for v in out["src_std"]]
        out["ref_std"] = [max(v, 1e-4) for v in out["ref_std"]]
        return out
    except Exception as e:
        warnings.append(f"match.data invalid ({e}); reference match disabled")
        return None


def validate(candidate, partial=False, base=None):
    """Return (clean_recipe, warnings).

    partial=True merges `candidate` over `base` (or defaults), which is how
    prompt/LLM output is applied: it only needs to mention what changes."""
    warnings = []
    out = copy.deepcopy(base) if (partial and base is not None) else default_recipe()
    if not isinstance(candidate, dict):
        return out, ["recipe is not an object; ignored"]
    for key in ("name", "notes"):
        if key in candidate:
            out[key] = str(candidate[key])[:500]
    for g, sub in candidate.items():
        if g in ("version", "name", "notes"):
            continue
        if g not in out or not isinstance(out[g], dict):
            warnings.append(f"unknown section '{g}' dropped")
            continue
        if not isinstance(sub, dict):
            warnings.append(f"section '{g}' is not an object; dropped")
            continue
        for k, v in sub.items():
            path = f"{g}.{k}"
            if g == "tone_curve":
                if k in CURVE_CHANNELS:
                    out[g][k] = _validate_curve(v, warnings, k)
                else:
                    warnings.append(f"unknown curve '{k}' dropped")
            elif path == "match.data":
                out[g][k] = _validate_match(v, warnings)
            elif path in NUMERIC_BY_PATH:
                p = NUMERIC_BY_PATH[path]
                try:
                    f = float(v)
                    if f != f:  # NaN
                        raise ValueError
                except Exception:
                    warnings.append(f"{path}: not a number ({v!r}); kept {out[g][k]}")
                    continue
                c = min(max(f, p.lo), p.hi)
                if c != f:
                    warnings.append(f"{path}: {f:g} clamped to {c:g} (limits {p.lo:g}..{p.hi:g})")
                out[g][k] = round(c, 4)
            elif path in ENUMS:
                if v in ENUMS[path][0]:
                    out[g][k] = v
                else:
                    warnings.append(f"{path}: '{v}' not one of {ENUMS[path][0]}")
            elif path in REGISTRIES:
                if v in REGISTRIES[path]:
                    out[g][k] = v
                else:
                    match = [n for n in REGISTRIES[path] if str(v).lower() in n.lower()]
                    if len(match) >= 1 and str(v).strip():
                        out[g][k] = match[0]
                        warnings.append(f"{path}: '{v}' resolved to '{match[0]}'")
                    else:
                        warnings.append(f"{path}: unknown name '{v}'")
            else:
                warnings.append(f"unknown parameter '{path}' dropped")
    return out, warnings


def is_identity(recipe):
    d = default_recipe()
    return canonical(recipe, drop_meta=True) == canonical(d, drop_meta=True)


def canonical(recipe, drop_meta=False):
    r = copy.deepcopy(recipe)
    if drop_meta:
        r.pop("name", None)
        r.pop("notes", None)
        r.get("effects", {}).pop("grain_seed", None)
    return json.dumps(r, sort_keys=True)


def diff(recipe):
    """Human-readable list of non-default settings."""
    d = default_recipe()
    out = []
    for p in NUMERIC:
        v = get(recipe, p.path)
        if abs(v - p.default) > 1e-9 and p.path != "effects.grain_seed":
            out.append(f"{p.path} = {v:g}")
    for path in list(ENUMS) + list(REGISTRIES):
        if get(recipe, path) != get(d, path):
            out.append(f"{path} = {get(recipe, path)}")
    for c in CURVE_CHANNELS:
        if recipe["tone_curve"][c] != IDENTITY_CURVE:
            out.append(f"tone_curve.{c} = {recipe['tone_curve'][c]}")
    if recipe["match"]["data"]:
        out.append(f"match.data = reference '{recipe['match']['data'].get('reference_name', '')}'")
    return out


def json_schema():
    """JSON Schema handed to the LLM so it knows the exact limits."""
    props = {}
    for p in NUMERIC:
        if p.path == "effects.grain_seed":
            continue
        g, k = p.path.split(".")
        props.setdefault(g, {"type": "object", "properties": {}, "additionalProperties": False})
        props[g]["properties"][k] = {"type": "number", "minimum": p.lo, "maximum": p.hi, "default": p.default}
        if p.help:
            props[g]["properties"][k]["description"] = p.help
    for path, (choices, _) in ENUMS.items():
        g, k = path.split(".")
        props[g]["properties"][k] = {"enum": choices}
    for path, choices in REGISTRIES.items():
        g, k = path.split(".")
        props[g]["properties"][k] = {"enum": choices}
    props["tone_curve"] = {"type": "object", "additionalProperties": False, "properties": {
        c: {"type": "array", "minItems": 2, "maxItems": 16,
            "items": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1},
                      "minItems": 2, "maxItems": 2}} for c in CURVE_CHANNELS}}
    props["match"]["properties"].pop("data", None)
    return {"type": "object", "additionalProperties": False,
            "properties": {"name": {"type": "string"}, "notes": {"type": "string"}, **props}}


class History:
    """Linear undo/redo stack of validated recipes."""

    def __init__(self, initial=None, limit=200):
        self.stack = [copy.deepcopy(initial or default_recipe())]
        self.index = 0
        self.limit = limit

    @property
    def current(self):
        return copy.deepcopy(self.stack[self.index])

    def push(self, recipe):
        if canonical(recipe) == canonical(self.stack[self.index]):
            return False
        del self.stack[self.index + 1:]
        self.stack.append(copy.deepcopy(recipe))
        if len(self.stack) > self.limit:
            self.stack.pop(0)
        self.index = len(self.stack) - 1
        return True

    def undo(self):
        if self.index > 0:
            self.index -= 1
        return self.current

    def redo(self):
        if self.index < len(self.stack) - 1:
            self.index += 1
        return self.current

    def can_undo(self):
        return self.index > 0

    def can_redo(self):
        return self.index < len(self.stack) - 1

    def reset(self):
        """Reset is itself undoable: it pushes the default recipe."""
        self.push(default_recipe())
        return self.current
