"""Projects: one photo, its master/proxy, its edit stack and history.

`Project.render_level(stack, level)` is the single rendering function
(ARCHITECTURE.md §3): pixel layers in stack order, then the folded grade.
The proxy and the master go through exactly the same code.
"""

from __future__ import annotations

import copy
import io
import json
import os
import secrets
import threading
import time

import cv2
import numpy as np
from PIL import Image

from darkroom_studio import colorspace as cms
from darkroom_studio import engine
from darkroom_studio import export as X
from darkroom_studio.imaging import SUPPORTED_EXTS, load_source, make_proxy
from darkroom_studio.match import METHODS, _stats, tone_curve
from darkroom_studio.session import EditSession, Studio

from . import composite, retouch
from .lutx import TetraLUTRegistry
from .prompting import build_prompt
from .providers import EditRequest, ProviderError
from .stack import PIXEL_TYPES, empty_stack, fold_recipe, new_id, validate_stack

EXPORT_FORMATS = {
    "tiff16": "TIFF 16-bit (master, lossless)",
    "png16": "PNG 16-bit (lossless)",
    "png8": "PNG 8-bit (lossless)",
    "jpeg": "JPEG 8-bit (high quality)",
}
# Instagram-ready resized exports: (label, aspect w/h or None, target long/width px)
SOCIAL_PRESETS = {
    "ig_portrait": ("Instagram portrait 4:5 · 1080 × 1350", 4 / 5, (1080, 1350)),
    "ig_square": ("Instagram square 1:1 · 1080 × 1080", 1.0, (1080, 1080)),
    "ig_landscape": ("Instagram landscape 1.91:1 · 1080 × 566", 1.91, (1080, 566)),
    "long_1080": ("1080 px long edge, uncropped", None, None),
}
HISTORY_LIMIT = 100


class ProjectError(ValueError):
    pass


def _canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"))


def _png_bytes(u8):
    buf = io.BytesIO()
    Image.fromarray(u8).save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


