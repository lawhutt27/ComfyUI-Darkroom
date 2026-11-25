"""Preservation instructions appended to every prompt edit (ARCHITECTURE.md §4)."""

PRESERVE_INSTRUCTIONS = (
    "Strict editing rules: make ONLY the change requested above. "
    "Preserve every person's identity, face, facial features, expression, skin texture, body shape, pose "
    "and clothing exactly. Keep the framing, crop, aspect ratio, camera angle, perspective and composition "
    "identical. Keep the lighting direction, colours, white balance, sharpness and grain of all areas that "
    "the request does not mention. Do not add, remove, move or restyle anything that was not asked for. "
    "Do not add text, borders, watermarks or signatures. Return the full image at the same framing."
)

TARGET_HINT = "Apply the change only inside the central target area of this image; leave its surroundings untouched."


def build_prompt(user_prompt, preserve=True, targeted=False):
    p = " ".join(str(user_prompt).split())
    if not p:
        raise ValueError("Describe the change you want.")
    parts = [p]
    if targeted:
        parts.append(TARGET_HINT)
    if preserve:
        parts.append(PRESERVE_INSTRUCTIONS)
    return "\n\n".join(parts)
