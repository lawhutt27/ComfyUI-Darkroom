"""Nano Banana through the Gemini API (generateContent with inline image data)."""

from __future__ import annotations

import base64

import httpx

from .base import EditRequest, EditResult, ImageEditProvider, ProviderError, decode_image_bytes, png_data_url


class GeminiProvider(ImageEditProvider):
    id = "gemini"
    label = "Nano Banana via Gemini API"

    def __init__(self, settings):
        self.key = settings.gemini_api_key
        self.model = settings.gemini_model
        self.base_url = settings.gemini_base_url.rstrip("/")
        self.timeout = settings.provider_timeout

    def available(self):
        return bool(self.key)

    def build_payload(self, req: EditRequest):
        b64 = png_data_url(req.image).split(",", 1)[1]
        return {"contents": [{"role": "user", "parts": [
            {"text": req.prompt},
            {"inline_data": {"mime_type": "image/png", "data": b64}},
        ]}], "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}

    def edit(self, req: EditRequest) -> EditResult:
        if not self.available():
            raise ProviderError("GEMINI_API_KEY is not set")
        model = req.model or self.model
        try:
            r = httpx.post(f"{self.base_url}/models/{model}:generateContent", json=self.build_payload(req),
                           headers={"x-goog-api-key": self.key}, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise ProviderError(f"Gemini request failed: {e}") from e
        if r.status_code != 200:
            raise ProviderError(f"Gemini returned HTTP {r.status_code}: {r.text[:300]}")
        text = []
        try:
            for cand in r.json().get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    blob = part.get("inlineData") or part.get("inline_data")
                    if blob and blob.get("data"):
                        return EditResult(image=decode_image_bytes(base64.b64decode(blob["data"])), model=model,
                                          notes=" ".join(text)[:500])
                    if part.get("text"):
                        text.append(part["text"])
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"unexpected Gemini response: {r.text[:300]}") from e
        raise ProviderError("the model returned no image" + (f": {' '.join(text)[:300]}" if text else ""))
