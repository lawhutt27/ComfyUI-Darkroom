"""One-call setup and launch for Google Colab (also works locally).

The notebook's start cell only fetches the repository and calls `start()`.
Everything else lives here so the notebook stays short:

    from darkroom_studio.colab import start
    start(use_drive=False, public_link=True)

Steps `start()` performs, in order, each printed as a single line:
  1. environment check   (Python, CPU, RAM, GPU is reported but not needed)
  2. dependencies        (installs only what is missing; never upgrades Colab's numpy/scipy/Pillow/torch)
  3. ExifTool            (apt, for full metadata copying; optional)
  4. self-check          (imports, versions, identity render is bit-exact, ICC round trip)
  5. working folder      (Google Drive if requested, so work survives the runtime)
  6. launch              (Gradio; a public link in Colab)

Nothing here downloads models or sends photographs anywhere.
"""

from __future__ import annotations

import importlib.metadata as _md
import os
import shutil
import subprocess
import sys

STUDIO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (distribution, oldest tested version, pip spec used only when missing or older; None = never install)
_NEEDS = [
    ("numpy", "2.0.2", None),        # Colab's own; never touched
    ("scipy", "1.14.1", None),       # Colab's own; never touched
    ("pillow", "11.0.0", None),      # Colab's own; never touched
    ("tifffile", "2024.8.30", "tifffile>=2024.8.30"),
    ("rawpy", "0.24.0", "rawpy>=0.24,<0.29"),
    ("exifread", "3.0.0", "exifread>=3.0,<4"),
    ("gradio", "6.2.0", "gradio>=6.2,<7"),
]


_HELD = ["numpy", "scipy", "pillow", "opencv-python", "opencv-python-headless", "opencv-contrib-python",
         "torch", "torchvision", "jax", "tensorflow", "pandas"]


def _in_colab():
    return "google.colab" in sys.modules or "COLAB_RELEASE_TAG" in os.environ


def _have(dist):
    try:
        return _md.version(dist)
    except _md.PackageNotFoundError:
        return None


def _vt(v):
    out = []
    for p in v.split(".")[:3]:
        n = "".join(ch for ch in p if ch.isdigit())
        out.append(int(n) if n else 0)
    return tuple(out)


def _say(step, msg):
    print(f"  {step:<16s}{msg}", flush=True)


def environment():
    from .runtime import gpu_info, memory_gb
    total, avail = memory_gb()
    gpu = gpu_info()
    _say("environment", f"Python {sys.version.split()[0]} · {os.cpu_count()} CPUs · "
                        f"{(avail or 0):.0f}/{(total or 0):.0f} GB RAM free · "
                        f"{'GPU ' + gpu['name'] + ' (not needed)' if gpu.get('available') else 'CPU only (fine)'}"
                        f"{' · Colab' if _in_colab() else ''}")


def ensure_dependencies():
    """Install only missing/too-old packages. Returns the list of pip specs installed."""
    todo = [spec for dist, lo, spec in _NEEDS if spec and (_have(dist) is None or _vt(_have(dist)) < _vt(lo))]
    if not any(_have(d) for d in ("opencv-python-headless", "opencv-python", "opencv-contrib-python")):
        todo.append("opencv-python-headless>=4.10")
    for dist, lo, spec in _NEEDS:
        if spec is None and (_have(dist) is None or _vt(_have(dist)) < _vt(lo)):
            _say("", f"warning: {dist} {_have(dist)} is older than the tested {lo} (not upgraded automatically)")
    if todo:
        _say("dependencies", "installing " + ", ".join(todo) + " ...")
        # Hold everything Colab already ships at its current version, so adding gradio/rawpy can
        # never upgrade numpy, scipy, Pillow, OpenCV or torch underneath other notebooks' code.
        import tempfile
        held = [f"{d}=={_have(d)}" for d in _HELD if _have(d)]
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as cf:
            cf.write("\n".join(held) + "\n")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-c", cf.name, *todo], check=True)
        os.unlink(cf.name)
    _say("dependencies", "ok" + (" (installed " + str(len(todo)) + ")" if todo else " (nothing to install)"))
    return todo


