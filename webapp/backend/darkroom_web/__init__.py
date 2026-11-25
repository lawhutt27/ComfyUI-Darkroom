"""Darkroom Web worker: edit stack, retouch, AI compositing and HTTP API.

The deterministic imaging core is the Darkroom Studio engine in ../../studio.
When that package is not installed, it is imported from the repository.
"""

import os
import sys

__version__ = "0.1.0"

try:  # pragma: no cover - depends on install layout
    import darkroom_studio  # noqa: F401
except ImportError:  # running from a checkout
    _studio = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "studio"))
    if os.path.isdir(os.path.join(_studio, "darkroom_studio")):
        sys.path.insert(0, _studio)
    import darkroom_studio  # noqa: F401,E402
