"""
Reference-image colour matching.

Only global statistics of the reference are used: the mean and spread of
its OkLab channels (Reinhard et al. 2001 colour transfer, computed in OkLab
instead of the 8-bit CIELAB of the ComfyUI-Darkroom/Gradia implementation)
and, optionally, its lightness distribution (a smoothed, monotonic
histogram-matching curve).  No pixels, shapes or regions of the reference
are copied: the result is a per-pixel colour transform of YOUR photograph.

The transfer is "frozen" into the recipe as a few numbers so the preview,
1:1 crops and the full-resolution export apply exactly the same transform.
"""

from __future__ import annotations

import os
import tempfile

import numpy as np

from . import engine
from .imaging import load_source, make_proxy

METHODS = {"Colour + tone": "lab_stats+tone", "Colour only": "lab_stats"}


def _stats(lab):
    flat = lab.reshape(-1, 3)
    return flat.mean(axis=0), flat.std(axis=0)


def tone_curve(src_L, ref_L, n=257, max_slope=3.0):
    qs = np.linspace(0, 1, 65)
    sq = np.quantile(np.clip(src_L, 0, 1.2), qs)
    rq = np.quantile(np.clip(ref_L, 0, 1.2), qs)
    sq = np.maximum.accumulate(sq + np.arange(len(sq)) * 1e-6)
    x = np.linspace(0, 1, n)
    y = np.interp(x, sq, rq)
    # gentle: limit slope, keep monotonic
    dy = np.clip(np.diff(y), 0, max_slope / (n - 1))
    y = np.concatenate([[y[0]], y[0] + np.cumsum(dy)])
    from scipy.ndimage import uniform_filter1d
    y = uniform_filter1d(y, 9, mode="nearest")
    return np.maximum.accumulate(y)


def load_reference(path, workdir=None):
    d = workdir or tempfile.mkdtemp(prefix="darkroom_ref_")
    ref = load_source(path, os.path.join(d, "references"))
    small, _ = make_proxy(ref.linear, 768)
    return ref, small


def compute_match(session, ref_small, ref_space, method="Colour + tone", reference_name=""):
    """Freeze the transfer between the session photo (as it is before the match
    stage) and the reference into recipe data."""
    rec = session.recipe
    pre = engine.render(session.analysis, rec, session.ctx, level=session.analysis.shape[1] / session.source.width,
                        stop="pre_match")
    src_lab = engine.OkLab(session.source.space).from_linear(pre)
    ref_lab = engine.OkLab(ref_space).from_linear(ref_small)
    sm, ss = _stats(src_lab)
    rm, rs = _stats(ref_lab)
    m = METHODS.get(method, "lab_stats+tone")
    data = {"method": m, "src_mean": sm.tolist(), "src_std": ss.tolist(), "ref_mean": rm.tolist(),
            "ref_std": rs.tolist(), "tone_lut": [], "reference_name": reference_name}
    if m == "lab_stats+tone":
        data["tone_lut"] = tone_curve(src_lab[..., 0].ravel(), ref_lab[..., 0].ravel()).round(5).tolist()
    return data


def describe(data):
    if not data:
        return "No reference matched."
    sm, rm = np.array(data["src_mean"]), np.array(data["ref_mean"])
    return (f"Matched to **{data.get('reference_name') or 'reference'}** ({data['method']}). "
            f"Lightness {sm[0]:.2f}→{rm[0]:.2f}, colour cast a {sm[1]:+.3f}→{rm[1]:+.3f}, "
            f"b {sm[2]:+.3f}→{rm[2]:+.3f}. Only colour/tone statistics are transferred; "
            "no content from the reference is copied.")
