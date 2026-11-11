"""PHASE 1 (synthetic): import, preview, grading, before/after, lossless export, integrity."""

import json
import os
import shutil

import numpy as np
import pytest
import tifffile
from PIL import Image

from darkroom_studio import colorspace as cms
from darkroom_studio import engine, recipe as R
from darkroom_studio.imaging import read_bytes_icc_from_image_file, sha256_file
from darkroom_studio.metadata import exiftool_available

TIFF = "TIFF 16-bit (master, lossless)"
PNG16 = "PNG 16-bit (lossless)"
PNG8 = "PNG 8-bit (lossless)"
JPEG = "JPEG 8-bit (high quality)"
SAME = "Same as source/working space"


def _reopen(path):
    if path.endswith(".tif"):
        return tifffile.imread(path)
    if path.endswith(".png"):
        import cv2
        a = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        return a[..., ::-1] if a.shape[-1] == 3 else a[..., [2, 1, 0, 3]]
    return np.asarray(Image.open(path))


# ---------------------------------------------------------------- 3,4,5: import
@pytest.mark.parametrize("kind,bits,fmt", [("jpeg", 8, "JPEG"), ("tiff16", 16, "TIFF"), ("tiff8", 8, "TIFF"),
                                           ("png16", 16, "PNG"), ("png8", 8, "PNG")])
def test_import_formats(studio, synth, kind, bits, fmt):
    s = studio.open(synth[kind])
    assert (s.source.width, s.source.height) == (600, 400)
    assert s.source.bit_depth == bits and s.source.file_format == fmt
    assert s.source.linear.dtype == np.float32
    if kind in ("tiff16", "png16"):   # 16-bit precision really survives import (not reduced to 8 bit)
        enc = cms.srgb_encode(s.source.linear)
        assert np.abs(enc * 65535 - synth["u16"]).max() < 1.0


def test_16bit_png_not_truncated_to_8bit(studio, synth):
    s = studio.open(synth["png16"])
    levels = np.unique(np.round(cms.srgb_encode(s.source.linear[..., 0]) * 65535))
    assert levels.size > 256 * 4


# ---------------------------------------------------------------- 16: original unchanged
def test_original_file_never_modified(studio, synth, tmp_path):
    src = synth["tiff16"]
    before = sha256_file(src)
    mtime = os.path.getmtime(src)
    s = studio.open(src)
    r = s.recipe
    r["basic"]["exposure"] = 1.0
    r["effects"]["grain_amount"] = 40
    s.commit(r)
    s.export(TIFF, SAME)
    s.export(JPEG, "sRGB", 95)
    assert sha256_file(src) == before and os.path.getmtime(src) == mtime
    # protected library copy is byte-identical and read-only
    assert sha256_file(s.source.path) == before
    assert not os.access(s.source.path, os.W_OK) or os.geteuid() == 0
    assert oct(os.stat(s.source.path).st_mode & 0o222) == "0o0"


# ---------------------------------------------------------------- identity / lossless
def test_identity_recipe_is_bit_exact_in_engine(synth):
    img = cms.srgb_decode(synth["array"])
    ctx = engine.Context(cms.SRGB, (600, 400))
    assert np.array_equal(engine.render(img, R.default_recipe(), ctx), img)


@pytest.mark.parametrize("kind,fmt,tol", [("tiff16", TIFF, 1), ("png16", PNG16, 1), ("png8", PNG8, 0),
                                          ("tiff8", PNG8, 0)])
def test_identity_export_roundtrip_lossless(studio, synth, kind, fmt, tol):
    s = studio.open(synth[kind])
    res = s.export(fmt, SAME)
    out = _reopen(res["path"])
    ref = synth["u16"] if kind.endswith("16") else (synth["array"] * 255 + 0.5).astype(np.uint8)
    if fmt == PNG8 and kind.endswith("16"):
        ref = (ref / 257.0 + 0.5).astype(np.uint8)
    assert out.shape == ref.shape and out.dtype == ref.dtype
    assert np.abs(out.astype(np.int64) - ref.astype(np.int64)).max() <= tol