class Project:
    def __init__(self, store, pid, upload_path=None):
        self.store = store
        self.id = pid
        self.dir = os.path.join(store.projects_dir, pid)
        self.assets_dir = os.path.join(self.dir, "assets")
        self.exports_dir = os.path.join(self.dir, "exports")
        os.makedirs(self.assets_dir, exist_ok=True)
        os.makedirs(self.exports_dir, exist_ok=True)
        self.lock = threading.RLock()
        meta_path = os.path.join(self.dir, "project.json")
        if upload_path is not None:
            self.session = EditSession(store.studio, upload_path)
            self.meta = {"id": pid, "name": self.session.source.original_name,
                         "upload": upload_path, "original": self.session.source.path, "created": time.time()}
            self.history = [empty_stack()]
            self.index = 0
            self._save()
        else:
            with open(meta_path) as f:
                self.meta = json.load(f)
            self.session = EditSession(store.studio, self.meta["upload"])
            with open(os.path.join(self.dir, "history.json")) as f:
                h = json.load(f)
            self.history, self.index = h["stacks"], h["index"]
        self._assets = {}
        self._pixel_cache = {}      # (level_key, canon(pixel layers)) -> linear base

    # ------------------------------------------------------------------ persistence
    def _save(self):
        with open(os.path.join(self.dir, "project.json"), "w") as f:
            json.dump(self.meta, f, indent=2)
        with open(os.path.join(self.dir, "history.json"), "w") as f:
            json.dump({"stacks": self.history, "index": self.index}, f)

    @property
    def stack(self):
        return copy.deepcopy(self.history[self.index])

    def commit(self, stack):
        clean, warnings = validate_stack(stack)
        for L in clean["layers"]:
            if L["type"] == "ai_edit":
                for k in ("input_asset", "output_asset"):
                    if not os.path.exists(self.asset_path(L["params"][k])):
                        raise ProjectError(f"asset {L['params'][k]} does not exist in this project")
        with self.lock:
            if _canon(clean) != _canon(self.history[self.index]):
                del self.history[self.index + 1:]
                self.history.append(clean)
                if len(self.history) > HISTORY_LIMIT:
                    self.history.pop(0)
                self.index = len(self.history) - 1
                self._save()
        return clean, warnings

    def undo(self):
        with self.lock:
            self.index = max(0, self.index - 1)
            self._save()
        return self.stack

    def redo(self):
        with self.lock:
            self.index = min(len(self.history) - 1, self.index + 1)
            self._save()
        return self.stack

    # ------------------------------------------------------------------ assets
    def asset_path(self, aid):
        if not aid or "/" in aid or ".." in aid:
            raise ProjectError("bad asset id")
        return os.path.join(self.assets_dir, aid)

    def save_asset(self, u8, kind):
        aid = f"a_{kind}_{secrets.token_hex(6)}.png"
        Image.fromarray(u8, "RGB").save(self.asset_path(aid), format="PNG")
        return aid

    def load_asset(self, aid):
        if aid not in self._assets:
            with Image.open(self.asset_path(aid)) as im:
                self._assets[aid] = np.asarray(im.convert("RGB"), dtype=np.uint8).copy()
        return self._assets[aid]

    # ------------------------------------------------------------------ info
    @property
    def source(self):
        return self.session.source

    @property
    def full_long(self):
        return max(self.source.width, self.source.height)

    def info(self):
        s = self.source
        return {"id": self.id, "name": self.meta["name"], "width": s.width, "height": s.height,
                "megapixels": round(s.width * s.height / 1e6, 1), "bit_depth": s.bit_depth,
                "kind": s.kind, "format": s.file_format, "color_space": s.space.name,
                "preview": {"width": int(self.session.proxy.shape[1]), "height": int(self.session.proxy.shape[0])},
                "camera": {k: s.info.get(k) for k in ("camera_make", "camera_model", "lens", "iso", "shutter",
                                                      "aperture", "focal_length", "datetime") if s.info.get(k)},
                "notes": s.notes, "can_undo": self.index > 0, "can_redo": self.index < len(self.history) - 1}

    # ------------------------------------------------------------------ rendering
    def _level(self, which):
        if which == "proxy":
            return self.session.proxy, self.session.proxy_scale
        return self.source.linear, 1.0

    def apply_pixel_layers(self, img, layers):
        """Apply enabled pixel layers to `img` (linear, any level). Returns a new
        array, or `img` itself when there is nothing to do."""
        active = [L for L in layers if L["enabled"] and L["type"] in PIXEL_TYPES]
        if not active:
            return img
        out = np.array(img, dtype=np.float32, copy=True)
        space = self.source.space
        for L in active:
            p = L["params"]
            if L["type"] == "red_eye":
                retouch.red_eye(out, space, p["eyes"], p["strength"])
            elif L["type"] == "teeth_whiten":
                retouch.whiten_teeth(out, space, p["regions"], p["strength"])
            elif L["type"] == "ai_edit":
                composite.composite(out, space, p, self.load_asset(p["input_asset"]),
                                    self.load_asset(p["output_asset"]), self.full_long)
        return out

    def pixel_base(self, stack, which):
        img, _ = self._level(which)
        pix = [L for L in stack["layers"] if L["enabled"] and L["type"] in PIXEL_TYPES]
        if not pix:
            return img
        if which != "proxy":
            return self.apply_pixel_layers(img, pix)
        key = (which, _canon(pix))
        if key not in self._pixel_cache:
            self._pixel_cache = {key: self.apply_pixel_layers(img, pix)}   # keep one entry
        return self._pixel_cache[key]

    def render_preview(self, stack=None):
        stack = stack if stack is not None else self.stack
        t0 = time.time()
        base = self.pixel_base(stack, "proxy")
        rec = fold_recipe(stack)
        out = engine.render(base, rec, self.session.ctx, level=self.session.proxy_scale)
        return engine.to_display(out, self.source.space), time.time() - t0

    def before_preview(self):
        return self.session.before_display

    def render_full(self, stack=None):
        stack = stack if stack is not None else self.stack
        base = self.pixel_base(stack, "full")
        rec = fold_recipe(stack)
        out = engine.render_tiled(base, rec, self.session.ctx, level=1.0)
        alpha = engine.render_alpha(self.source.alpha, rec, self.session.ctx, level=1.0)
        return out, alpha, rec

    # ------------------------------------------------------------------ tools
    def detect(self, kind):
        proxy = self.session.proxy
        if kind == "eyes":
            shapes, faces, method = retouch.detect_eyes(proxy, self.source.space)
        elif kind == "mouths":
            shapes, faces, method = retouch.detect_mouths(proxy, self.source.space)
        else:
            raise ProjectError("kind must be 'eyes' or 'mouths'")
        return {"shapes": shapes, "faces": faces, "method": method}

    def style_match(self, ref_path, strength=70, method="Colour + tone"):
        stack = self.stack
        ref = load_source(ref_path, os.path.join(self.store.workdir, "references"))
        ref_small, _ = make_proxy(ref.linear, 768)
        rec = fold_recipe({"layers": [L for L in stack["layers"] if L["type"] != "style_match"]})
        an = self.session.analysis
        pre = engine.render(an, rec, self.session.ctx, level=an.shape[1] / self.source.width, stop="pre_match")
        src_lab = engine.OkLab(self.source.space).from_linear(pre)
        ref_lab = engine.OkLab(ref.space).from_linear(ref_small)
        sm, ss = _stats(src_lab)
        rm, rs = _stats(ref_lab)
        m = METHODS.get(method, "lab_stats+tone")
        data = {"method": m, "src_mean": sm.tolist(), "src_std": ss.tolist(), "ref_mean": rm.tolist(),
                "ref_std": rs.tolist(), "tone_lut": [], "reference_name": ref.original_name}
        if m == "lab_stats+tone":
            data["tone_lut"] = tone_curve(src_lab[..., 0].ravel(), ref_lab[..., 0].ravel()).round(5).tolist()
        return {"id": new_id(), "type": "style_match", "enabled": True,
                "name": f"Match: {ref.original_name}"[:80],
                "params": {"strength": strength, "method": method, "reference_name": ref.original_name,
                           "data": data}}

    def ai_edit(self, provider, prompt, box=None, preserve=True, feather=40, sensitivity=50, strength=100,
                model=""):
        """Run one provider call and return a new (not yet committed) ai_edit layer."""
        from .stack import _box
        stack = self.stack
        box = _box(box)
        if provider.needs_mask and not box:
            raise ProviderError("This provider needs a target box: drag a box over the area to change.")
        W, H = self.source.width, self.source.height
        if box:     # target + 25 % context on each side
            mx, my = box["w"] * 0.25, box["h"] * 0.25
            cx0, cy0 = max(0.0, box["x"] - mx), max(0.0, box["y"] - my)
            cx1, cy1 = min(1.0, box["x"] + box["w"] + mx), min(1.0, box["y"] + box["h"] + my)
        else:
            cx0, cy0, cx1, cy1 = 0.0, 0.0, 1.0, 1.0
        x0, y0 = int(round(cx0 * W)), int(round(cy0 * H))
        x1, y1 = int(round(cx1 * W)), int(round(cy1 * H))
        crop = {"x": x0 / W, "y": y0 / H, "w": (x1 - x0) / W, "h": (y1 - y0) / H}
        # the model sees the photo with the pixel layers below it, at the best
        # resolution the provider takes: a crop of the master, downsampled
        pix = [L for L in stack["layers"] if L["enabled"] and L["type"] in PIXEL_TYPES]
        region = self.source.linear[y0:y1, x0:x1]
        if pix:
            region = self.apply_pixel_layers(self.source.linear, pix)[y0:y1, x0:x1]
        lim = self.store.settings.provider_long_edge
        sc = min(1.0, lim / max(region.shape[:2]))
        if sc < 1.0:
            region = cv2.resize(region, (max(1, round(region.shape[1] * sc)), max(1, round(region.shape[0] * sc))),
                                interpolation=cv2.INTER_AREA)
        input_u8 = engine.to_display(region, self.source.space)
        mask = None
        if box:
            ih, iw = input_u8.shape[:2]
            mask = np.zeros((ih, iw), np.uint8)
            bx0 = int(round((box["x"] - crop["x"]) / crop["w"] * iw))
            by0 = int(round((box["y"] - crop["y"]) / crop["h"] * ih))
            bx1 = int(round((box["x"] + box["w"] - crop["x"]) / crop["w"] * iw))
            by1 = int(round((box["y"] + box["h"] - crop["y"]) / crop["h"] * ih))
            mask[max(0, by0):by1, max(0, bx0):bx1] = 255
        full_prompt = build_prompt(prompt, preserve=preserve, targeted=bool(box))
        t0 = time.time()
        res = provider.edit(EditRequest(image=input_u8, prompt=full_prompt, user_prompt=prompt, mask=mask,
                                        model=model))
        aligned, ainfo = composite.align(res.image, input_u8)
        in_id, out_id = self.save_asset(input_u8, "in"), self.save_asset(aligned, "out")
        notes = {"provider": provider.id, "model": res.model, "seconds": round(time.time() - t0, 1),
                 "sent_px": [int(input_u8.shape[1]), int(input_u8.shape[0])], "alignment": ainfo,
                 "provider_notes": res.notes}
        name = " ".join(prompt.split())[:60] or "AI edit"
        return {"id": new_id(), "type": "ai_edit", "enabled": True, "name": name,
                "params": {"prompt": prompt, "provider": provider.id, "model": res.model, "preserve": preserve,
                           "box": box, "crop": crop, "feather": feather, "strength": strength,
                           "sensitivity": sensitivity, "input_asset": in_id, "output_asset": out_id,
                           "notes": json.dumps(notes)}}, full_prompt

    def ai_mask_png(self, layer):
        p = layer["params"]
        ph, pw = self.session.proxy.shape[:2]
        m = composite.mask_overlay(p, self.load_asset(p["input_asset"]), self.load_asset(p["output_asset"]),
                                   pw, ph, self.full_long)
        u8 = (np.clip(m, 0, 1) * 255 + 0.5).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(u8, "L").save(buf, format="PNG")
        return buf.getvalue()

    # ------------------------------------------------------------------ export
    def export(self, fmt="tiff16", output_space="Same as source/working space", quality=97,
               keep_metadata=True, social=None):
        if fmt not in EXPORT_FORMATS:
            raise ProjectError(f"format must be one of {list(EXPORT_FORMATS)}")
        stack = self.stack
        t0 = time.time()
        full, alpha, rec = self.render_full(stack)
        if not social:
            res = X.export(self.source, full, {"stack": stack, "folded_recipe": rec}, self.exports_dir,
                           EXPORT_FORMATS[fmt], output_space, quality, keep_metadata, alpha_full=alpha)
            res["full_resolution"] = True
        else:
            res = self._export_social(full, social, quality, keep_metadata, stack)
        res["seconds"] = round(time.time() - t0, 1)
        res["file"] = os.path.basename(res["path"])
        return res

    def _export_social(self, full, social, quality, keep_metadata, stack):
        if social not in SOCIAL_PRESETS:
            raise ProjectError(f"social preset must be one of {list(SOCIAL_PRESETS)}")
        label, aspect, target = SOCIAL_PRESETS[social]
        H, W = full.shape[:2]
        img = full
        if aspect is not None:          # centred crop to the aspect ratio
            if W / H > aspect:
                nw = int(round(H * aspect))
                x0 = (W - nw) // 2
                img = full[:, x0:x0 + nw]
            else:
                nh = int(round(W / aspect))
                y0 = (H - nh) // 2
                img = full[y0:y0 + nh]
            tw, th = target
        else:
            s = 1080.0 / max(W, H)
            tw, th = int(round(W * s)), int(round(H * s))
        if (img.shape[1], img.shape[0]) != (tw, th):
            interp = cv2.INTER_AREA if img.shape[1] > tw else cv2.INTER_CUBIC
            img = cv2.resize(np.ascontiguousarray(img), (tw, th), interpolation=interp)
        q, clip = X.quantise(img, self.source.space, cms.SRGB, 8)
        stem = os.path.splitext(self.source.original_name)[0] + f"_{social}"
        path = X._unique_path(self.exports_dir, stem, ".jpg")
        exif = X._prepared_exif(self.source, tw, th) if keep_metadata else None
        X.write_image(path, q, EXPORT_FORMATS["jpeg"], cms.SRGB.icc_bytes(), exif, max(95, int(quality)))
        warnings = [f"Resized export for social media ({label}). This is NOT the full-resolution master.",
                    "JPEG is lossy; keep a TIFF/PNG master."]
        side = os.path.splitext(path)[0] + ".darkroom.json"
        with open(side, "w") as f:
            json.dump({"source": self.source.original_name, "output": {"preset": label, "dimensions": [tw, th]},
                       "stack": stack, "warnings": warnings}, f, indent=2)
        return {"path": path, "sidecar": side, "width": tw, "height": th, "bits": 8, "warnings": warnings,
                "clipping": clip, "space": "sRGB", "full_resolution": False}


