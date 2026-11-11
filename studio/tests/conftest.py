"""
Shared fixtures.

Two kinds of tests live here and are reported separately:
  * SYNTHETIC tests (default): images generated on the fly with known content,
    EXIF and ICC profiles.  They prove the pipeline's integrity guarantees.
  * CAMERA tests (marker `camera`): real NEF/CR2 files from a Nikon D5300 and
    a Canon EOS 1000D (raw.pixls.us, CC0).  Fetch them with
    `python tools/fetch_samples.py`; the tests skip (not pass) when absent.
"""

import io
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

SAMPLES = os.environ.get("DARKROOM_SAMPLES", os.path.join(os.path.dirname(HERE), "samples"))


def pytest_configure(config):
    config.addinivalue_line("markers", "camera: real camera files (NEF/CR2) - needs tools/fetch_samples.py")
    config.addinivalue_line("markers", "slow: full-resolution renders of 10-24 MP files")


def sample(name):
    p = os.path.join(SAMPLES, name)
    if not os.path.exists(p):
        pytest.skip(f"camera sample {name} not present (run tools/fetch_samples.py)")
    return p


@pytest.fixture
def studio(tmp_path):
    from darkroom_studio.session import Studio
    return Studio(str(tmp_path / "work"), preview_long_edge=512)


def make_pattern(h=400, w=600, seed=0):
    """Smooth gradients + colour patches + a 1-px marker border + noise texture."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.stack([x / w, y / h, 0.5 + 0.4 * np.sin(x / 23.0) * np.cos(y / 17.0)], -1)
    for i, c in enumerate([(0.8, 0.2, 0.2), (0.2, 0.7, 0.3), (0.2, 0.3, 0.8), (0.9, 0.75, 0.6), (0.5, 0.5, 0.5)]):
        img[40:120, 40 + i * 100:120 + i * 100] = c
    img += rng.normal(0, 0.01, img.shape).astype(np.float32)
    img = np.clip(img, 0.02, 0.98)
    img[0, :] = img[-1, :] = (1.0, 0.0, 1.0)      # magenta marker border
    img[:, 0] = img[:, -1] = (1.0, 0.0, 1.0)
    return img.astype(np.float32)


def _exif_bytes(orientation=1, w=600, h=400):
    from PIL import Image
    from PIL.TiffImagePlugin import IFDRational
    ex = Image.Exif()
    ex[0x010F] = "NIKON CORPORATION"
    ex[0x0110] = "NIKON D5300"
    ex[0x0112] = orientation
    ex[0x0132] = "2024:05:01 10:20:30"
    ifd = {}
    ifd[0x829A] = IFDRational(1, 250)
    ifd[0x829D] = IFDRational(56, 10)
    ifd[0x8827] = 400
    ifd[0x920A] = IFDRational(35, 1)
    ifd[0xA434] = "18.0-55.0 mm f/3.5-5.6"
    ifd[0x9003] = "2024:05:01 10:20:30"
    ex[0x8769] = ifd
    return ex.tobytes()


@pytest.fixture
def synth(tmp_path):
    """Writes synthetic files and returns {kind: path}."""
    from PIL import Image, ImageCms
    import cv2
    import tifffile
    from darkroom_studio import colorspace as cms
    d = tmp_path / "synthetic"
    d.mkdir()
    img = make_pattern()
    srgb_icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    adobe_icc = cms.build_icc_profile(cms.ADOBE_RGB)
    out = {}
    u8 = (img * 255 + 0.5).astype(np.uint8)
    u16 = (img * 65535 + 0.5).astype(np.uint16)
    p = d / "srgb.jpg"
    Image.fromarray(u8).save(p, quality=95, icc_profile=srgb_icc, exif=_exif_bytes(), subsampling=0)
    out["jpeg"] = str(p)
    p = d / "rot6.jpg"   # stored 600x400, displayed 400x600
    Image.fromarray(u8).save(p, quality=95, exif=_exif_bytes(orientation=6))
    out["jpeg_rot6"] = str(p)
    p = d / "adobe.jpg"
    Image.fromarray(u8).save(p, quality=95, icc_profile=adobe_icc, exif=_exif_bytes())
    out["jpeg_adobe"] = str(p)
    p = d / "noprofile.jpg"
    Image.fromarray(u8).save(p, quality=95)
    out["jpeg_plain"] = str(p)
    p = d / "srgb16.tif"
    tifffile.imwrite(p, u16, photometric="rgb", iccprofile=srgb_icc)
    out["tiff16"] = str(p)
    p = d / "srgb8.tif"
    tifffile.imwrite(p, u8, photometric="rgb", iccprofile=srgb_icc)
    out["tiff8"] = str(p)
    p = d / "srgb16.png"
    cv2.imwrite(str(p), u16[..., ::-1])
    out["png16"] = str(p)
    p = d / "srgb8.png"
    Image.fromarray(u8).save(p, icc_profile=srgb_icc)
    out["png8"] = str(p)
    p = d / "alpha.png"
    a = np.zeros(u8.shape[:2], np.uint8)
    a[:, 300:] = 255
    Image.fromarray(np.dstack([u8, a]), "RGBA").save(p)
    out["png_alpha"] = str(p)
    out["array"] = img
    out["u16"] = u16
    out["icc_srgb"] = srgb_icc
    out["icc_adobe"] = adobe_icc
    return out
