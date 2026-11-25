"""Settings from environment variables (see ../../.env.example)."""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field


def load_dotenv(path=None):
    """Read KEY=VALUE lines from webapp/.env without overriding the real environment."""
    path = path or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.split("#", 1)[0].strip() if not line.lstrip().startswith("#") else ""
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _env(name, default=""):
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    workdir: str = field(default_factory=lambda: _env("DARKROOM_WORKDIR", os.path.abspath("darkroom_data")))
    preview_long_edge: int = field(default_factory=lambda: int(_env("DARKROOM_PREVIEW_EDGE", "1600")))
    provider_long_edge: int = field(default_factory=lambda: int(_env("DARKROOM_PROVIDER_EDGE", "1536")))
    max_upload_mb: int = field(default_factory=lambda: int(_env("DARKROOM_MAX_UPLOAD_MB", "200")))
    cors_origins: str = field(default_factory=lambda: _env("DARKROOM_CORS_ORIGINS", "http://localhost:5173"))
    openrouter_api_key: str = field(default_factory=lambda: _env("OPENROUTER_API_KEY"))
    openrouter_model: str = field(default_factory=lambda: _env("OPENROUTER_IMAGE_MODEL",
                                                                "google/gemini-2.5-flash-image"))
    openrouter_base_url: str = field(default_factory=lambda: _env("OPENROUTER_BASE_URL",
                                                                   "https://openrouter.ai/api/v1"))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"))
    gemini_base_url: str = field(default_factory=lambda: _env(
        "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"))
    provider_timeout: float = field(default_factory=lambda: float(_env("DARKROOM_PROVIDER_TIMEOUT", "120")))


def optional_packages():
    """What is installed, for the /api/health report and the UI."""
    from darkroom_studio.metadata import exiftool_available
    return {
        "exiftool": exiftool_available(),
        "rawpy": importlib.util.find_spec("rawpy") is not None,
        "mediapipe": importlib.util.find_spec("mediapipe") is not None,
    }
