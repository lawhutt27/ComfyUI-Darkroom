import cv2
import numpy as np
import pytest

from darkroom_studio import colorspace as cms
from darkroom_studio import engine
from darkroom_web import composite
from helpers_web import textured_image


def setup_case(shift_px=0):
    W, H = 2400, 1600
    master_E = textured_image(W, H, seed=5)
    # an "object": a high-contrast checker the model will remove
    yy, xx = np.mgrid[600:1000, 400:800]
    master_E[600:1000, 400:800] = np.where(((yy // 40 + xx // 40) % 2)[..., None] == 0, 0.9, 0.1)
    master = cms.srgb_decode(master_E)
    inp = cv2.resize((master_E * 255 + 0.5).astype(np.uint8), (600, 400), interpolation=cv2.INTER_AREA)
    out = inp.astype(np.float32)
    out[150:250, 100:200] = 128                       # object removed -> big change
    out[100:300, 350:500] *= 1.12                     # relight -> moderate change (delta transfer)
    out += 1.0                                        # global drift (must be ignored)
    out = np.clip(out, 0, 255).astype(np.uint8)
    if shift_px:
        out = np.roll(out, shift_px, axis=1)
    params = {"crop": {"x": 0, "y": 0, "w": 1, "h": 1}, "box": None, "preserve": True, "feather": 24,
              "strength": 100, "sensitivity": 50}
    return master, inp, out, params


def test_untouched_areas_are_bit_identical_at_full_resolution():
    master, inp, out, params = setup_case()
    res = composite.composite(master.copy(), cms.SRGB, params, inp, out, full_long=2400)
    changed = np.any(res != master, axis=-1)
    # nothing changes far from the two edited rectangles (full-res coords x4, plus feather)
    far = np.ones(changed.shape, bool)
    far[600 - 120:1000 + 120, 400 - 120:800 + 120] = False
    far[400 - 120:1200 + 120, 1400 - 120:2000 + 120] = False
    assert not changed[far].any()
    assert changed[650:950, 450:750].all()


def test_replacement_where_the_model_removed_something():
    master, inp, out, params = setup_case()
    res = composite.composite(master.copy(), cms.SRGB, params, inp, out, full_long=2400)
    E = engine.to_srgb_encoded(res[640:960, 440:760], cms.SRGB)
    assert abs(E.mean() - 129 / 255) < 0.02          # the model's flat fill, not the old content
    assert E.std() < 0.03                            # no ghost of the removed checker


def test_delta_transfer_keeps_full_resolution_texture():
    master, inp, out, params = setup_case()
    res = composite.composite(master.copy(), cms.SRGB, params, inp, out, full_long=2400)
    sl = (slice(500, 1100), slice(1500, 1900))      # inside the relit rectangle
    E0 = engine.to_srgb_encoded(master[sl], cms.SRGB)
    E1 = engine.to_srgb_encoded(res[sl], cms.SRGB)
    hf0 = E0 - cv2.GaussianBlur(E0, (0, 0), 3)
    hf1 = E1 - cv2.GaussianBlur(E1, (0, 0), 3)
    assert E1.mean() > E0.mean() + 0.03                       # the relight arrived
    assert np.corrcoef(hf0.ravel(), hf1.ravel())[0, 1] > 0.95  # same fine detail as the master
    assert hf1.std() == pytest.approx(hf0.std(), rel=0.1)


def test_strength_zero_and_box_limit():
    master, inp, out, params = setup_case()
    p0 = dict(params, strength=0)
    assert np.array_equal(composite.composite(master.copy(), cms.SRGB, p0, inp, out, 2400), master)
    pb = dict(params, box={"x": 0.1, "y": 0.3, "w": 0.2, "h": 0.3}, feather=0)
    res = composite.composite(master.copy(), cms.SRGB, pb, inp, out, 2400)
    changed = np.any(res != master, axis=-1)
    ys, xs = np.nonzero(changed)
    assert xs.min() >= 240 and xs.max() < 720 and ys.min() >= 480 and ys.max() < 960


def test_alignment_undoes_a_small_shift():
    _, inp, out, _ = setup_case(shift_px=3)
    aligned, info = composite.align(out, inp)
    assert isinstance(info["aligned"], dict), info
    assert abs(abs(info["aligned"]["shift_px"][0]) - 3) < 0.6
    err_before = np.abs(out.astype(int) - inp.astype(int))[:, 20:90].mean()
    err_after = np.abs(aligned.astype(int) - inp.astype(int))[:, 20:90].mean()
    assert err_after < err_before - 0.4      # remaining error is the +1 global drift and resampling


def test_provider_output_of_other_size_is_resized():
    _, inp, out, _ = setup_case()
    big = cv2.resize(out, (1024, 683))
    aligned, info = composite.align(big, inp)
    assert aligned.shape == inp.shape and info["provider_size"] == [1024, 683]
