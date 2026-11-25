from .base import EditRequest, EditResult, ImageEditProvider, ProviderError  # noqa: F401
from .gemini import GeminiProvider
from .local import LocalInpaintProvider
from .openrouter import OpenRouterProvider


def registry(settings):
    """Providers in preference order."""
    provs = [OpenRouterProvider(settings), GeminiProvider(settings), LocalInpaintProvider()]
    return {p.id: p for p in provs}


def default_provider(reg):
    for p in reg.values():
        if p.available() and p.sends_pixels_off_device:
            return p.id
    return "local"
