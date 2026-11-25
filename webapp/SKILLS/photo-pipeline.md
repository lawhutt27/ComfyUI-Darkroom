# Skill: the Darkroom photo pipeline

Exact interfaces for the three tool families. Everything lives in
`backend/darkroom_web/` and is called the same way at preview and at full
resolution. All images are `float32 (H, W, 3)` **linear light in the
source's working space** (`project.source.space`, a
`darkroom_studio.colorspace.ColorSpace`) unless marked `u8`.

Ground rules for anyone adding a tool:
* A tool is either a **grade** (a pure per-pixel or neighbourhood function of
  the whole image, expressed as recipe values) or a **pixel layer** (a local
  content fix with frozen, normalised geometry). Pick one.
* Geometry is stored normalised (0-1); never store level pixels.
* Pixel layers must return untouched pixels **bit-identical** (`np.where`
  on the mask, not `img + 0 * delta`). Tests check this.
* Nothing is detected or generated at render time. Detection and provider
  calls happen once, when the layer is created, and their results are frozen
  into the layer (or its assets).

---

## 1. `.cube` LUTs and reference style matching

### Load / import
```python
store.import_lut(path) -> darkroom_studio.lut.CubeLUT      # validates, copies into library/luts
store.studio.luts.names() -> ["None", "warm", ...]          # registry used by stack validation
```
`parse_cube` accepts 1D and 3D `.cube` files (Adobe/Resolve convention, R
fastest, `DOMAIN_MIN/MAX`). LUTs are assumed to take and return
**sRGB-encoded, Rec.709** values, which is what creative LUTs expect.

### Apply
The engine applies a LUT inside its encoded stage:
```python
store.studio.luts.apply(name, E, strength)   # E: sRGB-encoded float (H, W, 3); strength 0..1
```
`TetraLUTRegistry` (`lutx.py`) interpolates 3D LUTs **tetrahedrally**
(`lutx.tetrahedral(table, x)`), processing 256-row chunks so a 24 MP
frame stays within memory. 1D LUTs use per-channel linear interpolation.
Stack form:
```json
{"type": "lut", "params": {"name": "warm", "strength": 60}}
```

### Reference style match
```python
layer = project.style_match(ref_path, strength=70, method="Colour + tone" | "Colour only")
```
1. The reference is decoded like any upload (RAW works) and reduced to 768 px.
2. The target is measured on its 512 px analysis proxy **after** the
   current grade but **before** the match stage (`engine.render(..., stop="pre_match")`).
3. Both are converted to OkLab. Stored: per-channel mean and standard
   deviation for each (Reinhard transfer), plus, for *Colour + tone*, a
   smoothed, slope-limited, monotonic lightness curve (257 points) from
   quantile matching.
4. The result is frozen into the layer's `data`, so preview and export apply
   the identical transform. No pixels or shapes of the reference are kept.

---

## 2. Targeted fixes

### Detection (run once, on the 1600 px proxy)
```python
retouch.detect_eyes(linear, space)   -> (eyes: [{"x","y","r"}], n_faces, method)
retouch.detect_mouths(linear, space) -> (boxes: [{"x","y","w","h"}], n_faces, method)
```
* `method` is `"yunet"` (OpenCV YuNet, 5 landmarks), `"haar"` (OpenCV 4
  fallback with typical landmark positions) or `"none"`.
* Eye circle radius = 0.17 × interocular distance; `r` is a fraction of the
  image's **long edge**.
* Mouth box = 1.15 × mouth-corner distance wide, half as tall, centred on
  the corners.

### Red-eye
```python
retouch.red_eye(img, space, eyes, strength)   # in place; strength 0..100
```
Inside each circle (in linear sRGB): core mask where `R / max(G, B)` is
3-5× or more (flash red-eye; skin is ~2×), closed by a small dilation,
softened, then gated again by redness so iris and skin never change. The
fix sets R to the mean of G and B and darkens by 10 %, which leaves glints
(neutral) untouched. Stack form:
`{"type": "red_eye", "params": {"strength": 100, "eyes": [{"x": .39, "y": .2, "r": .016}]}}`

