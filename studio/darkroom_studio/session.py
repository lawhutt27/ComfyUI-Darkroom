"""
Application state independent of the UI (so it can be tested headlessly).

`Studio` owns the folders and shared registries; `EditSession` owns one open
photograph: the full-resolution source, the preview proxy, the undo history
and the render context.  The preview proxy is ONLY used for display; every
export renders from `source.linear` at full resolution.
"""

from __future__ import annotations

import copy
import json
import os
import re
import time

import cv2
import numpy as np

from . import colorspace as cms
from . import engine, recipe as R
from .export import export as export_image, write_raw_sidecar
from .film import FilmRegistry
from .imaging import load_source, make_proxy
from .lut import LUTRegistry, identity_lattice, write_cube


def _slug(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:80] or "untitled"


class Studio:
    def __init__(self, workdir, preview_long_edge=1600):
        self.workdir = os.path.abspath(workdir)
        self.dirs = {k: os.path.join(self.workdir, k) for k in
                     ("originals", "exports", "luts", "presets", "recipes", "uploads", "batch")}
        for d in self.dirs.values():
            os.makedirs(d, exist_ok=True)
        self.preview_long_edge = preview_long_edge
        self.films = FilmRegistry()
        self.luts = LUTRegistry(self.dirs["luts"])
        self.refresh_registries()

    def refresh_registries(self):
        R.REGISTRIES["film.stock"] = self.films.names()
        R.REGISTRIES["lut.name"] = self.luts.names()

    # ---- presets / saved recipes ------------------------------------------------
    def builtin_presets(self):
        from .presets import BUILTIN_PRESETS
        return BUILTIN_PRESETS

    def list_presets(self):
        names = [f"Built-in / {n}" for n in self.builtin_presets()]
        names += [f"User / {os.path.splitext(f)[0]}" for f in sorted(os.listdir(self.dirs["presets"]))
                  if f.endswith(".json")]
        return names

    def load_preset(self, name, base=None):
        if name.startswith("Built-in / "):
            partial = self.builtin_presets()[name[len("Built-in / "):]]["recipe"]
            return R.validate(partial, partial=True, base=R.default_recipe())
        if name.startswith("User / "):
            with open(os.path.join(self.dirs["presets"], name[len("User / "):] + ".json")) as f:
                return R.validate(json.load(f))
        raise KeyError(name)

    def save_preset(self, name, recipe):
        """Presets are look-only: reference-match data and grain seed are dropped."""
        r = copy.deepcopy(recipe)
        r["name"] = name
        r["match"]["data"] = None
        r["match"]["strength"] = 0
        path = os.path.join(self.dirs["presets"], _slug(name) + ".json")
        with open(path, "w") as f:
            json.dump(r, f, indent=2)
        return path

    def list_recipes(self):
        return sorted(os.path.splitext(f)[0] for f in os.listdir(self.dirs["recipes"]) if f.endswith(".json"))

    def save_recipe(self, name, recipe):
        r = copy.deepcopy(recipe)
        r["name"] = name
        path = os.path.join(self.dirs["recipes"], _slug(name) + ".json")
        with open(path, "w") as f:
            json.dump(r, f, indent=2)
        return path

    def load_recipe_file(self, path):
        with open(path) as f:
            return R.validate(json.load(f))

    def batch(self, files, recipe, fmt="TIFF 16-bit (master, lossless)", progress=None):
        """Apply one recipe to many photos at their own full resolution and zip the results.

        Each photo is opened (originals protected as usual), exported, and released before
        the next, so memory stays at one image. Failures are reported per file, not raised."""
        import zipfile
        recipe = R.validate(recipe)[0]
        out_zip = os.path.join(self.dirs["batch"], time.strftime("batch_%Y%m%d_%H%M%S.zip"))
        lines = []
        it = progress(files) if progress else files
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_STORED) as zf:
            for f in it:
                name = os.path.basename(f)
                try:
                    s = self.open(f)
                    res = s.export(fmt, "Same as source/working space", 97, True, recipe=recipe)
                    for k in ("path", "sidecar"):
                        zf.write(res[k], os.path.basename(res[k]))
                    lines.append((True, f"{name} \u2192 {res['width']}\u00d7{res['height']}"))
                    del s
                except Exception as e:  # noqa: BLE001 - reported per file
                    lines.append((False, f"{name}: {e}"))
        return out_zip, lines

    def open(self, path, raw_settings=None):
        return EditSession(self, path, raw_settings)


