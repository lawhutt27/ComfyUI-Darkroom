# Darkroom Web: architecture

A photo editor that combines Lightroom-style deterministic grading at full
camera resolution with surgical, prompt-driven AI edits. The one rule that
shapes everything:

> **The full-resolution master is never replaced by a diffusion output.**
> AI models see a working proxy (about 1-2K px). Their result is aligned,
> masked and feather-composited back onto the master, so every pixel the AI
> did not meaningfully change keeps its original sensor detail.

## 1. Processes

```
 Browser (React + Vite, frontend/)                 Worker (FastAPI, backend/)
 ─────────────────────────────────                 ───────────────────────────
 drag-and-drop upload ───────── POST /api/projects ─▶ protect original, decode RAW
 inspector edits the stack ──── PUT  /stack ────────▶ validate stack, store
 preview (debounced) ────────── POST /render ───────▶ render proxy (≤1600 px)
 before/after slider            GET  /before.png
 AI edit box + prompt ───────── POST /ai-edit ──────▶ crop proxy → provider →
                                                       align → mask → asset
 Export Full Resolution ─────── POST /export ───────▶ render master, write file
```

All image maths runs in the worker. The browser only ever receives 8-bit
sRGB preview PNGs and the finished export files. The browser never holds a
copy of the photo that could be mistaken for the master.

The worker reuses the tested Darkroom Studio engine in `../studio/darkroom_studio`
(RAW development via rawpy/LibRaw, ICC-aware colour management, float32
linear-light pipeline, .cube LUT parsing, film looks,
reference matching, 16-bit TIFF/PNG and JPEG export with EXIF/ICC). This
app adds the edit stack, tetrahedral LUT interpolation, retouch tools, AI
compositing and the web UI on top.

## 2. Full-Res Master vs. Edit Proxy

| Image | Resolution | Precision | Used for |
|---|---|---|---|
| **Original file** | as uploaded | as uploaded | Copied read-only to `originals/`, SHA-256 recorded. Never written to. RAW files stay RAW. |
| **Master** | native (e.g. 6016×4016) | float32 linear, working space (sRGB / ProPhoto for RAW) | The only source for exports. |
| **Proxy** | long edge 1600 px | float32 linear | Interactive preview only. |
| **Analysis** | long edge 512 px | float32 linear | Face/eye/mouth detection, dehaze airlight, style-match statistics. |
| **Provider input** | crop of the proxy, long edge ≤ 1536 px | 8-bit sRGB PNG | What an AI provider receives. Stored as an asset with the layer. |

Rules:
1. Every layer stores its parameters in **normalised coordinates** (0-1 of the
   full frame), never in pixels of a particular level, so the same stack
   renders at proxy and master resolution.
2. Nothing derived from an 8-bit preview is ever used as an export source.
   The only 8-bit images that reach the master are AI provider outputs, and
   those only inside their feathered mask (see §4).
3. Exports are rendered from the master at 100% resolution. The worker
   refuses to write a "full resolution" export whose dimensions differ from
   the source. The optional Instagram presets (4:5, 1:1, 1.91:1, 1080 px)
   are separate, explicitly labelled resized exports.

## 3. The non-destructive edit stack

The stack is a JSON document stored per project (`stack.json`) and is the
only thing the UI edits. Rendering is a pure function:
`render(master_or_proxy, stack) → image`.

