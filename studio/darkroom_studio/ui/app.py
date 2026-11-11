"""
Gradio Blocks interface for Darkroom Studio.

Layout:  top bar (prompt, apply, undo/redo/reset, save preset) ·
left (library, RAW develop, photo info, metadata, presets, reference, recipes, batch) ·
centre (before/after canvas, view & zoom, export, scopes) ·
right (all manual controls) · bottom status bar.

Slider *dragging* re-renders the preview only; *releasing* a slider commits
the recipe to the undo history.  Preview images are 8-bit sRGB proxies for
display; exports always re-render the full-resolution float32 source.
"""

from __future__ import annotations

import copy
import html
import json
import os
import tempfile
import time
import traceback
import zipfile

import gradio as gr
import numpy as np

from .. import recipe as R
from ..advisor import analyse, suggest
from ..export import FORMATS, OUTPUT_SPACE_CHOICES
from ..imaging import SUPPORTED_EXTS
from ..metadata import format_summary
from ..prompt import OpenRouterInterpreter, RuleInterpreter
from ..raw import (BASE_CURVE_OPTIONS, COLORSPACE_OPTIONS, DEMOSAIC_OPTIONS, HIGHLIGHT_OPTIONS,
                   WHITE_BALANCE_OPTIONS, RawSettings)
from ..scopes import curve_svg, histogram, vectorscope, waveform, wheels_svg
from ..session import Studio

CURVE_X = [0.0, 0.25, 0.5, 0.75, 1.0]
CURVE_LABELS = ["Black", "Shadows", "Midtones", "Lights", "White"]

PANELS = [  # (accordion title, [(subtitle or None, [paths])], open)
    ("Basic", [("White balance", ["basic.temperature", "basic.tint"]),
               ("Tone", ["basic.exposure", "basic.contrast", "basic.highlights", "basic.shadows",
                         "basic.whites", "basic.blacks"]),
               ("Presence", ["effects.texture", "effects.clarity", "effects.dehaze", "basic.vibrance",
                             "basic.saturation"])], True),
    ("Colour mixer (HSL)", [("Hue", [f"hsl.{b}_hue" for b in R.HSL_BANDS]),
                            ("Saturation", [f"hsl.{b}_saturation" for b in R.HSL_BANDS]),
                            ("Luminance", [f"hsl.{b}_luminance" for b in R.HSL_BANDS])], False),
    ("Colour grading", [(z.title(), [f"color_grading.{z}_hue", f"color_grading.{z}_saturation",
                                     f"color_grading.{z}_luminance"]) for z in R.ZONES]
     + [("Blend", ["color_grading.blending", "color_grading.balance"])], False),
    ("Lift · Gamma · Gain", [(w.title(), [f"lgg.{w}_{c}" for c in "mrgb"]) for w in ("lift", "gamma", "gain")],
     False),
    ("Effects", [("Film look", ["effects.fade"]),
                 ("Grain", ["effects.grain_amount", "effects.grain_size", "effects.grain_roughness",
                            "effects.grain_color", "effects.grain_seed"]),
                 ("Vignette", ["effects.vignette_amount", "effects.vignette_midpoint", "effects.vignette_roundness",
                               "effects.vignette_feather"]),
                 ("Halation", ["effects.halation_amount", "effects.halation_radius", "effects.halation_threshold"]),
                 ("Optics", ["effects.chromatic_aberration"])], False),
    ("Detail", [("Sharpening", ["detail.sharpen_amount", "detail.sharpen_radius", "detail.sharpen_masking"]),
                ("Noise reduction", ["detail.nr_luminance", "detail.nr_color"])], False),
    ("Lens corrections", [(None, ["lens.distortion", "lens.ca_red", "lens.ca_blue", "lens.vignette_correction"])],
     False),
]

STATUS_KEYS = [("original", "Original"), ("output", "Output"), ("bit_depth", "Depth"), ("profile", "Profile"),
               ("format", "Format"), ("preview", "Preview")]


def _status_html(sess, msg="", warn=False):
    if sess is None:
        body = '<span class="k">Status</span><span class="v">No photo loaded. Upload a NEF, CR2, DNG, JPEG, TIFF or PNG.</span>'
    else:
        st = sess.status()
        body = "".join(f'<span class="k">{lab}</span><span class="v">{html.escape(st[k])}</span>'
                       for k, lab in STATUS_KEYS)
    if msg:
        body += f'<br><span class="k">Processing</span><span class="v {"warn" if warn else ""}">{html.escape(msg)}</span>'
    return f'<div id="dr-status-inner">{body}</div>'


