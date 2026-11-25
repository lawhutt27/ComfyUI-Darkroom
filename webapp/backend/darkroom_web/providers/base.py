"""Pluggable image-edit providers.

A provider takes ONE 8-bit sRGB RGB image (a proxy crop, never the original
file, never metadata) plus a text instruction, and returns an edited 8-bit
sRGB RGB image. It never sees or produces the full-resolution master; the
worker composites its answer back (composite.py).

To add a provider (e.g. a ComfyUI worker running Qwen-Image-Edit or Flux
Kontext), subclass `ImageEditProvider`, implement `available()` and
`edit()`, and add it to `registry()` in providers/__init__.py.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image


class ProviderError(RuntimeError):
    pass


@dataclass
class EditRequest:
    image: np.ndarray          # (h, w, 3) uint8 sRGB
    prompt: str                # full prompt, preservation rules already appended
    user_prompt: str           # what the user typed (for local/rule providers)
    mask: np.ndarray | None    # (h, w) uint8 0/255 target area, or None
    model: str = ""


@dataclass
class EditResult:
    image: np.ndarray          # (h', w', 3) uint8 sRGB (any size; it is resized/aligned later)
    model: str
    notes: str = ""


class ImageEditProvider:
    id = "base"
    label = "Base"
    sends_pixels_off_device = True
    needs_mask = False

    def available(self) -> bool:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"id": self.id, "label": self.label, "available": self.available(),
                "sends_pixels_off_device": self.sends_pixels_off_device, "needs_mask": self.needs_mask,
                "default_model": getattr(self, "model", "")}

    def edit(self, req: EditRequest) -> EditResult:
        raise NotImplementedError


def png_data_url(img_u8):
    buf = io.BytesIO()
    Image.fromarray(img_u8, "RGB").save(buf, format="PNG", optimize=False, compress_level=3)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def decode_image_bytes(b):
    try:
        with Image.open(io.BytesIO(b)) as im:
            im = im.convert("RGB")      # providers return sRGB (or untagged = sRGB)
            return np.asarray(im, dtype=np.uint8).copy()
    except Exception as e:
        raise ProviderError(f"provider returned an unreadable image ({e})") from e


def decode_data_url(url):
    if not isinstance(url, str) or not url.startswith("data:"):
        raise ProviderError("provider returned an image URL instead of inline data")
    try:
        b64 = url.split(",", 1)[1]
        return decode_image_bytes(base64.b64decode(b64))
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"could not decode provider image ({e})") from e
