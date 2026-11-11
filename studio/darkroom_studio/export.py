"""
Export: full-resolution render -> file, with ICC + EXIF and a JSON sidecar.

Formats
  * TIFF 16-bit (master)  - tifffile, Deflate (lossless), ICC tag 34675.
  * PNG 16-bit / 8-bit    - lossless.  16-bit is encoded by OpenCV and the
                            iCCP + eXIf chunks are inserted by us.
  * JPEG 8-bit q95-100    - Pillow, 4:4:4 chroma (no subsampling), ICC, EXIF.
EXIF is copied with ExifTool when available (keeps maker notes, lens data),
otherwise a basic EXIF block is written with Pillow (JPEG/PNG) or baseline
TIFF tags.  Orientation is always written as 1 because the pixels are
stored upright.
"""

from __future__ import annotations

import json
import os
import struct
import time
import zlib

import numpy as np

from . import colorspace as cms
from .metadata import build_basic_exif, copy_metadata_exiftool, exiftool_available

FORMATS = {
    "TIFF 16-bit (master, lossless)": {"ext": ".tif", "bits": 16, "lossy": False},
    "PNG 16-bit (lossless)": {"ext": ".png", "bits": 16, "lossy": False},
    "PNG 8-bit (lossless)": {"ext": ".png", "bits": 8, "lossy": False},
    "JPEG 8-bit (high quality)": {"ext": ".jpg", "bits": 8, "lossy": True},
}
OUTPUT_SPACE_CHOICES = ["Same as source/working space", "sRGB", "Adobe RGB (1998)", "Display P3", "ProPhoto RGB"]


def format_warnings(fmt, source_bits, space_name, quality=None):
    f = FORMATS[fmt]
    w = []
    if f["bits"] < 16 and source_bits > 8:
        w.append(f"⚠ {fmt} stores 8 bits per channel; the {source_bits}-bit source and the float32 render "
                 "will be quantised. Use TIFF 16-bit for the master.")
    elif f["bits"] < 16:
        w.append("8-bit output: the float32 render is quantised to 256 levels per channel.")
    if f["lossy"]:
        w.append(f"⚠ JPEG is lossy compression (quality {quality}). Keep a TIFF/PNG master; never re-edit a JPEG export.")
    if f["bits"] == 8 and "ProPhoto" in space_name:
        w.append("⚠ ProPhoto RGB in 8 bits causes visible banding; use 16-bit TIFF/PNG.")
    return w


def quantise(linear, space_in, space_out, bits, alpha=None):
    """Working linear -> encoded integers in the output space.
    Returns (array, stats) where stats reports clipping before quantisation."""
    lin = cms.convert_linear(linear, space_in, space_out)
    enc = space_out.transfer.encode(lin)
    over = np.any(enc > 1.0, axis=-1)
    under = np.any(enc < 0.0, axis=-1)
    stats = {"clipped_highlights_pct": round(float(over.mean() * 100), 4),
             "clipped_shadows_pct": round(float(under.mean() * 100), 4)}
    maxv = 65535.0 if bits == 16 else 255.0
    dtype = np.uint16 if bits == 16 else np.uint8
    q = np.empty(enc.shape, dtype=dtype)
    np.clip(enc, 0.0, 1.0, out=enc)
    np.multiply(enc, maxv, out=enc)
    np.add(enc, 0.5, out=enc)
    q[...] = enc
    if alpha is not None:
        a = (np.clip(alpha, 0, 1) * maxv + 0.5).astype(dtype)
        q = np.concatenate([q, a[..., None]], axis=-1)
    return q, stats