# ---------------------------------------------------------------- 6, 7, 18: full-res + 16-bit TIFF
def test_full_resolution_16bit_tiff_export_and_reopen(studio, synth):
    s = studio.open(synth["jpeg"])
    r = s.recipe
    r["basic"].update(exposure=0.4, contrast=20, temperature=15, vibrance=20)
    r["film"]["stock"] = "Studio / Portra 400-inspired"
    s.commit(r)
    res = s.export(TIFF, SAME)
    with tifffile.TiffFile(res["path"]) as tf:
        page = tf.pages[0]
        assert page.shape == (400, 600, 3)
        assert page.dtype == np.uint16 and page.bitspersample == 16
        assert page.compression.name in ("ADOBE_DEFLATE", "DEFLATE")   # lossless
    side = json.load(open(res["sidecar"]))
    assert side["output"]["dimensions"] == [600, 400]
    assert side["recipe"]["film"]["stock"] == "Studio / Portra 400-inspired"


def test_export_never_uses_preview_proxy(studio, synth):
    """The proxy is 512 px here; the export must still be 600x400 and must be
    rendered from the float source (finer than the 8-bit preview)."""
    s = studio.open(synth["tiff16"])
    assert s.proxy.shape[1] == 512 and s.proxy_scale < 1
    r = s.recipe
    r["basic"]["exposure"] = 0.25
    s.commit(r)
    _, after_disp, _ = s.render_preview()
    assert after_disp.shape[:2] == (341, 512)
    res = s.export(TIFF, SAME)
    out = tifffile.imread(res["path"])
    assert out.shape == (400, 600, 3)
    assert np.unique(out[..., 1]).size > 1000           # 16-bit gradation, not 8-bit preview data


# ---------------------------------------------------------------- 14, 15, 17: no resize, no crop
CONTROL_EXTREMES = [(p.path, v) for p in R.NUMERIC if p.path != "effects.grain_seed" for v in (p.lo, p.hi)]


@pytest.mark.parametrize("path,value", CONTROL_EXTREMES)
def test_every_control_preserves_dimensions(synth, path, value):
    img = cms.srgb_decode(synth["array"])
    ctx = engine.Context(cms.SRGB, (600, 400))
    r = R.default_recipe()
    R.set_(r, path, value)
    if path == "match.strength":
        r["match"]["data"] = {"method": "lab_stats", "src_mean": [0.6, 0, 0], "src_std": [0.2, 0.05, 0.05],
                              "ref_mean": [0.5, 0.02, 0.03], "ref_std": [0.2, 0.06, 0.06]}
    out = engine.render(img, R.validate(r)[0], ctx)
    assert out.shape == img.shape and out.dtype == np.float32
    assert np.isfinite(out).all()


def test_colour_controls_do_not_crop_or_shift(synth):
    """With every non-geometric control active, the 1-px magenta marker border
    must still sit exactly on the outermost rows/columns."""
    img = cms.srgb_decode(synth["array"])
    ctx = engine.Context(cms.SRGB, (600, 400))
    r = R.default_recipe()
    r["basic"].update(exposure=0.5, contrast=40, highlights=-50, shadows=40, temperature=30, saturation=20)
    r["hsl"]["green_saturation"] = -50
    r["film"]["stock"] = "None"
    r["effects"]["fade"] = 20
    out = cms.srgb_encode(np.clip(engine.render(img, r, ctx), 0, 1))
    for edge in (out[0], out[-1], out[:, 0], out[:, -1]):
        assert np.all(edge[:, 0] > edge[:, 1] + 0.3) and np.all(edge[:, 2] > edge[:, 1] + 0.3)  # still magenta
    inner = out[1:-1, 1:-1]
    assert not np.all(inner[0, :, 0] > inner[0, :, 1] + 0.3)       # marker did not move inward


@pytest.mark.parametrize("fmt", [TIFF, PNG16, PNG8, JPEG])
def test_export_dimensions_all_formats(studio, synth, fmt):
    s = studio.open(synth["jpeg"])
    r = s.recipe
    r["effects"].update(clarity=30, vignette_amount=-40, grain_amount=30)
    r["detail"]["sharpen_amount"] = 50
    s.commit(r)
    res = s.export(fmt, "sRGB", 98)
    out = _reopen(res["path"])
    assert out.shape[:2] == (400, 600)
    if fmt == JPEG:
        assert any("lossy" in w for w in res["warnings"])
    if fmt in (PNG8, JPEG):
        assert any("8 bits" in w or "8-bit" in w for w in res["warnings"])


