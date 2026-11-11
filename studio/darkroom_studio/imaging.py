"""
Source image loading.

Every supported file becomes a `SourceImage`: a full-resolution float32
linear-light buffer in the file's own primaries plus everything needed to
export it faithfully (ICC bytes, EXIF, alpha, bit depth, original path).

Pixel decoders, chosen so that nothing is silently reduced to 8 bit:
  * TIFF -> tifffile (8/16/32-bit int and float)
  * PNG  -> OpenCV IMREAD_UNCHANGED (8/16-bit; Pillow would drop 16-bit RGB to 8)
  * JPEG -> Pillow (8-bit by definition)
  * NEF/CR2/DNG/... -> rawpy (see raw.py)
ICC and EXIF blocks are always read with Pillow/tifffile without re-decoding.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from dataclasses import dataclass, field

import numpy as np

from . import colorspace as cms
from .metadata import read_metadata

RAW_EXTS = {".nef", ".nrw", ".cr2", ".cr3", ".crw", ".dng", ".arw", ".raf", ".orf", ".rw2", ".pef", ".srw"}
RASTER_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
SUPPORTED_EXTS = RAW_EXTS | RASTER_EXTS


@dataclass
class SourceImage:
    path: str                       # protected copy of the original
    original_name: str
    kind: str                       # 'raster' | 'raw'
    file_format: str                # 'JPEG', 'PNG', 'TIFF', 'NEF', ...
    linear: np.ndarray              # (H, W, 3) float32, linear light, source primaries
    space: cms.ColorSpace           # working/source space (linear version is implied)
    bit_depth: int                  # bits per channel in the file (or RAW ADC depth)
    alpha: np.ndarray | None = None
    exif_bytes: bytes | None = None
    info: dict = field(default_factory=dict)
    full_metadata: dict = field(default_factory=dict)
    sha256: str = ""
    orientation_applied: int = 1
    file_dims: tuple = (0, 0)       # (W, H) as stored in the file, before orientation
    notes: list = field(default_factory=list)
    raw_settings: dict | None = None

    @property
    def width(self):
        return int(self.linear.shape[1])

    @property
    def height(self):
        return int(self.linear.shape[0])

    def describe_dims(self):
        w, h = self.width, self.height
        s = f"{w} × {h} px ({w * h / 1e6:.1f} MP)"
        if self.orientation_applied in (5, 6, 7, 8):
            s += f" · rotated from stored {self.file_dims[0]} × {self.file_dims[1]} by EXIF orientation"
        return s


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def protect_original(src_path, originals_dir):
    """Copy the upload into the originals library and make the copy read-only.
    The uploaded file itself is never opened for writing."""
    os.makedirs(originals_dir, exist_ok=True)
    digest = sha256_file(src_path)
    name = os.path.basename(src_path)
    dst = os.path.join(originals_dir, f"{digest[:12]}_{name}")
    if not os.path.exists(dst):
        shutil.copy2(src_path, dst)
        os.chmod(dst, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return dst, digest


def apply_orientation(arr, orientation):
    """Apply an EXIF orientation (1-8) to an (H, W, ...) array. Lossless."""
    o = int(orientation or 1)
    if o == 2:
        return arr[:, ::-1]
    if o == 3:
        return arr[::-1, ::-1]
    if o == 4:
        return arr[::-1, :]
    if o == 5:
        return np.swapaxes(arr, 0, 1)
    if o == 6:
        return np.rot90(arr, -1)
    if o == 7:
        return np.swapaxes(arr, 0, 1)[::-1, ::-1]
    if o == 8:
        return np.rot90(arr, 1)
    return arr


def _to_float(arr):
    if arr.dtype == np.uint8:
        return arr.astype(np.float32) / 255.0, 8
    if arr.dtype == np.uint16:
        return arr.astype(np.float32) / 65535.0, 16
    if arr.dtype in (np.float32, np.float64, np.float16):
        return arr.astype(np.float32), 32
    if arr.dtype == np.uint32:
        return (arr.astype(np.float64) / 4294967295.0).astype(np.float32), 32
    raise ValueError(f"unsupported sample type {arr.dtype}")


def _split_alpha(arr):
    if arr.ndim == 2:
        return np.repeat(arr[..., None], 3, axis=2), None, ["Greyscale image expanded to RGB."]
    c = arr.shape[2]
    if c == 1:
        return np.repeat(arr, 3, axis=2), None, ["Greyscale image expanded to RGB."]
    if c == 2:
        return np.repeat(arr[..., :1], 3, axis=2), arr[..., 1], ["Greyscale+alpha expanded to RGB; alpha kept."]
    if c == 3:
        return arr, None, []
    return arr[..., :3], arr[..., 3], ["Alpha channel kept and re-attached on PNG/TIFF export."]


def _pil_side_data(path):
    from PIL import Image
    with Image.open(path) as im:
        icc = im.info.get("icc_profile")
        exif = im.info.get("exif")
        if exif is None:
            try:
                e = im.getexif()
                exif = e.tobytes() if len(e) else None
            except Exception:
                exif = None
        mode = im.mode
    if isinstance(exif, bytes) and exif.startswith(b"Exif\x00\x00"):
        exif = exif[6:]
    return icc, exif, mode


def load_raster(path):
    ext = os.path.splitext(path)[1].lower()
    notes = []
    if ext in (".tif", ".tiff"):
        import tifffile
        with tifffile.TiffFile(path) as tf:
            page = tf.pages[0]
            arr = page.asarray()
            icc_tag = page.tags.get(34675)
            icc = bytes(icc_tag.value) if icc_tag is not None else None
            photometric = page.photometric
            if str(photometric).upper().endswith("SEPARATED"):
                raise ValueError("CMYK TIFF files are not supported for editing; convert to RGB first.")
            if page.planarconfig == 2 and arr.ndim == 3 and arr.shape[0] in (3, 4):
                arr = np.moveaxis(arr, 0, -1)
        try:
            _, exif, _ = _pil_side_data(path)
        except Exception:
            exif = None
        fmt = "TIFF"
    elif ext == ".png":
        import cv2
        arr = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if arr is None:
            raise ValueError("OpenCV could not decode this PNG")
        if arr.ndim == 3:
            arr = arr[..., [2, 1, 0, 3][:arr.shape[2]]] if arr.shape[2] == 4 else arr[..., ::-1]
        icc, exif, _ = _pil_side_data(path)
        fmt = "PNG"
    elif ext in (".jpg", ".jpeg"):
        from PIL import Image
        with Image.open(path) as im:
            icc = im.info.get("icc_profile")
            if im.mode == "CMYK":
                raise ValueError("CMYK JPEG files are not supported for editing; convert to RGB first.")
            if im.mode not in ("RGB", "L"):
                notes.append(f"JPEG mode {im.mode} converted to RGB.")
                im = im.convert("RGB")
            arr = np.asarray(im)
        _, exif, _ = _pil_side_data(path)
        fmt = "JPEG"
    else:
        raise ValueError(f"not a raster format: {ext}")

    rgb, alpha, n2 = _split_alpha(np.ascontiguousarray(arr))
    notes += n2
    rgbf, bits = _to_float(rgb)
    if alpha is not None:
        alpha, _ = _to_float(alpha)
    return rgbf, alpha, bits, icc, exif, fmt, notes


def load_source(upload_path, originals_dir, raw_settings=None):
    """Load any supported file into a SourceImage (protected original copy)."""
    ext = os.path.splitext(upload_path)[1].lower()
    if ext not in SUPPORTED_EXTS:
        raise ValueError(f"Unsupported file type '{ext}'. Supported: "
                         + ", ".join(sorted(e.lstrip('.').upper() for e in SUPPORTED_EXTS)))
    protected, digest = protect_original(upload_path, originals_dir)
    info, full = read_metadata(protected)
    name = os.path.basename(upload_path)
    if ext in RAW_EXTS:
        from .raw import develop_raw, RawSettings
        settings = raw_settings or RawSettings()
        linear, space, meta = develop_raw(protected, settings)
        src = SourceImage(path=protected, original_name=name, kind="raw", file_format=ext.lstrip(".").upper(),
                          linear=linear, space=space, bit_depth=meta.get("bits_per_sample", 14),
                          info=info, full_metadata=full, sha256=digest,
                          orientation_applied=meta.get("flip_orientation", 1),
                          file_dims=(meta["raw_width"], meta["raw_height"]), notes=meta["notes"],
                          raw_settings={"settings": settings.to_dict(), "libraw": meta})
        return src

    rgbf, alpha, bits, icc, exif, fmt, notes = load_raster(protected)
    file_dims = (rgbf.shape[1], rgbf.shape[0])
    space = cms.identify_source_space(icc, info.get("exif_colorspace"), info.get("interop_index"))
    notes += space.notes
    orient = int(info.get("orientation") or 1)
    if orient != 1:
        rgbf = np.ascontiguousarray(apply_orientation(rgbf, orient))
        if alpha is not None:
            alpha = np.ascontiguousarray(apply_orientation(alpha, orient))
        notes.append(f"EXIF orientation {orient} applied to pixels; exported files are written upright "
                     "with Orientation=1.")
    linear = space.transfer.decode(rgbf)
    return SourceImage(path=protected, original_name=name, kind="raster", file_format=fmt, linear=linear,
                       space=space, bit_depth=bits, alpha=alpha, exif_bytes=exif, info=info,
                       full_metadata=full, sha256=digest, orientation_applied=orient, file_dims=file_dims,
                       notes=notes)


def make_proxy(linear, long_edge=1600):
    """Downsample (area filter, in linear light) for interactive preview.
    Returns (proxy, scale). The full-resolution buffer is not modified."""
    import cv2
    h, w = linear.shape[:2]
    scale = min(1.0, float(long_edge) / max(h, w))
    if scale >= 1.0:
        return linear, 1.0
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    return cv2.resize(linear, (nw, nh), interpolation=cv2.INTER_AREA), nw / w


def read_bytes_icc_from_image_file(path):
    """Utility for tests: ICC bytes embedded in any written file."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".tif", ".tiff"):
        import tifffile
        with tifffile.TiffFile(path) as tf:
            t = tf.pages[0].tags.get(34675)
            return bytes(t.value) if t is not None else None
    from PIL import Image
    with Image.open(path) as im:
        return im.info.get("icc_profile")


__all__ = ["SourceImage", "load_source", "make_proxy", "protect_original", "sha256_file", "apply_orientation",
           "SUPPORTED_EXTS", "RAW_EXTS", "RASTER_EXTS"]
