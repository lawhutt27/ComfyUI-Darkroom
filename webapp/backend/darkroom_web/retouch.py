"""Assisted retouch: red-eye and teeth whitening.

Both tools are deterministic and local:
  * Detection runs once, when the layer is created, on the preview proxy,
    with OpenCV's YuNet face detector (5 landmarks: eyes, nose, mouth
    corners; falls back to Haar cascades on OpenCV 4 builds). The
    result is frozen into the layer as normalised circles/boxes, which the
    user can move, add or delete. Re-rendering never re-detects.
  * The correction only touches pixels selected by a colour mask inside
    those shapes. Every other pixel is returned bit-identical.

Coordinates: circles are {x, y, r} with x, y in 0-1 of width/height and r
as a fraction of the image's long edge. Boxes are {x, y, w, h} in 0-1.
"""

from __future__ import annotations

import hashlib
import os

import cv2
import numpy as np

from darkroom_studio import colorspace as cms
from darkroom_studio import engine

YUNET_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
_MODEL_DIR = os.environ.get("DARKROOM_MODEL_DIR", os.path.join(os.path.expanduser("~"), ".cache", "darkroom"))


def yunet_path(download=True):
    """Path to the YuNet face model (OpenCV Zoo, MIT licence, 230 KB).
    Uses $DARKROOM_YUNET_MODEL, else the cache, else downloads it once and
    checks its SHA-256. Returns None when it is unavailable."""
    p = os.environ.get("DARKROOM_YUNET_MODEL") or os.path.join(_MODEL_DIR, "face_detection_yunet_2023mar.onnx")
    if os.path.exists(p):
        return p
    if not download:
        return None
    try:
        import httpx
        r = httpx.get(YUNET_URL, timeout=30, follow_redirects=True)
        r.raise_for_status()
        if hashlib.sha256(r.content).hexdigest() != YUNET_SHA256:
            return None
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".part", "wb") as f:
            f.write(r.content)
        os.replace(p + ".part", p)
        return p
    except Exception:
        return None


def detector_status():
    if hasattr(cv2, "FaceDetectorYN") and yunet_path(download=False):
        return "yunet"
    if hasattr(cv2, "FaceDetectorYN"):
        return "yunet (model downloads on first use)"
    if hasattr(cv2, "CascadeClassifier"):
        return "haar"
    return "manual only"


try:  # YuNet on the new OpenCV 5 graph engine logs a harmless warning per call
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except Exception:  # pragma: no cover
    pass


def _display(linear, space):
    return engine.to_display(linear, space)


def faces_yunet(rgb_u8):
    """[(x, y, w, h, landmarks[5][2], score)] in pixels, or None if YuNet is unavailable."""
    if not hasattr(cv2, "FaceDetectorYN"):
        return None
    path = yunet_path()
    if not path:
        return None
    h, w = rgb_u8.shape[:2]
    det = cv2.FaceDetectorYN.create(path, "", (w, h), 0.7, 0.3, 50)
    _, faces = det.detect(cv2.cvtColor(rgb_u8, cv2.COLOR_RGB2BGR))
    out = []
    for f in (faces if faces is not None else []):
        lm = f[4:14].reshape(5, 2)
        out.append((float(f[0]), float(f[1]), float(f[2]), float(f[3]), lm, float(f[14])))
    return out


def _haar(name):
    return cv2.CascadeClassifier(cv2.data.haarcascades + name)