```jsonc
{
  "version": 1,
  "layers": [
    // ── pixel layers: applied in stack order to the developed master ──
    {"id": "l_3f9a", "type": "red_eye",      "enabled": true, "name": "Red-eye",
     "params": {"strength": 100, "eyes": [{"x": 0.41, "y": 0.33, "r": 0.012}]}},
    {"id": "l_77c0", "type": "teeth_whiten", "enabled": true, "name": "Whiten teeth",
     "params": {"strength": 35, "regions": [{"x": 0.44, "y": 0.47, "w": 0.08, "h": 0.03}]}},
    {"id": "l_a1b2", "type": "ai_edit",      "enabled": true, "name": "Remove stray person",
     "params": {"prompt": "remove the person on the left", "provider": "openrouter",
                "model": "google/gemini-2.5-flash-image", "preserve": true,
                "box": {"x": 0.02, "y": 0.30, "w": 0.18, "h": 0.55},   // null = whole frame
                "crop": {"x": 0.0, "y": 0.22, "w": 0.27, "h": 0.71},   // what was sent
                "feather": 40, "strength": 100, "sensitivity": 50,
                "input_asset": "a_…png", "output_asset": "a_…png"}},
    // ── grade layers: folded, in stack order, into one engine recipe ──
    {"id": "l_c4d5", "type": "look",        "enabled": true, "name": "Golden Hour",
     "params": {"preset": "IG / Golden Hour", "amount": 80}},
    {"id": "l_e6f7", "type": "lut",         "enabled": true, "name": "teal.cube",
     "params": {"name": "teal.cube", "strength": 60}},
    {"id": "l_0a1b", "type": "style_match", "enabled": true, "name": "Match: beach.jpg",
     "params": {"strength": 70, "method": "Colour + tone", "data": {/* frozen statistics */}}},
    {"id": "l_9c8d", "type": "adjust",      "enabled": true, "name": "Tone",
     "params": {"basic": {"exposure": 0.3, "shadows": 20}, "effects": {"grain_amount": 10}}}
  ]
}
```

### Layer types

| Type | Kind | Parameters | Deterministic? |
|---|---|---|---|
| `adjust` | grade | any subset of the Darkroom recipe groups (`basic`, `effects`, `hsl`, `color_grading`, `tone_curve`, `lgg`, `detail`) | yes |
| `look` | grade | `preset`, `amount` 0-100 | yes |
| `lut` | grade | `name` (imported .cube), `strength` 0-100 | yes |
| `film` | grade | `stock`, `strength` | yes |
| `style_match` | grade | `strength`, `method`, frozen `data` | yes (statistics are frozen when the layer is created) |
| `red_eye` | pixel | `strength`, `eyes[]` (normalised circles) | yes (detection runs once, result is frozen into params) |
| `teeth_whiten` | pixel | `strength`, `regions[]` (normalised boxes) | yes (same) |
| `ai_edit` | pixel | prompt, provider, box, crop, feather, strength, sensitivity, stored provider input/output assets | yes **after** the provider call: re-rendering never calls the provider again |

### Rendering order

1. **Pixel layers**, in stack order, on a copy of the master (or proxy).
   These are content-level fixes, so they act on the developed photograph
   before any grading, and the grade then applies uniformly to fixed and
   unfixed pixels alike. That is what keeps composited AI regions from
   looking "pasted on" after a LUT is applied.
2. **Grade layers** are folded into a single Darkroom recipe in stack order:
   * numeric sliders are **additive** relative to their defaults, scaled by
     the layer's `amount`/`strength` where it has one, then clamped to the
     schema limits;
   * curves, film stock, LUT name and match data are **last wins** (the
     engine applies one of each).
   The engine then renders that recipe with its fixed, colour-correct
   operation order (linear-light exposure/WB/tone → OkLab match/HSL/grading
   → encoded curves/film/LUT/fade → detail → optics → grain).

Disabled layers are skipped. Reordering is free within each kind. A pixel
layer can't be dragged below a grade layer, because grading always follows
content fixes.

### History

Each accepted stack is pushed to an undo/redo history (last 100 states).
Undo/redo swap whole stack documents, so any step can be reverted or
re-tweaked without replaying anything.

## 4. Prompt-edit preservation rules

1. **Opt-in transmission.** Only the AI Edit tool sends pixels anywhere, and
   only after the user presses *Run AI edit*. The UI states what is sent
   (a ≤1536 px crop of the photo, never the original file or its metadata).
