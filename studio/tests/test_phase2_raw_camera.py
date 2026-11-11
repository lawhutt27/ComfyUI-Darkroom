"""PHASE 2 (REAL CAMERA FILES): RAW development, metadata, export from NEF/CR2.

These use genuine Nikon D5300 and Canon EOS 1000D files (CC0, raw.pixls.us).
They skip, not pass, when the samples are absent."""

import json
import os
import subprocess

import numpy as np
import pytest
import tifffile

from helpers import sample
from darkroom_studio import recipe as R
from darkroom_studio.imaging import sha256_file
from darkroom_studio.metadata import exiftool_available
from darkroom_studio.raw import RawSettings, develop_raw, HIGHLIGHT_OPTIONS, DEMOSAIC_OPTIONS

pytestmark = pytest.mark.camera

CAMERAS = [
    ("D5300_14bit.NEF", (6016, 4016), "Nikon D5300", "18-140", 14),
    ("D5300_12bit.NEF", (6016, 4016), "Nikon D5300", "18-140", 12),
    ("EOS1000D.CR2", (3906, 2602), "Canon EOS Rebel XS / 1000D", "18-55", 12),
]


@pytest.mark.parametrize("name,dims,camera,lens,bits", CAMERAS)
def test_raw_decode_full_resolution(studio, name, dims, camera, lens, bits):
    path = sample(name)
    s = studio.open(path)
    src = s.source
    assert src.kind == "raw"
    assert (src.width, src.height) == dims                  # LibRaw full output, nothing cropped/resized
    assert src.linear.dtype == np.float32
    assert src.bit_depth == bits
    assert src.info["known_camera"] == camera
    assert lens in src.info["lens"]
    assert src.info["iso"] and src.info["aperture"] and src.info["shutter"] and src.info["focal_length"]
    lm = src.raw_settings["libraw"]
    assert lm["postprocess"]["demosaic"] == "AHD" and lm["postprocess"]["output_bps"] == 16
    assert lm["postprocess"]["no_auto_bright"] is True
    # developed image is a plausible photograph, not black/blown
    med = float(np.median(src.linear))
    assert 0.02 < med < 0.8


@pytest.mark.slow
@pytest.mark.parametrize("name,dims,camera,lens,bits", CAMERAS[::2])
def test_raw_export_16bit_tiff_with_metadata_and_original_unchanged(studio, name, dims, camera, lens, bits):
    path = sample(name)
    digest = sha256_file(path)
    s = studio.open(path)
    r = s.recipe
    r["film"]["stock"] = "Studio / Portra 400-inspired"
    r["basic"].update(exposure=0.2, highlights=-30)
    s.commit(r)
    res = s.export("TIFF 16-bit (master, lossless)", "Same as source/working space")
    with tifffile.TiffFile(res["path"]) as tf:
        assert tf.pages[0].shape == (dims[1], dims[0], 3) and tf.pages[0].dtype == np.uint16
    assert sha256_file(path) == digest and sha256_file(s.source.path) == digest
    side = json.load(open(res["sidecar"]))
    assert side["raw_development"]["libraw"]["postprocess"]["demosaic"] == "AHD"
    raw_side = json.load(open(res["raw_sidecar"]))
    assert raw_side["sha256"] == digest and "not" in raw_side["note"].lower()
    if exiftool_available():
        o = subprocess.run(["exiftool", "-j", "-Model", "-LensID", "-ISO", "-FNumber", "-ExposureTime",
                            "-FocalLength", "-Orientation#", res["path"]], capture_output=True, text=True)
        m = json.loads(o.stdout)[0]
        assert lens in m["LensID"] and m["ISO"] == s.source.info["iso"]
        assert m["Orientation"] == 1


@pytest.mark.parametrize("hl", HIGHLIGHT_OPTIONS)
def test_raw_highlight_modes(hl):
    lin, space, meta = develop_raw(sample("EOS1000D.CR2"), RawSettings(highlight_mode=hl))
    assert lin.shape == (2602, 3906, 3) and np.isfinite(lin).all()
    if hl.startswith("Unclip"):
        assert meta["postprocess"]["highlight_mode"] == hl


