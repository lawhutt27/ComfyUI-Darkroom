# Test report

Date: 2026-11-11. Python 3.12.3, Linux x86-64, 4 CPU cores, 16 GB RAM, no GPU (similar to a free Colab runtime).

## Summary

| Dependency set | Result |
|---|---|
| Current (`requirements.txt`: numpy 2.5.4, gradio 6.31.0, rawpy 0.28.0 / LibRaw 0.22.2, Pillow 12.3.0, OpenCV 5.0) | **317 passed**, 0 failed, 0 skipped, 3 min 36 s |
| Oldest supported (`requirements-min.txt`: numpy 2.0.2, gradio 6.2.0, rawpy 0.24.0 / LibRaw 0.21.3, Pillow 11.0.0, OpenCV 4.10) | **317 passed**, 0 failed, 0 skipped, 3 min 34 s |
| Notebook start cell in a fresh Colab-like environment (only numpy 2.0.2, scipy 1.14.1, Pillow 11.0, OpenCV 4.10 preinstalled) | Installed tifffile, rawpy, exifread and Gradio 6.31 in about a minute **without changing NumPy, SciPy, Pillow or OpenCV**; self-check passed; the app launched |
| Notebook start cell on an environment that already has everything | Nothing installed; app launched in a few seconds |
| Notebook test cell (`run_tests`) in that fresh environment (Gradio 6.31 with NumPy 2.0.2), with the real camera files | 315 passed, 2 slow tests deselected |
| Browser walk-through, `tools/e2e_ui.js` (Chromium, Playwright) on the D5300 NEF and the Pentax DNG | Every step passed, 0 browser errors, 0 server tracebacks |

Running on the older set found one real bug (rawpy 0.24 has no camera-crop fields), and the real DNG found another (the Pentax maker-note ISO code was shown instead of the EXIF ISO). Both are fixed and covered by tests.

## Synthetic tests and real-camera tests are separate

| File | Kind | Tests | What it covers |
|---|---|---|---|
| `test_phase1_core.py` | Synthetic | 213 | Import of JPEG/TIFF/PNG (8 and 16-bit, alpha, rotated, Adobe RGB, untagged); exports in every format; EXIF and ICC retention; sRGB conversion; dimensions under every control; before/after; undo/redo/reset; originals untouched; preview never used for export; strip/crop renders identical to full renders |
| `test_phase2_film_lut.py` | Synthetic plus third-party `.cube` files | 52 | All 22 Studio film looks and 20 Darkroom stocks (no tone reversal, strength blending), B&W looks are monochrome, `.cube` parse/write/axis order/1D/malformed, real spectral_film_lut LUTs, LUT import and intensity, LUT export matches the render |
| `test_phase2_raw_camera.py` | **Real camera files** (`-m camera`) | 28 | See below |
| `test_phase3_prompt_match.py` | Synthetic plus a local mock LLM server | 18 | All six example prompts from the brief, intensity and negation words, relative and clamped edits, no geometry from prompts, schema rejects code and garbage, LLM reply validated not executed, request contains no image data, key handling, reference matching moves colour but not content |
| `test_phase4_tools.py` | Synthetic | 6 | Built-in and user presets, saved recipes, scopes, batch at full resolution with originals untouched, the Gradio app builds |

Camera tests are skipped (never passed) when the sample files are absent; `tools/fetch_samples.py` downloads them and checks their SHA-256.

### Real camera files

From raw.pixls.us (listed there as CC0). Not committed to the repository.

