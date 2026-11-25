"""Shared test helpers."""

import numpy as np


def textured_image(w=1200, h=800, seed=0):
    """Smooth gradient + fine noise texture (stands in for sensor detail)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    base = np.stack([0.25 + 0.5 * xx / w, 0.3 + 0.4 * yy / h, 0.55 - 0.3 * xx / w], -1)
    tex = rng.normal(0, 0.03, (h, w, 1)).astype(np.float32)
    return np.clip(base + tex, 0, 1)