def faces_haar(rgb_u8):
    """Fallback for OpenCV 4.x builds without the YuNet model. No landmarks."""
    if not hasattr(cv2, "CascadeClassifier"):
        return None
    g = cv2.equalizeHist(cv2.cvtColor(rgb_u8, cv2.COLOR_RGB2GRAY))
    side = max(24, int(min(g.shape) * 0.06))
    found = _haar("haarcascade_frontalface_default.xml").detectMultiScale(g, 1.1, 6, minSize=(side, side))
    out = []
    for x, y, fw, fh in found:
        # typical landmark positions in a frontal face box
        lm = np.array([[x + fw * 0.31, y + fh * 0.40], [x + fw * 0.69, y + fh * 0.40], [x + fw * 0.5, y + fh * 0.58],
                       [x + fw * 0.35, y + fh * 0.76], [x + fw * 0.65, y + fh * 0.76]], np.float32)
        out.append((float(x), float(y), float(fw), float(fh), lm, 0.5))
    return out


def detect_faces(linear, space):
    rgb = _display(linear, space)
    faces = faces_yunet(rgb)
    method = "yunet"
    if faces is None:
        faces, method = faces_haar(rgb), "haar"
    if faces is None:
        faces, method = [], "none"
    return rgb.shape[:2], faces, method


def detect_eyes(linear, space):
    """Eye circles (normalised) from face landmarks."""
    (h, w), faces, method = detect_faces(linear, space)
    long_edge = max(h, w)
    eyes = []
    for fx, fy, fw, fh, lm, _ in faces:
        d = float(np.hypot(*(lm[1] - lm[0])))
        r = max(2.0, 0.17 * d)
        for ex, ey in lm[:2]:
            eyes.append({"x": round(float(ex) / w, 6), "y": round(float(ey) / h, 6), "r": round(r / long_edge, 6)})
    return eyes, len(faces), method


def detect_mouths(linear, space):
    """Mouth boxes (normalised) around the mouth-corner landmarks."""
    (h, w), faces, method = detect_faces(linear, space)
    boxes = []
    for fx, fy, fw, fh, lm, _ in faces:
        (rx, ry), (lx, ly) = lm[3], lm[4]
        mw = max(4.0, float(np.hypot(lx - rx, ly - ry)) * 1.15)
        mh = max(3.0, mw * 0.5)
        cx, cy = (rx + lx) / 2, (ry + ly) / 2 + 0.01 * fh
        x0, y0 = max(0.0, cx - mw / 2), max(0.0, cy - mh / 2)
        boxes.append({"x": round(float(x0) / w, 6), "y": round(float(y0) / h, 6),
                      "w": round(float(min(mw, w - x0)) / w, 6), "h": round(float(min(mh, h - y0)) / h, 6)})
    return boxes, len(faces), method


# --------------------------------------------------------------------------- helpers
def _smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _apply_delta(img, sl, delta_ws, mask):
    """img[sl] += mask * delta, leaving mask == 0 pixels bit-identical."""
    region = img[sl]
    m = mask[..., None]
    upd = region + delta_ws * m
    img[sl] = np.where(m > 0, upd, region)