def ensure_exiftool():
    if shutil.which("exiftool") is None and shutil.which("apt-get") and _in_colab():
        _say("ExifTool", "installing ...")
        subprocess.run("apt-get -qq update && apt-get -qq install -y libimage-exiftool-perl > /dev/null",
                       shell=True, check=False)
    path = shutil.which("exiftool")
    _say("ExifTool", "ok" if path else "not available (exports get a basic EXIF block instead)")
    return path


def self_check():
    """Fast correctness check of the core; raises if something is wrong."""
    import numpy as np
    import gradio
    import rawpy
    from . import colorspace as cms, engine, recipe as R
    if int(gradio.__version__.split(".")[0]) < 6:
        raise RuntimeError(f"Gradio {gradio.__version__} is too old; restart the runtime and run the cell again.")
    img = cms.srgb_decode(np.random.default_rng(0).random((64, 96, 3), dtype=np.float32))
    if not np.array_equal(engine.render(img, R.default_recipe(), engine.Context(cms.SRGB, (96, 64))), img):
        raise RuntimeError("engine self-check failed (identity render is not bit-exact)")
    if not cms.parse_icc(cms.ADOBE_RGB.icc_bytes()).same_primaries(cms.ADOBE_RGB):
        raise RuntimeError("ICC self-check failed")
    _say("self-check", f"ok · gradio {gradio.__version__} · numpy {np.__version__} · "
                       f"LibRaw {'.'.join(map(str, rawpy.libraw_version))}")


def working_folder(use_drive=False, workdir=None):
    if workdir is None and use_drive:
        if not _in_colab():
            raise RuntimeError("use_drive=True only works in Google Colab.")
        from google.colab import drive  # type: ignore
        if not os.path.isdir("/content/drive/MyDrive"):
            drive.mount("/content/drive")
        workdir = "/content/drive/MyDrive/Darkroom Studio"
    if workdir is None:
        workdir = "/content/darkroom_work" if _in_colab() else os.path.abspath("darkroom_work")
    os.makedirs(workdir, exist_ok=True)
    os.environ["DARKROOM_WORKDIR"] = workdir
    _say("working folder", workdir + ("" if use_drive else "  (temporary: lost when the runtime resets)"))
    return workdir


def start(use_drive=False, public_link=None, workdir=None, port=None, **launch_kw):
    """Set everything up and open the app. Safe to run again; it only redoes what is needed."""
    print("Darkroom Studio")
    environment()
    ensure_dependencies()
    ensure_exiftool()
    self_check()
    workdir = working_folder(use_drive, workdir)
    from .prompt import get_api_key
    _say("AI prompts", "OpenRouter key found (optional mode available; photos are never sent)"
         if get_api_key() else "local rules (add an OPENROUTER_API_KEY Colab secret to enable the optional LLM)")
    share = _in_colab() if public_link is None else bool(public_link)
    _say("launch", "starting ... the link appears below" + (" (public link, valid 72 h)" if share else ""))
    from .ui.app import launch
    return launch(workdir=workdir, share=share, server_port=port, **launch_kw)


def run_tests(camera=True, slow=False):
    """Run the automated test suite. camera=True downloads ~105 MB of real RAW samples once."""
    ensure_dependencies()
    if _have("pytest") is None:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pytest"], check=True)
    if camera:
        subprocess.run([sys.executable, os.path.join(STUDIO_DIR, "tools", "fetch_samples.py"),
                        os.path.join(STUDIO_DIR, "samples")], check=False)
    args = [sys.executable, "-m", "pytest", "tests", "-q", "-rs"]
    if not slow:
        args += ["-m", "not slow"]
    p = subprocess.run(args, cwd=STUDIO_DIR, capture_output=True, text=True)
    print(p.stdout[-6000:], p.stderr[-2000:])
    return p.returncode
