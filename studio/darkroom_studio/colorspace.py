"""
Colour management for Darkroom Studio.

Design (see docs/COLOR_MANAGEMENT.md for the long version):

* Every image is described by an RGB colour space = (primaries matrix to
  PCS XYZ/D50, transfer function).  Matrix/TRC ICC profiles (sRGB, Adobe RGB,
  Display P3, ProPhoto, Rec.2020 and most camera-embedded profiles) are parsed
  directly so the conversion happens in float32, not through an 8-bit CMM.
* The *working space* is linear light in the SOURCE primaries.  We decode the
  transfer curve once on load and never re-encode until export.  No gamut
  conversion is done unless the user asks for a different output space, which
  is what "avoid unnecessary colour-space conversions" means in practice.
* "Same as source" export re-encodes with the source TRC and embeds the
  original ICC bytes unchanged, so an identity edit round-trips.
* Values outside [0, 1] are kept through the whole pipeline; they are clipped
  only once, at the final quantisation, and the clipped fraction is reported.
"""

from __future__ import annotations

import hashlib
import struct
import time
from dataclasses import dataclass, field

import numpy as np

D50 = np.array([0.9642, 1.0, 0.8249], dtype=np.float64)
D65_XY = (0.3127, 0.3290)
D50_XY = (0.3457, 0.3585)

_BRADFORD = np.array([[0.8951, 0.2664, -0.1614],
                      [-0.7502, 1.7135, 0.0367],
                      [0.0389, -0.0685, 1.0296]], dtype=np.float64)


def xy_to_XYZ(x, y, Y=1.0):
    return np.array([x * Y / y, Y, (1.0 - x - y) * Y / y], dtype=np.float64)


def bradford_adapt(src_white_XYZ, dst_white_XYZ):
    """3x3 Bradford chromatic-adaptation matrix (XYZ -> XYZ)."""
    s = _BRADFORD @ src_white_XYZ
    d = _BRADFORD @ dst_white_XYZ
    return np.linalg.inv(_BRADFORD) @ np.diag(d / s) @ _BRADFORD


def primaries_to_matrix(rxy, gxy, bxy, wxy):
    """RGB->XYZ matrix at the native white, then Bradford-adapted to D50."""
    P = np.stack([xy_to_XYZ(*rxy), xy_to_XYZ(*gxy), xy_to_XYZ(*bxy)], axis=1)
    W = xy_to_XYZ(*wxy)
    S = np.linalg.solve(P, W)
    M_native = P * S[np.newaxis, :]
    return bradford_adapt(W, D50) @ M_native


# ---------------------------------------------------------------------------
# Transfer functions.  All are sign-preserving and extend past 1.0 so that
# out-of-range working values survive an encode/decode round trip.
# ---------------------------------------------------------------------------

def _spow(x, p):
    return np.sign(x) * np.power(np.abs(x), p)


def srgb_decode(v):
    v = np.asarray(v, dtype=np.float32)
    if v.size and v.min() >= 0:                   # fast path (no sign handling)
        hi = np.power((v + np.float32(0.055)) * np.float32(1 / 1.055), np.float32(2.4))
        return np.where(v <= 0.04045, v * np.float32(1 / 12.92), hi).astype(np.float32, copy=False)
    a = np.abs(v)
    out = np.where(a <= 0.04045, a / 12.92, np.power((a + 0.055) / 1.055, 2.4))
    return (np.sign(v) * out).astype(np.float32)


def srgb_encode(v):
    v = np.asarray(v, dtype=np.float32)
    if v.size and v.min() >= 0:
        hi = np.power(v, np.float32(1 / 2.4))
        hi *= np.float32(1.055)
        hi -= np.float32(0.055)
        return np.where(v <= 0.0031308, v * np.float32(12.92), hi).astype(np.float32, copy=False)
    a = np.abs(v)
    out = np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(a, 1.0 / 2.4) - 0.055)
    return (np.sign(v) * out).astype(np.float32)