def test_orientation_applied_and_reset(studio, synth):
    s = studio.open(synth["jpeg_rot6"])
    assert (s.source.width, s.source.height) == (400, 600)        # displayed upright
    assert s.source.file_dims == (600, 400)
    res = s.export(TIFF, SAME)
    out = tifffile.imread(res["path"])
    assert out.shape[:2] == (600, 400)
    if exiftool_available():
        import subprocess
        o = subprocess.run(["exiftool", "-Orientation#", "-s3", res["path"]], capture_output=True, text=True)
        assert o.stdout.strip() in ("1", "")


def test_alpha_channel_preserved(studio, synth):
    s = studio.open(synth["png_alpha"])
    assert s.source.alpha is not None
    res = s.export(PNG16, SAME)
    out = _reopen(res["path"])
    assert out.shape == (400, 600, 4)
    assert out[..., 3][:, :300].max() == 0 and out[..., 3][:, 300:].min() == 65535


# ---------------------------------------------------------------- 8, 9: EXIF + ICC
@pytest.mark.parametrize("fmt", [TIFF, PNG16, PNG8, JPEG])
def test_exif_retained(studio, synth, fmt):
    s = studio.open(synth["jpeg"])
    res = s.export(fmt, SAME, 97, True)
    if exiftool_available():
        import subprocess
        o = subprocess.run(["exiftool", "-j", "-Model", "-ISO", "-FNumber", "-ExposureTime", "-LensModel",
                            "-DateTimeOriginal", res["path"]], capture_output=True, text=True)
        m = json.loads(o.stdout)[0]
        assert m["Model"] == "NIKON D5300" and m["ISO"] == 400 and float(m["FNumber"]) == 5.6
        assert m["DateTimeOriginal"] == "2024:05:01 10:20:30"
    else:
        from darkroom_studio.metadata import read_metadata
        info, _ = read_metadata(res["path"])
        assert info["camera_model"].strip() == "NIKON D5300"


def test_exif_fallback_without_exiftool(studio, synth, monkeypatch):
    """Simulate a runtime without ExifTool: Pillow fallback must still write EXIF to JPEG/PNG."""
    import darkroom_studio.export as ex
    monkeypatch.setattr(ex, "exiftool_available", lambda: False)
    s = studio.open(synth["jpeg"])
    for fmt in (JPEG, PNG8):
        res = s.export(fmt, SAME, 97, True)
        exif = Image.open(res["path"]).getexif()
        assert exif.get(0x0110) == "NIKON D5300" and exif.get(0x0112) == 1
        assert exif.get_ifd(0x8769).get(0x8827) == 400


@pytest.mark.parametrize("fmt", [TIFF, PNG16, PNG8, JPEG])
def test_icc_profile_retained_same_space(studio, synth, fmt):
    s = studio.open(synth["jpeg_adobe"])
    assert "Adobe RGB" in s.source.space.name
    res = s.export(fmt, SAME)
    assert read_bytes_icc_from_image_file(res["path"]) == synth["icc_adobe"]   # byte-identical


def test_explicit_srgb_conversion(studio, synth):
    s = studio.open(synth["jpeg_adobe"])
    res = s.export(TIFF, "sRGB")
    icc = read_bytes_icc_from_image_file(res["path"])
    cs = cms.parse_icc(icc)
    assert cs.is_srgb_like()
    # colours were converted: AdobeRGB green patch decoded as sRGB must be more saturated
    out = tifffile.imread(res["path"]).astype(np.float32) / 65535
    src = synth["array"]
    g_src, g_out = src[80, 180], out[80, 180]
    assert g_out[1] - g_out[0] > g_src[1] - g_src[0]


def test_untagged_image_assumed_srgb_and_reported(studio, synth):
    s = studio.open(synth["jpeg_plain"])
    assert s.source.space.is_srgb_like()
    assert any("assuming sRGB" in n for n in s.source.notes)