| File | Camera | Lens (read from metadata) | Decoded size | Bits |
|---|---|---|---|---|
| D5300_14bit.NEF | Nikon D5300 | AF-S DX Nikkor 18-140mm f/3.5-5.6G ED VR | 6016 × 4016 | 14 |
| D5300_12bit.NEF | Nikon D5300 | AF-S DX Nikkor 18-140mm | 6016 × 4016 | 12 |
| EOS1000D.CR2 | Canon EOS 1000D / Rebel XS | Canon EF-S 18-55mm f/3.5-5.6 IS | 3906 × 2602 | 12 |
| PentaxKx.DNG | Pentax K-x (camera-native DNG 1.1) | smc PENTAX-DA L 18-55mm | 4309 × 2868 | 12 |
| D5300 camera JPEG | Nikon D5300 (the full-size JPEG the camera embeds in the NEF, Nikon's own processing) | — | 6000 × 4000 | 8 |

Camera tests: full-resolution decode of all three NEF/CR2 files with camera, lens, ISO, aperture, shutter and focal length; every highlight mode and demosaic algorithm; white-balance options and RAW exposure (+1 EV doubles the median, 1.8–2.2×); all four RAW working spaces with matching ICC in the export; full-resolution 16-bit TIFF export with ExifTool metadata (lens, ISO, Orientation = 1) and unchanged SHA-256 of the RAW; re-developing keeps the edit history; "developed RAW" export is labelled as not the RAW original; the camera JPEG import and export; camera-specific advice; the DNG decode, metadata and export.

## The 18 required checks

| # | Requirement | Test(s) | Data |
|---|---|---|---|
| 1 | NEF decode | `test_raw_decode_full_resolution[D5300_14bit/12bit]` | Real |
| 2 | CR2 decode | `test_raw_decode_full_resolution[EOS1000D.CR2]` | Real |
| 3 | JPEG import | `test_import_formats[jpeg*]`, `test_real_camera_jpeg_from_nef` | Synthetic and real |
| 4 | TIFF import | `test_import_formats[tiff16/tiff8]` | Synthetic |
| 5 | PNG import | `test_import_formats[png16/png8]`, `test_16bit_png_not_truncated_to_8bit`, `test_alpha_channel_preserved` | Synthetic |
| 6 | Full-resolution export | `test_export_dimensions_all_formats`, `test_raw_export_16bit_tiff_...`, `test_real_camera_dng` | Both |
| 7 | 16-bit TIFF export | `test_full_resolution_16bit_tiff_export_and_reopen`, `test_raw_export_16bit_tiff_...` | Both |
| 8 | EXIF retention | `test_exif_retained[*]`, `test_exif_fallback_without_exiftool`, ExifTool check in the RAW export test | Both |
| 9 | ICC retention | `test_icc_profile_retained_same_space[*]`, `test_explicit_srgb_conversion`, `test_raw_wide_gamut_working_space_and_icc[*]` | Both |
| 10 | Before/after comparison | `test_before_after_comparison`; browser walk-through (slider view) | Synthetic, UI |
| 11 | Prompt → recipe | `test_example_prompt_1970s_warm_muted_soft`, `test_user_example_prompts[*]`, `test_llm_reply_is_validated_not_executed` | Synthetic |
| 12 | Undo/redo | `test_undo_redo_reset`; browser walk-through | Synthetic, UI |
| 13 | Reset | `test_undo_redo_reset` (reset is itself undoable); browser walk-through | Synthetic, UI |
| 14 | No resize | `test_every_control_preserves_dimensions[*]`, `test_export_never_uses_preview_proxy`, export refuses a size mismatch | Synthetic |
| 15 | No crop | `test_colour_controls_do_not_crop_or_shift` (1-pixel border marker survives), `test_crop_and_strip_renders_match_full` | Synthetic |
| 16 | Original unchanged | `test_original_file_never_modified`, SHA-256 checks in the RAW, DNG and batch tests | Both |
| 17 | Colour adjustments keep dimensions | `test_every_control_preserves_dimensions[*]` (every numeric control at extreme values) | Synthetic |
| 18 | Reopened TIFF has expected dimensions and bit depth | `test_full_resolution_16bit_tiff_export_and_reopen`, RAW and DNG export tests (reopened with tifffile: shape and `uint16`) | Both |

## Browser walk-through

`tools/e2e_ui.js` drives the real interface in Chromium: upload → slider → undo → redo → prompt → 100% zoom → Original view → reset → preset → LUT import → full-resolution export → scopes. Results on the D5300 14-bit NEF:

* Upload and develop of a 24 MP NEF: status bar shows 6016 × 4016, 14-bit, LibRaw sRGB linear.
* Proxy preview updates: 52–60 ms. 100% full-resolution crop: 0.56 s. Preset with film look: about 1 s.
* Full-resolution 16-bit TIFF export with the 1970s preset and a LUT: 19.7 s, 133 MB, metadata copied with ExifTool.
* 0 browser errors, 0 server tracebacks. The same run on the Pentax DNG exported 4309 × 2868 in 9.6 s.

## Not tested

* A Google Colab runtime itself. The notebook was executed in a local Python 3.12 environment with the same package versions Colab uses; the `apt` install of ExifTool, Colab secrets and the `gradio.live` share link were not exercised here.
* A real OpenRouter request. The client was tested against a local mock server (request shape, auth header, no image data, reply validation), not the live service.
* GPU execution (not implemented; the engine is CPU-only).
* Cameras other than the four above.
