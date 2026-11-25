# Darkroom Web

A photo editor that pairs Lightroom-style precision with surgical,
prompt-based AI edits, without ever trading away your camera's resolution.

* **Full-resolution, non-destructive.** RAW (NEF, CR2, CR3, DNG, ARW…),
  TIFF, PNG and JPEG are developed into a float32 master at native size.
  Every edit is a layer in an edit stack you can toggle, re-tweak, reorder
  or delete; exports are rendered from the master at 100 %.
* **Lightroom-style grading.** Quick Instagram and film looks with an amount
  slider, `.cube` LUT import with strength (tetrahedral interpolation), film
  stocks, tone sliders, and **Style Match** from a reference photo.
* **Assisted retouch.** One-click red-eye and subtle teeth whitening with
  face/eye/mouth detection, plus manual placement.
* **Surgical AI edits.** Describe a small change ("remove the stray person on
  the left"). Nano Banana (via OpenRouter or the Gemini API) edits a proxy
  crop; the result is aligned, masked to what actually changed and
  feather-blended back onto the full-resolution master. Everything else
  stays bit-identical.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for the Full-Res Master vs. Edit
Proxy rule and the edit-stack data model, and
[SKILLS/photo-pipeline.md](SKILLS/photo-pipeline.md) for the exact tool
interfaces.

## Quick start

Requirements: Python 3.12 or 3.13, Node 20+, optionally ExifTool
(`apt install libimage-exiftool-perl` / `brew install exiftool`) for full
metadata copying.

```bash
cd webapp

# 1. worker (Python)
python -m venv .venv && . .venv/bin/activate
pip install -r ../studio/requirements.txt -r backend/requirements.txt

# 2. UI (built once; the worker serves it)
cd frontend && npm install && npm run build && cd ..

# 3. optional: AI provider keys
cp .env.example .env          # then fill in OPENROUTER_API_KEY or GEMINI_API_KEY

# 4. run
cd backend && uvicorn darkroom_web.api:app --port 8000
```
Open http://localhost:8000, drop a photo, or click the sample.

### Developing the UI
Run the worker as above, then in another terminal:
```bash
cd webapp/frontend && npm run dev     # http://localhost:5173, proxies /api to :8000
```

## Configuration

All settings are environment variables (or `webapp/.env`, which never
overrides the real environment). See [.env.example](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | – | Enables *Nano Banana via OpenRouter* |
| `OPENROUTER_IMAGE_MODEL` | `google/gemini-2.5-flash-image` | Any OpenRouter model that returns images |
| `GEMINI_API_KEY` | – | Enables *Nano Banana via Gemini API* |
| `GEMINI_IMAGE_MODEL` | `gemini-2.5-flash-image` | |
| `DARKROOM_WORKDIR` | `./darkroom_data` | Projects, protected originals, exports, LUT library |
| `DARKROOM_PREVIEW_EDGE` | `1600` | Preview proxy size (display only) |
| `DARKROOM_PROVIDER_EDGE` | `1536` | Largest image sent to an AI provider |
| `DARKROOM_YUNET_MODEL` | – | Path to the face model for fully offline use |

## Fallbacks

| If this is missing | What happens |
|---|---|
| Both API keys | AI Edit offers *Local clean-up*: offline OpenCV inpainting inside a box you draw. It does not interpret the prompt. |
| Internet on first retouch | The 230 KB YuNet face model can't be fetched. Eyes and mouths are then placed by hand (click / drag), or set `DARKROOM_YUNET_MODEL`. |
| ExifTool | A basic EXIF block is written instead of a full copy (lens, maker notes are lost). |
| `rawpy` | RAW files can't be opened; rasters still work. |

## Privacy

Only the AI Edit tool sends pixels anywhere, and only when you press
*Run AI edit*. It sends a copy of the target area (or the photo) at most
1536 px, never the original file or its metadata. The panel's *What gets
sent* section shows exactly what will be sent and the preservation
instructions added to your prompt.

## Tests

```bash
cd webapp/backend && python -m pytest          # 30 tests, ~45 s
python ../../studio/tools/fetch_samples.py     # optional: real Nikon/Canon RAW samples for the camera test
```
The tests use stand-in OpenRouter and Gemini servers, so no keys or network
are needed (except the one-time face-model download).

## What this first slice covers, and what's next

Working end to end: upload (incl. RAW) → looks / LUT / film / style match /
tone → red-eye / teeth → AI edit with masked full-res compositing →
layer stack with undo/redo → full-resolution TIFF/PNG/JPEG export or
Instagram 4:5 / 1:1 / 1.91:1 / 1080 px exports.

Next, roughly in order:
1. 100 % zoom and pan (render full-res crops; the engine already supports regions).
2. Brush masks for AI edits and a spot-healing / blemish brush.
3. Curves, HSL and colour-wheel panels (the engine already supports them; the UI shows only the core sliders).
4. ComfyUI provider for Qwen-Image-Edit / Flux Kontext (interface in `providers/base.py`).
5. Multi-turn AI editing on the same region, and batch export.