### Teeth whitening
```python
retouch.whiten_teeth(img, space, regions, strength)   # in place; strength 0..100
```
Inside each box, in OkLab: tooth mask = brightest pixels relative to the
box's 95th lightness percentile, excluding red-pink (lips, gums, a > 0.035),
blue, and high-chroma pixels, opened, blurred and elliptically feathered to
the box edge. The correction:
* `b ← b − 0.8·s·max(b − 0.004, 0)` (removes yellow, never goes blue)
* `a ← a·(1 − 0.35·s)`
* `L ← L + 0.05·s·(1 − L)` (gentle, capped lift; an offset, so texture stays)

25-45 % looks natural. Stack form:
`{"type": "teeth_whiten", "params": {"strength": 35, "regions": [{"x":..,"y":..,"w":..,"h":..}]}}`

### Instagram-style looks
`looks.py` holds partial recipes (`IG_LOOKS`, plus the Darkroom Studio film
presets as `Film / …`). A `look` layer adds the look's slider offsets ×
`amount/100` to the recipe; shape parameters (grain size, colour-wheel hue,
…) and film stock names are taken as-is. Add a look by adding an entry; its
values are validated against the recipe schema when folded.

---

## 3. Prompt-based minor edits

### Provider interface (`providers/base.py`)
```python
class ImageEditProvider:
    id: str; label: str
    sends_pixels_off_device: bool      # shown to the user before they press Run
    needs_mask: bool                   # True -> UI requires a target box
    def available(self) -> bool
    def edit(self, req: EditRequest) -> EditResult

EditRequest(image: u8 (h, w, 3) sRGB, prompt: str, user_prompt: str, mask: u8 (h, w) | None, model: str)
EditResult(image: u8 (h', w', 3) sRGB, model: str, notes: str)
```
Shipped: `openrouter` (Nano Banana / Gemini image models via
`/chat/completions` with `modalities: ["image","text"]`), `gemini`
(`models/{model}:generateContent`), `local` (OpenCV Telea inpainting inside
the box; offline; ignores the prompt text). Register new ones in
`providers/__init__.py`. A ComfyUI provider for Qwen-Image-Edit or Flux
Kontext would POST a workflow to a ComfyUI server's `/prompt`, poll
`/history/{id}`, and fetch the output via `/view`.

### Creating an AI layer
```python
layer, sent_prompt = project.ai_edit(provider, prompt, box=None|{x,y,w,h}, preserve=True,
                                     feather=40, sensitivity=50, strength=100, model="")
```
1. **Crop**: the target box plus 25 % context per side, or the whole
   frame. Cut from the **master** (with the pixel layers below applied) and
   downsampled to ≤ `DARKROOM_PROVIDER_EDGE` (1536) px, so small targets are
   sent at full detail.
2. **Prompt**: `prompting.build_prompt(user, preserve, targeted)` appends
   `TARGET_HINT` when there is a box and `PRESERVE_INSTRUCTIONS` when
   *Preserve* is on.
3. **Call** the provider; **align** its answer (`composite.align`: resize,
   then ECC translation and a small affine refinement, rejected if >6 %).
4. **Store** the sent image and the aligned answer as PNG assets; the layer
   keeps their ids, the crop, and a JSON `notes` record (provider, model,
   seconds, pixels sent, alignment).

### Compositing (every render, any level)
```python
composite.composite(img, space, params, input_u8, output_u8, full_long)   # in place
```
* `change_map` = blurred CIELAB ΔE / 100 between sent and returned images.
* Mask = (box, feathered by `feather` full-res px) × (when `preserve`:
  `change > 0.12 − 0.105·sensitivity/100`, opened, dilated, feathered).
* `structure_change` = blurred |high-pass(out) − high-pass(in)|.
* Inside the mask, in sRGB-encoded space:
  `delta = base + (out − in)` (keeps full-res texture),
  `replace = out` (upsampled), weighted by
  `max(smooth(0.012, 0.035, structure), smooth(0.25, 0.4, change))`.
* Result = `base + (candidate − base)·mask·strength`, and exactly `base`
  wherever the mask is 0.

`composite.mask_overlay(...)` returns the same mask at preview size for the
UI's "Show blend mask".