# ---------------------------------------------------------------- 10: before/after
def test_before_after_comparison(studio, synth):
    s = studio.open(synth["jpeg"])
    b0, a0, _ = s.render_preview()
    assert np.array_equal(b0, a0)                    # identity: after == before
    r = s.recipe
    r["basic"]["exposure"] = 1.0
    s.commit(r)
    b1, a1, _ = s.render_preview()
    assert np.array_equal(b1, b0)                    # "before" never changes
    assert a1.shape == b1.shape and a1.mean() > b1.mean() + 10
    # 1:1 crop comparison renders full-resolution pixels
    before, after, _, region = s.render_crop(s.recipe, "100%", 0.5, 0.5)
    assert region[2:] == (600, 400) and before.shape == after.shape


# ---------------------------------------------------------------- 12, 13: undo/redo/reset
def test_undo_redo_reset(studio, synth):
    s = studio.open(synth["jpeg"])
    r = s.recipe
    r["basic"]["exposure"] = 0.5
    s.commit(r)
    r = s.recipe
    r["basic"]["contrast"] = 30
    s.commit(r)
    assert s.recipe["basic"]["contrast"] == 30
    s.history.undo()
    assert s.recipe["basic"]["contrast"] == 0 and s.recipe["basic"]["exposure"] == 0.5
    s.history.undo()
    assert R.is_identity(s.recipe)
    s.history.redo()
    s.history.redo()
    assert s.recipe["basic"]["contrast"] == 30
    s.history.reset()
    assert R.is_identity(s.recipe)
    _, a, _ = s.render_preview()
    assert np.array_equal(a, s.before_display)       # reset really returns to the original
    s.history.undo()                                 # reset is undoable
    assert s.recipe["basic"]["contrast"] == 30


def test_controls_independent_and_reversible(synth):
    """Setting a control and setting it back yields the exact original render."""
    img = cms.srgb_decode(synth["array"])
    ctx = engine.Context(cms.SRGB, (600, 400))
    base = engine.render(img, R.default_recipe(), ctx)
    for p in R.NUMERIC:
        r = R.default_recipe()
        R.set_(r, p.path, p.hi)
        R.set_(r, p.path, p.default)
        assert np.array_equal(engine.render(img, r, ctx), base), p.path


# ---------------------------------------------------------------- consistency
def test_crop_and_strip_renders_match_full(synth):
    img = cms.srgb_decode(synth["array"])
    ctx = engine.Context(cms.SRGB, (600, 400), analysis=img[::2, ::2])
    r = R.default_recipe()
    r["basic"]["exposure"] = 0.3
    r["effects"].update(clarity=40, texture=30, grain_amount=40, vignette_amount=-30, halation_amount=30, dehaze=20)
    r["detail"].update(sharpen_amount=60, nr_luminance=30, nr_color=30)
    r["lens"].update(distortion=20, ca_red=30)
    full = engine.render(img, r, ctx, level=1.0)
    crop = engine.render(img, r, ctx, region=(150, 100, 200, 150), level=1.0)
    assert np.abs(full[100:250, 150:350] - crop).max() < 1e-5
    tiled = engine.render_tiled(img, r, ctx, level=1.0, strip=64)
    assert np.abs(full - tiled).max() < 1e-5


def test_preview_is_faithful_proxy_of_export(studio, synth):
    """For point-wise edits, downsampling the full render ≈ rendering the proxy."""
    import cv2
    s = studio.open(synth["tiff16"])
    r = s.recipe
    r["basic"].update(exposure=0.3, contrast=25, temperature=20, saturation=-20)
    r["film"]["stock"] = "Studio / Gold 200-inspired"
    s.commit(r)
    full, _ = s.render_full()
    down = cv2.resize(full, (s.proxy.shape[1], s.proxy.shape[0]), interpolation=cv2.INTER_AREA)
    prev = engine.render(s.proxy, s.recipe, s.ctx, level=s.proxy_scale)
    err = np.abs(engine.to_display(down, s.source.space).astype(int) - engine.to_display(prev, s.source.space))
    assert np.median(err) <= 2 and np.percentile(err, 99) <= 8
