# Known limitations

These are honest limits of the current version. None of them can damage an original file.

## Processing

* **CPU only.** The engine is NumPy/OpenCV. A GPU is detected and reported, but not used. A heavy grade on a 24 MP RAW exports in about 20 s on a 4-core Colab CPU; the preview proxy updates in about 50–100 ms and a 100% view in about 0.5 s.
* **The Fit preview is approximate for fine detail.** Grain, sharpening, noise reduction and texture are scaled to the proxy, so judge them at 100%. The 100% and 200% views use the exact export pipeline.
* **No lens profiles for the kit lenses.** The AF-S DX 18-140mm and EF-S 18-55mm are recognised from metadata, but distortion, vignetting and chromatic-aberration correction are manual sliders, not profile-based.
* **Lens distortion correction moves content near the edges.** It is the only geometric operation, it is off unless you set it, and the output keeps the source dimensions. Areas that the correction pulls in from outside the frame are filled by repeating edge pixels (or black, if chosen), never invented.
* **Dehaze** uses a dark-channel prior estimated on a 512 px analysis copy. Strong values can shift colour in skies.
* **Gamut mapping is clipping.** Converting wide-gamut data to a smaller output space clips per channel; there is no perceptual rendering intent. The export reports the clipped percentage.
* **The creative stage (curves, lift/gamma/gain, film looks, LUTs) runs in sRGB-encoded values.** Colours outside sRGB in a wide-gamut source are pulled into sRGB where a film look or LUT is applied.
* **ICC support covers matrix/TRC profiles** (sRGB, Adobe RGB, Display P3, ProPhoto, camera and monitor profiles of that kind). LUT-based (A2B) profiles fall back to sRGB with a visible note. CMYK and greyscale-with-profile sources are not supported.
* **8-bit outputs are rounded, not dithered.** Use 16-bit TIFF or PNG for smooth gradients.
* **Alpha** is preserved for TIFF/PNG sources and exports, passed through unedited; it is dropped (with a warning) for JPEG.

## RAW

* **One RAW development per photo.** RAW settings (white balance preset, exposure, highlight mode, demosaic, colour space) are applied by LibRaw; changing them re-develops and keeps the grade. There is no custom white-balance picker in the RAW stage (use Temperature/Tint in Basic).
* **Camera crop hints are recorded, not applied** (to honour "never crop"). LibRaw's full output is used, which can include a few extra edge pixels compared with the camera JPEG.
* **Only the Nikon D5300 and Canon EOS 1000D were tested with real files.** Other cameras supported by LibRaw 0.22 should work but were not verified. DNG is supported through LibRaw and was tested only with synthetic files, not a real camera DNG.

## Film looks and LUTs

* **Film looks are aesthetic simulations**, tuned by eye on real photographs. They are not measured reproductions of any product and are named "-inspired".
* **LUTs are assumed to be sRGB/Rec.709 display LUTs.** Log or other-space LUTs need converting first.
* **Exported LUTs contain only the point-wise part of a grade**; spatial effects are listed as omitted.

## Prompts

* **The local interpreter is a vocabulary, not language understanding.** It recognises styles, film names, decades, colour and tone words, intensity words ("slightly", "very") and negation ("no grain"). Anything outside it is ignored and reported. Sentences with unusual phrasing work better with the OpenRouter option.
* **The OpenRouter option needs network access and a key**, and its choices depend on the model. Its output is clamped to the schema, so it can never do more than the sliders can.

## Interface

* **One user at a time.** Requests are queued (concurrency 1). The shared Colab link gives anyone who has it access to the session's working folder while it runs.
* **No manual crop or rotate.** Deliberately left out: the brief prioritises never changing dimensions.
* **Batch** applies one recipe to many photos at full resolution, one after another, and returns a zip. It has no per-photo adjustment step.
* **Working files live in the runtime** unless you point the working folder at Google Drive (tick *save_to_google_drive* on the notebook's start cell).