class EditSession:
    def __init__(self, studio: Studio, upload_path, raw_settings=None):
        self.studio = studio
        t0 = time.time()
        self.source = load_source(upload_path, studio.dirs["originals"], raw_settings)
        self.load_seconds = time.time() - t0
        self.history = R.History()
        self._build_levels()

    def _build_levels(self):
        src = self.source
        self.proxy, self.proxy_scale = make_proxy(src.linear, self.studio.preview_long_edge)
        self.analysis, _ = make_proxy(src.linear, 512)
        self.ctx = engine.Context(src.space, (src.width, src.height), films=self.studio.films,
                                  luts=self.studio.luts, analysis=self.analysis)
        self.before_display = engine.to_display(self.proxy, src.space)
        self._crop_cache = {}

    def redevelop(self, raw_settings):
        """Re-run the RAW stage from the untouched original (keeps the edit history)."""
        if self.source.kind != "raw":
            return
        from .imaging import load_source as _ls
        self.source = _ls(self.source.path, self.studio.dirs["originals"], raw_settings)
        self._build_levels()

    # ---- recipe ------------------------------------------------------------------
    @property
    def recipe(self):
        return self.history.current

    def commit(self, recipe):
        clean, warnings = R.validate(recipe)
        self.history.push(clean)
        return clean, warnings

    # ---- rendering -----------------------------------------------------------------
    def render_preview(self, recipe=None):
        recipe = recipe if recipe is not None else self.recipe
        t0 = time.time()
        out = engine.render(self.proxy, recipe, self.ctx, level=self.proxy_scale)
        disp = engine.to_display(out, self.source.space)
        return self.before_display, disp, time.time() - t0

    def viewport(self, zoom, cx=0.5, cy=0.5, vw=1400, vh=900):
        """Full-resolution crop for 100%/200% zoom. Returns region in full-res px."""
        z = {"100%": 1, "200%": 2}.get(zoom, 1)
        w = min(self.source.width, int(vw / z))
        h = min(self.source.height, int(vh / z))
        x = int(round(cx * (self.source.width - w)))
        y = int(round(cy * (self.source.height - h)))
        return (x, y, w, h), z

    def render_crop(self, recipe, zoom, cx=0.5, cy=0.5):
        region, z = self.viewport(zoom, cx, cy)
        x, y, w, h = region
        key = (region,)
        if key not in self._crop_cache:
            self._crop_cache = {key: engine.to_display(self.source.linear[y:y + h, x:x + w], self.source.space)}
        before = self._crop_cache[key]
        t0 = time.time()
        out = engine.render(self.source.linear, recipe, self.ctx, region=region, level=1.0)
        after = engine.to_display(out, self.source.space)
        if z > 1:
            before = cv2.resize(before, (w * z, h * z), interpolation=cv2.INTER_NEAREST)
            after = cv2.resize(after, (w * z, h * z), interpolation=cv2.INTER_NEAREST)
        return before, after, time.time() - t0, region

    def render_full(self, recipe=None):
        recipe = recipe if recipe is not None else self.recipe
        out = engine.render_tiled(self.source.linear, recipe, self.ctx, level=1.0)
        alpha = engine.render_alpha(self.source.alpha, recipe, self.ctx, level=1.0)
        return out, alpha

    def export(self, fmt, output_space, quality=97, keep_metadata=True, recipe=None):
        recipe = recipe if recipe is not None else self.recipe
        full, alpha = self.render_full(recipe)
        res = export_image(self.source, full, recipe, self.studio.dirs["exports"], fmt, output_space,
                           quality, keep_metadata, alpha_full=alpha)
        if self.source.kind == "raw":
            res["raw_sidecar"] = write_raw_sidecar(self.source, self.studio.dirs["exports"])
        return res

    def export_developed(self, fmt="TIFF 16-bit (master, lossless)", output_space="Same as source/working space"):
        """The developed RAW (no grading) as a 16-bit master. This is NOT the RAW file."""
        res = export_image(self.source, self.source.linear, R.default_recipe(), self.studio.dirs["exports"],
                           fmt, output_space, 100, True, stem_suffix="_developed",
                           extra_sidecar={"note": "Developed from RAW with the settings in raw_development; "
                                                  "no grading applied. This is not the RAW original."})
        return res

    def export_lut(self, name, size=33, recipe=None):
        """Bake the point-wise part of the recipe into a .cube (sRGB in/out)."""
        recipe = copy.deepcopy(recipe if recipe is not None else self.recipe)
        spatial = []
        for path in ("effects.clarity", "effects.texture", "effects.dehaze", "effects.grain_amount",
                     "effects.vignette_amount", "effects.halation_amount", "effects.chromatic_aberration",
                     "detail.sharpen_amount", "detail.nr_luminance", "detail.nr_color", "lens.distortion",
                     "lens.ca_red", "lens.ca_blue", "lens.vignette_correction"):
            if R.get(recipe, path):
                spatial.append(path)
                R.set_(recipe, path, 0)
        lat = identity_lattice(size)
        img = cms.srgb_decode(lat.reshape(size * size, size, 3))
        ctx = engine.Context(cms.SRGB, (size, size * size), films=self.studio.films, luts=self.studio.luts)
        out = engine.render(img, recipe, ctx, level=1.0)
        table = np.clip(cms.srgb_encode(out), 0, 1).reshape(size, size, size, 3)
        path = os.path.join(self.studio.dirs["exports"], _slug(name or "darkroom_grade") + ".cube")
        comments = ["Input/output: sRGB-encoded, Rec.709 primaries."]
        if spatial:
            comments.append("Not representable in a LUT and omitted: " + ", ".join(spatial))
        write_cube(path, table, title=name or "Darkroom grade", comments=comments)
        return path, spatial

    # ---- info ----------------------------------------------------------------------
    def status(self, extra=""):
        s = self.source
        return {
            "original": s.describe_dims(),
            "output": f"{s.width} × {s.height} px (unchanged)",
            "bit_depth": f"{s.bit_depth}-bit source → float32 working",
            "profile": s.space.summary(),
            "format": s.file_format + (" (RAW, developed with LibRaw)" if s.kind == "raw" else ""),
            "preview": f"proxy {self.proxy.shape[1]} × {self.proxy.shape[0]} (display only)",
            "extra": extra,
        }
