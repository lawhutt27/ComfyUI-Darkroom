# Troubleshooting

## Installation and launch

**`TypeError: ... unexpected keyword argument 'theme'` or the app looks unstyled.**
Darkroom Studio needs Gradio 6, where theme and CSS are passed to `launch()`. Re-run notebook cell 2; it installs `gradio>=6.2,<7` only when the installed version is older.

**Cell 2 says NumPy/SciPy/Pillow is older than tested.**
The notebook deliberately never upgrades packages Colab ships, because that can break other Colab libraries mid-session. The oldest tested set is in `requirements-min.txt`. If you must upgrade, do it, then use *Runtime → Restart session* before running the cells again.

**`ModuleNotFoundError: darkroom_studio`.**
Run cell 4; it puts `studio/` on `sys.path`. If you restarted the runtime, run cells 1 to 4 again.

**The Colab link does not open, or shows "No interface is running".**
The `*.gradio.live` link only works while cell 13 is running. Re-run cell 13 to get a new link. Corporate networks sometimes block `gradio.live`; set `SHARE = False` in cell 13 to show the app inside the notebook instead.

**The app stops responding after a long idle.**
Colab disconnects idle runtimes. Your originals, exports, presets and recipes are in the working folder (`/content/darkroom_work` by default, lost when the runtime is recycled). Mount Google Drive and point `WORKDIR` at it (see cell 10) to keep them.

## Opening photos

**"CMYK ... files are not supported for editing".**
Convert the file to RGB in the application that produced it. Converting CMYK to RGB needs the press profile and rendering intent, which Darkroom Studio does not guess.

**A RAW file fails to open.**
Check the camera is supported by LibRaw 0.22 (cell 3 prints the version). Very new cameras can need a newer rawpy. The error message shows LibRaw's reason.

**My RAW looks too dark/bright/flat compared with the camera JPEG.**
The RAW stage uses no auto-brightening, so exposure is exactly what the sensor recorded. Use *RAW development → RAW exposure*, or the Basic panel. The default "Camera-like" base curve approximates a typical camera rendering; choose "Linear (scene-referred)" for a flat start.

**Highlights are flat grey or magenta in a RAW.**
These pixels were clipped by the sensor. In *RAW development*, try *Highlight recovery: Reconstruct (5)* or *Blend*, or a negative RAW exposure. The photo-information panel reports the clipped percentage.

**Colours look different from another viewer.**
Check the profile shown in the status bar. Untagged images are assumed to be sRGB. If the other viewer is not colour-managed, a wide-gamut (Adobe RGB, ProPhoto) export will look dull there; export with *Colour space: sRGB* for the web.

**The photo is rotated.**
EXIF orientation is applied on import, and exports are written upright with Orientation = 1. If the camera recorded the wrong orientation, the photo will be rotated the same way as in any other EXIF-aware viewer; Darkroom Studio has no manual rotate (rotating would change the stored dimensions).

## Editing

**The grain/sharpening in the Fit view does not match the export.**
The Fit preview is a reduced proxy, so fine-scale effects are approximate there. Switch to 100% zoom: that view is rendered from the full-resolution source with exactly the export pipeline.

**The prompt did nothing.**
The local interpreter only understands its vocabulary (styles, film names, colour words, tone words). The explanation under the preview lists what it recognised; "No recognised" means nothing matched. Try simpler words ("warmer", "muted greens", "soft highlights", "Portra").

**The OpenRouter option falls back to local rules.**
The message says why. Usually no `OPENROUTER_API_KEY` secret is set, notebook access to it is not enabled, or the network blocked the request. Set `OPENROUTER_MODEL` to use another model.

**A LUT looks wrong (very contrasty, washed out or colour-shifted).**
LUTs are applied to sRGB-encoded data. A LUT that expects log footage (S-Log, LogC, V-Log) or a different colour space will not look right. Use a "Rec.709"/"display" creative LUT.

**Reference matching changed my photo too much.**
Lower the *Strength* slider, or use the *Colour only* method so tones are left alone. Matching only transfers colour statistics and a tone curve; it never copies content.

## Export

**Why is my JPEG export warned about?**
JPEG is 8-bit and lossy. The app always says so. Keep the 16-bit TIFF master for archiving and re-editing; never re-edit a JPEG export.

**Export says a percentage of pixels was clipped.**
Parts of the grade go beyond the output range or gamut. Lower Exposure or Whites, use Highlights, or export in a wider colour space (16-bit only).

**Metadata is missing from exports.**
Install ExifTool (cell 2 does it with apt). Without it a basic EXIF block (camera, lens, exposure, date) is written instead of the full metadata. *Keep metadata* must be ticked.

**Export runs out of memory.**
The status bar shows the full-resolution size. Rendering is done in strips, but the full float32 image is held in memory (about 300 MB for 24 MP) and a heavy grade can briefly need about 14 such buffers (roughly 4 GB for 24 MP). The free Colab tier (about 12 GB) handles 24 MP files comfortably; `runtime.report()` (cell 5) shows the estimated safe size for your runtime.

## Tests

**Camera tests are "skipped".**
They need the real sample files. Run `python tools/fetch_samples.py samples` (or set `RUN_CAMERA_TESTS = True` in cell 12). Skipped is not passed; the report lists the reason.

**The browser walk-through (`tools/e2e_ui.js`).**
It needs Node with Playwright and a running app: `python -m darkroom_studio --port 7860`, then `node tools/e2e_ui.js samples/D5300_14bit.NEF tools/teal_test.cube /tmp/shots`.