# --------------------------------------------------------------------------- red-eye
def red_eye(img, space, eyes, strength):
    """Neutralise red pupils inside each circle. `img` is linear, modified in place."""
    s = float(strength) / 100.0
    if s <= 0 or not eyes:
        return img
    H, W = img.shape[:2]
    long_edge = max(H, W)
    to_srgb = space.matrix_to(cms.SRGB)
    from_srgb = np.linalg.inv(to_srgb).astype(np.float32)
    for e in eyes:
        r = max(1.5, e["r"] * long_edge)
        cx, cy = e["x"] * W, e["y"] * H
        x0, x1 = int(max(0, np.floor(cx - r - 2))), int(min(W, np.ceil(cx + r + 2)))
        y0, y1 = int(max(0, np.floor(cy - r - 2))), int(min(H, np.ceil(cy + r + 2)))
        if x1 <= x0 or y1 <= y0:
            continue
        sl = (slice(y0, y1), slice(x0, x1))
        patch = cms.apply_matrix(img[sl], to_srgb)
        R_, G, B = patch[..., 0], patch[..., 1], patch[..., 2]
        gb = 0.5 * (G + B)
        # flash red-eye is a very saturated red: in linear light R is many times
        # G and B (skin is about 2x, so it is excluded)
        ratio = R_ / (np.maximum(G, B) + 0.002)
        core = _smooth(3.0, 5.0, ratio) * _smooth(0.004, 0.02, R_)
        gate = _smooth(2.2, 3.5, ratio)
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        d = np.sqrt((xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2) / r
        inside = 1.0 - _smooth(0.85, 1.0, d)
        core *= inside
        if core.max() <= 0:
            continue
        # close small holes (glints are left alone by the gate), then soften the edge
        k = max(1, int(round(r * 0.06)))
        mask = cv2.dilate(core.astype(np.float32), np.ones((2 * k + 1, 2 * k + 1), np.float32))
        mask = cv2.GaussianBlur(mask, (0, 0), max(0.6, r * 0.05))
        mask = mask * gate * inside
        mask = np.where(mask > 1e-3, mask, 0).astype(np.float32)
        # red channel -> neighbours' level, slightly darkened: a natural dark pupil
        fixed = np.stack([gb * 0.9, G * 0.9, B * 0.9], -1)
        delta = cms.apply_matrix(fixed - patch, from_srgb)
        _apply_delta(img, sl, delta, mask * s)
    return img


# --------------------------------------------------------------------------- teeth
def teeth_mask(lab, box_h, box_w):
    """Soft mask of tooth-coloured pixels in an OkLab patch."""
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    # teeth are the brightest thing in the mouth box; skin and lips sit well below
    Lref = np.quantile(L, 0.95)
    bright = _smooth(max(0.35, Lref - 0.13), max(0.42, Lref - 0.05), L)
    not_lip = 1.0 - _smooth(0.035, 0.07, a)            # lips/gums are clearly red-pink
    not_blue = _smooth(-0.03, 0.0, b)
    low_chroma = 1.0 - _smooth(0.10, 0.16, np.hypot(a, b))
    m = (bright * not_lip * not_blue * low_chroma).astype(np.float32)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.float32))
    m = cv2.GaussianBlur(m, (0, 0), max(0.6, min(box_h, box_w) * 0.02))
    # elliptical feather toward the box edge
    yy, xx = np.mgrid[0:box_h, 0:box_w].astype(np.float32)
    ny = (yy + 0.5) / box_h * 2 - 1
    nx = (xx + 0.5) / box_w * 2 - 1
    m *= 1.0 - _smooth(0.8, 1.0, np.sqrt(nx ** 2 + ny ** 2))
    return np.where(m > 1e-3, m, 0).astype(np.float32)


def whiten_teeth(img, space, regions, strength):
    """Remove yellow from tooth pixels and lift them a little. Texture (local
    lightness variation) is kept because lightness is offset, not replaced."""
    s = float(strength) / 100.0
    if s <= 0 or not regions:
        return img
    H, W = img.shape[:2]
    ok = engine.OkLab(space)
    for bx in regions:
        x0, y0 = int(np.floor(bx["x"] * W)), int(np.floor(bx["y"] * H))
        x1, y1 = int(np.ceil((bx["x"] + bx["w"]) * W)), int(np.ceil((bx["y"] + bx["h"]) * H))
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        if x1 - x0 < 3 or y1 - y0 < 3:
            continue
        sl = (slice(y0, y1), slice(x0, x1))
        patch = img[sl]
        lab = ok.from_linear(patch)
        mask = teeth_mask(lab, y1 - y0, x1 - x0)
        if mask.max() <= 0:
            continue
        L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
        new = lab.copy()
        new[..., 2] = b - 0.8 * s * np.maximum(b - 0.004, 0)        # de-yellow, never to blue
        new[..., 1] = a * (1 - 0.35 * s)
        new[..., 0] = L + s * 0.05 * np.clip(1.0 - L, 0, 1)          # gentle lift, capped
        delta = ok.to_linear(new) - patch
        _apply_delta(img, sl, delta, mask)
    return img
