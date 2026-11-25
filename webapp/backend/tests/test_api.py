"""End-to-end through the HTTP API, with stand-in OpenRouter and Gemini servers."""

import base64
import io
import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from conftest import SAMPLES


def _edit_image(png_bytes):
    """What our fake model does: paint a grey patch in the middle, leave the rest."""
    im = np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGB")).copy()
    h, w = im.shape[:2]
    im[h // 3:2 * h // 3, w // 3:2 * w // 3] = 128
    buf = io.BytesIO()
    Image.fromarray(im).save(buf, format="PNG")
    return buf.getvalue()


class FakeAPI(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAPI.calls.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        if self.path.endswith("/chat/completions"):
            url = body["messages"][0]["content"][1]["image_url"]["url"]
            out = _edit_image(base64.b64decode(url.split(",", 1)[1]))
            resp = {"choices": [{"message": {"role": "assistant", "content": "Done.", "images": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(out).decode()}}]}}]}
        elif ":generateContent" in self.path:
            data = body["contents"][0]["parts"][1]["inline_data"]["data"]
            out = _edit_image(base64.b64decode(data))
            resp = {"candidates": [{"content": {"parts": [
                {"text": "ok"}, {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(out).decode()}}]}}]}
        else:
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def fake_server():
    FakeAPI.calls = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def client(settings, fake_server):
    from darkroom_web.api import create_app
    settings.openrouter_api_key = "test-key"
    settings.openrouter_base_url = fake_server + "/api/v1"
    settings.gemini_api_key = "test-key"
    settings.gemini_base_url = fake_server + "/v1beta"
    return TestClient(create_app(settings))


