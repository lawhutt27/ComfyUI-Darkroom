"""PHASE 2 (synthetic + bundled third-party data): film emulation and LUTs."""

import glob
import os

import numpy as np
import pytest

from helpers import make_pattern
from darkroom_studio import colorspace as cms
from darkroom_studio import engine, recipe as R
from darkroom_studio.film import FilmRegistry, REPO_ROOT
from darkroom_studio.lut import CubeLUT, LUTRegistry, identity_lattice, parse_cube, write_cube

FILMS = FilmRegistry()
PRIORITY = ["Portra", "Gold", "Ektar", "Fuji", "Vision3", "Cinestill", "Ilford", "Vintage faded", "Documentary",
            "Cinematic"]


def test_priority_film_looks_present():
    names = " ".join(FILMS.looks)
    for k in PRIORITY:
        assert k in names, k


def test_darkroom_library_loaded_and_screened():
    assert len(FILMS.darkroom) > 50
    # stocks whose parametric fallback crushes shadows are excluded, not shown
    assert all(n.startswith("Darkroom / ") for n in FILMS.darkroom)


@pytest.mark.parametrize("name", list(FILMS.looks) + list(FILMS.darkroom)[:20])
def test_film_stock_behaviour(name):
    E = make_pattern(120, 180)
    out = FILMS.apply(name, E, 1.0)
    assert out.shape == E.shape and np.isfinite(out).all()
    x = np.linspace(0, 1, 64, dtype=np.float32)
    ramp = FILMS.apply(name, np.stack([x, x, x], -1)[None], 1.0)[0].mean(-1)
    assert np.all(np.diff(ramp) >= -2e-3), "tone reversal"
    half = FILMS.apply(name, E, 0.5)
    assert np.allclose(half, E + (out - E) * 0.5, atol=1e-5)


def test_bw_looks_are_monochrome():
    E = make_pattern(60, 90)
    for name in FILMS.looks:
        if "(B&W)" in name:
            out = FILMS.apply(name, E, 1.0)
            assert np.abs(out[..., 0] - out[..., 1]).max() < 1e-5


def test_cube_roundtrip_and_identity(tmp_path):
    lat = identity_lattice(17)
    p = write_cube(str(tmp_path / "id.cube"), lat)
    lut = parse_cube(p)
    assert lut.size == 17 and lut.kind == "3d"
    E = make_pattern(80, 100)
    assert np.abs(lut.apply(E) - E).max() < 2e-6


def test_cube_axis_order(tmp_path):
    """R must vary fastest in the file: a LUT that swaps R and B must swap them."""
    lat = identity_lattice(9)[..., ::-1].copy()
    lut = parse_cube(write_cube(str(tmp_path / "swap.cube"), lat))
    px = np.array([[[0.9, 0.5, 0.1]]], np.float32)
    assert np.allclose(lut.apply(px), [[[0.1, 0.5, 0.9]]], atol=1e-5)


def test_1d_cube(tmp_path):
    p = tmp_path / "gamma.cube"
    n = 64
    x = np.linspace(0, 1, n)
    with open(p, "w") as f:
        f.write(f"LUT_1D_SIZE {n}\n")
        for v in x:
            f.write(f"{v ** 2:.6f} {v ** 2:.6f} {v ** 2:.6f}\n")
    lut = parse_cube(str(p))
    assert lut.kind == "1d"
    assert abs(float(lut.apply(np.full((1, 1, 3), 0.5, np.float32))[0, 0, 0]) - 0.25) < 2e-3


def test_malformed_cube_rejected(tmp_path):
    p = tmp_path / "bad.cube"
    p.write_text("LUT_3D_SIZE 3\n0 0 0\n1 1 1\n")
    with pytest.raises(ValueError):
        parse_cube(str(p))


def test_real_third_party_cubes_from_darkroom_repo():
    """Spectral film LUTs shipped in ComfyUI-Darkroom (spectral_film_lut, MIT)."""
    files = sorted(glob.glob(os.path.join(REPO_ROOT, "data", "spectral_luts", "*.cube")))
    if not files:
        pytest.skip("ComfyUI-Darkroom data/spectral_luts not present")
    for f in files[:3]:
        lut = parse_cube(f)
        out = lut.apply(make_pattern(50, 70))
        assert out.shape == (50, 70, 3) and np.isfinite(out).all()


def test_lut_registry_import_strength_and_engine(tmp_path):
    lat = identity_lattice(17)
    warm = lat.copy()
    warm[..., 0] = np.clip(warm[..., 0] * 1.1, 0, 1)
    src = write_cube(str(tmp_path / "upload" / "warm.cube"), warm)
    reg = LUTRegistry(str(tmp_path / "luts"))
    lut = reg.import_file(src)
    assert "warm" in reg.names() and os.path.exists(lut.path)
    E = make_pattern(50, 60)
    full = reg.apply("warm", E, 1.0)
    half = reg.apply("warm", E, 0.5)
    assert np.allclose(half, E + (full - E) * 0.5, atol=1e-6)
    R.REGISTRIES["lut.name"] = reg.names()
    r = R.default_recipe()
    r["lut"]["name"] = "warm"
    ctx = engine.Context(cms.SRGB, (60, 50), luts=reg)
    out = engine.render(cms.srgb_decode(E), r, ctx)
    assert out.shape == (50, 60, 3)
    assert cms.srgb_encode(out)[..., 0].mean() > E[..., 0].mean()


def test_lut_export_matches_pointwise_render(studio, synth):
    s = studio.open(synth["tiff16"])
    r = s.recipe
    r["basic"].update(contrast=30, temperature=20, saturation=-20)
    r["film"]["stock"] = "Studio / Portra 400-inspired"
    r["effects"]["grain_amount"] = 30        # spatial: must be reported and omitted
    s.commit(r)
    path, spatial = s.export_lut("test_grade", size=33)
    assert spatial == ["effects.grain_amount"]
    lut = parse_cube(path)
    r2 = s.recipe
    r2["effects"]["grain_amount"] = 0
    direct = cms.srgb_encode(engine.render(s.source.linear, r2, s.ctx, level=1.0))
    via_lut = lut.apply(cms.srgb_encode(s.source.linear))
    err = np.abs(np.clip(direct, 0, 1) - via_lut)
    assert np.percentile(err, 99) < 0.01 and err.mean() < 0.003
