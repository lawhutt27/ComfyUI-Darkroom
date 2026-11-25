"""HTTP API for the Darkroom Web worker.

Run:  uvicorn darkroom_web.api:app --port 8000   (from webapp/backend)

Heavy routes are plain `def` so FastAPI runs them in its thread pool and
the event loop stays responsive.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from darkroom_studio import recipe as R
from darkroom_studio.export import OUTPUT_SPACE_CHOICES
from darkroom_studio.imaging import SUPPORTED_EXTS

from . import __version__, retouch
from .config import Settings, load_dotenv, optional_packages
from .looks import catalogue
from .pipeline import EXPORT_FORMATS, SOCIAL_PRESETS, ProjectError, ProjectStore, _png_bytes
from .prompting import PRESERVE_INSTRUCTIONS
from .providers import ProviderError, default_provider, registry
from .stack import PIXEL_TYPES, StackError


def _safe_name(name, default="upload"):
    base = os.path.basename(name or default)
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" .") or default
    return base[:120]


SAMPLES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "samples"))


def list_samples():
    if not os.path.isdir(SAMPLES_DIR):
        return []
    return sorted(f for f in os.listdir(SAMPLES_DIR) if os.path.splitext(f)[1].lower() in SUPPORTED_EXTS)


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        load_dotenv()
        settings = Settings()
    store = ProjectStore(settings)
    providers = registry(settings)
    app = FastAPI(title="Darkroom Web", version=__version__)
    app.state.store, app.state.providers = store, providers
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                       allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Render-Ms"])

    def project(pid):
        try:
            return store.get(pid)
        except KeyError:
            raise HTTPException(404, "project not found")
        except ProjectError as e:
            raise HTTPException(400, str(e))

    def save_upload(f: UploadFile, allowed):
        name = _safe_name(f.filename)
        ext = os.path.splitext(name)[1].lower()
        if ext not in allowed:
            raise HTTPException(415, f"Unsupported file type '{ext or '?'}'. Supported: "
                                + ", ".join(sorted(e.lstrip('.').upper() for e in allowed)))
        d = tempfile.mkdtemp(prefix="darkroom_up_", dir=store.workdir)
        path = os.path.join(d, name)
        limit, n = settings.max_upload_mb * 1024 * 1024, 0
        with open(path, "wb") as out:
            while chunk := f.file.read(1 << 20):
                n += len(chunk)
                if n > limit:
                    out.close()
                    shutil.rmtree(d, ignore_errors=True)
                    raise HTTPException(413, f"file is larger than {settings.max_upload_mb} MB")
                out.write(chunk)
        return path, d

    def state(p, warnings=()):
        return {"project": p.info(), "stack": p.stack, "warnings": list(warnings)}

    # ------------------------------------------------------------------ catalogue
    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "optional": optional_packages(),
                "providers": [p.describe() for p in providers.values()],
                "default_provider": default_provider(providers),
                "looks": catalogue(), "film_stocks": [n for n in R.REGISTRIES["film.stock"] if n != "None"],
                "luts": [n for n in store.studio.luts.names() if n != "None"],
                "export_formats": EXPORT_FORMATS, "output_spaces": OUTPUT_SPACE_CHOICES,
                "social_presets": {k: v[0] for k, v in SOCIAL_PRESETS.items()},
                "preservation_instructions": PRESERVE_INSTRUCTIONS,
                "upload_types": sorted(SUPPORTED_EXTS), "provider_long_edge": settings.provider_long_edge,
                "samples": list_samples(), "face_detector": retouch.detector_status()}

    @app.post("/api/luts")
    def import_lut(file: UploadFile = File(...)):
        path, d = save_upload(file, {".cube"})
        try:
            lut = store.import_lut(path)
        except Exception as e:
            raise HTTPException(400, f"not a valid .cube LUT: {e}")
        finally:
            shutil.rmtree(d, ignore_errors=True)
        return {"name": lut.name, "kind": lut.kind, "size": lut.size,
                "luts": [n for n in store.studio.luts.names() if n != "None"]}

    # ------------------------------------------------------------------ projects
    @app.post("/api/projects")
    def create_project(file: UploadFile = File(...)):
        path, d = save_upload(file, SUPPORTED_EXTS)
        try:
            p = store.create(path)
        except (ProjectError, ValueError) as e:
            raise HTTPException(400, str(e))
        except Exception as e:  # decoding failures from rawpy/Pillow
            raise HTTPException(422, f"could not open this photo: {e}")
        finally:
            shutil.rmtree(d, ignore_errors=True)
        return state(p)

    @app.post("/api/samples/{name}")
    def open_sample(name: str):
        if name not in list_samples():
            raise HTTPException(404, "no such sample")
        d = tempfile.mkdtemp(prefix="darkroom_up_", dir=store.workdir)
        try:
            path = os.path.join(d, name)
            shutil.copy(os.path.join(SAMPLES_DIR, name), path)
            return state(store.create(path))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        return state(project(pid))

    @app.get("/api/projects/{pid}/before.png")
    def before(pid: str):
        return Response(_png_bytes(project(pid).before_preview()), media_type="image/png",
                        headers={"Cache-Control": "max-age=3600"})

    @app.post("/api/projects/{pid}/render")
    def render(pid: str, body: dict = Body(default={})):
        p = project(pid)
        stack = body.get("stack")
        if stack is not None:
            try:
                from .stack import validate_stack
                stack, _ = validate_stack(stack)
            except StackError as e:
                raise HTTPException(400, str(e))
        img, secs = p.render_preview(stack)
        return Response(_png_bytes(img), media_type="image/png", headers={"X-Render-Ms": str(int(secs * 1000))})

    @app.put("/api/projects/{pid}/stack")
    def put_stack(pid: str, body: dict = Body(...)):
        p = project(pid)
        try:
            _, warnings = p.commit(body.get("stack", body))
        except (StackError, ProjectError) as e:
            raise HTTPException(400, str(e))
        return state(p, warnings)

    @app.post("/api/projects/{pid}/undo")
    def undo(pid: str):
        p = project(pid)
        p.undo()
        return state(p)

    @app.post("/api/projects/{pid}/redo")
    def redo(pid: str):
        p = project(pid)
        p.redo()
        return state(p)

    # ------------------------------------------------------------------ tools
    @app.post("/api/projects/{pid}/detect")
    def detect(pid: str, body: dict = Body(...)):
        try:
            return project(pid).detect(body.get("kind"))
        except ProjectError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/projects/{pid}/style-match")
    def style_match(pid: str, file: UploadFile = File(...), strength: float = Form(70),
                    method: str = Form("Colour + tone")):
        p = project(pid)
        path, d = save_upload(file, SUPPORTED_EXTS)
        try:
            layer = p.style_match(path, strength, method)
            stack = p.stack
            stack["layers"] = [L for L in stack["layers"] if L["type"] != "style_match"] + [layer]
            _, warnings = p.commit(stack)
        except Exception as e:
            raise HTTPException(422, f"style match failed: {e}")
        finally:
            shutil.rmtree(d, ignore_errors=True)
        return state(p, warnings)

    @app.post("/api/projects/{pid}/ai-edit")
    def ai_edit(pid: str, body: dict = Body(...)):
        p = project(pid)
        pid_ = body.get("provider") or default_provider(providers)
        prov = providers.get(pid_)
        if prov is None:
            raise HTTPException(400, f"unknown provider '{pid_}'")
        if not prov.available():
            raise HTTPException(400, f"{prov.label} is not configured (set its API key; see README)")
        try:
            layer, full_prompt = p.ai_edit(
                prov, str(body.get("prompt", "")), box=body.get("box"), preserve=bool(body.get("preserve", True)),
                feather=float(body.get("feather", 40)), sensitivity=float(body.get("sensitivity", 50)),
                strength=float(body.get("strength", 100)), model=str(body.get("model", "") or ""))
            stack = p.stack
            n_pix = sum(1 for L in stack["layers"] if L["type"] in PIXEL_TYPES)
            stack["layers"].insert(n_pix, layer)          # after the other content fixes
            _, warnings = p.commit(stack)
        except (ProviderError, ValueError) as e:
            raise HTTPException(422, str(e))
        out = state(p, warnings)
        out["layer_id"] = layer["id"]
        out["sent_prompt"] = full_prompt
        return out

    @app.get("/api/projects/{pid}/layers/{lid}/mask.png")
    def layer_mask(pid: str, lid: str):
        p = project(pid)
        layer = next((L for L in p.stack["layers"] if L["id"] == lid and L["type"] == "ai_edit"), None)
        if layer is None:
            raise HTTPException(404, "no such AI layer")
        return Response(p.ai_mask_png(layer), media_type="image/png")

    @app.get("/api/projects/{pid}/assets/{aid}")
    def asset(pid: str, aid: str):
        p = project(pid)
        try:
            path = p.asset_path(aid)
        except ProjectError as e:
            raise HTTPException(400, str(e))
        if not os.path.exists(path):
            raise HTTPException(404, "no such asset")
        return FileResponse(path, media_type="image/png")

    # ------------------------------------------------------------------ export
    @app.post("/api/projects/{pid}/export")
    def export(pid: str, body: dict = Body(default={})):
        p = project(pid)
        try:
            res = p.export(fmt=body.get("format", "tiff16"),
                           output_space=body.get("output_space", "Same as source/working space"),
                           quality=int(body.get("quality", 97)), keep_metadata=bool(body.get("keep_metadata", True)),
                           social=body.get("social") or None)
        except ProjectError as e:
            raise HTTPException(400, str(e))
        res["download"] = f"/api/projects/{pid}/exports/{res['file']}"
        res.pop("path", None)
        res["sidecar"] = os.path.basename(res.get("sidecar") or "")
        return JSONResponse(res)

    @app.get("/api/projects/{pid}/exports/{name}")
    def download(pid: str, name: str):
        p = project(pid)
        name = os.path.basename(name)
        path = os.path.join(p.exports_dir, name)
        if not os.path.isfile(path):
            raise HTTPException(404, "no such export")
        return FileResponse(path, filename=name)

    # ------------------------------------------------------------------ built UI
    dist = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist"))
    if os.path.isdir(dist):
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    return app


app = create_app() if os.environ.get("DARKROOM_NO_APP") != "1" else None
