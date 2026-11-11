"""PHASE 3 (synthetic + mocked network): prompt -> recipe, LLM safety, reference matching."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from helpers import make_pattern
from darkroom_studio import colorspace as cms
from darkroom_studio import engine, recipe as R
from darkroom_studio.prompt import OpenRouterInterpreter, RuleInterpreter, extract_json
from darkroom_studio.session import Studio

RULES = RuleInterpreter()


@pytest.fixture(scope="module", autouse=True)
def registries(tmp_path_factory):
    Studio(str(tmp_path_factory.mktemp("w")))      # fills film/LUT registries


def interp(text, base=None):
    r, notes, warns = RULES.interpret(text, base or R.default_recipe())
    return r, notes


# ---------------------------------------------------------------- 11: prompt -> recipe
def test_example_prompt_1970s_warm_muted_soft():
    r, notes = interp("Make this photograph look like a warm 1970s analog film photograph with muted greens "
                      "and soft highlights.")
    assert r["film"]["stock"] == "Studio / 1970s warm print"
    assert r["basic"]["temperature"] > 15
    assert r["hsl"]["green_saturation"] < -30
    assert r["basic"]["highlights"] < -30
    assert r["effects"]["grain_amount"] > 0
    assert r["effects"]["vignette_amount"] < 0
    assert any("muted greens" in n for n in notes)


@pytest.mark.parametrize("text,check", [
    ("Give this image a Kodak Portra-inspired appearance.",
     lambda r: r["film"]["stock"] == "Studio / Portra 400-inspired"),
    ("Make it look like a 1970s independent film.",
     lambda r: r["film"]["stock"] == "Studio / 1970s warm print" and r["effects"]["fade"] > 0),
    ("Create a warm, nostalgic 2000s music-video aesthetic.",
     lambda r: r["basic"]["temperature"] > 10 and r["effects"]["halation_amount"] > 0),
    ("Make the photograph darker, earthy and cinematic.",
     lambda r: r["basic"]["exposure"] < 0 and r["hsl"]["green_saturation"] < 0
     and r["film"]["stock"] == "Studio / Cinematic teal & orange"),
    ("Give it a natural documentary photography look.",
     lambda r: r["film"]["stock"] == "Studio / Documentary natural" and abs(r["basic"]["contrast"]) < 15),
    ("Create a moody, atmospheric, desaturated appearance.",
     lambda r: r["basic"]["saturation"] < -20 and r["basic"]["exposure"] < 0),
])
def test_user_example_prompts(text, check):
    r, _ = interp(text)
    assert check(r), R.diff(r)
    assert R.validate(r)[1] == []                        # already a clean, valid recipe


def test_prompt_modifiers_intensity_and_negation():
    a, _ = interp("warm")
    b, _ = interp("very warm")
    c, _ = interp("slightly warm")
    assert c["basic"]["temperature"] < a["basic"]["temperature"] < b["basic"]["temperature"]
    d, _ = interp("vintage film look with no grain and no vignette")
    assert d["effects"]["grain_amount"] == 0 and d["effects"]["vignette_amount"] == 0


def test_prompt_is_relative_to_current_and_clamped():
    base = R.default_recipe()
    base["basic"]["exposure"] = 4.8
    r, _ = interp("much brighter", base)
    assert r["basic"]["exposure"] == 5.0                 # clamped to the schema limit
    r2, _ = interp("teal shadows and golden highlights")
    assert r2["color_grading"]["shadows_hue"] == 190 and r2["color_grading"]["highlights_saturation"] > 0


def test_unknown_prompt_changes_nothing():
    r, notes = interp("asdf qwerty")
    assert R.is_identity(r) and "No recognised" in notes[0]


def test_prompt_never_touches_geometry():
    for text in ["cinematic 1970s film noir with heavy grain", "dreamy pastel summer", "Ektar landscape vivid"]:
        r, _ = interp(text)
        assert r["lens"]["distortion"] == 0 and r["lens"]["ca_red"] == 0


# ---------------------------------------------------------------- schema safety
def test_validate_rejects_garbage_and_code():
    evil = {"basic": {"exposure": "__import__('os').system('rm -rf /')", "contrast": 1e9, "bogus": 3},
            "exec": "print(1)", "film": {"stock": "Kodak Portra; rm -rf"},
            "tone_curve": {"master": [[0, 0], ["x", 1], [1, 1]] * 20, "evil": 1},
            "match": {"data": {"method": "lambda: 1"}}}
    r, warns = R.validate(evil)
    assert r["basic"]["exposure"] == 0 and r["basic"]["contrast"] == 100
    assert "exec" not in r and r["film"]["stock"] == "None"
    assert len(r["tone_curve"]["master"]) <= 16 and r["match"]["data"] is None
    assert len(warns) >= 6


def test_json_schema_has_limits():
    sch = R.json_schema()
    assert sch["properties"]["basic"]["properties"]["exposure"]["maximum"] == 5
    assert "Studio / Portra 400-inspired" in sch["properties"]["film"]["properties"]["stock"]["enum"]


def test_extract_json_tolerates_fences():
    assert extract_json('Sure!\n```json\n{"basic": {"contrast": 10}}\n```') == {"basic": {"contrast": 10}}


# ---------------------------------------------------------------- OpenRouter client (mock server)
class _Handler(BaseHTTPRequestHandler):
    reply = {}
    seen = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Handler.seen.append((self.path, dict(self.headers), body))
        out = json.dumps({"choices": [{"message": {"content": _Handler.reply}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def mock_llm():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _Handler.seen.clear()
    yield f"http://127.0.0.1:{srv.server_port}/api/v1"
    srv.shutdown()


def test_llm_reply_is_validated_not_executed(mock_llm, tmp_path):
    marker = tmp_path / "pwned"
    _Handler.reply = ("Here you go: ```json\n" + json.dumps({
        "notes": "warm 70s", "basic": {"temperature": 30, "exposure": 99, "tint": "__import__('os')"},
        "film": {"stock": "Studio / 1970s warm print"}, "hsl": {"green_saturation": -40},
        "effects": {"code": f"open('{marker}','w')"}, "unknown_section": {"x": 1}}) + "\n```")
    llm = OpenRouterInterpreter(api_key="test-key", base_url=mock_llm, model="test/model")
    r, notes, warns = llm.interpret("warm 1970s film with muted greens", R.default_recipe())
    assert r["basic"]["temperature"] == 30 and r["basic"]["exposure"] == 5 and r["basic"]["tint"] == 0
    assert r["film"]["stock"] == "Studio / 1970s warm print" and r["hsl"]["green_saturation"] == -40
    assert not marker.exists()
    assert any("unknown" in w for w in warns)
    path, headers, body = _Handler.seen[0]
    assert path.endswith("/chat/completions") and headers["Authorization"] == "Bearer test-key"
    req = json.loads(body)
    blob = json.dumps(req)
    assert "data:image" not in blob and "base64" not in blob and len(blob) < 60000   # text only, no photo
    assert req["messages"][1]["content"].endswith("warm 1970s film with muted greens")


def test_llm_without_key_raises_and_app_falls_back(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    llm = OpenRouterInterpreter(api_key=None)
    llm.api_key = None
    with pytest.raises(RuntimeError):
        llm.interpret("warm")


# ---------------------------------------------------------------- reference matching
def _write(path, arr):
    from PIL import Image
    Image.fromarray((np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)).save(path, quality=95)


def test_reference_match_transfers_colour_not_content(tmp_path):
    from darkroom_studio.match import compute_match, describe, load_reference
    st = Studio(str(tmp_path / "w"), preview_long_edge=256)
    src = make_pattern(300, 450, seed=1)
    _write(tmp_path / "src.jpg", src)
    # reference: different content (stripes) with a strong warm, dark grade
    y, x = np.mgrid[0:200, 0:300]
    ref = np.stack([0.55 + 0.3 * np.sin(x / 7.0), 0.35 + 0.2 * np.sin(x / 7.0), 0.2 + 0.1 * np.cos(y / 5.0)], -1)
    _write(tmp_path / "ref.jpg", ref)
    s = st.open(str(tmp_path / "src.jpg"))
    rr, small = load_reference(str(tmp_path / "ref.jpg"), str(tmp_path / "w"))
    data = compute_match(s, small, rr.space, "Colour + tone", "ref.jpg")
    r = s.recipe
    r["match"]["data"], r["match"]["strength"] = data, 100
    s.commit(r)
    assert "ref.jpg" in describe(data)
    out, _ = s.render_full()
    assert out.shape == s.source.linear.shape                       # dimensions unchanged
    ok = engine.OkLab(cms.SRGB)
    lab_o, lab_s = ok.from_linear(out), ok.from_linear(s.source.linear)
    lab_r = ok.from_linear(small)
    # colour statistics move toward the reference (warm: +a, +b)
    for c in (1, 2):
        assert abs(lab_o[..., c].mean() - lab_r[..., c].mean()) < 0.5 * abs(lab_s[..., c].mean() - lab_r[..., c].mean())
    # content is the source's: lightness structure stays highly correlated with the source
    corr = np.corrcoef(lab_o[..., 0].ravel(), lab_s[..., 0].ravel())[0, 1]
    assert corr > 0.95
    # strength scales the effect
    r["match"]["strength"] = 30
    half = engine.render(s.source.linear, r, s.ctx, level=1.0)
    d_half = np.abs(ok.from_linear(half)[..., 2].mean() - lab_s[..., 2].mean())
    d_full = np.abs(lab_o[..., 2].mean() - lab_s[..., 2].mean())
    assert 0.15 * d_full < d_half < 0.5 * d_full


def test_match_data_roundtrips_through_recipe_json(tmp_path):
    from darkroom_studio.match import compute_match, load_reference
    st = Studio(str(tmp_path / "w"), preview_long_edge=256)
    _write(tmp_path / "a.jpg", make_pattern(100, 150))
    _write(tmp_path / "b.jpg", make_pattern(100, 150, seed=3)[..., ::-1])
    s = st.open(str(tmp_path / "a.jpg"))
    ref, small = load_reference(str(tmp_path / "b.jpg"))
    r = s.recipe
    r["match"]["data"] = compute_match(s, small, ref.space)
    r["match"]["strength"] = 70
    again, warns = R.validate(json.loads(json.dumps(r)))
    assert again["match"]["data"]["method"] == "lab_stats+tone" and not warns