2. **Preservation prompt.** When *Preserve original details* is on (the
   default), the worker appends a fixed instruction block to the user's
   prompt: keep identity, faces, pose, framing, perspective, lighting
   direction, colours and every area not mentioned unchanged; return the
   same framing and aspect ratio. The exact text is in
   `backend/darkroom_web/prompting.py` and is shown in the UI.
3. **Targeting.** If the user draws a target box, the provider receives
   that box plus 25% context, and the composite mask is limited to the
   (feathered) box. Without a box the whole proxy is sent.
4. **Alignment.** The provider output is resized to the input size and
   aligned to it (OpenCV ECC: translation, then a small affine refinement;
   anything larger than a 6 % shift or zoom is rejected) to undo small reframing.
5. **Change mask.** With *Preserve* on, the composite only uses pixels the
   model really changed: a blurred colour-difference map is thresholded
   (`sensitivity`), cleaned with morphology and feathered (`feather`, in
   full-res pixels). Small global drift such as a slight colour shift is
   rejected, so unchanged areas stay bit-identical to the master.
6. **Detail-preserving blend.** Inside the mask the worker blends two
   candidates:
   * *delta transfer* `master + upsample(output − input)` keeps the master's
     full-resolution texture where the model only nudged tone or colour;
   * *replacement* `upsample(output)` is used where local **structure**
     changed (edges/texture appeared or vanished: objects removed or added)
     or the colour changed drastically, so no ghost of the old content
     remains.
   The blend weight follows a high-pass structure-change map, and the
   layer's `strength` fades the result.
7. **Outside the mask the master is untouched.** The output is not
   "almost the same" there. It is exactly the master, and tests check this.

## 5. Folder layout

```
webapp/
  ARCHITECTURE.md          this file
  SKILLS/photo-pipeline.md exact interfaces for LUTs, style match, retouch, AI edits
  README.md                setup, environment variables, fallbacks
  .env.example
  samples/                 public-domain sample photo for the start screen
  backend/
    requirements.txt
    darkroom_web/
      config.py            environment variables, optional-package detection
      stack.py             edit-stack schema, validation, fold to recipe
      looks.py             Instagram-style looks (partial recipes)
      lutx.py              tetrahedral 3D-LUT interpolation
      pipeline.py          render(stack) at any level; full-res export
      retouch.py           red-eye + teeth: detection and correction
      composite.py         align, change mask, feathered detail-preserving blend
      prompting.py         preservation instructions
      providers/           pluggable image-edit providers
        base.py            ImageEditProvider interface + registry
        openrouter.py      Nano Banana (Gemini image) via OpenRouter
        gemini.py          Nano Banana via the Gemini API
        local.py           offline fallback: OpenCV inpainting inside a box
      projects.py          on-disk project store, assets, history
      api.py               FastAPI routes
    tests/
  frontend/
    package.json, vite.config.ts, index.html
    src/
      api.ts               typed client
      types.ts             stack types (mirror of stack.py)
      App.tsx              layout, state, history
      components/          Dropzone, Viewer (before/after), Inspector tabs,
                           LayersPanel, ExportDialog
```

## 6. Optional packages and fallbacks

| Missing | Effect |
|---|---|
| ExifTool | Basic EXIF written with Pillow instead of a full metadata copy. |
| `OPENROUTER_API_KEY` and `GEMINI_API_KEY` | AI Edit offers only the local inpainting provider (box required). |
| Face model | Eye and mouth detection uses OpenCV's YuNet detector (MIT, 230 KB), downloaded once and checked against a pinned SHA-256. On OpenCV 4 builds without it, the Haar cascades bundled with OpenCV are used. If neither is available, users click the eyes or draw the mouth box by hand. MediaPipe is not required. |
| ComfyUI (Qwen-Image-Edit / Flux Kontext) | Planned provider. The interface is in place (`providers/base.py`); see the README roadmap. |
