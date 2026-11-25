"""Tetrahedral 3D-LUT interpolation (what Resolve/OCIO use for .cube files).

The Darkroom Studio engine interpolates 3D LUTs trilinearly. Tetrahedral
interpolation uses 4 lattice points per sample instead of 8, preserves the
neutral axis exactly and avoids the slight hue shifts trilinear shows on
steep creative LUTs. It is a drop-in registry: the engine calls
`registry.apply(name, E, strength)` and gets tetrahedral results.
"""

from __future__ import annotations

import numpy as np

from darkroom_studio.lut import LUTRegistry


def tetrahedral(table, x):
    """table: (N, N, N, 3) indexed [r, g, b]; x: (..., 3) in 0..1."""
    n = table.shape[0]
    shp = x.shape
    x = x.reshape(-1, 3)
    c = np.clip(x, 0.0, 1.0) * (n - 1)
    i0 = np.minimum(np.floor(c).astype(np.int32), n - 2)
    f = (c - i0).astype(np.float32)
    order = np.argsort(-f, axis=1, kind="stable")          # axes by descending fraction
    fs = np.take_along_axis(f, order, axis=1)
    idx = i0.copy()
    out = table[idx[:, 0], idx[:, 1], idx[:, 2]] * (1.0 - fs[:, 0:1])
    rows = np.arange(len(x))
    for k in range(3):
        idx[rows, order[:, k]] += 1
        w = (fs[:, k] - (fs[:, k + 1] if k < 2 else 0.0))[:, None]
        out += table[idx[:, 0], idx[:, 1], idx[:, 2]] * w
    return out.reshape(shp).astype(np.float32)


class TetraLUTRegistry(LUTRegistry):
    def apply(self, name, E, strength, chunk_rows=256):
        lut = self.luts.get(name)
        if lut is None:
            return E
        if lut.kind != "3d":
            return super().apply(name, E, strength)
        dmin, dmax = lut.domain_min, lut.domain_max
        out = np.empty_like(E, dtype=np.float32)
        for y0 in range(0, E.shape[0], chunk_rows):
            sl = slice(y0, min(E.shape[0], y0 + chunk_rows))
            xin = (E[sl] - dmin) / np.maximum(dmax - dmin, 1e-6)
            out[sl] = tetrahedral(lut.table, xin)
        if strength >= 1.0:
            return out
        return (E + (out - E) * np.float32(strength)).astype(np.float32)
