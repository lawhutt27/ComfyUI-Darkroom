"""The non-destructive edit stack: schema, validation and folding.

See ARCHITECTURE.md §3. A stack is plain JSON; `validate_stack` is the only
way one gets into the worker, and `fold_recipe` turns its grade layers into
the single Darkroom recipe the engine renders.
"""

from __future__ import annotations

import copy
import secrets

from darkroom_studio import recipe as R

from .looks import all_looks

STACK_VERSION = 1
GRADE_TYPES = ("adjust", "look", "lut", "film", "style_match")
PIXEL_TYPES = ("red_eye", "teeth_whiten", "ai_edit")
LAYER_TYPES = GRADE_TYPES + PIXEL_TYPES
MAX_LAYERS = 64
# Recipe groups an `adjust` layer may carry.
ADJUST_GROUPS = ("basic", "effects", "hsl", "color_grading", "tone_curve", "lgg", "detail", "lens")


class StackError(ValueError):
    pass


def new_id(prefix="l"):
    return f"{prefix}_{secrets.token_hex(4)}"


def _num(v, lo, hi, default):
    try:
        f = float(v)
        if f != f:
            raise ValueError
    except Exception:
        return default
    return min(max(f, lo), hi)


def _box(b):
    """Normalised (x, y, w, h) box clipped to the frame, or None."""
    if not isinstance(b, dict):
        return None
    x, y = _num(b.get("x"), 0, 1, 0), _num(b.get("y"), 0, 1, 0)
    w, h = _num(b.get("w"), 0, 1, 0), _num(b.get("h"), 0, 1, 0)
    w, h = min(w, 1 - x), min(h, 1 - y)
    if w <= 1e-4 or h <= 1e-4:
        return None
    return {"x": round(x, 6), "y": round(y, 6), "w": round(w, 6), "h": round(h, 6)}


def _circle(c):
    if not isinstance(c, dict):
        return None
    r = _num(c.get("r"), 0, 0.25, 0)
    if r <= 1e-4:
        return None
    return {"x": round(_num(c.get("x"), 0, 1, 0.5), 6), "y": round(_num(c.get("y"), 0, 1, 0.5), 6),
            "r": round(r, 6)}


def _asset(v):
    v = str(v or "")
    return v if (v.startswith("a_") and v.replace("_", "").replace(".", "").isalnum() and len(v) < 64) else None


def _validate_params(t, p, warnings):
    p = p if isinstance(p, dict) else {}
    if t == "adjust":
        partial = {g: p[g] for g in ADJUST_GROUPS if isinstance(p.get(g), dict)}
        dropped = [g for g in p if g not in ADJUST_GROUPS]
        if dropped:
            warnings.append(f"adjust: dropped {dropped}")
        clean, w = R.validate(partial, partial=True, base=R.default_recipe())
        warnings += w
        # keep only what the layer set, so folding is additive
        return {g: {k: clean[g][k] for k in partial[g] if k in clean[g]} for g in partial}
    if t == "look":
        name = str(p.get("preset", ""))
        if name not in all_looks():
            raise StackError(f"unknown look '{name}'")
        return {"preset": name, "amount": _num(p.get("amount"), 0, 100, 100)}
    if t == "lut":
        name = str(p.get("name", ""))
        if name not in R.REGISTRIES["lut.name"] or name == "None":
            raise StackError(f"unknown LUT '{name}' (import it first)")
        return {"name": name, "strength": _num(p.get("strength"), 0, 100, 100)}
    if t == "film":
        stock = str(p.get("stock", ""))
        if stock not in R.REGISTRIES["film.stock"] or stock == "None":
            raise StackError(f"unknown film stock '{stock}'")
        return {"stock": stock, "strength": _num(p.get("strength"), 0, 100, 100)}
    if t == "style_match":
        w = []
        data = R._validate_match(p.get("data"), w)
        warnings += w
        if data is None:
            raise StackError("style_match needs frozen match data (create it with /style-match)")
        return {"strength": _num(p.get("strength"), 0, 100, 70), "method": str(p.get("method", ""))[:40],
                "reference_name": str(p.get("reference_name", data.get("reference_name", "")))[:200],
                "data": data}
    if t == "red_eye":
        eyes = [e for e in (_circle(c) for c in (p.get("eyes") or [])[:16]) if e]
        return {"strength": _num(p.get("strength"), 0, 100, 100), "eyes": eyes}
    if t == "teeth_whiten":
        regions = [b for b in (_box(r) for r in (p.get("regions") or [])[:8]) if b]
        return {"strength": _num(p.get("strength"), 0, 100, 35), "regions": regions}
    if t == "ai_edit":
        out = {
            "prompt": str(p.get("prompt", ""))[:2000],
            "provider": str(p.get("provider", ""))[:40],
            "model": str(p.get("model", ""))[:120],
            "preserve": bool(p.get("preserve", True)),
            "box": _box(p.get("box")),
            "crop": _box(p.get("crop")) or {"x": 0, "y": 0, "w": 1, "h": 1},
            "feather": _num(p.get("feather"), 0, 400, 40),
            "strength": _num(p.get("strength"), 0, 100, 100),
            "sensitivity": _num(p.get("sensitivity"), 0, 100, 50),
            "input_asset": _asset(p.get("input_asset")),
            "output_asset": _asset(p.get("output_asset")),
            "notes": str(p.get("notes", ""))[:1000],
        }
        if not out["input_asset"] or not out["output_asset"]:
            raise StackError("ai_edit layer has no stored provider result (create it with /ai-edit)")
        return out
    raise StackError(f"unknown layer type '{t}'")


