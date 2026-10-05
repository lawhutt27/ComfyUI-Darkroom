"""
Pixel bridge (pixel_bridge.py): wrapper transparency, proxy correctness, route
validation, and the exact-LUT contract that makes Tone Curve's live scope exact.

Executions are simulated with ComfyUI's own CurrentNodeContext, the context
manager execution.py wraps around every node call, so the wrapper learns its
node id exactly as it does in a real run.

Run: python.exe tools/test_pixel_bridge.py
"""

import asyncio
import json
import os
import sys
import types

import numpy as np
import torch
from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.dirname(HERE)
COMFY = r"F:\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable\ComfyUI"
for p in (COMFY, os.path.dirname(PACK)):
    if p not in sys.path:
        sys.path.insert(0, p)
import server as _server_mod  # noqa: E402
if getattr(_server_mod.PromptServer, "instance", None) is None:
    _server_mod.PromptServer.instance = types.SimpleNamespace(routes=web.RouteTableDef())
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("darkroom_pack", os.path.join(PACK, "__init__.py"),
                                               submodule_search_locations=[PACK])
_pack = importlib.util.module_from_spec(_spec)
sys.modules["darkroom_pack"] = _pack
_spec.loader.exec_module(_pack)
import darkroom_pack.pixel_bridge as PB  # noqa: E402
from comfy_execution.utils import CurrentNodeContext  # noqa: E402

NCM = _pack.NODE_CLASS_MAPPINGS
PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")
    return ok


def get(handler, query):
    req = types.SimpleNamespace(query=query)
    r = asyncio.run(handler(req))
    return r.status, r.body if isinstance(r.body, (bytes, bytearray)) else r.text


def post(handler, body):
    req = types.SimpleNamespace()

    async def _json():
        if isinstance(body, Exception):
            raise body
        return body
    req.json = _json
    r = asyncio.run(handler(req))
    return r.status, (r.body if isinstance(r.body, (bytes, bytearray)) else r.text)


def run_node(key, node_id, **kw):
    cls = NCM[key]
    with CurrentNodeContext("test-prompt", node_id, 0):
        return getattr(cls(), cls.FUNCTION)(**kw)


