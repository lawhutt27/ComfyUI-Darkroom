"""
Live pixel bridge (shipped 1.31.0; spiked 2026-08-27, signed 2026-10-05:
live scopes, uint8 proxy, input + output taps -- decisions.md).

Gets real pixel data from an executing Darkroom node into the browser, which is
the blocker for eyedroppers, live scopes and qualifier matte previews. Nothing
in ComfyUI provides this today: the frontend only ever sees images that a node
deliberately wrote to disk and reported through `ui.images`.

Mechanism, in three parts:

1. `install()` wraps the FUNCTION of every Darkroom node that takes an IMAGE.
   One wrap site in `nodes/__init__.py`, not ten edits across node files.
   The wrapper is transparent: it calls the original, returns its result
   unchanged, and swallows every capture error.

2. The wrapper learns which graph node it is inside via
   `comfy_execution.utils.get_executing_context()` — a public ComfyUI API that
   is set (contextvar) around every V1 and V3 node call in execution.py. No
   `INPUT_TYPES` edit, so no hidden `UNIQUE_ID` input, so no repeat of the
   2026-08-19 stored-API-format rejection.

3. A downsampled uint8 RGB proxy is cached in memory and served raw over
   `/darkroom/pixels`. Raw bytes rather than PNG: no decode step, no browser
   colour management in the path, exact values.

Known limits, stated not hidden:
  - `node_id` is the executor's `unique_id`. Top-level nodes: identical to the
    frontend's id. Inside a subgraph it is NOT, so subgraph nodes will miss.
  - The proxy is uint8 and decimated to a 256px long edge. Fine for a scope or
    an eyedropper; it is not the pixel a full-res exporter would produce.
  - Decimation is point-sampling, not area-averaging, deliberately: averaging
    pulls a histogram toward its mean and hides clipping, which is the one
    thing a scope exists to show.
  - The cache only fills when the graph actually runs.
"""

import threading
import time
from collections import OrderedDict

from aiohttp import web
from server import PromptServer

import math
import re

MAX_DIM = 256          # long edge of the served proxy
TAPS = ("in", "out", "out1")
_NODE_ID_RE = re.compile(r"^[A-Za-z0-9:_.\-]{1,64}$")   # executor ids, incl. subgraph "a:b"
MAX_ENTRIES = 64       # LRU bound; 64 * 256*256*3 = ~12 MB worst case

_LOCK = threading.Lock()
_CACHE = OrderedDict()
_STAMP = 0

# Spike instrumentation. Cheap, and the numbers are the whole point of a spike.
STATS = {"captures": 0, "capture_ns_total": 0, "capture_ns_max": 0, "errors": 0}


def _key(node_id, tap):
    return "%s\x00%s" % (node_id, tap)


