import numpy as np

from darkroom_studio.lut import CubeLUT, identity_lattice
from darkroom_web.lutx import tetrahedral


def test_identity_lut_is_exact():
    lat = identity_lattice(17)
    x = np.random.default_rng(1).random((64, 64, 3)).astype(np.float32)
    assert np.abs(tetrahedral(lat, x) - x).max() < 1e-6


def test_linear_lut_is_reproduced_exactly():
    M = np.array([[0.8, 0.1, 0.1], [0.05, 0.9, 0.05], [0.0, 0.2, 0.8]], np.float32)
    lat = identity_lattice(9) @ M.T
    x = np.random.default_rng(2).random((32, 32, 3)).astype(np.float32)
    assert np.abs(tetrahedral(lat, x) - x @ M.T).max() < 1e-5


def test_neutral_axis_preserved_and_close_to_trilinear():
    n = 17
    lat = identity_lattice(n)
    lat = np.clip(lat + 0.08 * np.sin(lat[..., [1, 2, 0]] * 6.0), 0, 1)
    g = np.linspace(0, 1, 50, dtype=np.float32)
    grey = np.stack([g, g, g], -1)[None]
    lut = CubeLUT("t", lat, "3d")
    tri = lut.apply(grey)
    tet = tetrahedral(lat, grey)
    # on the neutral axis tetrahedral only uses lattice points on the diagonal
    diag = lat[np.arange(n), np.arange(n), np.arange(n)]
    expect = np.stack([np.interp(g, np.linspace(0, 1, n), diag[:, c]) for c in range(3)], -1)[None]
    assert np.abs(tet - expect).max() < 1e-5
    x = np.random.default_rng(3).random((40, 40, 3)).astype(np.float32)
    assert np.abs(tetrahedral(lat, x) - lut.apply(x)).max() < 0.03
    assert tri.shape == tet.shape
