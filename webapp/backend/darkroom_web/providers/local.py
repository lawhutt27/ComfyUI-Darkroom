"""Offline fallback: OpenCV inpainting inside the target box.

Not generative and not prompt-aware beyond "remove/clean up": it fills the
box from its surroundings (Telea fast marching). Good for small distractions
(dust, a sign, a stray object against a simple background). It never leaves
the machine.
"""

from __future__ import annotations

import cv2
import numpy as np

from .base import EditRequest, EditResult, ImageEditProvider, ProviderError


class LocalInpaintProvider(ImageEditProvider):
    id = "local"
    label = "Local clean-up (offline, no AI)"
    sends_pixels_off_device = False
    needs_mask = True
    model = "opencv-telea"

    def available(self):
        return True

    def edit(self, req: EditRequest) -> EditResult:
        if req.mask is None or not req.mask.any():
            raise ProviderError("Local clean-up needs a target box: drag a box over what to remove.")
        m = (req.mask > 127).astype(np.uint8) * 255
        radius = max(3, int(round(max(req.image.shape[:2]) / 150)))
        out = cv2.inpaint(np.ascontiguousarray(req.image), m, radius, cv2.INPAINT_TELEA)
        return EditResult(image=out, model=self.model,
                          notes="Filled the box from its surroundings (prompt text is not interpreted offline).")