def romm_decode(v):  # ProPhoto / ROMM RGB
    v = np.asarray(v, dtype=np.float32)
    a = np.abs(v)
    out = np.where(a < 16.0 / 512.0, a / 16.0, np.power(a, 1.8))
    return (np.sign(v) * out).astype(np.float32)


def romm_encode(v):
    v = np.asarray(v, dtype=np.float32)
    a = np.abs(v)
    out = np.where(a < 1.0 / 512.0, a * 16.0, np.power(a, 1.0 / 1.8))
    return (np.sign(v) * out).astype(np.float32)


@dataclass
class Transfer:
    """Transfer function (encoded <-> linear)."""
    kind: str                       # 'srgb' | 'gamma' | 'linear' | 'romm' | 'table'
    gamma: float = 1.0
    table: np.ndarray | None = None  # forward samples (encoded->linear) on [0,1]

    def decode(self, v):
        if self.kind == "linear":
            return np.asarray(v, dtype=np.float32)
        if self.kind == "srgb":
            return srgb_decode(v)
        if self.kind == "romm":
            return romm_decode(v)
        if self.kind == "gamma":
            return _spow(np.asarray(v, dtype=np.float32), self.gamma).astype(np.float32)
        # sampled table: monotonic curve; extend linearly past the ends.
        v = np.asarray(v, dtype=np.float32)
        xs = np.linspace(0.0, 1.0, len(self.table), dtype=np.float32)
        out = np.interp(np.clip(v, 0, 1), xs, self.table).astype(np.float32)
        slope = (self.table[-1] - self.table[-2]) * (len(self.table) - 1)
        out = np.where(v > 1, self.table[-1] + (v - 1) * slope, out)
        return out.astype(np.float32)

    def encode(self, v):
        if self.kind == "linear":
            return np.asarray(v, dtype=np.float32)
        if self.kind == "srgb":
            return srgb_encode(v)
        if self.kind == "romm":
            return romm_encode(v)
        if self.kind == "gamma":
            return _spow(np.asarray(v, dtype=np.float32), 1.0 / self.gamma).astype(np.float32)
        v = np.asarray(v, dtype=np.float32)
        xs = np.linspace(0.0, 1.0, len(self.table), dtype=np.float32)
        tab = np.maximum.accumulate(self.table)
        out = np.interp(np.clip(v, tab[0], tab[-1]), tab, xs).astype(np.float32)
        slope = (tab[-1] - tab[-2]) * (len(tab) - 1)
        out = np.where(v > tab[-1], 1.0 + (v - tab[-1]) / max(slope, 1e-6), out)
        return out.astype(np.float32)

    def describe(self):
        if self.kind == "gamma":
            return f"gamma {self.gamma:.4g}"
        if self.kind == "table":
            return f"sampled curve ({len(self.table)} pts)"
        return {"srgb": "sRGB piecewise", "linear": "linear", "romm": "ROMM (1.8)"}[self.kind]


@dataclass
class ColorSpace:
    name: str
    to_xyz_d50: np.ndarray          # 3x3, linear RGB -> XYZ (D50 PCS)
    transfer: Transfer
    icc: bytes | None = None        # original/embedded ICC bytes (if any)
    source: str = "builtin"         # 'builtin' | 'embedded ICC' | 'assumed' | 'EXIF'
    notes: list = field(default_factory=list)

    @property
    def luma(self):
        """Relative-luminance weights for linear RGB in this space."""
        return self.to_xyz_d50[1].astype(np.float32)

    def matrix_to(self, other: "ColorSpace"):
        return (np.linalg.inv(other.to_xyz_d50) @ self.to_xyz_d50).astype(np.float32)

    def same_primaries(self, other: "ColorSpace", tol=2e-3):
        return np.allclose(self.to_xyz_d50, other.to_xyz_d50, atol=tol)

    def is_srgb_like(self):
        return self.same_primaries(SRGB) and self.transfer.kind == "srgb"

    def icc_bytes(self):
        """ICC profile to embed for this space (original bytes if we have them)."""
        if self.icc:
            return self.icc
        return build_icc_profile(self)

    def summary(self):
        return f"{self.name} [{self.transfer.describe()}; {self.source}]"


