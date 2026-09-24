# This file are functions that are essential for the renderer export

import enum
import re
import unicodedata
from typing import List

import cv2
import numpy as np


def color_difference(rgb1: List, rgb2: List) -> float:
    # https://en.wikipedia.org/wiki/Color_difference#CIE76
    color1 = np.array(rgb1, dtype=np.uint8).reshape(1, 1, 3)
    color2 = np.array(rgb2, dtype=np.uint8).reshape(1, 1, 3)
    diff = cv2.cvtColor(color1, cv2.COLOR_RGB2LAB).astype(np.float32) - cv2.cvtColor(color2, cv2.COLOR_RGB2LAB).astype(
        np.float32)
    diff[..., 0] *= 0.392
    diff = np.linalg.norm(diff, axis=2)
    return diff.item()


def is_punctuation(ch):
    """Checks whether `chars` is a punctuation character."""
    cp = ord(ch)
    # We treat all non-letter/number ASCII as punctuation.
    # Characters such as "^", "$", and "`" are not in the Unicode
    # Punctuation class but we treat them as punctuation anyways, for
    # consistency.
    if ((cp >= 33 and cp <= 47) or (cp >= 58 and cp <= 64) or
            (cp >= 91 and cp <= 96) or (cp >= 123 and cp <= 126)):
        return True
    cat = unicodedata.category(ch)
    if cat.startswith("P"):
        return True
    return False


def is_whitespace(ch):
    """Checks whether `chars` is a whitespace character."""
    # \t, \n, and \r are technically control characters but we treat them
    # as whitespace since they are generally considered as such.
    if ch == " " or ch == "\t" or ch == "\n" or ch == "\r" or ord(ch) == 0:
        return True
    cat = unicodedata.category(ch)
    if cat == "Zs":
        return True
    return False


def is_control(ch):
    """Checks whether `chars` is a control character."""
    # These are technically control characters but we count them as whitespace
    # characters.
    if ch == "\t" or ch == "\n" or ch == "\r":
        return False
    cat = unicodedata.category(ch)
    if cat in ("Cc", "Cf"):
        return True
    return False


class NumericClassification(str, enum.Enum):
    MEANINGFUL_NUMERIC = "meaningful_numeric"
    POSSIBLE_NUMERIC = "possible_numeric"
    NOISE_NUMERIC = "noise_numeric"
    LINGUISTIC = "linguistic"


_NUMERIC_CONTEXT_REGEX = re.compile(
    r"""
    [\d\uFF10-\uFF19]+(?:\.[\d\uFF10-\uFF19]+)?\s*([%％‰:：/／\+\-\±\×\÷\=~〜¥￥$€£#№]|kg|g|mg|cm|mm|m|km|L|ml|mL|cc|°|℃|℉|K|W|kW|V|A|Hz|dB|GB|MB|KB|TB|pt|px|em|rem|No\.|NO\.|no\.|話|第|巻|回|頁|ページ|p\.|p|P|F|階|号|室|分|秒|時|日|月|年|歳|才|人|個|本|枚|匹|頭|台|点|位|段|級|th|st|nd|rd)
    |
    ([¥￥$€£#№]|No\.|NO\.|no\.|第)\s*[\d\uFF10-\uFF19]+
    |
    \d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}   # Date format
    |
    \d{1,2}[:：]\d{2}(?:[:：]\d{2})?      # Time format
    """,
    re.VERBOSE | re.IGNORECASE,
)

_BRACKET_ENCLOSED_NUMERIC = re.compile(
    r"^[\(\[\{（【「『〈〔《‹«<]\s*[\d\uFF10-\uFF19\u2160-\u217F\s\-+.]+\s*[\)\]\}）】」』〉〕》›»>]$"
)


