import os
import shutil
import sys

import numpy as np
import pytest
from PIL import Image

os.environ["DARKROOM_NO_APP"] = "1"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import darkroom_web  # noqa: E402,F401  (puts darkroom_studio on the path)
from helpers_web import textured_image  # noqa: E402

FIXTURES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "samples"))
SAMPLES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "studio", "samples"))


@pytest.fixture
def png_path(tmp_path):
    p = tmp_path / "scene.png"
    Image.fromarray((textured_image() * 255 + 0.5).astype(np.uint8)).save(p)
    return str(p)


@pytest.fixture
def astronaut_path(tmp_path):
    p = tmp_path / "astronaut.png"
    shutil.copy(os.path.join(FIXTURES, "astronaut.png"), p)
    return str(p)


@pytest.fixture
def settings(tmp_path):
    from darkroom_web.config import Settings
    return Settings(workdir=str(tmp_path / "work"), openrouter_api_key="", gemini_api_key="")


@pytest.fixture
def store(settings):
    from darkroom_web.pipeline import ProjectStore
    return ProjectStore(settings)
