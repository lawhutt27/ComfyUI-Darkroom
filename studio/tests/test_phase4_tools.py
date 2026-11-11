"""PHASE 4 (synthetic): presets, scopes, batch, saved recipes and the Gradio app wiring."""

import json
import os
import zipfile

import numpy as np
import pytest
import tifffile

from darkroom_studio import recipe as R
from darkroom_studio import scopes
from darkroom_studio.imaging import sha256_file


def test_builtin_presets_are_valid_and_non_geometric(studio):
    names = [n for n in studio.list_presets() if n.startswith("Built-in / ")]
    assert len(names) >= 5
    for n in names:
        r, warns = studio.load_preset(n)
        assert not warns, (n, warns)
        assert not R.is_identity(r)
        assert r["lens"]["distortion"] == 0 and r["detail"]["sharpen_amount"] == 0


def test_user_preset_roundtrip_drops_match_data(studio):
    r = R.default_recipe()
    r["basic"]["contrast"] = 22
    r["match"]["data"] = {"method": "lab_stats", "src_mean": [0, 0, 0], "src_std": [1, 1, 1],
                          "ref_mean": [0, 0, 0], "ref_std": [1, 1, 1], "name": "x"}
    r["match"]["strength"] = 50
    studio.save_preset("My Look!", r)
    name = [n for n in studio.list_presets() if n.startswith("User / ")][0]
    back, _ = studio.load_preset(name)
    assert back["basic"]["contrast"] == 22 and back["match"]["data"] is None


def test_saved_recipe_roundtrip(studio):
    r = R.default_recipe()
    r["hsl"]["green_saturation"] = -40
    r["film"]["stock"] = "Studio / Ektar 100-inspired"
    path = studio.save_recipe("landscape", r)
    again, warns = studio.load_recipe_file(path)
    assert not warns and again["hsl"]["green_saturation"] == -40 and again["film"]["stock"] == r["film"]["stock"]


def test_scopes_shapes_and_clip_flags():
    rng = np.random.default_rng(0)
    disp = (rng.random((300, 400, 3)) * 255).astype(np.uint8)
    h = scopes.histogram(disp)
    w = scopes.waveform(disp)
    v = scopes.vectorscope(disp)
    assert h.shape == (150, 512, 3) and w.shape == (150, 512, 3) and v.shape == (220, 220, 3)
    blown = np.full((100, 100, 3), 255, np.uint8)
    hb = scopes.histogram(blown)
    assert tuple(hb[8, 512 - 8]) == (255, 80, 80)          # highlight-clip indicator lit
    svg = scopes.curve_svg({"master": [[0, 0], [0.5, 0.6], [1, 1]]})
    assert svg.startswith("<svg") and "polyline" in svg
    assert "svg" in scopes.wheels_svg(R.default_recipe()["color_grading"])


def test_batch_full_resolution_and_originals_untouched(studio, synth):
    files = [synth["tiff16"], synth["png16"], synth["jpeg"]]
    digests = [sha256_file(f) for f in files]
    r = R.default_recipe()
    r["basic"].update(contrast=20, temperature=15)
    r["film"]["stock"] = "Studio / Portra 400-inspired"
    out_zip, lines = studio.batch(files + ["/nonexistent/photo.tif"], r)
    assert [ok for ok, _ in lines] == [True, True, True, False]
    assert [sha256_file(f) for f in files] == digests
    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()
        assert sum(n.endswith(".tif") for n in names) == 3 and sum(n.endswith(".json") for n in names) == 3
        for f in files:
            src = studio.open(f).source
            tif = [n for n in names if n.startswith(os.path.splitext(os.path.basename(f))[0]) and n.endswith(".tif")][0]
            zf.extract(tif, studio.dirs["batch"])
            arr = tifffile.imread(os.path.join(studio.dirs["batch"], tif))
            assert arr.shape[:2] == (src.height, src.width) and arr.dtype == np.uint16
        side = json.loads(zf.read([n for n in names if n.endswith(".json")][0]))
        assert side["recipe"]["film"]["stock"] == "Studio / Portra 400-inspired"


def test_gradio_app_builds(tmp_path):
    gr = pytest.importorskip("gradio")
    from darkroom_studio.ui.app import build_app, css_text
    demo, studio = build_app(str(tmp_path / "w"), preview_long_edge=512)
    assert isinstance(demo, gr.Blocks)
    labels = {getattr(b, "label", None) for b in demo.blocks.values()}
    for needed in ("Upload photograph", "Exposure", "Import .cube", "Reference photograph"):
        assert any(needed == l or (l and needed in l) for l in labels), needed
    assert "#dr-status" in css_text()
