# Colour management

This page describes exactly what happens to colour between the file you upload and the file you export.

## 1. Identifying the source

| Source | How its colour space is determined |
|---|---|
| JPEG / TIFF / PNG with an embedded ICC profile | The profile is parsed (matrix/TRC profiles: `rXYZ gXYZ bXYZ`, `wtpt`, `chad` and `rTRC gTRC bTRC` curves, gamma or table). Its primaries and transfer curve define the working space. The original ICC bytes are kept for "Same as source" export. |
| No ICC, EXIF Interoperability index `R03` | Adobe RGB (1998) (the camera "Adobe RGB" setting on Nikon and Canon bodies). |
| No ICC, no tag | sRGB, the standard for untagged images. The photo-information panel says so. |
| Unusable ICC (e.g. LUT-based, CMYK, corrupt) | sRGB is assumed and a note is shown. CMYK images are rejected. |
| RAW (NEF, CR2, DNG, ...) | LibRaw converts camera RGB to the RAW-stage colour space you choose (sRGB, Adobe RGB, Display P3, ProPhoto RGB) with gamma 1.0, 16-bit output and no auto-brightening. That space, in linear light, becomes the working space. |

The photo-information panel and the status bar always show the profile in use and how it was determined.

## 2. Working space

* Pixel data is decoded to **float32 linear light in the source's own primaries**. Nothing is converted to a different gamut on import.
* All colour spaces are stored as a 3×3 matrix to the ICC connection space (XYZ, D50; Bradford chromatic adaptation) plus a transfer function. Conversions between spaces are matrix products in linear light.
* Values are **not clipped** inside the pipeline; out-of-range values (bright highlights, wide-gamut colours) survive until export.

## 3. Where each stage works

| Stage | Domain | Why |
|---|---|---|
| Lens vignetting correction, white balance (Planckian/daylight locus + Bradford, luminance preserving), exposure, tone (highlights/shadows/whites/blacks/contrast applied to luminance as a ratio), dehaze, halation | Linear light, working primaries | These model light; they must be done on linear data. |
| Reference matching, clarity, texture, vibrance, saturation, HSL, colour grading | OkLab (computed from the working space) | Perceptually uniform: hue and saturation edits do not shift lightness. |
| Tone curves, lift/gamma/gain, film looks, LUTs, fade | sRGB-encoded values, Rec.709 primaries, unclamped | This is what curve tools, film looks and creative `.cube` LUTs are designed for. |
| Noise reduction, sharpening | OkLab lightness / chroma | Sharpen lightness only, so no colour fringes. |
| Creative vignette, chromatic aberration (effect) | Linear light | Optical effects. |
| Grain | sRGB-encoded | Perceptually even grain across tones. |

If no control in a stage is active, the stage is skipped entirely, so an unedited photo passes through bit-exact.

## 4. Preview

The browser preview is a reduced-size proxy, converted from the working space to sRGB, clipped and encoded to 8 bits **for display only**. Wide-gamut images are therefore previewed as sRGB. The 100% and 200% views are rendered from the full-resolution source. The preview is never used for export.

## 5. Export

| Output colour space option | What happens |
|---|---|
| Same as source/working space (default) | Encoded with the source's own transfer curve. The source's original ICC profile bytes are embedded unchanged. For RAW, an ICC profile for the chosen RAW space is generated and embedded. |
| sRGB | Linear working data → linear sRGB (matrix) → sRGB curve. An sRGB ICC v2 profile is embedded. This is the explicit option for the web and for most viewers. |
| Adobe RGB (1998), Display P3, ProPhoto RGB | Same, with the matching primaries, curve and generated ICC profile. |

* Out-of-gamut and over-range values are clipped only here. The percentage of clipped pixels (highlights and shadows/out-of-gamut separately) is reported in the export warnings and the sidecar.
* Gamut mapping is clipping per channel; there is no perceptual rendering intent. Converting a wide-gamut photo to sRGB can therefore clip saturated colours. The scopes and the clipping report show when this happens.
* 16-bit outputs use the full 0–65535 range; 8-bit outputs are rounded (not dithered).
* Generated ICC profiles are ICC v2.1 matrix/TRC profiles. Their accuracy was checked against LittleCMS: an Adobe RGB → sRGB conversion agrees within 0.59 of one 8-bit level.

## 6. LUTs and film looks

`.cube` LUTs are assumed to take and return sRGB-encoded, Rec.709-primary values (the convention for almost all creative LUTs). A LUT built for log footage or another colour space will look wrong; convert it first. LUT input is limited to the LUT's domain, so wide-gamut colours outside sRGB are pulled into the sRGB gamut where a LUT is applied.

Exported LUTs (`Export LUT`) use the same convention and contain only the point-wise part of the grade; spatial effects (grain, clarity, sharpening, vignette, halation and similar) cannot be represented in a LUT and are listed when they are left out.
