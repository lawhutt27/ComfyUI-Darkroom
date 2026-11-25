"""Nano Banana (Gemini image models) through OpenRouter's chat-completions API.

Request: one user message with the instruction text and the image as a
data URL, with `modalities: ["image", "text"]`. The edited image comes back
in `choices[0].message.images[*].image_url.url` as a data URL.
"""

from __future__ import annotations

import httpx

from .base import EditRequest, EditResult, ImageEditProvider, ProviderError, decode_data_url, png_data_url


class OpenRouterProvider(ImageEditProvider):
    id = "openrouter"
    label = "Nano Banana via OpenRouter"

    def __init__(self, settings):
        self.key = settings.openrouter_api_key
        self.model = settings.openrouter_model
        self.base_url = settings.openrouter_base_url.rstrip("/")
        self.timeout = settings.provider_timeout

    def available(self):
        return bool(self.key)

    def build_payload(self, req: EditRequest):
        return {
            "model": req.model or self.model,
            "modalities": ["image", "text"],
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": req.prompt},
                {"type": "image_url", "image_url": {"url": png_data_url(req.image)}},
            ]}],
        }

    def edit(self, req: EditRequest) -> EditResult:
        if not self.available():
            raise ProviderError("OPENROUTER_API_KEY is not set")
        payload = self.build_payload(req)
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", json=payload, timeout=self.timeout,
                           headers={"Authorization": f"Bearer {self.key}", "X-Title": "Darkroom Web"})
        except httpx.HTTPError as e:
            raise ProviderError(f"OpenRouter request failed: {e}") from e
        if r.status_code != 200:
            raise ProviderError(f"OpenRouter returned HTTP {r.status_code}: {r.text[:300]}")
        try:
            msg = r.json()["choices"][0]["message"]
        except Exception as e:
            raise ProviderError(f"unexpected OpenRouter response: {r.text[:300]}") from e
        images = msg.get("images") or []
        if not images:
            text = msg.get("content") or ""
            raise ProviderError("the model returned no image" + (f": {str(text)[:300]}" if text else ""))
        url = images[0].get("image_url", {}).get("url") if isinstance(images[0], dict) else None
        return EditResult(image=decode_data_url(url), model=payload["model"],
                          notes=str(msg.get("content") or "")[:500])
