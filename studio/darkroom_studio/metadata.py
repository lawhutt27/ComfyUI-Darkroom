"""
Photo metadata: reading (ExifTool preferred, exifread fallback), camera
identification for the two target bodies, and EXIF preparation for export.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction

EXIFTOOL = shutil.which("exiftool")

KNOWN_CAMERAS = {
    "nikon d5300": "Nikon D5300",
    "canon eos 1000d": "Canon EOS Rebel XS / 1000D",
    "canon eos rebel xs": "Canon EOS Rebel XS / 1000D",
    "canon eos kiss f": "Canon EOS Rebel XS / 1000D",
}

_NUMERIC_TAGS = ["ExposureTime", "FNumber", "ISO", "FocalLength", "FocalLengthIn35mmFormat",
                 "ExposureCompensation", "Orientation", "ColorSpace", "ImageWidth", "ImageHeight"]


def exiftool_available():
    return EXIFTOOL is not None


def _run_exiftool(args, timeout=30):
    out = subprocess.run([EXIFTOOL, *args], capture_output=True, timeout=timeout)
    if out.returncode not in (0, 1):
        raise RuntimeError(out.stderr.decode(errors="replace"))
    return out.stdout


def read_exiftool(path):
    """Return (formatted_dict, numeric_dict) via ExifTool, or (None, None)."""
    if not EXIFTOOL:
        return None, None
    try:
        fmt = json.loads(_run_exiftool(["-j", "-a", "-G1", "-struct", "-api", "largefilesupport=1", path]))[0]
        raw = json.loads(_run_exiftool(["-j", "-n", "-a", "-G1", *[f"-{t}" for t in _NUMERIC_TAGS],
                                        "-InteropIndex", path]))[0]
        return fmt, _prefer_standard(raw)
    except Exception as e:  # pragma: no cover - depends on host binary
        print(f"[Darkroom] exiftool failed: {e}")
        return None, None


# Standard EXIF/TIFF groups win over maker notes: with -n, maker-note values can be
# vendor codes (a Pentax K-x stores ISO 2000 as the index 19 in Pentax:ISO).
_GROUP_PRIORITY = ("ExifIFD", "IFD0", "SubIFD", "InteropIFD", "XMP-exif", "XMP-tiff", "Composite")


def _prefer_standard(grouped):
    out = {}
    for key, v in grouped.items():
        if ":" not in key:
            continue
        group, tag = key.split(":", 1)
        rank = _GROUP_PRIORITY.index(group) if group in _GROUP_PRIORITY else len(_GROUP_PRIORITY)
        if v in (None, "") or (tag in out and out[tag][0] <= rank):
            continue
        out[tag] = (rank, v)
    return {t: v for t, (_, v) in out.items()}


def _first(d, *keys):
    """First non-empty value among keys, matching with or without a group prefix."""
    for k in keys:
        for dk, v in d.items():
            if dk == k or dk.split(":")[-1] == k:
                if v not in (None, "", "Unknown", "n/a"):
                    return v
    return None


def _ratio(v):
    try:
        if hasattr(v, "num"):
            return v.num / v.den if v.den else 0.0
        if isinstance(v, str) and "/" in v:
            return float(Fraction(v))
        return float(v)
    except Exception:
        return None


def _shutter_str(s):
    if not s:
        return ""
    if s >= 1:
        return f"{s:g} s"
    return f"1/{round(1 / s)} s"


def read_exifread(path):
    try:
        import exifread
    except ImportError:
        return {}
    with open(path, "rb") as fh:
        try:
            tags = exifread.process_file(fh, details=True)
        except Exception:
            return {}
    return {k: v for k, v in tags.items() if k not in ("JPEGThumbnail", "TIFFThumbnail")}


def read_metadata(path):
    """Unified metadata summary dict + full dump for the sidecar."""
    fmt, num = read_exiftool(path)
    info = {"reader": None, "camera_make": "", "camera_model": "", "camera": "", "known_camera": None,
            "lens": "", "iso": None, "aperture": None, "shutter_s": None, "shutter": "",
            "focal_length": None, "focal_length_35mm": None, "exposure_comp": None,
            "datetime": "", "flash": "", "metering": "", "white_balance": "", "orientation": 1,
            "exif_colorspace": None, "interop_index": None, "software": "", "has_gps": False}
    full = {}
    if fmt is not None:
        info["reader"] = f"ExifTool {fmt.get('ExifTool:ExifToolVersion', '')}".strip()
        full = fmt
        info["camera_make"] = str(_first(fmt, "Make") or "")
        info["camera_model"] = str(_first(fmt, "Model") or "")
        info["lens"] = str(_first(fmt, "LensID", "LensModel", "Lens", "LensType") or "")
        info["iso"] = num.get("ISO")
        info["aperture"] = num.get("FNumber")
        info["shutter_s"] = num.get("ExposureTime")
        info["focal_length"] = num.get("FocalLength")
        info["focal_length_35mm"] = num.get("FocalLengthIn35mmFormat")
        info["exposure_comp"] = num.get("ExposureCompensation")
        info["datetime"] = str(_first(fmt, "DateTimeOriginal", "CreateDate", "ModifyDate") or "")
        info["flash"] = str(_first(fmt, "Flash") or "")
        info["metering"] = str(_first(fmt, "MeteringMode") or "")
        info["white_balance"] = str(_first(fmt, "WhiteBalance") or "")
        info["orientation"] = int(num.get("Orientation") or 1)
        info["exif_colorspace"] = num.get("ColorSpace")
        info["interop_index"] = num.get("InteropIndex")
        info["software"] = str(_first(fmt, "Software") or "")
        info["has_gps"] = any(k.startswith("GPS") or ":GPS" in k for k in fmt)
    else:
        tags = read_exifread(path)
        if tags:
            info["reader"] = "exifread (ExifTool not installed: lens names may be incomplete)"
            g = lambda k: tags.get(k)  # noqa: E731
            info["camera_make"] = str(g("Image Make") or "").strip()
            info["camera_model"] = str(g("Image Model") or "").strip()
            lens = g("EXIF LensModel") or g("MakerNote LensModel") or g("MakerNote LensType")
            if lens is None and g("MakerNote LensMinMaxFocalMaxAperture") is not None:
                v = g("MakerNote LensMinMaxFocalMaxAperture").values
                try:
                    f = [float(_ratio(x)) for x in v]
                    lens = f"{f[0]:g}-{f[1]:g}mm f/{f[2]:g}-{f[3]:g}"
                except Exception:
                    lens = None
            info["lens"] = str(lens or "")
            iso = g("EXIF ISOSpeedRatings")
            info["iso"] = int(str(iso)) if iso is not None and str(iso).isdigit() else None
            fn = g("EXIF FNumber")
            info["aperture"] = _ratio(fn.values[0]) if fn is not None else None
            et = g("EXIF ExposureTime")
            info["shutter_s"] = _ratio(et.values[0]) if et is not None else None
            fl = g("EXIF FocalLength")
            info["focal_length"] = _ratio(fl.values[0]) if fl is not None else None
            fl35 = g("EXIF FocalLengthIn35mmFilm")
            info["focal_length_35mm"] = _ratio(fl35.values[0]) if fl35 is not None else None
            info["datetime"] = str(g("EXIF DateTimeOriginal") or g("Image DateTime") or "")
            info["flash"] = str(g("EXIF Flash") or "")
            info["metering"] = str(g("EXIF MeteringMode") or "")
            o = g("Image Orientation")
            info["orientation"] = int(o.values[0]) if o is not None else 1
            cs = g("EXIF ColorSpace")
            info["exif_colorspace"] = int(cs.values[0]) if cs is not None else None
            ii = g("Interoperability InteroperabilityIndex")
            info["interop_index"] = str(ii) if ii is not None else None
            info["software"] = str(g("Image Software") or "")
            info["has_gps"] = any(k.startswith("GPS") for k in tags)
            full = {k: str(v)[:200] for k, v in tags.items()}
        else:
            info["reader"] = "none (no EXIF found)"
    info["shutter"] = _shutter_str(info["shutter_s"])
    make = info["camera_make"].strip()
    model = info["camera_model"].strip()
    if make and model.upper().startswith(make.split()[0].upper()):
        info["camera"] = model
    else:
        info["camera"] = f"{make} {model}".strip()
    key = info["camera"].lower().replace("corporation", "").replace("  ", " ").strip()
    for k, v in KNOWN_CAMERAS.items():
        if k in key or k.replace("canon ", "") in key.replace("canon ", ""):
            info["known_camera"] = v
            break
    return info, full


def format_summary(info):
    """Markdown block for the left panel."""
    def v(x, fmt="{}", empty="—"):
        return fmt.format(x) if x not in (None, "", 0) else empty
    lines = [
        f"**Camera** {v(info.get('camera'))}" + (f"  \n*Recognised: {info['known_camera']}*" if info.get("known_camera") else ""),
        f"**Lens** {v(info.get('lens'))}",
        f"**ISO** {v(info.get('iso'))} · **Aperture** {v(info.get('aperture'), 'f/{:g}')} · "
        f"**Shutter** {v(info.get('shutter'))}",
        f"**Focal length** {v(info.get('focal_length'), '{:g} mm')}"
        + (f" ({info['focal_length_35mm']:g} mm eq.)" if info.get("focal_length_35mm") else ""),
        f"**Captured** {v(info.get('datetime'))}",
        f"**Exposure comp.** {v(info.get('exposure_comp'), '{:+g} EV', '0 EV')} · **Flash** {v(info.get('flash'))}",
        f"<sub>Metadata read with {info.get('reader')}</sub>",
    ]
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Export-side helpers
# ---------------------------------------------------------------------------

def copy_metadata_exiftool(src, dst, width, height, software="Darkroom Studio"):
    """Copy all copyable metadata from src to dst with ExifTool, excluding the
    ICC profile (dst already carries the correct one), normalising orientation
    and dimension tags. Returns (ok, message)."""
    if not EXIFTOOL:
        return False, "ExifTool not installed"
    args = ["-overwrite_original", "-m", "-q", "-q", "-TagsFromFile", src, "-all:all", "--ICC_Profile:all",
            "--Orientation", "--ThumbnailImage", "--PreviewImage", "--JpgFromRaw", "--OtherImage",
            "-Orientation#=1", f"-ExifImageWidth={width}", f"-ExifImageHeight={height}",
            f"-Software={software}", dst]
    try:
        out = subprocess.run([EXIFTOOL, *args], capture_output=True, timeout=60)
    except Exception as e:  # pragma: no cover
        return False, str(e)
    if out.returncode != 0:
        return False, out.stderr.decode(errors="replace")[:300]
    return True, "metadata copied with ExifTool"


def build_basic_exif(info, width, height, software="Darkroom Studio"):
    """Fallback EXIF (Pillow) when ExifTool is unavailable."""
    from PIL import Image
    from PIL.TiffImagePlugin import IFDRational

    def rat(v, d):
        f = Fraction(v).limit_denominator(d)
        return IFDRational(f.numerator, f.denominator)

    exif = Image.Exif()
    if info.get("camera_make"):
        exif[0x010F] = info["camera_make"]
    if info.get("camera_model"):
        exif[0x0110] = info["camera_model"]
    exif[0x0131] = software
    exif[0x0112] = 1
    if info.get("datetime"):
        exif[0x0132] = info["datetime"][:19]
    ifd = {}   # assigned as a dict below: works on old and new Pillow
    if info.get("shutter_s"):
        ifd[0x829A] = rat(info["shutter_s"], 100000)
    if info.get("aperture"):
        ifd[0x829D] = rat(info["aperture"], 100)
    if info.get("iso"):
        ifd[0x8827] = int(info["iso"])
    if info.get("focal_length"):
        ifd[0x920A] = rat(info["focal_length"], 100)
    if info.get("datetime"):
        ifd[0x9003] = info["datetime"][:19]
    if info.get("lens"):
        ifd[0xA434] = info["lens"]
    ifd[0xA002] = int(width)
    ifd[0xA003] = int(height)
    exif[0x8769] = ifd
    return exif.tobytes()