def _png_chunk(tag, data):
    c = struct.pack(">I", len(data)) + tag + data
    return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def insert_png_chunks(png_bytes, icc=None, exif=None, text=None):
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    ihdr_len = struct.unpack(">I", png_bytes[8:12])[0]
    split = 8 + 12 + ihdr_len
    extra = b""
    if icc:
        extra += _png_chunk(b"iCCP", b"ICC profile\x00\x00" + zlib.compress(icc))
    if exif:
        extra += _png_chunk(b"eXIf", exif)
    for k, v in (text or {}).items():
        extra += _png_chunk(b"tEXt", k.encode("latin-1") + b"\x00" + v.encode("latin-1", "replace"))
    return png_bytes[:split] + extra + png_bytes[split:]


def _unique_path(out_dir, stem, ext):
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, stem + ext)
    i = 2
    while os.path.exists(p):
        p = os.path.join(out_dir, f"{stem}_{i}{ext}")
        i += 1
    return p


def write_image(path, q, fmt, icc, exif_bytes=None, quality=97, info=None):
    f = FORMATS[fmt]
    h, w = q.shape[:2]
    if f["ext"] == ".tif":
        import tifffile
        extratags = []
        if info:
            for code, key in ((271, "camera_make"), (272, "camera_model")):
                if info.get(key):
                    extratags.append((code, "s", 0, str(info[key]), True))
        tifffile.imwrite(path, q, photometric="rgb", compression="zlib", iccprofile=icc,
                         extrasamples=("unassalpha",) if q.shape[-1] == 4 else None,
                         software="Darkroom Studio", extratags=extratags, metadata=None)
    elif f["ext"] == ".png" and f["bits"] == 16:
        import cv2
        arr = q[..., [2, 1, 0, 3]] if q.shape[-1] == 4 else q[..., ::-1]
        ok, buf = cv2.imencode(".png", np.ascontiguousarray(arr), [cv2.IMWRITE_PNG_COMPRESSION, 6])
        if not ok:
            raise RuntimeError("PNG encode failed")
        with open(path, "wb") as fh:
            fh.write(insert_png_chunks(buf.tobytes(), icc=icc, exif=exif_bytes,
                                       text={"Software": "Darkroom Studio"}))
    elif f["ext"] == ".png":
        from PIL import Image
        mode = "RGBA" if q.shape[-1] == 4 else "RGB"
        im = Image.fromarray(q, mode)
        kw = {"icc_profile": icc, "compress_level": 6}
        if exif_bytes:
            kw["exif"] = exif_bytes
        im.save(path, format="PNG", **kw)
    else:
        from PIL import Image
        im = Image.fromarray(q[..., :3], "RGB")
        kw = {"quality": int(quality), "subsampling": 0, "optimize": True, "icc_profile": icc}
        if exif_bytes:
            kw["exif"] = exif_bytes
        im.save(path, format="JPEG", **kw)
    return path