def has_numeric_unit_or_symbol_context(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if _BRACKET_ENCLOSED_NUMERIC.match(stripped):
        return True
    return bool(_NUMERIC_CONTEXT_REGEX.search(stripped))


def is_valuable_char(ch):
    # return re.search(r'[^\d\W]', ch)
    return not is_punctuation(ch) and not is_control(ch) and not is_whitespace(ch) and not ch.isdigit()


def is_valuable_text(text):
    for ch in text:
        if is_valuable_char(ch):
            return True
    return False


def is_meaningful_ocr_text(text):
    return any(
        not is_control(ch) and not is_whitespace(ch)
        and unicodedata.category(ch)[0] in ("L", "N")
        for ch in text
    )


def contains_linguistic_ocr_text(text):
    return any(unicodedata.category(ch).startswith("L") for ch in text)


def classify_numeric_ocr_region(
    text: str,
    prob: float = 1.0,
    is_in_bubble: bool = False,
    is_near_text: bool = False,
    min_confidence: float = 0.40,
) -> NumericClassification:
    """Classify an OCR text region into 3-tier numeric or linguistic categories.

    1. LINGUISTIC: Contains translatable letters/words.
    2. MEANINGFUL_NUMERIC: High confidence, in bubble/box, has units/brackets, or near text.
    3. POSSIBLE_NUMERIC: Digits exist but lacks strong context (preserve + review flag).
    4. NOISE_NUMERIC: Noise punctuation/detector artifacts (discard).
    """
    stripped = text.strip()
    if not stripped or not is_meaningful_ocr_text(stripped):
        return NumericClassification.NOISE_NUMERIC

    if contains_linguistic_ocr_text(stripped):
        # If it's a standard numeric token with unit (e.g. 100円, 第3話, 3F, 2kg)
        # Check if it has translatable words or is a simple unit-attached number
        return NumericClassification.LINGUISTIC

    # It is purely numeric / symbolic
    has_digits = any(unicodedata.category(ch).startswith("N") for ch in stripped)
    if not has_digits:
        return NumericClassification.NOISE_NUMERIC

    has_context = has_numeric_unit_or_symbol_context(stripped)
    digits_count = sum(1 for ch in stripped if unicodedata.category(ch).startswith("N"))

    # Conditions for MEANINGFUL_NUMERIC:
    # 1. OCR confidence is reasonably strong (prob >= min_confidence)
    # 2. Inside / near speech bubble or label box
    # 3. Has semantic punctuation/unit context ((), %, :, /, +, -, #, ¥, etc.)
    # 4. Spatially associated with another text region
    # 5. Multi-digit intentional number (>= 2 digits) with reasonable confidence
    is_strong_confidence = prob >= min_confidence
    is_multi_digit = digits_count >= 2

    if (
        has_context
        or is_in_bubble
        or is_near_text
        or (is_strong_confidence and is_multi_digit)
        or (is_strong_confidence and prob >= 0.70)
    ):
        return NumericClassification.MEANINGFUL_NUMERIC

    # Isolated single digit with low confidence and no surrounding text
    if prob < 0.15 and not is_in_bubble and not is_near_text and not has_context:
        return NumericClassification.NOISE_NUMERIC

    # Default to POSSIBLE_NUMERIC (preserve, review candidate)
    return NumericClassification.POSSIBLE_NUMERIC


def is_preserved_region(region):
    return getattr(region, "translation_policy", None) == "preserve"


def dist(x1, y1, x2, y2):
    return np.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)


def rect_distance(x1, y1, x1b, y1b, x2, y2, x2b, y2b):
    left = x2b < x1
    right = x1b < x2
    bottom = y2b < y1
    top = y1b < y2
    if top and left:
        return dist(x1, y1b, x2b, y2)
    elif left and bottom:
        return dist(x1, y1, x2b, y2b)
    elif bottom and right:
        return dist(x1b, y1, x2, y2b)
    elif right and top:
        return dist(x1b, y1b, x2, y2)
    elif left:
        return x1 - x2b
    elif right:
        return x2 - x1b
    elif bottom:
        return y1 - y2b
    elif top:
        return y2 - y1b
    else:  # rectangles intersect
        return 0


def is_right_to_left_char(ch):
    """Checks whether the char belongs to a right to left alphabet."""
    # Arabic (from https://stackoverflow.com/a/49346768)
    if ('\u0600' <= ch <= '\u06FF' or
            '\u0750' <= ch <= '\u077F' or
            '\u08A0' <= ch <= '\u08FF' or
            '\uFB50' <= ch <= '\uFDFF' or
            '\uFE70' <= ch <= '\uFEFF' or
            '\U00010E60' <= ch <= '\U00010E7F' or
            '\U0001EE00' <= ch <= '\U0001EEFF'):
        return True
    return False