def build_app(workdir=None, preview_long_edge=None):
    workdir = workdir or os.environ.get("DARKROOM_WORKDIR", os.path.join(tempfile.gettempdir(), "darkroom_studio"))
    preview_long_edge = int(preview_long_edge or os.environ.get("DARKROOM_PREVIEW_EDGE", 1600))
    studio = Studio(workdir, preview_long_edge)
    rules = RuleInterpreter()

    controls = {}            # path -> component (numeric + enums + registries)
    curve_sliders = {}       # (channel, i) -> slider

    with gr.Blocks(title="Darkroom Studio", fill_width=True) as demo:
        sess_state = gr.State(None)

        # ------------------------------------------------------------- top bar
        with gr.Row(elem_id="dr-header", equal_height=True):
            gr.HTML('<div id="dr-brand">DARK<b>ROOM</b></div>', min_width=150, scale=0)
            prompt = gr.Textbox(show_label=False, lines=1, max_lines=3, scale=8, elem_id="dr-prompt",
                                placeholder="Describe the look… e.g. “warm 1970s analog film with muted greens and "
                                            "soft highlights”")
            interp = gr.Dropdown([rules.name, "OpenRouter LLM (text only)"], value=rules.name, show_label=False,
                                 scale=2, min_width=150, container=False)
            fresh = gr.Checkbox(label="From original", value=False, scale=1, min_width=90, container=False)
            apply_btn = gr.Button("Apply", elem_id="dr-apply", elem_classes="dr-toolbtn", scale=0, min_width=76)
            undo_btn = gr.Button("Undo", elem_classes="dr-toolbtn", scale=0, min_width=76)
            redo_btn = gr.Button("Redo", elem_classes="dr-toolbtn", scale=0, min_width=76)
            reset_btn = gr.Button("Reset", elem_classes="dr-toolbtn", scale=0, min_width=76)
            savepre_btn = gr.Button("Save preset", elem_classes="dr-toolbtn", scale=0, min_width=112)

        with gr.Row(equal_height=False):
            # --------------------------------------------------------- left panel
            with gr.Column(scale=3, min_width=270, elem_classes="dr-panel"):
                with gr.Accordion("Library", open=True):
                    upload = gr.File(label="Upload photograph", file_count="single",
                                     file_types=sorted(SUPPORTED_EXTS), type="filepath", height=110)
                    gr.Markdown("NEF · CR2 · DNG · JPEG · TIFF · PNG. The original is copied to a read-only library "
                                "and never modified.", elem_classes="dr-note")
                with gr.Accordion("RAW development", open=False, visible=True) as raw_acc:
                    gr.Markdown("Applies to RAW files only. Re-develops from the untouched RAW.",
                                elem_classes="dr-note")
                    raw_wb = gr.Dropdown(WHITE_BALANCE_OPTIONS, value=WHITE_BALANCE_OPTIONS[0], label="White balance")
                    raw_ev = gr.Slider(-2, 3, 0, step=0.1, label="RAW exposure (EV, before clipping)")
                    raw_hl = gr.Dropdown(HIGHLIGHT_OPTIONS, value="Clip", label="Highlight recovery")
                    raw_dm = gr.Dropdown(DEMOSAIC_OPTIONS, value="AHD", label="Demosaic")
                    raw_cs = gr.Dropdown(COLORSPACE_OPTIONS, value="sRGB", label="Working colour space")
                    raw_bc = gr.Dropdown(BASE_CURVE_OPTIONS, value=BASE_CURVE_OPTIONS[0], label="Base tone curve")
                    redevelop_btn = gr.Button("Re-develop RAW", size="sm")
                with gr.Accordion("Photo information", open=True):
                    photo_info = gr.Markdown("—")
                with gr.Accordion("Camera metadata", open=True):
                    meta_md = gr.Markdown("—")
                    advice_md = gr.Markdown("", elem_classes="dr-note")
                    advice_btn = gr.Button("Apply suggested starting point", size="sm")
                with gr.Accordion("Presets", open=False):
                    preset_dd = gr.Dropdown(studio.list_presets(), label="Preset library", value=None)
                    preset_desc = gr.Markdown("", elem_classes="dr-note")
                    preset_apply = gr.Button("Apply preset", size="sm")
                    preset_name = gr.Textbox(label="New preset name", placeholder="My look")
                with gr.Accordion("Reference match", open=False):
                    ref_file = gr.File(label="Reference photograph (style only)", type="filepath",
                                       file_types=sorted(SUPPORTED_EXTS), height=90)
                    ref_method = gr.Radio(["Colour + tone", "Colour only"], value="Colour + tone",
                                          label="Transfer")
                    controls["match.strength"] = gr.Slider(0, 100, 0, step=1, label="Match strength")
                    ref_btn = gr.Button("Match to reference", size="sm")
                    ref_md = gr.Markdown("Colour and tone statistics are transferred; no content from the "
                                         "reference is copied.", elem_classes="dr-note")
                with gr.Accordion("Saved recipes", open=False):
                    recipe_name = gr.Textbox(label="Recipe name", placeholder="Beach sunset grade")
                    with gr.Row():
                        recipe_save = gr.Button("Save", size="sm")
                        recipe_load = gr.Button("Load", size="sm")
                    recipe_dd = gr.Dropdown(studio.list_recipes(), label="Saved recipes", value=None)
                    recipe_upload = gr.File(label="Import recipe JSON", file_types=[".json"], type="filepath",
                                            height=80)
                    recipe_file = gr.File(label="Download", interactive=False, height=60)
                with gr.Accordion("Batch (apply current recipe)", open=False):
                    batch_files = gr.File(label="Photos", file_count="multiple", type="filepath",
                                          file_types=sorted(SUPPORTED_EXTS), height=90)
                    batch_fmt = gr.Dropdown(list(FORMATS), value="TIFF 16-bit (master, lossless)", label="Format")
                    batch_btn = gr.Button("Run batch", size="sm")
                    batch_out = gr.File(label="Batch results (zip)", interactive=False, height=60)
                    batch_md = gr.Markdown("", elem_classes="dr-note")

            # --------------------------------------------------------- centre
            with gr.Column(scale=8, min_width=420):
                with gr.Row(equal_height=True):
                    view_mode = gr.Radio(["Before / After", "Edited", "Original"], value="Before / After",
                                         show_label=False, container=False, scale=3)
                    zoom = gr.Radio(["Fit", "100%", "200%"], value="Fit", show_label=False, container=False, scale=2)
                    pan_x = gr.Slider(0, 1, 0.5, step=0.01, label="Pan X", visible=False, scale=1)
                    pan_y = gr.Slider(0, 1, 0.5, step=0.01, label="Pan Y", visible=False, scale=1)
                canvas_slider = gr.ImageSlider(label="Before / After", show_label=False, type="numpy",
                                               format="png", interactive=False, elem_id="dr-canvas",
                                               max_height=780, buttons=["fullscreen"])
                canvas_single = gr.Image(show_label=False, type="numpy", format="png", interactive=False,
                                         visible=False, elem_id="dr-canvas", height=780,
                                         buttons=["fullscreen"])
                explain_md = gr.Markdown("", elem_classes="dr-note")
                with gr.Accordion("Export (full resolution)", open=True):
                    with gr.Row():
                        exp_fmt = gr.Dropdown(list(FORMATS), value="TIFF 16-bit (master, lossless)", label="Format")
                        exp_space = gr.Dropdown(OUTPUT_SPACE_CHOICES, value=OUTPUT_SPACE_CHOICES[0],
                                                label="Colour space")
                        exp_q = gr.Slider(95, 100, 97, step=1, label="JPEG quality")
                        exp_meta = gr.Checkbox(value=True, label="Keep metadata (EXIF/ICC)")
                    exp_warn = gr.Markdown("", elem_classes="dr-warn")
                    with gr.Row():
                        exp_btn = gr.Button("Export full resolution", variant="primary")
                        dev_btn = gr.Button("Export developed RAW (16-bit, no grade)")
                        orig_btn = gr.Button("Download original file")
                    exp_files = gr.File(label="Files", file_count="multiple", interactive=False)
                with gr.Accordion("Scopes", open=False):
                    with gr.Row():
                        scope_hist = gr.Image(label="Histogram", show_label=True, interactive=False, height=170)
                        scope_wave = gr.Image(label="Waveform", show_label=True, interactive=False, height=170)
                        scope_vec = gr.Image(label="Vectorscope", show_label=True, interactive=False, height=170)

            # --------------------------------------------------------- right panel
            with gr.Column(scale=4, min_width=330, elem_classes="dr-panel"):
                for title, sections, is_open in PANELS:
                    with gr.Accordion(title, open=is_open):
                        if title == "Colour grading":
                            wheels_html = gr.HTML(wheels_svg(R.default_recipe()["color_grading"]))
                        for sub, paths in sections:
                            if sub:
                                gr.Markdown(f"<div class='dr-section-title'>{sub}</div>")
                            for path in paths:
                                p = R.NUMERIC_BY_PATH[path]
                                lab = p.label
                                if title.startswith("Colour mixer"):
                                    lab = path.split(".")[1].split("_")[0].title()
                                controls[path] = gr.Slider(p.lo, p.hi, p.default, step=p.step, label=lab,
                                                           info=p.help or None)
                        if title == "Lens corrections":
                            controls["lens.fill_mode"] = gr.Dropdown(R.ENUMS["lens.fill_mode"][0],
                                                                     value=R.ENUMS["lens.fill_mode"][1],
                                                                     label="Edge handling")
                            gr.Markdown("Distortion correction moves edge content; image size never changes.",
                                        elem_classes="dr-note")
                    if title == "Basic":
                        with gr.Accordion("Tone curve", open=False):
                            curve_html = gr.HTML(curve_svg({"master": R.IDENTITY_CURVE}))
                            with gr.Tabs():
                                for ch in R.CURVE_CHANNELS:
                                    with gr.Tab(ch.title()):
                                        for i, x in enumerate(CURVE_X):
                                            curve_sliders[(ch, i)] = gr.Slider(0, 1, x, step=0.005,
                                                                               label=f"{CURVE_LABELS[i]} ({x:g})")
                with gr.Accordion("Film emulation", open=True):
                    controls["film.stock"] = gr.Dropdown(studio.films.names(), value="None", label="Film stock",
                                                         filterable=True)
                    controls["film.strength"] = gr.Slider(0, 100, 100, step=1, label="Strength")
                    film_md = gr.Markdown("Aesthetic simulations, not chemical reproductions.",
                                          elem_classes="dr-note")
                with gr.Accordion("LUT", open=False):
                    lut_up = gr.File(label="Import .cube", file_types=[".cube"], type="filepath", height=80)
                    controls["lut.name"] = gr.Dropdown(studio.luts.names(), value="None", label="LUT")
                    controls["lut.strength"] = gr.Slider(0, 100, 100, step=1, label="Intensity")
                    lut_md = gr.Markdown("", elem_classes="dr-note")
                    with gr.Row():
                        lut_export_name = gr.Textbox(label="Export current grade as .cube", placeholder="my_grade")
                        lut_export_btn = gr.Button("Export LUT", size="sm")
                    lut_file = gr.File(label="LUT file", interactive=False, height=60)
                with gr.Accordion("Recipe (JSON)", open=False):
                    recipe_json = gr.Code(language="json", label="Current recipe", interactive=False)

        status = gr.HTML(_status_html(None), elem_id="dr-status")

        # ================================================================ wiring
        ctrl_paths = list(controls.keys())
        ctrl_list = [controls[p] for p in ctrl_paths]
        curve_keys = [(ch, i) for ch in R.CURVE_CHANNELS for i in range(len(CURVE_X))]
        curve_list = [curve_sliders[k] for k in curve_keys]
        all_inputs = ctrl_list + curve_list
        n_ctrl = len(ctrl_list)

        def recipe_from_values(sess, vals):
            base = sess.recipe if sess is not None else R.default_recipe()
            r = copy.deepcopy(base)
            for path, v in zip(ctrl_paths, vals[:n_ctrl]):
                if v is None:
                    continue
                R.set_(r, path, v)
            cv = vals[n_ctrl:]
            for c_i, ch in enumerate(R.CURVE_CHANNELS):
                ys = [float(y) for y in cv[c_i * len(CURVE_X):(c_i + 1) * len(CURVE_X)]]
                if all(abs(a - b) < 2e-3 for a, b in zip(ys, _sample_curve(base["tone_curve"][ch]))):
                    continue    # untouched: keep the stored curve (may have more than 5 points)
                pts = [[x, y] for x, y in zip(CURVE_X, ys)]
                r["tone_curve"][ch] = (copy.deepcopy(R.IDENTITY_CURVE)
                                       if all(abs(p[0] - p[1]) < 1e-6 for p in pts) else pts)
            clean, _ = R.validate(r)
            return clean

        def _sample_curve(pts):
            from ..engine import pchip_lut
            xs, ys = pchip_lut(pts, n=1025)
            return [round(float(np.interp(x, xs, ys)), 3) for x in CURVE_X]

        def values_from_recipe(r):
            out = [R.get(r, p) for p in ctrl_paths]
            for ch in R.CURVE_CHANNELS:
                out += _sample_curve(r["tone_curve"][ch])
            return out

        def side_outputs(r):
            return (curve_svg({c: r["tone_curve"][c] for c in R.CURVE_CHANNELS
                               if r["tone_curve"][c] != R.IDENTITY_CURVE} or {"master": R.IDENTITY_CURVE}),
                    wheels_svg(r["color_grading"]), json.dumps(r, indent=1),
                    studio.films.describe(r["film"]["stock"]) or "Aesthetic simulations, not chemical reproductions.")

        def view_outputs(sess, r, mode, zm, px, py):
            if sess is None:
                return None, None, "", 0.0
            if zm == "Fit":
                before, after, dt = sess.render_preview(r)
                where = "fit (proxy)"
            else:
                before, after, dt, region = sess.render_crop(r, zm, px, py)
                where = f"{zm} full-resolution crop at x={region[0]} y={region[1]}"
            if mode == "Before / After":
                return (before, after), None, where, dt
            return None, (after if mode == "Edited" else before), where, dt

        def full_refresh(sess, r, mode, zm, px, py, msg="", warn=False):
            sl, single, where, dt = view_outputs(sess, r, mode, zm, px, py)
            st = _status_html(sess, (msg + " · " if msg else "") + f"rendered {where} in {dt * 1000:.0f} ms", warn)
            scopes = _scopes(sl, single)
            ba = mode == "Before / After"
            return [gr.update(value=sl, visible=ba), gr.update(value=single, visible=not ba), st,
                    *side_outputs(r), *scopes]

        def _scopes(sl, single):
            img = sl[1] if sl is not None else single
            if img is None:
                return None, None, None
            return histogram(img), waveform(img), vectorscope(img)

        view_outs = [canvas_slider, canvas_single, status, curve_html, wheels_html, recipe_json, film_md,
                     scope_hist, scope_wave, scope_vec]
        view_inputs = [view_mode, zoom, pan_x, pan_y]

        def guarded(fn):
            def wrapper(*args):
                try:
                    return fn(*args)
                except gr.Error:
                    raise
                except Exception as e:
                    traceback.print_exc()
                    raise gr.Error(f"{type(e).__name__}: {e}")
            return wrapper

        # ---- upload / develop -------------------------------------------------------
        def raw_settings(wb, ev, hl, dm, cs, bc):
            return RawSettings(white_balance=wb, exposure_ev=ev, highlight_mode=hl, demosaic=dm,
                               color_space=cs, base_curve=bc)

        def info_outputs(sess):
            s = sess.source
            lines = [f"**{html.escape(s.original_name)}**",
                     f"{s.describe_dims()}",
                     f"{s.file_format} · {s.bit_depth}-bit · {s.space.summary()}",
                     f"SHA-256 `{s.sha256[:16]}…` (original kept read-only)",
                     f"Loaded in {sess.load_seconds:.1f} s"]
            if s.kind == "raw":
                lm = s.raw_settings["libraw"]
                lines.append(f"RAW: {lm['color_desc']} CFA, white level {lm['white_level']}, "
                             f"LibRaw {lm['libraw_version']} · demosaic {lm['postprocess']['demosaic']}")
            lines += [f"<span class='dr-warn'>{html.escape(n)}</span>" for n in s.notes]
            stats = analyse(sess.analysis, s.space)
            sugg, why = suggest(s.info, stats, s.kind == "raw")
            sess.suggestion = sugg
            return "  \n".join(lines), format_summary(s.info), "\n".join(f"- {w}" for w in why)

        @guarded
        def on_upload(path, wb, ev, hl, dm, cs, bc, mode, zm, px, py):
            if not path:
                return [None, "—", "—", "", *[gr.update()] * len(all_inputs),
                        *full_refresh(None, R.default_recipe(), mode, zm, px, py)]
            sess = studio.open(path, raw_settings(wb, ev, hl, dm, cs, bc))
            r = sess.recipe
            info, meta, advice = info_outputs(sess)
            msg = f"loaded {sess.source.original_name}"
            return [sess, info, meta, advice, *values_from_recipe(r), *full_refresh(sess, r, mode, zm, px, py, msg)]

        upload.upload(on_upload, [upload, raw_wb, raw_ev, raw_hl, raw_dm, raw_cs, raw_bc, *view_inputs],
                      [sess_state, photo_info, meta_md, advice_md, *all_inputs, *view_outs])

        @guarded
        def on_redevelop(sess, wb, ev, hl, dm, cs, bc, mode, zm, px, py):
            if sess is None or sess.source.kind != "raw":
                raise gr.Error("Load a RAW file (NEF, CR2, DNG…) first.")
            t0 = time.time()
            sess.redevelop(raw_settings(wb, ev, hl, dm, cs, bc))
            info, meta, advice = info_outputs(sess)
            return [sess, info, meta, advice,
                    *full_refresh(sess, sess.recipe, mode, zm, px, py, f"re-developed in {time.time() - t0:.1f} s")]

        redevelop_btn.click(on_redevelop, [sess_state, raw_wb, raw_ev, raw_hl, raw_dm, raw_cs, raw_bc, *view_inputs],
                            [sess_state, photo_info, meta_md, advice_md, *view_outs])

        # ---- live preview / commit ----------------------------------------------------
        @guarded
        def on_preview(sess, mode, zm, px, py, *vals):
            if sess is None:
                return [gr.update()] * len(view_outs)
            r = recipe_from_values(sess, vals)
            return full_refresh(sess, r, mode, zm, px, py)

        @guarded
        def on_commit(sess, mode, zm, px, py, *vals):
            if sess is None:
                return [gr.update()] * len(view_outs)
            r = recipe_from_values(sess, vals)
            sess.commit(r)
            return full_refresh(sess, sess.recipe, mode, zm, px, py,
                                f"history {sess.history.index + 1}/{len(sess.history.stack)}")

        sliders = [c for c in all_inputs if isinstance(c, gr.Slider)]
        others = [c for c in all_inputs if not isinstance(c, gr.Slider)]
        gr.on([s.input for s in sliders], on_preview, [sess_state, *view_inputs, *all_inputs], view_outs,
              trigger_mode="always_last", show_progress="hidden")
        gr.on([s.release for s in sliders] + [o.input for o in others], on_commit,
              [sess_state, *view_inputs, *all_inputs], view_outs, trigger_mode="always_last",
              show_progress="hidden")

        def on_view(sess, mode, zm, px, py):
            show_pan = zm != "Fit"
            r = sess.recipe if sess else R.default_recipe()
            return [gr.update(visible=show_pan), gr.update(visible=show_pan),
                    *full_refresh(sess, r, mode, zm, px, py)]

        gr.on([view_mode.input, zoom.input, pan_x.release, pan_y.release], guarded(on_view),
              [sess_state, *view_inputs], [pan_x, pan_y, *view_outs], trigger_mode="always_last")

        # ---- history --------------------------------------------------------------
        def _apply_recipe(sess, r, mode, zm, px, py, msg):
            return [*values_from_recipe(r), *full_refresh(sess, r, mode, zm, px, py, msg)]

        @guarded
        def on_undo(sess, mode, zm, px, py):
            if sess is None:
                raise gr.Error("Nothing to undo.")
            r = sess.history.undo()
            return _apply_recipe(sess, r, mode, zm, px, py, f"undo → {sess.history.index + 1}/{len(sess.history.stack)}")

        @guarded
        def on_redo(sess, mode, zm, px, py):
            if sess is None:
                raise gr.Error("Nothing to redo.")
            r = sess.history.redo()
            return _apply_recipe(sess, r, mode, zm, px, py, f"redo → {sess.history.index + 1}/{len(sess.history.stack)}")

        @guarded
        def on_reset(sess, mode, zm, px, py):
            if sess is None:
                return _apply_recipe(None, R.default_recipe(), mode, zm, px, py, "reset")
            r = sess.history.reset()
            return _apply_recipe(sess, r, mode, zm, px, py, "reset to original (undoable)")

        hist_outs = [*all_inputs, *view_outs]
        undo_btn.click(on_undo, [sess_state, *view_inputs], hist_outs)
        redo_btn.click(on_redo, [sess_state, *view_inputs], hist_outs)
        reset_btn.click(on_reset, [sess_state, *view_inputs], hist_outs)

        # ---- prompt -----------------------------------------------------------------
        @guarded
        def on_prompt(sess, text, engine_name, from_scratch, mode, zm, px, py):
            if sess is None:
                raise gr.Error("Upload a photograph first.")
            if not (text or "").strip():
                raise gr.Error("Type a description of the look you want.")
            base = R.default_recipe() if from_scratch else sess.recipe
            if from_scratch:
                base["match"] = sess.recipe["match"]
            used = rules.name
            try:
                if engine_name.startswith("OpenRouter"):
                    llm = OpenRouterInterpreter()
                    r, notes, warns = llm.interpret(text, base)
                    used = f"OpenRouter · {llm.model}"
                else:
                    r, notes, warns = rules.interpret(text, base)
            except Exception as e:
                r, notes, warns = rules.interpret(text, base)
                notes = [f"LLM unavailable ({e}); used local rules instead."] + notes
                used = rules.name + " (fallback)"
            sess.commit(r)
            r = sess.recipe
            expl = f"**Interpreted with {used}** (the photo itself was not sent anywhere):\n" + \
                   "\n".join(f"- {html.escape(n)}" for n in notes)
            if warns:
                expl += "\n\n<span class='dr-warn'>" + html.escape("; ".join(warns[:6])) + "</span>"
            return [*_apply_recipe(sess, r, mode, zm, px, py, "prompt applied"), expl]

        def on_interp(engine_name):
            if engine_name.startswith("OpenRouter"):
                gr.Warning("Optional AI mode: your prompt text and the current recipe numbers are sent to "
                           "openrouter.ai. The photograph is never sent. The reply is checked against the recipe "
                           "schema and is never run as code. Needs OPENROUTER_API_KEY (Colab secret or "
                           "environment variable); without it the local rules are used.", duration=12)

        interp.change(on_interp, interp, None)

        apply_btn.click(on_prompt, [sess_state, prompt, interp, fresh, *view_inputs], [*hist_outs, explain_md])
        prompt.submit(on_prompt, [sess_state, prompt, interp, fresh, *view_inputs], [*hist_outs, explain_md])

        # ---- suggestions / presets / recipes ---------------------------------------------
        @guarded
        def on_advice(sess, mode, zm, px, py):
            if sess is None or not getattr(sess, "suggestion", None):
                raise gr.Error("No suggestions for this photo.")
            r, _ = R.validate(sess.suggestion, partial=True, base=sess.recipe)
            sess.commit(r)
            return _apply_recipe(sess, sess.recipe, mode, zm, px, py, "suggested starting point applied")

        advice_btn.click(on_advice, [sess_state, *view_inputs], hist_outs)

        def on_preset_select(name):
            if not name:
                return ""
            if name.startswith("Built-in / "):
                return studio.builtin_presets()[name[11:]]["description"]
            return "User preset"

        preset_dd.change(on_preset_select, preset_dd, preset_desc)

        @guarded
        def on_preset_apply(sess, name, mode, zm, px, py):
            if sess is None:
                raise gr.Error("Upload a photograph first.")
            if not name:
                raise gr.Error("Choose a preset.")
            r, _ = studio.load_preset(name)
            r["match"] = sess.recipe["match"]
            sess.commit(r)
            return _apply_recipe(sess, sess.recipe, mode, zm, px, py, f"preset {name}")

        preset_apply.click(on_preset_apply, [sess_state, preset_dd, *view_inputs], hist_outs)

        @guarded
        def on_save_preset(sess, name):
            if sess is None:
                raise gr.Error("Nothing to save yet.")
            name = (name or "").strip() or time.strftime("Preset %Y-%m-%d %H%M")
            studio.save_preset(name, sess.recipe)
            gr.Info(f"Saved preset “{name}”.")
            return gr.update(choices=studio.list_presets(), value=f"User / {name}")

        savepre_btn.click(on_save_preset, [sess_state, preset_name], preset_dd)

        @guarded
        def on_recipe_save(sess, name):
            if sess is None:
                raise gr.Error("Nothing to save yet.")
            name = (name or "").strip() or time.strftime("Recipe %Y-%m-%d %H%M")
            path = studio.save_recipe(name, sess.recipe)
            return gr.update(choices=studio.list_recipes(), value=os.path.splitext(os.path.basename(path))[0]), path

        recipe_save.click(on_recipe_save, [sess_state, recipe_name], [recipe_dd, recipe_file])

        @guarded
        def on_recipe_load(sess, name, mode, zm, px, py):
            if sess is None or not name:
                raise gr.Error("Upload a photo and choose a recipe.")
            r, w = studio.load_recipe_file(os.path.join(studio.dirs["recipes"], name + ".json"))
            sess.commit(r)
            return _apply_recipe(sess, sess.recipe, mode, zm, px, py, f"recipe {name}" + (f" ({len(w)} warnings)" if w else ""))

        recipe_load.click(on_recipe_load, [sess_state, recipe_dd, *view_inputs], hist_outs)

        @guarded
        def on_recipe_upload(sess, path, mode, zm, px, py):
            if sess is None or not path:
                raise gr.Error("Upload a photo first.")
            r, w = studio.load_recipe_file(path)
            sess.commit(r)
            return _apply_recipe(sess, sess.recipe, mode, zm, px, py, "recipe imported" + (f" ({len(w)} warnings)" if w else ""))

        recipe_upload.upload(on_recipe_upload, [sess_state, recipe_upload, *view_inputs], hist_outs)

        # ---- reference match ----------------------------------------------------------
        @guarded
        def on_match(sess, ref_path, method, strength, mode, zm, px, py):
            from ..match import compute_match, describe, load_reference
            if sess is None or not ref_path:
                raise gr.Error("Upload both your photograph and a reference.")
            ref, small = load_reference(ref_path, studio.workdir)
            data = compute_match(sess, small, ref.space, method, ref.original_name)
            r = sess.recipe
            r["match"]["data"] = data
            r["match"]["strength"] = strength if strength else 80
            sess.commit(r)
            return [*_apply_recipe(sess, sess.recipe, mode, zm, px, py, "reference matched"), describe(data)]

        ref_btn.click(on_match, [sess_state, ref_file, ref_method, controls["match.strength"], *view_inputs],
                      [*hist_outs, ref_md])

        # ---- LUTs ------------------------------------------------------------------------
        @guarded
        def on_lut_import(path):
            if not path:
                return gr.update(), ""
            lut = studio.luts.import_file(path)
            studio.refresh_registries()
            return gr.update(choices=studio.luts.names(), value=lut.name), studio.luts.describe(lut.name) + \
                " — select it and adjust intensity; compare with Before / After."

        lut_up.upload(on_lut_import, lut_up, [controls["lut.name"], lut_md]).then(
            on_commit, [sess_state, *view_inputs, *all_inputs], view_outs)

        @guarded
        def on_lut_export(sess, name):
            if sess is None:
                raise gr.Error("Upload a photograph first.")
            path, spatial = sess.export_lut(name or "darkroom_grade")
            if spatial:
                gr.Warning("Spatial effects are not representable in a LUT and were left out: "
                           + ", ".join(s.split('.')[1] for s in spatial))
            return path

        lut_export_btn.click(on_lut_export, [sess_state, lut_export_name], lut_file)

        # ---- export -------------------------------------------------------------------
        def on_fmt(sess, fmt, space, q):
            from ..export import format_warnings
            bits = sess.source.bit_depth if sess else 16
            return "\n\n".join(format_warnings(fmt, bits, space, q))

        gr.on([exp_fmt.change, exp_space.change, exp_q.change, upload.upload], on_fmt,
              [sess_state, exp_fmt, exp_space, exp_q], exp_warn)

        @guarded
        def on_export(sess, fmt, space, q, meta):
            if sess is None:
                raise gr.Error("Upload a photograph first.")
            t0 = time.time()
            res = sess.export(fmt, space, q, meta)
            files = [res["path"], res["sidecar"]] + ([res["raw_sidecar"]] if res.get("raw_sidecar") else [])
            msg = (f"Exported {res['width']}×{res['height']} {res['bits']}-bit {res['space']} in "
                   f"{time.time() - t0:.1f} s → {os.path.basename(res['path'])} · {res['metadata']}")
            warn = "\n\n".join(res["warnings"])
            return files, warn, _status_html(sess, msg, bool(res["warnings"]))

        exp_btn.click(on_export, [sess_state, exp_fmt, exp_space, exp_q, exp_meta], [exp_files, exp_warn, status])

        @guarded
        def on_export_dev(sess, space):
            if sess is None or sess.source.kind != "raw":
                raise gr.Error("Load a RAW file first.")
            res = sess.export_developed("TIFF 16-bit (master, lossless)", space)
            return [res["path"], res["sidecar"]], _status_html(sess, "developed RAW exported (not the RAW original)")

        dev_btn.click(on_export_dev, [sess_state, exp_space], [exp_files, status])

        def on_orig(sess):
            if sess is None:
                raise gr.Error("Upload a photograph first.")
            return [sess.source.path]

        orig_btn.click(on_orig, sess_state, exp_files)

        # ---- batch ------------------------------------------------------------------------
        @guarded
        def on_batch(sess, files, fmt, progress=gr.Progress()):
            if not files:
                raise gr.Error("Add photos to the batch.")
            r = sess.recipe if sess else R.default_recipe()
            out_zip, res = studio.batch(files, r, fmt, progress=lambda fs: progress.tqdm(fs, desc="Batch"))
            lines = [("✓ " if ok else "✗ ") + msg for ok, msg in res]
            return out_zip, "\n".join(f"- {l}" for l in lines)

        batch_btn.click(on_batch, [sess_state, batch_files, batch_fmt], [batch_out, batch_md])

    return demo, studio


