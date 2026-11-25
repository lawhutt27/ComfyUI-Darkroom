import pytest

from darkroom_studio import recipe as R
from darkroom_web.stack import StackError, fold_recipe, validate_stack


def test_empty_stack_folds_to_identity_recipe(store):
    assert fold_recipe({"layers": []}) == R.default_recipe()


def test_unknown_type_and_bad_input_rejected(store):
    with pytest.raises(StackError):
        validate_stack({"layers": [{"type": "run_python", "params": {}}]})
    with pytest.raises(StackError):
        validate_stack({"layers": "nope"})
    with pytest.raises(StackError):
        validate_stack({"layers": [{"type": "look", "params": {"preset": "No such look"}}]})


def test_values_clamped_and_unknown_params_dropped(store):
    clean, warnings = validate_stack({"layers": [
        {"type": "adjust", "params": {"basic": {"exposure": 99, "bogus": 1}, "evil": {"x": 1}}}]})
    p = clean["layers"][0]["params"]
    assert p["basic"] == {"exposure": 5.0}
    assert any("clamped" in w for w in warnings) and any("dropped" in w for w in warnings)


def test_pixel_layers_render_before_grade_layers(store):
    clean, warnings = validate_stack({"layers": [
        {"type": "adjust", "params": {"basic": {"contrast": 10}}},
        {"type": "red_eye", "params": {"eyes": [{"x": 0.5, "y": 0.5, "r": 0.01}]}},
    ]})
    assert [L["type"] for L in clean["layers"]] == ["red_eye", "adjust"]
    assert warnings


def test_adjust_layers_add_and_disabled_layers_skip(store):
    stack, _ = validate_stack({"layers": [
        {"type": "adjust", "params": {"basic": {"exposure": 0.5, "contrast": 10}}},
        {"type": "adjust", "params": {"basic": {"exposure": 0.25}}},
        {"type": "adjust", "enabled": False, "params": {"basic": {"exposure": 3}}},
    ]})
    rec = fold_recipe(stack)
    assert rec["basic"]["exposure"] == pytest.approx(0.75)
    assert rec["basic"]["contrast"] == 10


def test_look_amount_scales_the_look(store):
    full, _ = validate_stack({"layers": [{"type": "look", "params": {"preset": "IG / Golden Hour", "amount": 100}}]})
    half, _ = validate_stack({"layers": [{"type": "look", "params": {"preset": "IG / Golden Hour", "amount": 50}}]})
    rf, rh = fold_recipe(full), fold_recipe(half)
    assert rf["basic"]["temperature"] == pytest.approx(22)
    assert rh["basic"]["temperature"] == pytest.approx(11)
    assert rh["color_grading"]["highlights_hue"] == rf["color_grading"]["highlights_hue"]   # shape param


def test_ai_layer_requires_stored_result(store):
    with pytest.raises(StackError):
        validate_stack({"layers": [{"type": "ai_edit", "params": {"prompt": "x"}}]})
