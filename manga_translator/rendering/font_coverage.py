"""Font-chain character coverage checks."""

import unicodedata


def missing_glyphs(text):
    from . import text_render

    faces = text_render._font_state().selection
    missing = []
    for char in str(text or ""):
        if char.isspace() or unicodedata.category(char) in {"Cc", "Cf"}:
            continue
        for face in faces:
            try:
                if face.get_char_index(char):
                    break
            except Exception:
                continue
        else:
            missing.append(char)
    return missing