def css_text():
    with open(os.path.join(os.path.dirname(__file__), "theme.css")) as f:
        return f.read()


def theme():
    return gr.themes.Base(primary_hue=gr.themes.colors.amber, neutral_hue=gr.themes.colors.zinc,
                          font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
                          font_mono=[gr.themes.GoogleFont("JetBrains Mono"), "ui-monospace", "monospace"]).set(
        body_background_fill="#111113", body_background_fill_dark="#111113",
        block_background_fill="#1a1a1d", block_background_fill_dark="#1a1a1d",
        block_border_color="#2c2c31", block_border_color_dark="#2c2c31",
        body_text_color="#d8d8dc", body_text_color_dark="#d8d8dc",
        input_background_fill="#212125", input_background_fill_dark="#212125",
        button_secondary_background_fill="#26262b", button_secondary_background_fill_dark="#26262b",
        button_secondary_text_color="#d8d8dc", button_secondary_text_color_dark="#d8d8dc",
        slider_color="#d9a441", slider_color_dark="#d9a441",
        button_primary_background_fill="#d9a441", button_primary_background_fill_dark="#d9a441",
        button_primary_text_color="#1a1408", button_primary_text_color_dark="#1a1408",
    )


def launch(workdir=None, share=None, server_port=None, debug=False, **kw):
    demo, studio = build_app(workdir)
    if share is None:
        from ..runtime import in_colab
        share = in_colab()
    demo.queue(default_concurrency_limit=1)
    allowed = [studio.workdir]
    return demo.launch(share=share, server_port=server_port, debug=debug, theme=theme(), css=css_text(),
                       allowed_paths=allowed, js="() => document.body.classList.add('dark')", **kw)