def validate_stack(stack):
    """Return (clean_stack, warnings). Raises StackError for unusable input."""
    if not isinstance(stack, dict):
        raise StackError("stack must be an object")
    layers = stack.get("layers", [])
    if not isinstance(layers, list):
        raise StackError("layers must be a list")
    if len(layers) > MAX_LAYERS:
        raise StackError(f"too many layers (max {MAX_LAYERS})")
    warnings, out, seen = [], [], set()
    for i, L in enumerate(layers):
        if not isinstance(L, dict):
            raise StackError(f"layer {i} is not an object")
        t = L.get("type")
        if t not in LAYER_TYPES:
            raise StackError(f"layer {i}: unknown type '{t}'")
        lid = str(L.get("id") or new_id())
        if lid in seen or not lid.replace("_", "").isalnum() or len(lid) > 40:
            lid = new_id()
        seen.add(lid)
        w = []
        params = _validate_params(t, L.get("params"), w)
        warnings += [f"{t} ({lid}): {m}" for m in w]
        out.append({"id": lid, "type": t, "enabled": bool(L.get("enabled", True)),
                    "name": str(L.get("name") or t.replace("_", " ").title())[:80], "params": params})
    # pixel layers always render before grade layers; keep the user's order within each kind
    pix = [L for L in out if L["type"] in PIXEL_TYPES]
    grade = [L for L in out if L["type"] in GRADE_TYPES]
    if out != pix + grade:
        warnings.append("pixel layers (retouch, AI edits) were moved above grade layers: "
                        "content fixes always render before grading")
    return {"version": STACK_VERSION, "layers": pix + grade}, warnings


def empty_stack():
    return {"version": STACK_VERSION, "layers": []}


# --------------------------------------------------------------------------- folding
def _add_partial(recipe, partial, scale=1.0):
    """Add `partial` onto `recipe`: numeric values add their offset from the
    default (scaled); curves/enums/registry names replace."""
    for g, sub in partial.items():
        if g not in recipe or not isinstance(sub, dict):
            continue
        for k, v in sub.items():
            path = f"{g}.{k}"
            if g == "tone_curve":
                if scale >= 1.0:
                    recipe[g][k] = copy.deepcopy(v)
                else:  # pull curve points toward identity
                    recipe[g][k] = [[x, x + (y - x) * scale] for x, y in v]
            elif path in R.NUMERIC_BY_PATH:
                p = R.NUMERIC_BY_PATH[path]
                if path in ("film.strength", "lut.strength", "match.strength"):
                    recipe[g][k] = float(v) * scale
                elif path in ("effects.grain_size", "effects.grain_roughness", "effects.grain_seed",
                              "effects.vignette_midpoint", "effects.vignette_feather",
                              "effects.vignette_roundness", "effects.halation_radius",
                              "effects.halation_threshold", "detail.sharpen_radius",
                              "detail.sharpen_masking", "color_grading.blending", "color_grading.balance") \
                        or path.endswith("_hue") and g == "color_grading":
                    recipe[g][k] = v      # shape parameters, not amounts: last wins
                else:
                    recipe[g][k] = recipe[g][k] + (float(v) - p.default) * scale
            else:
                recipe[g][k] = v


def fold_recipe(stack):
    """Fold the enabled grade layers into one validated Darkroom recipe."""
    rec = R.default_recipe()
    looks = all_looks()
    for L in stack["layers"]:
        if not L["enabled"] or L["type"] not in GRADE_TYPES:
            continue
        p = L["params"]
        if L["type"] == "adjust":
            _add_partial(rec, p)
        elif L["type"] == "look":
            partial, _ = R.validate(looks[p["preset"]]["recipe"], partial=True, base=R.default_recipe())
            src = looks[p["preset"]]["recipe"]
            only = {g: {k: partial[g][k] for k in src[g]} for g in src if g in partial}
            _add_partial(rec, only, p["amount"] / 100.0)
        elif L["type"] == "lut":
            rec["lut"]["name"], rec["lut"]["strength"] = p["name"], p["strength"]
        elif L["type"] == "film":
            rec["film"]["stock"], rec["film"]["strength"] = p["stock"], p["strength"]
        elif L["type"] == "style_match":
            rec["match"]["data"], rec["match"]["strength"] = p["data"], p["strength"]
    clean, _ = R.validate(rec)
    return clean