@pytest.mark.parametrize("dm", DEMOSAIC_OPTIONS)
def test_raw_demosaic_algorithms(dm):
    lin, _, meta = develop_raw(sample("EOS1000D.CR2"), RawSettings(demosaic=dm))
    assert lin.shape == (2602, 3906, 3) and meta["postprocess"]["demosaic"] == dm


def test_raw_white_balance_and_exposure_options():
    p = sample("D5300_12bit.NEF")
    base, _, _ = develop_raw(p, RawSettings(base_curve="Linear (scene-referred)"))
    plus, _, m = develop_raw(p, RawSettings(base_curve="Linear (scene-referred)", exposure_ev=1.0))
    ratio = np.median(plus) / np.median(base)
    assert 1.8 < ratio < 2.2 and m["postprocess"]["exp_shift"] == 2.0
    auto, _, _ = develop_raw(p, RawSettings(white_balance="Auto (LibRaw grey-world)"))
    day, _, _ = develop_raw(p, RawSettings(white_balance="Daylight (camera preset)"))
    assert auto.shape == day.shape == base.shape


@pytest.mark.parametrize("cs", ["sRGB", "Adobe RGB (1998)", "Display P3", "ProPhoto RGB"])
def test_raw_wide_gamut_working_space_and_icc(studio, cs):
    s = studio.open(sample("EOS1000D.CR2"), RawSettings(color_space=cs))
    assert cs.split(" ")[0] in s.source.space.name
    res = s.export("PNG 16-bit (lossless)", "Same as source/working space", keep_metadata=False)
    from darkroom_studio.colorspace import parse_icc
    from darkroom_studio.imaging import read_bytes_icc_from_image_file
    icc = parse_icc(read_bytes_icc_from_image_file(res["path"]))
    assert icc.same_primaries(s.source.space)


def test_redevelop_keeps_history_and_original(studio):
    p = sample("EOS1000D.CR2")
    s = studio.open(p)
    r = s.recipe
    r["basic"]["contrast"] = 25
    s.commit(r)
    s.redevelop(RawSettings(highlight_mode="Reconstruct (5)", exposure_ev=-0.5))
    assert s.recipe["basic"]["contrast"] == 25
    assert s.source.raw_settings["settings"]["highlight_mode"] == "Reconstruct (5)"
    assert sha256_file(s.source.path) == sha256_file(p)


def test_export_developed_is_labelled_not_raw(studio):
    s = studio.open(sample("EOS1000D.CR2"))
    res = s.export_developed()
    side = json.load(open(res["sidecar"]))
    assert "not the raw original" in side["note"].lower()
    assert tifffile.imread(res["path"]).dtype == np.uint16


def test_real_camera_jpeg_from_nef(studio, tmp_path):
    """The D5300 embeds a full-size camera-rendered JPEG in every NEF; it is a
    genuine in-camera JPEG (Nikon's own processing) and is used here as the
    real-camera JPEG import test."""
    import rawpy
    with rawpy.imread(sample("D5300_14bit.NEF")) as raw:
        th = raw.extract_thumb()
    jpg = tmp_path / "D5300_camera.jpg"
    jpg.write_bytes(th.data)
    s = studio.open(str(jpg))
    assert s.source.file_format == "JPEG" and s.source.bit_depth == 8
    assert (s.source.width, s.source.height) == (6000, 4000)
    assert s.source.info["known_camera"] == "Nikon D5300"     # preview EXIF says Make "Nikon", Model "D5300"
    res = s.export("TIFF 16-bit (master, lossless)", "Same as source/working space")
    assert tifffile.imread(res["path"]).shape == (4000, 6000, 3)


def test_camera_advice(studio):
    from darkroom_studio.advisor import analyse, suggest
    for name, cam in (("D5300_14bit.NEF", "Nikon D5300"), ("EOS1000D.CR2", "Canon EOS Rebel XS / 1000D")):
        s = studio.open(sample(name))
        rec, why = suggest(s.source.info, analyse(s.analysis, s.source.space), True)
        assert any(cam in w for w in why)
        assert rec["detail"]["sharpen_amount"] > 0
        clean, warns = R.validate(rec, partial=True, base=R.default_recipe())
        assert not [w for w in warns if "unknown" in w]