class ProjectStore:
    def __init__(self, settings):
        self.settings = settings
        self.workdir = os.path.abspath(settings.workdir)
        self.projects_dir = os.path.join(self.workdir, "projects")
        os.makedirs(self.projects_dir, exist_ok=True)
        self.studio = Studio(os.path.join(self.workdir, "library"), settings.preview_long_edge)
        self.studio.luts = TetraLUTRegistry(self.studio.dirs["luts"])
        self.studio.refresh_registries()
        self._open = {}
        self._lock = threading.Lock()

    def create(self, upload_path):
        ext = os.path.splitext(upload_path)[1].lower()
        if ext not in SUPPORTED_EXTS:
            raise ProjectError(f"Unsupported file type '{ext}'.")
        pid = "p_" + secrets.token_hex(6)
        up_dir = os.path.join(self.projects_dir, pid, "upload")
        os.makedirs(up_dir, exist_ok=True)
        kept = os.path.join(up_dir, os.path.basename(upload_path))
        import shutil
        shutil.move(upload_path, kept)
        p = Project(self, pid, kept)
        with self._lock:
            self._open = {pid: p}          # one open photo at a time keeps memory bounded
        return p

    def get(self, pid):
        if not pid.startswith("p_") or not pid[2:].isalnum():
            raise ProjectError("bad project id")
        with self._lock:
            if pid in self._open:
                return self._open[pid]
        if not os.path.exists(os.path.join(self.projects_dir, pid, "project.json")):
            raise KeyError(pid)
        p = Project(self, pid)
        with self._lock:
            self._open = {pid: p}
        return p

    def import_lut(self, path):
        lut = self.studio.luts.import_file(path)
        self.studio.refresh_registries()
        return lut
