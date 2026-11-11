"""
.cube LUT import, application, management and export.

Parsing/writing follows the Adobe/Resolve .cube convention (R fastest), as
in ComfyUI-Darkroom utils/lut.py (MIT), rewritten to be vectorised and to
process large images in row chunks so a 24 MP frame does not need gigabytes
of interpolation temporaries.  LUTs are assumed to take and return
sRGB-encoded (display-referred, Rec.709 primaries) values, which is what
almost all creative .cube LUTs expect.  Both 3D and 1D LUTs are supported.
"""

from __future__ import annotations

import os
import re
import shutil

import numpy as np


class CubeLUT:
    def __init__(self, name, table, kind, domain_min=(0, 0, 0), domain_max=(1, 1, 1), path=None, title=""):
        self.name = name
        self.table = table.astype(np.float32)   # 3D: (N,N,N,3) indexed [r,g,b]; 1D: (N,3)
        self.kind = kind                         # '3d' | '1d'
        self.size = table.shape[0]
        self.domain_min = np.asarray(domain_min, np.float32)
        self.domain_max = np.asarray(domain_max, np.float32)
        self.path = path
        self.title = title

    def apply(self, E, chunk_rows=256):
        dmin, dmax = self.domain_min, self.domain_max
        x = (E - dmin) / np.maximum(dmax - dmin, 1e-6)
        out = np.empty_like(E, dtype=np.float32)
        h = E.shape[0]
        for y0 in range(0, h, chunk_rows):
            sl = slice(y0, min(h, y0 + chunk_rows))
            out[sl] = self._apply_chunk(np.clip(x[sl], 0.0, 1.0))
        return out

    def _apply_chunk(self, x):
        n = self.size
        if self.kind == "1d":
            xs = np.linspace(0, 1, n, dtype=np.float32)
            return np.stack([np.interp(x[..., c], xs, self.table[:, c]) for c in range(3)], -1).astype(np.float32)
        c = x * (n - 1)
        i0 = np.minimum(np.floor(c).astype(np.int32), n - 2)
        f = (c - i0).astype(np.float32)
        r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
        fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
        T = self.table
        c00 = T[r0, g0, b0] * (1 - fr) + T[r0 + 1, g0, b0] * fr
        c10 = T[r0, g0 + 1, b0] * (1 - fr) + T[r0 + 1, g0 + 1, b0] * fr
        c01 = T[r0, g0, b0 + 1] * (1 - fr) + T[r0 + 1, g0, b0 + 1] * fr
        c11 = T[r0, g0 + 1, b0 + 1] * (1 - fr) + T[r0 + 1, g0 + 1, b0 + 1] * fr
        c0 = c00 * (1 - fg) + c10 * fg
        c1 = c01 * (1 - fg) + c11 * fg
        return (c0 * (1 - fb) + c1 * fb).astype(np.float32)


def parse_cube(path, name=None):
    size3 = size1 = None
    dmin, dmax = [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]
    title = ""
    rows = []
    num = re.compile(r"^[\s]*[-+0-9.eE]")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            key = s.split()[0].upper()
            if key == "TITLE":
                title = s[5:].strip().strip('"')
            elif key == "LUT_3D_SIZE":
                size3 = int(s.split()[1])
            elif key == "LUT_1D_SIZE":
                size1 = int(s.split()[1])
            elif key == "DOMAIN_MIN":
                dmin = [float(v) for v in s.split()[1:4]]
            elif key == "DOMAIN_MAX":
                dmax = [float(v) for v in s.split()[1:4]]
            elif key in ("LUT_3D_INPUT_RANGE", "LUT_1D_INPUT_RANGE"):
                lo, hi = (float(v) for v in s.split()[1:3])
                dmin, dmax = [lo] * 3, [hi] * 3
            elif num.match(s):
                parts = s.split()
                if len(parts) >= 3:
                    rows.append([float(parts[0]), float(parts[1]), float(parts[2])])
    data = np.asarray(rows, dtype=np.float32)
    name = name or os.path.splitext(os.path.basename(path))[0]
    if size3:
        if size3 < 2 or size3 > 129:
            raise ValueError(f"LUT_3D_SIZE {size3} out of range")
        if len(data) != size3 ** 3:
            raise ValueError(f"expected {size3 ** 3} entries, found {len(data)}")
        table = data.reshape(size3, size3, size3, 3).transpose(2, 1, 0, 3)  # file order b,g,r -> [r,g,b]
        return CubeLUT(name, np.ascontiguousarray(table), "3d", dmin, dmax, path, title)
    if size1:
        if len(data) != size1:
            raise ValueError(f"expected {size1} entries, found {len(data)}")
        return CubeLUT(name, data, "1d", dmin, dmax, path, title)
    raise ValueError("no LUT_3D_SIZE or LUT_1D_SIZE header found")


def write_cube(path, table, title="Darkroom Studio grade", comments=()):
    n = table.shape[0]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    flat = np.clip(table, 0, 1).transpose(2, 1, 0, 3).reshape(-1, 3)  # [r,g,b] -> b slowest
    with open(path, "w") as f:
        f.write("# Created by Darkroom Studio\n")
        for c in comments:
            f.write(f"# {c}\n")
        f.write(f'TITLE "{title}"\nLUT_3D_SIZE {n}\nDOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1.0 1.0 1.0\n')
        np.savetxt(f, flat, fmt="%.6f")
    return path


def identity_lattice(n=33):
    g = np.linspace(0, 1, n, dtype=np.float32)
    r, gg, b = np.meshgrid(g, g, g, indexing="ij")
    return np.stack([r, gg, b], axis=-1)        # (n,n,n,3) indexed [r,g,b]


class LUTRegistry:
    def __init__(self, lut_dir):
        self.dir = lut_dir
        os.makedirs(lut_dir, exist_ok=True)
        self.luts = {}
        self.errors = {}
        self.rescan()

    def rescan(self):
        for fn in sorted(os.listdir(self.dir)):
            if fn.lower().endswith(".cube"):
                p = os.path.join(self.dir, fn)
                try:
                    lut = parse_cube(p)
                    self.luts[lut.name] = lut
                except Exception as e:
                    self.errors[fn] = str(e)

    def names(self):
        return ["None"] + sorted(self.luts)

    def import_file(self, src_path):
        lut = parse_cube(src_path)        # validate before copying
        dst = os.path.join(self.dir, os.path.basename(src_path))
        if os.path.abspath(dst) != os.path.abspath(src_path):
            shutil.copy2(src_path, dst)
        lut.path = dst
        self.luts[lut.name] = lut
        return lut

    def delete(self, name):
        lut = self.luts.pop(name, None)
        if lut and lut.path and os.path.exists(lut.path):
            os.remove(lut.path)

    def describe(self, name):
        lut = self.luts.get(name)
        if not lut:
            return ""
        return f"**{name}** · {lut.kind.upper()} LUT, size {lut.size}" + (f" · {lut.title}" if lut.title else "")

    def apply(self, name, E, strength):
        lut = self.luts.get(name)
        if lut is None:
            return E
        out = lut.apply(E)
        if strength >= 1.0:
            return out
        return (E + (out - E) * np.float32(strength)).astype(np.float32)