def _to_proxy(tensor):
    """(B,H,W,C) float tensor -> (rgb_bytes, w, h, src_w, src_h). None if unusable."""
    import torch

    if not isinstance(tensor, torch.Tensor):
        return None
    x = tensor
    if x.dim() == 4:
        x = x[0]                       # batch frame 0, the house convention
    if x.dim() == 2:
        x = x.unsqueeze(-1)            # a MASK arrives as (H,W)
    if x.dim() != 3:
        return None

    src_h, src_w, c = int(x.shape[0]), int(x.shape[1]), int(x.shape[2])
    if src_h == 0 or src_w == 0 or c == 0:
        return None

    step = max(1, max(src_h, src_w) // MAX_DIM)
    x = x[::step, ::step, :]
    if c == 1:
        x = x.expand(-1, -1, 3)
    else:
        x = x[:, :, :3]

    x = (x.float().clamp(0.0, 1.0) * 255.0 + 0.5).to(torch.uint8)
    x = x.contiguous().cpu()
    return bytes(x.numpy().tobytes()), int(x.shape[1]), int(x.shape[0]), src_w, src_h


def capture(node_id, tap, tensor):
    global _STAMP
    t0 = time.perf_counter_ns()
    try:
        proxy = _to_proxy(tensor)
        if proxy is None:
            return
        rgb, w, h, src_w, src_h = proxy
        with _LOCK:
            _STAMP += 1
            _CACHE[_key(node_id, tap)] = {
                "rgb": rgb, "w": w, "h": h,
                "src_w": src_w, "src_h": src_h,
                "stamp": _STAMP, "t": time.time(),
            }
            _CACHE.move_to_end(_key(node_id, tap))
            while len(_CACHE) > MAX_ENTRIES:
                _CACHE.popitem(last=False)
        STATS["captures"] += 1
    except Exception:
        STATS["errors"] += 1
    finally:
        dt = time.perf_counter_ns() - t0
        STATS["capture_ns_total"] += dt
        if dt > STATS["capture_ns_max"]:
            STATS["capture_ns_max"] = dt


# --------------------------------------------------------------------------
# the wrap
# --------------------------------------------------------------------------

def _first_image_input(cls):
    """Name of the node's first IMAGE input, or None."""
    try:
        spec = cls.INPUT_TYPES()
    except Exception:
        return None
    for section in ("required", "optional"):
        for name, decl in (spec.get(section) or {}).items():
            if isinstance(decl, (tuple, list)) and decl and decl[0] == "IMAGE":
                return name
    return None


def _unwrap_result(result, index=0):
    """A node returns a tuple, or an OUTPUT_NODE dict with a 'result' tuple."""
    if isinstance(result, dict):
        result = result.get("result")
    if isinstance(result, (tuple, list)) and len(result) > index:
        return result[index]
    return None


def install(node_class_mappings, verbose=False):
    """Wrap every IMAGE-taking Darkroom node. Idempotent. Returns wrapped names."""
    import functools
    import inspect

    from comfy_execution.utils import get_executing_context

    _NODE_CLASSES.update(node_class_mappings)
    wrapped_names = []
    for name, cls in node_class_mappings.items():
        fn_name = getattr(cls, "FUNCTION", None)
        if not fn_name:
            continue
        orig = getattr(cls, fn_name, None)
        if orig is None or getattr(orig, "_darkroom_bridged", False):
            continue
        if inspect.iscoroutinefunction(orig):
            continue                    # no async Darkroom node today; skip rather than break one
        img_arg = _first_image_input(cls)
        if img_arg is None:
            continue

        def _make(orig, img_arg):
            @functools.wraps(orig)
            def bridged(self, *args, **kwargs):
                node_id = None
                try:
                    ctx = get_executing_context()
                    node_id = ctx.node_id if ctx is not None else None
                except Exception:
                    node_id = None

                if node_id is not None:
                    src = kwargs.get(img_arg)
                    if src is None and args:
                        src = args[0]
                    if src is not None:
                        capture(node_id, "in", src)

                result = orig(self, *args, **kwargs)

                if node_id is not None:
                    out = _unwrap_result(result, 0)
                    if out is not None:
                        capture(node_id, "out", out)
                    out1 = _unwrap_result(result, 1)   # e.g. Color Qualifier's matte preview
                    if out1 is not None:
                        capture(node_id, "out1", out1)
                return result

            bridged._darkroom_bridged = True
            bridged._darkroom_img_arg = img_arg
            return bridged

        setattr(cls, fn_name, _make(orig, img_arg))
        wrapped_names.append(name)

    if verbose:
        print("[Darkroom] pixel bridge wrapped %d nodes" % len(wrapped_names))
    return wrapped_names


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

@PromptServer.instance.routes.get("/darkroom/pixels")
async def darkroom_pixels(request):
    node_id = request.query.get("node_id", "")
    tap = request.query.get("tap", "in")
    if not _NODE_ID_RE.match(node_id) or tap not in TAPS:
        return web.json_response({"error": "node_id and tap=in|out|out1 required"}, status=400)

    with _LOCK:
        entry = _CACHE.get(_key(node_id, tap))
        if entry is not None:
            _CACHE.move_to_end(_key(node_id, tap))
    if entry is None:
        return web.json_response(
            {"error": "no pixels cached for this node", "node_id": node_id, "tap": tap},
            status=404,
        )

    return web.Response(
        body=entry["rgb"],
        content_type="application/octet-stream",
        headers={
            "X-Darkroom-Width": str(entry["w"]),
            "X-Darkroom-Height": str(entry["h"]),
            "X-Darkroom-Src-Width": str(entry["src_w"]),
            "X-Darkroom-Src-Height": str(entry["src_h"]),
            "X-Darkroom-Stamp": str(entry["stamp"]),
            "Cache-Control": "no-store",
            "Access-Control-Expose-Headers": (
                "X-Darkroom-Width, X-Darkroom-Height, X-Darkroom-Src-Width, "
                "X-Darkroom-Src-Height, X-Darkroom-Stamp"
            ),
        },
    )


@PromptServer.instance.routes.get("/darkroom/pixels/index")
async def darkroom_pixels_index(_request):
    with _LOCK:
        items = [
            {
                "node_id": k.split("\x00")[0],
                "tap": k.split("\x00")[1],
                "w": v["w"], "h": v["h"],
                "src_w": v["src_w"], "src_h": v["src_h"],
                "stamp": v["stamp"], "age_s": round(time.time() - v["t"], 3),
            }
            for k, v in _CACHE.items()
        ]
    n = max(1, STATS["captures"])
    return web.json_response({
        "entries": items,
        "stats": {
            "captures": STATS["captures"],
            "errors": STATS["errors"],
            "capture_ms_mean": round(STATS["capture_ns_total"] / n / 1e6, 3),
            "capture_ms_max": round(STATS["capture_ns_max"] / 1e6, 3),
        },
        "max_dim": MAX_DIM,
    })


# --------------------------------------------------------------------------
# exact LUTs for separable nodes (live scopes)
# --------------------------------------------------------------------------
#
# A node whose grade is a pure per-channel function of the input value is
# reproduced EXACTLY by a 256-entry table harvested from its own execute() on a
# neutral ramp (measured 2026-08-27: max |diff| 0 at uint8 for Tone Curve). The
# browser applies that table to the node's input proxy on every drag, so the
# scope is live and exact without a second implementation of the grade in JS.
# Only whitelisted nodes; every value is validated against INPUT_TYPES.

_NODE_CLASSES = {}
LUT_NODES = {"DarkroomToneCurve"}


def _validate_values(cls, values):
    """Client widget values -> kwargs, or raise ValueError. IMAGE inputs, STRING
    inputs and unknown names are refused; numbers must be finite and in range."""
    spec = cls.INPUT_TYPES()
    decl = {**(spec.get("required") or {}), **(spec.get("optional") or {})}
    if not isinstance(values, dict) or len(values) > 64:
        raise ValueError("values must be an object")
    out = {}
    for name, val in values.items():
        d = decl.get(name)
        if d is None:
            raise ValueError(f"unknown input {name!r}")
        kind, opts = d[0], (d[1] if len(d) > 1 else {})
        if isinstance(kind, (list, tuple)):
            if val not in kind:
                raise ValueError(f"{name}: not an allowed value")
            out[name] = val
        elif kind in ("FLOAT", "INT"):
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
                raise ValueError(f"{name}: not a number")
            lo, hi = opts.get("min", -math.inf), opts.get("max", math.inf)
            if not lo <= val <= hi:
                raise ValueError(f"{name}: out of range")
            out[name] = int(val) if kind == "INT" else float(val)
        elif kind == "BOOLEAN":
            if not isinstance(val, bool):
                raise ValueError(f"{name}: not a boolean")
            out[name] = val
        else:
            raise ValueError(f"{name}: input type {kind} not accepted")
    return out


def harvest_lut(cls, kwargs):
    """(256 * 3) uint8 bytes: the node's output for a neutral 0..255 ramp."""
    import torch
    ramp = (torch.arange(256, dtype=torch.float32) / 255.0).view(1, 1, 256, 1).expand(1, 1, 256, 3).contiguous()
    node = cls()
    fn = getattr(node, cls.FUNCTION)
    out = _unwrap_result(fn(image=ramp, **kwargs), 0)
    x = (out[0, 0].float().clamp(0.0, 1.0) * 255.0 + 0.5).to(torch.uint8).contiguous().cpu()
    return bytes(x.numpy().tobytes())


@PromptServer.instance.routes.post("/darkroom/lut")
async def darkroom_lut(request):
    import asyncio
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    name = data.get("node") if isinstance(data, dict) else None
    if name not in LUT_NODES or name not in _NODE_CLASSES:
        return web.json_response({"error": "node not supported"}, status=400)
    cls = _NODE_CLASSES[name]
    try:
        kwargs = _validate_values(cls, data.get("values") or {})
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    try:
        body = await asyncio.to_thread(harvest_lut, cls, kwargs)
    except Exception as e:
        print(f"[Darkroom] pixel bridge: LUT harvest failed: {type(e).__name__}: {e}")
        return web.json_response({"error": "LUT harvest failed"}, status=500)
    return web.Response(body=body, content_type="application/octet-stream",
                        headers={"Cache-Control": "no-store"})
