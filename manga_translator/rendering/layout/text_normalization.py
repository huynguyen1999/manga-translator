"""Repair OCR hyphen splits before word-level layout."""

from __future__ import annotations

import os
from typing import List, Optional

from .hard_line_breaks import HARD_LINE_BREAK, split_layout_words
from .line_breaking import _PUNCT_STRIP


# ---------------------------------------------------------------------------
# Phase 2 — text normalization (OCR hyphen-split repair)
# ---------------------------------------------------------------------------

# Hyphenated compounds that must survive normalization (never join these).
_COMPOUND_PREFIXES = {
    "X", "E", "SELF", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY",
    "SEVENTY", "EIGHTY", "NINETY", "WELL", "HALF", "FULL", "ALL",
    "PRO", "ANTI", "MID", "NEO", "PAN", "SUPER", "ULTRA", "MULTI",
}

_DICTIONARY_CACHE: Optional[Optional[set]] = None
_DICTIONARY_PATHS = (
    "/usr/share/dict/words",
    "/usr/share/dict/web2",
    "/usr/dict/words",
)

def _load_dictionary() -> Optional[set]:
    """Load a system word list for hyphenation evidence; None if unavailable."""
    global _DICTIONARY_CACHE
    if _DICTIONARY_CACHE is not None:
        return _DICTIONARY_CACHE
    words: Optional[set] = None
    for path in _DICTIONARY_PATHS:
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    words = {ln.strip().lower() for ln in f if ln.strip()}
                break
        except OSError:
            continue
    _DICTIONARY_CACHE = words
    return words


def _dict_contains(word: str) -> bool:
    dictionary = _load_dictionary()
    if dictionary is None:
        return False
    return word.lower() in dictionary


def normalize_words(words: List[str]) -> List[str]:
    """Repair OCR hyphen splits so ordinary words are atomic for the DP.

    "GOT- TEN" -> "GOTTEN", "IN- JURED" -> "INJURED", "SCRATCH- ES." ->
    "SCRATCHES." — but real compounds ("TWENTY-ONE", "SELF-DEFENSE",
    "X-RAY") stay intact. Decisions use dictionary evidence plus compound
    prefixes; when no dictionary exists, a conservative heuristic keeps the
    hyphen unless both fragments look like non-words (split proper names).
    """
    import re as _re

    dictionary = _load_dictionary()
    out: List[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        m_next = (
            _re.match(r"^([A-Za-z]+)([.,!?;:…]*)$", words[i + 1])
            if (w.endswith("-") and not w.endswith("--") and len(w) > 1 and i + 1 < len(words))
            else None
        )
        if m_next:
            frag1 = w[:-1]
            frag2, punct = m_next.group(1), m_next.group(2)

            # Known compound prefix: never join (TWENTY-ONE, X-RAY, ...).
            if frag1.upper() in _COMPOUND_PREFIXES:
                out.append(w)
                i += 1
                continue

            joined = frag1 + frag2
            if dictionary is not None:
                join_is_word = joined.lower() in dictionary
                frag1_is_word = frag1.lower() in dictionary
                frag2_is_word = frag2.lower() in dictionary
                if join_is_word:
                    out.append(joined + punct)
                    i += 2
                    continue
                # Split proper names / unlisted words: "HA- RUTO" -> "HARUTO".
                if not frag1_is_word and not frag2_is_word and len(joined) >= 4:
                    out.append(joined + punct)
                    i += 2
                    continue
            else:
                # No dictionary: join only when the second fragment cannot
                # stand alone (lowercase start strongly suggests a fragment).
                if frag2[:1].islower() and frag1[:1].isupper():
                    out.append(joined + punct)
                    i += 2
                    continue

        out.append(w)
        i += 1

    # Attach floating punctuation tokens (e.g. "?!", "...", "!") to preceding word
    cleaned: List[str] = []
    for w in out:
        if cleaned and not w.strip(_PUNCT_STRIP) and len(w) > 0:
            cleaned[-1] = cleaned[-1] + w
        else:
            cleaned.append(w)
    return cleaned
