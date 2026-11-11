"""Test helpers shared by all test modules."""

import os

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))

SAMPLES = os.environ.get("DARKROOM_SAMPLES", os.path.join(os.path.dirname(HERE), "samples"))


def sample(name):
    p = os.path.join(SAMPLES, name)
    if not os.path.exists(p):
        pytest.skip(f"camera sample {name} not present (run tools/fetch_samples.py)")
    return p



def make_pattern(h=400, w=600, seed=0):
    """Smooth gradients + colour patches + a 1-px marker border + noise texture."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.stack([x / w, y / h, 0.5 + 0.4 * np.sin(x / 23.0) * np.cos(y / 17.0)], -1)
    for i, c in enumerate([(0.8, 0.2, 0.2), (0.2, 0.7, 0.3), (0.2, 0.3, 0.8), (0.9, 0.75, 0.6), (0.5, 0.5, 0.5)]):
        img[40:120, 40 + i * 100:120 + i * 100] = c
    img += rng.normal(0, 0.01, img.shape).astype(np.float32)
    img = np.clip(img, 0.02, 0.98)
    img[0, :] = img[-1, :] = (1.0, 0.0, 1.0)      # magenta marker border
    img[:, 0] = img[:, -1] = (1.0, 0.0, 1.0)
    return img.astype(np.float32)