def _builtin(name, prim, white, transfer):
    return ColorSpace(name, primaries_to_matrix(*prim, white), transfer)


SRGB = _builtin("sRGB IEC61966-2.1", ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06)), D65_XY, Transfer("srgb"))
LINEAR_SRGB = _builtin("Linear sRGB (Rec.709)", ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06)), D65_XY, Transfer("linear"))
ADOBE_RGB = _builtin("Adobe RGB (1998)", ((0.64, 0.33), (0.21, 0.71), (0.15, 0.06)), D65_XY,
                     Transfer("gamma", gamma=563.0 / 256.0))
DISPLAY_P3 = _builtin("Display P3", ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060)), D65_XY, Transfer("srgb"))
PROPHOTO = _builtin("ProPhoto RGB (ROMM)", ((0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)), D50_XY,
                    Transfer("romm"))
REC2020 = _builtin("Rec.2020 (gamma 2.2 encoding)", ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046)), D65_XY,
                   Transfer("gamma", gamma=2.2))

OUTPUT_SPACES = {
    "sRGB": SRGB,
    "Adobe RGB (1998)": ADOBE_RGB,
    "Display P3": DISPLAY_P3,
    "ProPhoto RGB": PROPHOTO,
}


# ---------------------------------------------------------------------------
# ICC parsing (matrix/TRC profiles) and writing (v2 matrix/TRC)
# ---------------------------------------------------------------------------

def _s15f16(b):
    return struct.unpack(">i", b)[0] / 65536.0


def _parse_curve(data):
    sig = data[:4]
    if sig == b"curv":
        n = struct.unpack(">I", data[8:12])[0]
        if n == 0:
            return Transfer("linear")
        if n == 1:
            return Transfer("gamma", gamma=struct.unpack(">H", data[12:14])[0] / 256.0)
        vals = np.frombuffer(data[12:12 + 2 * n], dtype=">u2").astype(np.float32) / 65535.0
        return Transfer("table", table=vals)
    if sig == b"para":
        ftype = struct.unpack(">H", data[8:10])[0]
        nparams = {0: 1, 1: 3, 2: 4, 3: 5, 4: 7}[ftype]
        p = [_s15f16(data[12 + 4 * i:16 + 4 * i]) for i in range(nparams)]
        g = p[0]
        x = np.linspace(0, 1, 4096, dtype=np.float64)
        if ftype == 0:
            y = x ** g
        elif ftype == 1:
            a, b = p[1], p[2]
            y = np.where(x >= -b / a, np.clip(a * x + b, 0, None) ** g, 0)
        elif ftype == 2:
            a, b, c = p[1], p[2], p[3]
            y = np.where(x >= -b / a, np.clip(a * x + b, 0, None) ** g + c, c)
        elif ftype == 3:
            a, b, c, d = p[1], p[2], p[3], p[4]
            y = np.where(x >= d, np.clip(a * x + b, 0, None) ** g, c * x)
        else:
            a, b, c, d, e, f = p[1:7]
            y = np.where(x >= d, np.clip(a * x + b, 0, None) ** g + e, c * x + f)
        if ftype == 0:
            return Transfer("gamma", gamma=g)
        # Recognise the sRGB parametric curve
        srgb = srgb_decode(x.astype(np.float32))
        if np.max(np.abs(y - srgb)) < 2e-3:
            return Transfer("srgb")
        return Transfer("table", table=y.astype(np.float32))
    raise ValueError(f"unsupported curve type {sig!r}")


