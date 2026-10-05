"""
LUT Bake Inject node for ComfyUI-Darkroom.
Pads a photo and an identity lattice to a shared canvas and stacks them as a
2-image batch. Downstream grading nodes process both at once with identical
settings. Pair with LUT Bake Extract after the grading chain.
"""

import torch
import torch.nn.functional as F


def _pad_replicate(img_bhwc, target_h, target_w):
    """Edge-replicate pad a (1, H, W, C) tensor to target dimensions."""
    _, h, w, _ = img_bhwc.shape
    pad_bottom = target_h - h
    pad_right = target_w - w
    if pad_bottom == 0 and pad_right == 0:
        return img_bhwc
    nchw = img_bhwc.permute(0, 3, 1, 2)
    padded = F.pad(nchw, (0, pad_right, 0, pad_bottom), mode='replicate')
    return padded.permute(0, 2, 3, 1).contiguous()


class LUTBakeInject:

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "photo": ("IMAGE", {
                    "tooltip": "Your real photo. Will be graded by the chain and come "
                               "out the other side via LUT Bake Extract."
                }),
                "identity_lattice": ("IMAGE", {
                    "tooltip": "From LUT Identity Generator. Will be batched alongside "
                               "the photo so the grading chain transforms both identically."
                }),
                "lut_size": ("INT", {
                    "default": 33, "min": 2, "max": 129,
                    "tooltip": "Must match the LUT Identity Generator size. Passed "
                               "through to LUT Bake Extract for cropping."
                }),
            }
        }

    RETURN_TYPES = ("IMAGE", "LUT_BAKE_META")
    RETURN_NAMES = ("batched_image", "bake_meta")
    FUNCTION = "execute"
    CATEGORY = "AKURATE/Darkroom/Pipeline"

    def execute(self, photo, identity_lattice, lut_size):
        size = int(lut_size)

        if photo.shape[0] != 1:
            print(f"[Darkroom] LUT Bake Inject: photo batch size is {photo.shape[0]}, "
                  f"only the first image will be used.")
            photo = photo[:1]

        if identity_lattice.shape[0] != 1:
            identity_lattice = identity_lattice[:1]

        _, ph, pw, pc = photo.shape
        _, lh, lw, lc = identity_lattice.shape

        expected_lh = size * size
        expected_lw = size
        if lh != expected_lh or lw != expected_lw:
            raise ValueError(
                f"[Darkroom] LUT Bake Inject: identity lattice is {lw}x{lh}, "
                f"expected {expected_lw}x{expected_lh} for lut_size={size}. "
                f"Make sure lut_size matches the LUT Identity Generator."
            )

        if pc != lc:
            raise ValueError(
                f"[Darkroom] LUT Bake Inject: photo has {pc} channels, lattice has {lc}. "
                f"Both must be RGB (3 channels)."
            )

        target_h = max(ph, lh)
        target_w = max(pw, lw)

        photo_padded = _pad_replicate(photo, target_h, target_w)
        lattice_padded = _pad_replicate(identity_lattice, target_h, target_w)

        batched = torch.cat([photo_padded, lattice_padded], dim=0)

        meta = {
            "lut_size": size,
            "photo_h": ph,
            "photo_w": pw,
            "lattice_h": lh,
            "lattice_w": lw,
            "canvas_h": target_h,
            "canvas_w": target_w,
        }

        print(f"[Darkroom] LUT Bake Inject: photo {pw}x{ph} + lattice {lw}x{lh} "
              f"-> canvas {target_w}x{target_h}, batch=2")

        return (batched, meta)


NODE_CLASS_MAPPINGS = {"DarkroomLUTBakeInject": LUTBakeInject}
NODE_DISPLAY_NAME_MAPPINGS = {"DarkroomLUTBakeInject": "LUT Bake Inject"}