def upload(client, path):
    with open(path, "rb") as f:
        r = client.post("/api/projects", files={"file": (os.path.basename(path), f, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()


def test_health_lists_capabilities(client):
    h = client.get("/api/health").json()
    ids = {p["id"]: p for p in h["providers"]}
    assert ids["openrouter"]["available"] and ids["local"]["available"]
    assert ids["local"]["sends_pixels_off_device"] is False
    assert any(L["name"] == "IG / Golden Hour" for L in h["looks"])
    assert "tiff16" in h["export_formats"] and "ig_portrait" in h["social_presets"]


def test_upload_rejects_unsupported(client, tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("hi")
    with open(p, "rb") as f:
        r = client.post("/api/projects", files={"file": ("notes.txt", f, "text/plain")})
    assert r.status_code == 415


def test_stack_render_undo_redo(client, png_path):
    st = upload(client, png_path)
    pid = st["project"]["id"]
    assert st["project"]["width"] == 1200 and st["stack"]["layers"] == []
    before = client.get(f"/api/projects/{pid}/before.png")
    assert before.status_code == 200 and before.headers["content-type"] == "image/png"
    stack = {"layers": [{"type": "look", "params": {"preset": "IG / Moody Matte", "amount": 60}}]}
    r = client.put(f"/api/projects/{pid}/stack", json={"stack": stack})
    assert r.status_code == 200 and r.json()["project"]["can_undo"]
    img = client.post(f"/api/projects/{pid}/render", json={})
    assert img.status_code == 200 and "x-render-ms" in img.headers
    a = np.asarray(Image.open(io.BytesIO(img.content)))
    b = np.asarray(Image.open(io.BytesIO(before.content)))
    assert a.shape == b.shape and np.abs(a.astype(int) - b.astype(int)).mean() > 2
    # live (uncommitted) render of a different stack
    live = client.post(f"/api/projects/{pid}/render", json={"stack": {"layers": []}})
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(live.content))), b)
    assert client.post(f"/api/projects/{pid}/undo").json()["stack"]["layers"] == []
    assert len(client.post(f"/api/projects/{pid}/redo").json()["stack"]["layers"]) == 1
    bad = client.put(f"/api/projects/{pid}/stack", json={"stack": {"layers": [{"type": "exec"}]}})
    assert bad.status_code == 400


def test_ai_edit_openrouter_composites_and_preserves(client, png_path):
    pid = upload(client, png_path)["project"]["id"]
    r = client.post(f"/api/projects/{pid}/ai-edit", json={
        "provider": "openrouter", "prompt": "remove the sign in the middle", "preserve": True})
    assert r.status_code == 200, r.text
    out = r.json()
    call = FakeAPI.calls[-1]
    assert call["headers"]["authorization"] == "Bearer test-key"
    assert call["body"]["modalities"] == ["image", "text"]
    text = call["body"]["messages"][0]["content"][0]["text"]
    assert text.startswith("remove the sign in the middle") and "Preserve every person's identity" in text
    layer = out["stack"]["layers"][-1]
    assert layer["type"] == "ai_edit" and layer["params"]["provider"] == "openrouter"
    sent = Image.open(io.BytesIO(client.get(f"/api/projects/{pid}/assets/{layer['params']['input_asset']}").content))
    assert max(sent.size) <= 1536
    mask = np.asarray(Image.open(io.BytesIO(client.get(f"/api/projects/{pid}/layers/{layer['id']}/mask.png").content)))
    h, w = mask.shape
    assert mask[h // 2, w // 2] > 200 and mask[5, 5] == 0        # only the changed middle
    # full-resolution export: outside the mask the pixels are the original's
    ex = client.post(f"/api/projects/{pid}/export", json={"format": "png8"}).json()
    assert ex["full_resolution"] and (ex["width"], ex["height"]) == (1200, 800)
    got = np.asarray(Image.open(io.BytesIO(client.get(ex["download"]).content)).convert("RGB"))
    src = np.asarray(Image.open(png_path).convert("RGB"))
    assert np.array_equal(got[:150], src[:150])
    assert abs(int(got[400, 600].mean()) - 128) < 6


def test_ai_edit_gemini_with_box(client, png_path):
    pid = upload(client, png_path)["project"]["id"]
    box = {"x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2}
    r = client.post(f"/api/projects/{pid}/ai-edit", json={"provider": "gemini", "prompt": "make it grey", "box": box,
                                                          "preserve": False, "feather": 0})
    assert r.status_code == 200, r.text
    call = FakeAPI.calls[-1]
    assert "gemini-2.5-flash-image:generateContent" in call["path"]
    assert "target area" in call["body"]["contents"][0]["parts"][0]["text"]
    layer = r.json()["stack"]["layers"][-1]
    c = layer["params"]["crop"]
    assert c["x"] == pytest.approx(0.35, abs=0.002) and c["w"] == pytest.approx(0.3, abs=0.002)


def test_local_provider_needs_box_and_unconfigured_provider_refused(client, settings, png_path):
    pid = upload(client, png_path)["project"]["id"]
    r = client.post(f"/api/projects/{pid}/ai-edit", json={"provider": "local", "prompt": "remove"})
    assert r.status_code == 422 and "box" in r.json()["detail"]
    r = client.post(f"/api/projects/{pid}/ai-edit", json={"provider": "local", "prompt": "remove",
                                                          "box": {"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}})
    assert r.status_code == 200
    from darkroom_web.api import create_app
    settings.openrouter_api_key = ""
    c2 = TestClient(create_app(settings))
    r = c2.post(f"/api/projects/{pid}/ai-edit", json={"provider": "openrouter", "prompt": "x"})
    assert r.status_code == 400 and "not configured" in r.json()["detail"]


def test_style_match_lut_and_social_export(client, png_path, tmp_path):
    pid = upload(client, png_path)["project"]["id"]
    ref = tmp_path / "ref.png"
    Image.fromarray(np.full((200, 300, 3), (200, 120, 60), np.uint8)).save(ref)
    with open(ref, "rb") as f:
        r = client.post(f"/api/projects/{pid}/style-match", files={"file": ("ref.png", f, "image/png")},
                        data={"strength": "80"})
    assert r.status_code == 200, r.text
    assert r.json()["stack"]["layers"][-1]["type"] == "style_match"
    cube = tmp_path / "warm.cube"
    n = 5
    lines = ["TITLE \"warm\"", f"LUT_3D_SIZE {n}"]
    for b in range(n):
        for g in range(n):
            for r_ in range(n):
                lines.append(f"{min(1, r_ / (n - 1) * 1.1):.4f} {g / (n - 1):.4f} {b / (n - 1) * 0.9:.4f}")
    cube.write_text("\n".join(lines))
    with open(cube, "rb") as f:
        lut = client.post("/api/luts", files={"file": ("warm.cube", f, "text/plain")}).json()
    assert "warm" in lut["luts"]
    stack = client.get(f"/api/projects/{pid}").json()["stack"]
    stack["layers"].append({"type": "lut", "params": {"name": "warm", "strength": 50}})
    assert client.put(f"/api/projects/{pid}/stack", json={"stack": stack}).status_code == 200
    ex = client.post(f"/api/projects/{pid}/export", json={"format": "jpeg", "social": "ig_portrait"}).json()
    assert (ex["width"], ex["height"]) == (1080, 1350) and not ex["full_resolution"]
    im = Image.open(io.BytesIO(client.get(ex["download"]).content))
    assert im.size == (1080, 1350)
    ex = client.post(f"/api/projects/{pid}/export", json={"format": "tiff16"}).json()
    assert (ex["width"], ex["height"], ex["bits"]) == (1200, 800, 16)


def test_project_reopens_after_restart(client, settings, png_path):
    pid = upload(client, png_path)["project"]["id"]
    stack = {"layers": [{"type": "adjust", "params": {"basic": {"exposure": 0.5}}}]}
    client.put(f"/api/projects/{pid}/stack", json={"stack": stack})
    from darkroom_web.api import create_app
    c2 = TestClient(create_app(settings))
    st = c2.get(f"/api/projects/{pid}").json()
    assert st["project"]["name"] == "scene.png"
    assert st["stack"]["layers"][0]["params"]["basic"]["exposure"] == 0.5


@pytest.mark.camera
@pytest.mark.skipif(not os.path.exists(os.path.join(SAMPLES, "D5300_14bit.NEF")), reason="camera samples not fetched")
def test_nikon_nef_full_resolution_tiff(client, tmp_path):
    src = tmp_path / "D5300_14bit.NEF"
    shutil.copy(os.path.join(SAMPLES, "D5300_14bit.NEF"), src)
    st = upload(client, str(src))
    assert (st["project"]["width"], st["project"]["height"]) == (6016, 4016) and st["project"]["kind"] == "raw"
    pid = st["project"]["id"]
    r = client.post(f"/api/projects/{pid}/ai-edit", json={"provider": "openrouter", "prompt": "remove the object",
                                                          "box": {"x": 0.45, "y": 0.45, "w": 0.1, "h": 0.1}})
    assert r.status_code == 200
    stack = r.json()["stack"]
    stack["layers"].append({"type": "look", "params": {"preset": "IG / Portra Glow", "amount": 80}})
    client.put(f"/api/projects/{pid}/stack", json={"stack": stack})
    ex = client.post(f"/api/projects/{pid}/export", json={"format": "tiff16"}).json()
    assert (ex["width"], ex["height"], ex["bits"]) == (6016, 4016, 16) and ex["full_resolution"]


def test_sample_opens(client):
    h = client.get("/api/health").json()
    assert "astronaut.png" in h["samples"]
    r = client.post("/api/samples/astronaut.png")
    assert r.status_code == 200 and r.json()["project"]["width"] == 512
    assert client.post("/api/samples/nope.png").status_code == 404
    assert client.post("/api/samples/..%2Fsecret.png").status_code in (404, 405)
