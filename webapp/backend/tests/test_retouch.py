import numpy as np
import pytest
from PIL import Image

from darkroom_studio import colorspace as cms
from darkroom_studio import engine
from darkroom_web import retouch


def load(path, size=1536):
    import cv2
    a = np.asarray(Image.open(path).convert("RGB"))
    a = cv2.resize(a, (size, size), interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255
    return cms.srgb_decode(np.clip(a, 0, 1))


needs_detector = pytest.mark.skipif(retouch.faces_yunet(np.zeros((64, 64, 3), np.uint8)) is None
                                    and not hasattr(__import__("cv2"), "CascadeClassifier"),
                                    reason="no face detector available (YuNet model could not be fetched)")


@needs_detector
def test_detects_face_eyes_and_mouth(astronaut_path):
    lin = load(astronaut_path, 512)
    eyes, faces, method = retouch.detect_eyes(lin, cms.SRGB)
    mouths, _, _ = retouch.detect_mouths(lin, cms.SRGB)
    assert faces == 1 and len(eyes) == 2 and len(mouths) == 1
    # known positions in the 512 px portrait: eyes ~ (200, 102) and (247, 103), mouth ~ (220, 145)
    xs = sorted(e["x"] * 512 for e in eyes)
    assert abs(xs[0] - 200) < 12 and abs(xs[1] - 247) < 12
    m = mouths[0]
    assert m["x"] * 512 < 220 < (m["x"] + m["w"]) * 512 and m["y"] * 512 < 147 < (m["y"] + m["h"]) * 512


def _paint_red_pupil(lin, cx, cy, r):
    H, W = lin.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W]
    m = np.clip((r - np.hypot(xx - cx, yy - cy)) / 2, 0, 1)[..., None]
    return lin * (1 - m) + np.array([0.55, 0.03, 0.03], np.float32) * m


def test_red_eye_neutralises_pupil_and_leaves_the_rest(astronaut_path):
    lin = load(astronaut_path)
    W = lin.shape[1]
    cx, cy, r = 200 / 512 * W, 102 / 512 * W, 7 / 512 * W
    red = _paint_red_pupil(lin, cx, cy, r * 0.5)
    out = red.copy()
    retouch.red_eye(out, cms.SRGB, [{"x": cx / W, "y": cy / W, "r": r / W}], 100)
    c = out[int(cy), int(cx)]
    assert c[0] < 2.0 * max(c[1], c[2]) + 0.01, c          # no longer red
    yy, xx = np.mgrid[0:W, 0:W]
    outside = np.hypot(xx - cx, yy - cy) > r * 1.05
    assert np.array_equal(out[outside], red[outside])       # bit-identical outside the circle
    # skin inside the circle (reddish but not red-eye red) barely moves
    ring = (np.hypot(xx - cx, yy - cy) > r * 0.7) & ~outside
    assert np.abs(out[ring] - red[ring]).mean() < 0.01


def test_teeth_whitening_removes_yellow_inside_box_only(astronaut_path):
    lin = load(astronaut_path)
    W = lin.shape[1]
    box = {"x": 196 / 512, "y": 132 / 512, "w": 48 / 512, "h": 24 / 512}
    out = lin.copy()
    retouch.whiten_teeth(out, cms.SRGB, [box], 70)
    ok = engine.OkLab(cms.SRGB)
    x0, y0, x1, y1 = [int(v * W) for v in (box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"])]
    before, after = ok.from_linear(lin[y0:y1, x0:x1]), ok.from_linear(out[y0:y1, x0:x1])
    teeth = before[..., 0] > np.quantile(before[..., 0], 0.9)
    assert after[..., 2][teeth].mean() < before[..., 2][teeth].mean() - 0.005     # less yellow
    # texture kept: lightness variation among tooth pixels is preserved
    assert after[..., 0][teeth].std() == pytest.approx(before[..., 0][teeth].std(), rel=0.35)
    mask = np.ones(out.shape[:2], bool)
    mask[y0:y1, x0:x1] = False
    assert np.array_equal(out[mask], lin[mask])


def test_zero_strength_is_identity(astronaut_path):
    lin = load(astronaut_path, 256)
    out = lin.copy()
    retouch.red_eye(out, cms.SRGB, [{"x": 0.4, "y": 0.2, "r": 0.02}], 0)
    retouch.whiten_teeth(out, cms.SRGB, [{"x": 0.38, "y": 0.26, "w": 0.1, "h": 0.05}], 0)
    assert np.array_equal(out, lin)
