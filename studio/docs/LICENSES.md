# Dependencies and licences

Darkroom Studio's own code (everything under `studio/`) is released under the same terms as the repository it lives in (MIT, see the note below).

## Python dependencies

| Package | Version tested (min / current) | Licence | Used for |
|---|---|---|---|
| NumPy | 2.0.2 / 2.5.4 | BSD-3-Clause | All pixel processing |
| SciPy | 1.14.1 / 1.18.1 | BSD-3-Clause | PCHIP curve interpolation |
| Pillow | 11.0.0 / 12.3.0 | MIT-CMU (HPND) | JPEG and 8-bit PNG read/write, EXIF |
| opencv-python-headless | 4.10.0.84 / 5.0.0.93 | Apache-2.0 | Resampling for previews, blurs, remapping, 16-bit PNG |
| tifffile | 2024.8.30 / 2026.11.8 | BSD-3-Clause | TIFF read/write (16-bit, ICC, compression) |
| rawpy | 0.24.0 / 0.28.0 | MIT | Python binding to LibRaw |
| LibRaw (inside the rawpy wheel) | 0.21 / 0.22.2 | LGPL-2.1 or CDDL-1.0 (user's choice) | RAW decoding and demosaicing |
| exifread | 3.0.0 / 3.5.1 | BSD-3-Clause | Metadata fallback when ExifTool is missing |
| Gradio | 6.2.0 / 6.31.0 | Apache-2.0 | User interface |
| pytest (tests only) | 9.2.0 | MIT | Test suite |

LibRaw is dynamically bundled in the rawpy wheel, unmodified. Using it under LGPL-2.1 from Python imposes no obligations on Darkroom Studio's own licence; if you redistribute the wheel, keep its licence files.

## System tools

| Tool | Version tested | Licence | Used for |
|---|---|---|---|
| ExifTool (`libimage-exiftool-perl`) | 12.76 | Artistic-1.0 / GPL-1.0+ (Perl terms) | Reading full maker notes and lens IDs; copying EXIF/XMP/IPTC to exports. Called as a separate program, never linked. Optional. |

## Reused material from the reference repositories

| Source | Licence | What was reused |
|---|---|---|
| ComfyUI-Darkroom (this repository) | MIT, as stated in its README and `pyproject.toml` | `data/color_stocks.py` and `data/bw_stocks.py` (film-stock parameters, loaded at runtime); `CAMERA_RAW_DEFAULT_CURVE` from `utils/raw_loader.py` (copied into `raw.py`, attributed); ideas adapted for halation, resolution-aware grain and `.cube` axis order (marked in the code). |
| spectral_film_lut (vendored in `third_party/`, by Jan Lohse) | MIT (licence file present) | Its pre-baked `.cube` LUTs in `data/spectral_luts/` are used only by the test suite as real third-party LUT files. |
| rawpy, Gradio | MIT, Apache-2.0 | Used as libraries, not copied. |
| OpenImageIO | Apache-2.0 | Evaluated, **not used** (see below). |

### Not used, on purpose

* **`data/c1_film_luts.npz`** in ComfyUI-Darkroom. Its README describes these 56 LUTs as rendered by Capture One from the Capture One Film Styles pack. That makes them derived from a commercial product, so Darkroom Studio does not load them. The Studio film looks are its own parameter sets instead.
* Darkroom stocks whose parametric fallback crushes shadows or reverses tones when used without those LUTs (29 of them) are screened out automatically and not shown.
* No diffusion, segmentation or other generative models are downloaded or used.

### Note on the ComfyUI-Darkroom licence

The README (`## License: MIT`) and `pyproject.toml` declare MIT, but **the repository (and its upstream) has no `LICENSE` file** with the MIT text and copyright line. The intent is clear, but strictly an MIT grant should ship its notice. Adding a `LICENSE` file to the repository (copyright Jeremie Louvaert) would remove the ambiguity; this was not done here because it is the original author's licence to state.

## OpenImageIO evaluation

The brief asked for OpenImageIO to be evaluated as an optional component.

* The PyPI wheel (`OpenImageIO` 3.2.2) installs on Python 3.12 and reads and writes TIFF, PNG and JPEG correctly.
* That wheel is built **without the LibRaw plugin** (`raw` is absent from its extension list). Worse than failing, it silently opens NEF and CR2 files through its TIFF reader and returns only the embedded 8-bit preview: 1936×1288 for the EOS 1000D CR2 and 160×120 for the D5300 NEF, instead of the 3906×2602 and 6016×4016 sensor data. Using it for RAW would violate the "never silently resize" rule. Real RAW support would require building OIIO from source, which is impractical in Colab.
* Everything else it would provide (16-bit TIFF with ICC, PNG, JPEG, resampling) is already covered by tifffile, OpenCV and Pillow, which are smaller and already present in Colab.

Decision: OpenImageIO is not a dependency and is not used. Nothing in the app depends on it.

## Film names

Film-stock names (Portra, Ektar, Gold, Vision3, Fuji, Velvia, Cinestill, Ilford, Tri-X) are trademarks of their owners and are used only to describe the look each simulation is inspired by. The looks are aesthetic simulations, not reproductions or measurements of those products, and are labelled "-inspired" in the interface.
