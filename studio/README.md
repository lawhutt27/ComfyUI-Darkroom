# Darkroom Studio

A professional, deterministic photo colour-grading and editing application for Google Colab, built with Python and Gradio. It lives in the `studio/` folder of this repository and reuses parts of ComfyUI-Darkroom (film data and the camera-matched base curve), but it is a standalone app: it does not need ComfyUI.

## What it is (and is not)

* **Non-generative.** Every change is a numeric recipe executed by NumPy/OpenCV code in `darkroom_studio/`. Nothing redraws, invents, removes or moves content (lens distortion correction, which you must request, is the only geometric operation).
* **Prompt to recipe, not prompt to pixels.** A sentence like *"warm 1970s analog film with muted greens and soft highlights"* is turned into a validated JSON recipe, and Python executes it. The default interpreter is local and offline. An optional OpenRouter mode sends **only the prompt text and the current recipe numbers**, never the photograph; its reply is schema-checked and never executed.
* **Your originals are safe.** Uploads are copied to a read-only library (`originals/`, mode 0444, SHA-256 recorded). RAW files are never altered. Exports go to new files.
* **Exports come from the full-resolution float32 source**, rendered in strips, never from the 8-bit preview. Dimensions are never changed; an export whose size differs from the source is refused.

## Quick start (Colab)

1. Open `studio/Darkroom_Studio.ipynb` in Colab (use the "Open in Colab" badge in the notebook, or File → Open notebook → GitHub, `lawhutt27/ComfyUI-Darkroom`).
2. Press ▶ on **Start Darkroom Studio**. That one cell downloads the app, installs only what is missing (it never upgrades Colab's own NumPy, SciPy, Pillow, OpenCV or PyTorch), runs a quick self-check and prints a link to the app. The first run takes about a minute; later runs a few seconds.
3. Two tick-boxes on the cell: *save_to_google_drive* keeps originals, exports and presets in `My Drive/Darkroom Studio`; *public_link* gives a `gradio.live` link (untick it to show the app inside the notebook).
4. Optional: to use the OpenRouter interpreter, add a Colab secret named `OPENROUTER_API_KEY` (key icon in the left sidebar), enable notebook access, and run the start cell again. Never paste a key into a cell.

A second, optional cell runs the automated tests. The same steps can be called from Python: `from darkroom_studio.colab import start; start()`.

## Quick start (local)

```bash
cd studio
pip install -r requirements.txt          # or requirements-min.txt
sudo apt-get install libimage-exiftool-perl   # optional, for full metadata copying
python -m darkroom_studio --port 7860    # --workdir DIR, --share
```

## Supported files

| Input | Notes |
|---|---|
| NEF, CR2, DNG (and other LibRaw RAWs) | Developed with LibRaw 0.22 via rawpy. Tested on real Nikon D5300 (12- and 14-bit) and Canon EOS 1000D files. |
| JPEG | 8-bit, EXIF orientation applied, ICC or EXIF colour space honoured. |
| TIFF | 8/16-bit and float, via tifffile. Alpha is kept separately. CMYK is rejected with a clear message. |
| PNG | 8/16-bit, alpha kept separately. |

| Output | Bit depth | Compression |
|---|---|---|
| TIFF 16-bit (master) | 16 | lossless (zlib) |
| PNG 16-bit | 16 | lossless |
| PNG 8-bit | 8 | lossless |
| JPEG | 8 | lossy, quality 95–100, 4:4:4 chroma |
| Developed RAW | 16-bit TIFF of the RAW development without grading, labelled as not the RAW original |
| Original file | Byte-identical download of the upload |

Each export writes a `.darkroom.json` sidecar with the full recipe, source hash, RAW development settings, colour conversion and warnings. The app warns whenever an export reduces bit depth or uses lossy compression, and reports how many pixels were clipped.

## Interface

* **Top bar:** prompt, interpreter choice (local rules or OpenRouter), Apply, Undo, Redo, Reset, Save preset.
* **Left:** upload, RAW development, photo information, camera metadata with suggested starting points, presets, reference-image matching, saved recipes, batch.
* **Centre:** large preview with Edited / Original / Before-after slider views, Fit / 100% / 200% zoom with panning (the 100% view is rendered from the full-resolution source), export panel, scopes (histogram, waveform, vectorscope).
* **Right:** Basic, Tone curve (master/R/G/B), Colour mixer (HSL, 8 bands), Colour grading wheels, Lift/Gamma/Gain, Effects (clarity, texture, dehaze, fade, grain, vignette, halation, chromatic aberration), Detail (sharpening, noise reduction), Lens corrections, Film emulation, LUT, Recipe JSON.
* **Bottom:** original and output dimensions, bit depth, colour profile, format, preview size and processing time.

Every control is independent and resettable, every change is undoable, and Reset itself can be undone.

## Package layout

```
studio/
  Darkroom_Studio.ipynb     Colab notebook (one start cell, one optional test cell)
  darkroom_studio/
    colorspace.py           colour spaces, ICC read/write, conversions
    imaging.py              loaders, orientation, original protection
    raw.py                  LibRaw development stage
    metadata.py             ExifTool / exifread metadata, EXIF writing
    recipe.py               schema, validation, history (undo/redo)
    engine.py               the rendering pipeline
    film.py, lut.py         film looks, .cube LUTs
    prompt.py               rule interpreter, OpenRouter client
    match.py                reference colour matching
    export.py, session.py   export and editing sessions
    colab.py                one-call setup and launch used by the notebook
    advisor.py, presets.py, scopes.py, runtime.py
    ui/app.py, ui/theme.css Gradio interface
  tests/                    pytest suite (synthetic and real-camera, kept apart)
  tools/fetch_samples.py    downloads the CC0 camera files used by the camera tests
  tools/e2e_ui.js           Playwright browser walk-through of the interface
  docs/                     colour management, licences, troubleshooting, test report, limitations
```

## Documentation

* [Colour management](docs/COLOUR_MANAGEMENT.md)
* [Dependencies and licences](docs/LICENSES.md)
* [Troubleshooting](docs/TROUBLESHOOTING.md)
* [Test report](docs/TEST_REPORT.md)
* [Known limitations](docs/LIMITATIONS.md)

## Running the tests

```bash
cd studio
python tools/fetch_samples.py samples      # optional: real camera files (CC0, ~90 MB)
python -m pytest tests -q                  # everything
python -m pytest tests -q -m "not camera"  # synthetic only
python -m pytest tests -q -m camera        # real-camera only (skipped if samples are absent)
```