def proxy_of(t):
    x = t[0]
    step = max(1, max(x.shape[0], x.shape[1]) // PB.MAX_DIM)
    x = x[::step, ::step, :3]
    return (x.float().clamp(0, 1) * 255 + 0.5).to(torch.uint8).numpy().tobytes()


# 8-bit photo-like input (LoadImage gives k/255 values), large enough to decimate
rng = np.random.default_rng(4)
IMG8 = torch.from_numpy(rng.integers(0, 256, (1, 300, 420, 3)).astype(np.float32) / 255.0)
TC = {"preset": "Custom (manual)", "shadows": -12.0, "darks": 8.0, "midtones": 15.0, "lights": -6.0,
      "highlights": 10.0, "red_shadows": 5.0, "red_highlights": -4.0, "green_shadows": 0.0,
      "green_highlights": 3.0, "blue_shadows": -6.0, "blue_highlights": 2.0, "strength": 1.0}


def wrapper():
    wrapped = [k for k, c in NCM.items() if getattr(getattr(c, c.FUNCTION, None), "_darkroom_bridged", False)]
    check(f"bridge wraps the IMAGE nodes ({len(wrapped)})", len(wrapped) >= 56)
    cls = NCM["DarkroomToneCurve"]
    orig = getattr(cls, cls.FUNCTION).__wrapped__
    a = run_node("DarkroomToneCurve", "7", image=IMG8, **TC)[0]
    b = orig(cls(), image=IMG8, **TC)[0]
    check("wrapped output is bitwise the unwrapped output", torch.equal(a, b))
    s, body = get(PB.darkroom_pixels, {"node_id": "7", "tap": "in"})
    check("tap 'in' served", s == 200 and body == proxy_of(IMG8), f"status {s}")
    s, body = get(PB.darkroom_pixels, {"node_id": "7", "tap": "out"})
    check("tap 'out' is the node's real output", s == 200 and body == proxy_of(a), f"status {s}")
    q = run_node("DarkroomColorQualifier", "9", image=IMG8)
    s, body = get(PB.darkroom_pixels, {"node_id": "9", "tap": "out1"})
    check("tap 'out1' carries Color Qualifier's matte preview", s == 200 and body == proxy_of(q[1]), f"status {s}")
    # outside an execution (e.g. the LUT harvest) nothing is captured
    before = PB.STATS["captures"]
    getattr(cls(), cls.FUNCTION)(image=IMG8, **TC)
    check("no capture outside an execution context", PB.STATS["captures"] == before)


def routes():
    for q, want in (({"node_id": "7", "tap": "bogus"}, 400), ({"node_id": "", "tap": "in"}, 400),
                    ({"node_id": "x" * 65, "tap": "in"}, 400), ({"node_id": "../7", "tap": "in"}, 400),
                    ({"node_id": "999", "tap": "in"}, 404), ({"node_id": "12:3", "tap": "out"}, 404)):
        s, body = get(PB.darkroom_pixels, q)
        check(f"pixels {q} -> {want}", s == want, f"got {s}")
        if s >= 400:
            check(f"pixels {q}: no exception text", "Traceback" not in str(body) and "Error:" not in str(body))
    bad = [
        ({"node": "DarkroomHalation", "values": {}}, "non-whitelisted node"),
        ({"node": "DarkroomToneCurve", "values": {"shadows": 9999}}, "out of range"),
        ({"node": "DarkroomToneCurve", "values": {"shadows": float("nan")}}, "NaN"),
        ({"node": "DarkroomToneCurve", "values": {"shadows": True}}, "bool as number"),
        ({"node": "DarkroomToneCurve", "values": {"image": 1}}, "IMAGE input"),
        ({"node": "DarkroomToneCurve", "values": {"nope": 1}}, "unknown input"),
        ({"node": "DarkroomToneCurve", "values": {"preset": "../../etc"}}, "bad combo value"),
        ({"node": "DarkroomToneCurve", "values": "x"}, "values not an object"),
        (ValueError("bad json"), "invalid JSON"),
    ]
    for body, what in bad:
        s, _ = post(PB.darkroom_lut, body)
        check(f"lut rejects {what}", s == 400, f"got {s}")


def lut_exact():
    for name, values in (("manual curve", TC), ("preset", {"preset": PB._NODE_CLASSES["DarkroomToneCurve"].INPUT_TYPES()["required"]["preset"][0][2]})):
        s, body = post(PB.darkroom_lut, {"node": "DarkroomToneCurve", "values": values})
        if not check(f"lut {name}: 768 bytes", s == 200 and len(body) == 768, f"status {s}"):
            continue
        lut = np.frombuffer(body, np.uint8).reshape(256, 3)
        out = run_node("DarkroomToneCurve", "7", image=IMG8, **values)[0][0].numpy()
        node_u8 = (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)
        idx = np.round(IMG8[0].numpy() * 255).astype(np.int64)
        via_lut = np.stack([lut[idx[..., c], c] for c in range(3)], -1)
        diff = np.abs(via_lut.astype(int) - node_u8.astype(int)).max()
        check(f"lut {name}: LUT(input) == node output, every pixel", diff == 0, f"max diff {diff}")
        check(f"lut {name}: not identity (the check has something to prove)", np.abs(lut.astype(int) - np.arange(256)[:, None]).max() > 2)
        # channel-swap control: only meaningful when the channels' curves differ
        # (a neutral preset gives three identical columns)
        if np.abs(lut[:, 0].astype(int) - lut[:, 1].astype(int)).max() > 0:
            swapped = np.stack([lut[idx[..., c], (c + 1) % 3] for c in range(3)], -1)
            check(f"lut {name}: channel-swap negative control fires",
                  np.abs(swapped.astype(int) - node_u8.astype(int)).max() > 0)


if __name__ == "__main__":
    for n, f in (("wrapper + taps", wrapper), ("route validation", routes), ("exact LUT", lut_exact)):
        print(f"[{n}]")
        f()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