def export(source, linear_full, recipe, out_dir, fmt="TIFF 16-bit (master, lossless)",
           output_space="Same as source/working space", quality=97, keep_metadata=True,
           alpha_full=None, stem_suffix="_darkroom", extra_sidecar=None):
    """Write `linear_full` (the full-resolution render) to disk.

    Returns dict(path, sidecar, width, height, bits, warnings, clipping)."""
    t0 = time.time()
    f = FORMATS[fmt]
    if output_space.startswith("Same"):
        space_out = source.space
    else:
        space_out = cms.OUTPUT_SPACES[output_space]
    # Embedding the original ICC is only correct when we re-encode to exactly that space
    icc = space_out.icc_bytes()
    quality = int(min(max(int(quality), 95), 100))
    warnings = format_warnings(fmt, source.bit_depth, space_out.name, quality)
    if linear_full.shape[:2] != (source.height, source.width):
        raise RuntimeError(f"refusing to export: render is {linear_full.shape[1]}x{linear_full.shape[0]} but "
                           f"source is {source.width}x{source.height}")
    alpha = alpha_full if (alpha_full is not None and f["ext"] in (".png", ".tif")) else None
    if alpha_full is not None and alpha is None:
        warnings.append("Alpha channel dropped: JPEG has no transparency.")
    q, clip = quantise(linear_full, source.space, space_out, f["bits"], alpha)
    if clip["clipped_highlights_pct"] > 0.01:
        warnings.append(f"{clip['clipped_highlights_pct']:.2f}% of pixels exceed the output range and were "
                        "clipped in highlights (lower Exposure/Whites or use Highlights to recover).")
    if clip["clipped_shadows_pct"] > 0.01:
        warnings.append(f"{clip['clipped_shadows_pct']:.2f}% of pixels fall below black or outside the output "
                        "gamut and were clipped (wider output space keeps more colour).")
    stem = os.path.splitext(source.original_name)[0] + stem_suffix
    path = _unique_path(out_dir, stem, f["ext"])
    h, w = q.shape[:2]

    meta_msg = "metadata not copied (disabled)"
    exif_bytes = None
    if keep_metadata and not exiftool_available():
        exif_bytes = _prepared_exif(source, w, h)
        meta_msg = "basic EXIF written with Pillow (install ExifTool for full metadata incl. lens/maker notes)"
    write_image(path, q, fmt, icc, exif_bytes, quality, source.info if keep_metadata else None)
    if keep_metadata and exiftool_available():
        ok, msg = copy_metadata_exiftool(source.path, path, w, h)
        meta_msg = msg if ok else f"ExifTool copy failed: {msg}"
        if not ok:
            warnings.append(meta_msg)

    sidecar = {
        "darkroom_studio": {"version": __import__("darkroom_studio").__version__,
                            "exported": time.strftime("%Y-%m-%dT%H:%M:%S")},
        "source": {"file": source.original_name, "sha256": source.sha256, "kind": source.kind,
                   "format": source.file_format, "bit_depth": source.bit_depth,
                   "dimensions": [source.width, source.height], "stored_dimensions": list(source.file_dims),
                   "orientation_applied": source.orientation_applied, "color_space": source.space.summary()},
        "output": {"file": os.path.basename(path), "format": fmt, "bits_per_channel": f["bits"],
                   "dimensions": [w, h], "color_space": space_out.name, "icc_embedded": bool(icc),
                   "jpeg_quality": quality if f["lossy"] else None, "metadata": meta_msg,
                   "clipping": clip, "render_seconds": round(time.time() - t0, 2)},
        "recipe": recipe,
        "warnings": warnings,
    }
    if source.raw_settings:
        sidecar["raw_development"] = source.raw_settings
    if extra_sidecar:
        sidecar.update(extra_sidecar)
    side_path = os.path.splitext(path)[0] + ".darkroom.json"
    with open(side_path, "w") as fh:
        json.dump(sidecar, fh, indent=2, default=str)
    return {"path": path, "sidecar": side_path, "width": w, "height": h, "bits": f["bits"],
            "warnings": warnings, "clipping": clip, "metadata": meta_msg, "space": space_out.name}


def _prepared_exif(source, w, h):
    """EXIF bytes for Pillow-written files when ExifTool is not available."""
    if source.exif_bytes:
        try:
            from PIL import Image
            ex = Image.Exif()
            ex.load(source.exif_bytes)
            ex[0x0112] = 1
            ifd = dict(ex.get_ifd(0x8769))
            ifd[0xA002], ifd[0xA003] = int(w), int(h)
            ex[0x8769] = ifd
            return ex.tobytes()
        except Exception:
            pass
    return build_basic_exif(source.info, w, h)


def write_raw_sidecar(source, out_dir):
    """Metadata sidecar for a RAW original (the RAW file itself is untouched)."""
    if source.kind != "raw":
        return None
    path = _unique_path(out_dir, os.path.splitext(source.original_name)[0] + "_raw_metadata", ".json")
    with open(path, "w") as fh:
        json.dump({"raw_file": source.original_name, "sha256": source.sha256,
                   "note": "The RAW original is kept unchanged in the originals library. "
                           "The values below describe how it was developed, not the RAW itself.",
                   "development": source.raw_settings, "summary": source.info,
                   "exif": source.full_metadata}, fh, indent=2, default=str)
    return path