def _parse_text(data):
    sig = data[:4]
    try:
        if sig == b"desc":
            n = struct.unpack(">I", data[8:12])[0]
            return data[12:12 + n].split(b"\x00")[0].decode("latin-1", "replace")
        if sig == b"mluc":
            count = struct.unpack(">I", data[8:12])[0]
            if count:
                ln, off = struct.unpack(">II", data[20:28])
                return data[off:off + ln].decode("utf-16-be", "replace")
        if sig == b"text":
            return data[8:].split(b"\x00")[0].decode("latin-1", "replace")
    except Exception:
        pass
    return ""


def parse_icc(icc: bytes) -> ColorSpace:
    """Build a ColorSpace from an ICC profile. Raises ValueError for profiles
    that are not RGB matrix/TRC (e.g. LUT-based or CMYK)."""
    if len(icc) < 132:
        raise ValueError("ICC profile too short")
    colorspace = icc[16:20]
    if colorspace != b"RGB ":
        raise ValueError(f"ICC colour space is {colorspace.decode(errors='replace').strip()}, not RGB")
    count = struct.unpack(">I", icc[128:132])[0]
    tags = {}
    for i in range(count):
        sig, off, size = struct.unpack(">4sII", icc[132 + 12 * i:144 + 12 * i])
        tags[sig] = icc[off:off + size]
    needed = [b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC", b"gTRC", b"bTRC"]
    if not all(t in tags for t in needed):
        raise ValueError("ICC profile is not a matrix/TRC profile (LUT-based profiles are not parsed)")

    def xyz(tag):
        d = tags[tag]
        return np.array([_s15f16(d[8:12]), _s15f16(d[12:16]), _s15f16(d[16:20])])

    M = np.stack([xyz(b"rXYZ"), xyz(b"gXYZ"), xyz(b"bXYZ")], axis=1)
    curves = [_parse_curve(tags[t]) for t in (b"rTRC", b"gTRC", b"bTRC")]
    name = _parse_text(tags.get(b"desc", b"")) or "Embedded ICC profile"
    notes = []
    c0 = curves[0]
    if not all(c.kind == c0.kind and abs(c.gamma - c0.gamma) < 1e-4 for c in curves):
        notes.append("Per-channel TRCs differ; the red-channel TRC is used for all channels.")
    cs = ColorSpace(name, M, c0, icc=icc, source="embedded ICC", notes=notes)
    if c0.kind == "table":
        x = np.linspace(0, 1, len(c0.table), dtype=np.float32)
        if np.max(np.abs(c0.table - srgb_decode(x))) < 2e-3:
            cs.transfer = Transfer("srgb")
    return cs


def _s15(v):
    return struct.pack(">i", int(round(v * 65536.0)))


def _pad4(b):
    return b + b"\x00" * ((4 - len(b) % 4) % 4)


def build_icc_profile(cs: ColorSpace, description: str | None = None) -> bytes:
    """Write a v2.1 display-class matrix/TRC ICC profile for `cs`."""
    desc = (description or cs.name).encode("ascii", "replace")
    desc_tag = b"desc" + b"\x00" * 4 + struct.pack(">I", len(desc) + 1) + desc + b"\x00"
    desc_tag += b"\x00" * 8 + b"\x00" * 3 + b"\x00" * 67
    cprt_tag = b"text" + b"\x00" * 4 + b"No copyright, use freely (generated by Darkroom Studio)\x00"

    def xyz_tag(v):
        return b"XYZ " + b"\x00" * 4 + b"".join(_s15(c) for c in v)

    t = cs.transfer
    if t.kind == "linear":
        trc = b"curv" + b"\x00" * 4 + struct.pack(">I", 0)
    elif t.kind == "gamma":
        trc = b"curv" + b"\x00" * 4 + struct.pack(">I", 1) + struct.pack(">H", int(round(t.gamma * 256)))
    else:
        x = np.linspace(0, 1, 1024, dtype=np.float32)
        y = np.clip(t.decode(x), 0, 1)
        trc = b"curv" + b"\x00" * 4 + struct.pack(">I", 1024) + (np.round(y * 65535).astype(">u2")).tobytes()

    M = cs.to_xyz_d50
    entries = [
        (b"desc", desc_tag), (b"cprt", cprt_tag), (b"wtpt", xyz_tag(D50)),
        (b"rXYZ", xyz_tag(M[:, 0])), (b"gXYZ", xyz_tag(M[:, 1])), (b"bXYZ", xyz_tag(M[:, 2])),
        (b"rTRC", trc), (b"gTRC", trc), (b"bTRC", trc),
    ]
    n = len(entries)
    offset = 128 + 4 + 12 * n
    table = b""
    body = b""
    trc_off = None
    for sig, data in entries:
        if sig in (b"gTRC", b"bTRC") and trc_off is not None:
            table += struct.pack(">4sII", sig, trc_off, len(trc))
            continue
        data_p = _pad4(data)
        table += struct.pack(">4sII", sig, offset + len(body), len(data))
        if sig == b"rTRC":
            trc_off = offset + len(body)
        body += data_p
    size = 128 + 4 + len(table) + len(body)
    lt = time.gmtime()
    header = struct.pack(">I", size) + b"\x00" * 4 + struct.pack(">I", 0x02100000)
    header += b"mntr" + b"RGB " + b"XYZ "
    header += struct.pack(">6H", lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min, lt.tm_sec)
    header += b"acsp" + b"\x00" * 4 + struct.pack(">I", 0) + b"\x00" * 4 + b"\x00" * 4
    header += b"\x00" * 8 + struct.pack(">I", 0)
    header += _s15(D50[0]) + _s15(D50[1]) + _s15(D50[2])
    header += b"\x00" * 4 + b"\x00" * 16 + b"\x00" * 28
    assert len(header) == 128, len(header)
    return header + struct.pack(">I", n) + table + body


def identify_source_space(icc: bytes | None, exif_colorspace: int | None = None,
                          interop_index: str | None = None) -> ColorSpace:
    """Choose the source colour space for a raster file, documenting why."""
    if icc:
        try:
            cs = parse_icc(icc)
            if cs.same_primaries(SRGB) and cs.transfer.kind == "srgb":
                cs.notes.append("Embedded profile is sRGB-equivalent.")
            return cs
        except ValueError as e:
            cs = ColorSpace(SRGB.name, SRGB.to_xyz_d50, SRGB.transfer, icc=None, source="assumed")
            cs.notes.append(f"Embedded ICC could not be used ({e}); assuming sRGB. "
                            "Export will embed sRGB, not the original profile.")
            return cs
    if interop_index and interop_index.strip().upper().startswith("R03"):
        cs = ColorSpace(ADOBE_RGB.name, ADOBE_RGB.to_xyz_d50, ADOBE_RGB.transfer, source="EXIF (Interop R03)")
        cs.notes.append("No ICC profile; EXIF interoperability index R03 means Adobe RGB.")
        return cs
    cs = ColorSpace(SRGB.name, SRGB.to_xyz_d50, SRGB.transfer, source="assumed")
    if exif_colorspace == 1:
        cs.source = "EXIF (ColorSpace=sRGB)"
    else:
        cs.notes.append("No embedded ICC profile; assuming sRGB (the standard for untagged images).")
    return cs


def convert_linear(img, src: ColorSpace, dst: ColorSpace):
    """Linear RGB in src primaries -> linear RGB in dst primaries (no clipping)."""
    if src.same_primaries(dst, tol=1e-5):
        return img
    return apply_matrix(img, src.matrix_to(dst))


def apply_matrix(img, M):
    """Apply a 3x3 matrix to every pixel of an (..., 3) float32 array."""
    shp = img.shape
    out = np.asarray(img, dtype=np.float32).reshape(-1, 3) @ np.asarray(M, dtype=np.float32).T
    return out.reshape(shp)


def icc_fingerprint(icc: bytes | None):
    return hashlib.sha256(icc).hexdigest()[:12] if icc else None
