"""Runtime detection: Colab, CPU/GPU, memory, library versions."""

from __future__ import annotations

import importlib
import os
import platform
import shutil
import sys


def in_colab():
    return "google.colab" in sys.modules or os.path.exists("/content") and "COLAB_RELEASE_TAG" in os.environ


def memory_gb():
    try:
        with open("/proc/meminfo") as f:
            info = {line.split(":")[0]: int(line.split()[1]) for line in f}
        return info["MemTotal"] / 1e6, info.get("MemAvailable", info["MemFree"]) / 1e6
    except Exception:
        return None, None


def gpu_info():
    """Report a CUDA GPU if PyTorch can see one. Darkroom Studio's engine is
    CPU (NumPy/OpenCV, multi-threaded); a GPU is only reported, see README."""
    try:
        import torch  # noqa: F401  (pre-installed on Colab; never installed by us)
        if torch.cuda.is_available():
            p = torch.cuda.get_device_properties(0)
            return {"available": True, "name": p.name, "memory_gb": round(p.total_memory / 1e9, 1)}
        return {"available": False, "reason": "torch present, no CUDA device"}
    except Exception:
        return {"available": False, "reason": "torch not installed"}


def versions(mods=("numpy", "scipy", "PIL", "cv2", "tifffile", "rawpy", "gradio", "exifread", "imageio")):
    out = {}
    for m in mods:
        try:
            mod = importlib.import_module(m)
            out[m] = getattr(mod, "__version__", "?")
        except Exception as e:
            out[m] = f"MISSING ({type(e).__name__})"
    try:
        import rawpy
        out["libraw"] = ".".join(map(str, rawpy.libraw_version))
    except Exception:
        pass
    out["exiftool"] = shutil.which("exiftool") or "not installed"
    return out


def max_safe_megapixels():
    """Rough ceiling for a full-resolution render: ~14 float32 HxWx3 buffers live at peak."""
    total, avail = memory_gb()
    if not avail:
        return None
    return avail * 1e9 / (14 * 12) / 1e6


def report():
    total, avail = memory_gb()
    import cv2
    return {
        "python": sys.version.split()[0], "platform": platform.platform(), "colab": in_colab(),
        "cpu_count": os.cpu_count(), "opencv_threads": cv2.getNumThreads(),
        "memory_total_gb": round(total, 1) if total else None,
        "memory_available_gb": round(avail, 1) if avail else None,
        "max_safe_megapixels": round(max_safe_megapixels() or 0, 1),
        "gpu": gpu_info(), "versions": versions(),
    }
