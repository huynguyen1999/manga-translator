import os
from typing import List, Optional

from ..utils import BASE_PATH

WILD_WORDS_FONT_NAMES = [
    'Wild Words.ttf',
    'wild_words.ttf',
    'wildwords.ttf',
    'Wild Words Roman.ttf',
    'wild_words_roman.ttf',
    'CC Wild Words Roman.ttf',
    'CCWildWords-Roman.ttf',
    'CC Wild Words.ttf',
    'CCWildWords.ttf',
    'Wild Words.otf',
    'wild_words.otf',
    'wildwords.otf',
]


def get_default_eng_font() -> str:
    """Return the default font path for English rendering, preferring Wild Words."""
    fonts_dir = os.path.join(BASE_PATH, 'fonts')
    for name in WILD_WORDS_FONT_NAMES:
        candidate = os.path.join(fonts_dir, name)
        if os.path.isfile(candidate):
            return candidate
    for fallback in ['anime_ace.ttf', 'comic shanns 2.ttf', 'NotoSansMonoCJK-VF.ttf.ttc']:
        candidate = os.path.join(fonts_dir, fallback)
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(fonts_dir, 'Wild Words.ttf')


FONT_NAME_MAP = {
    'wildwords': WILD_WORDS_FONT_NAMES,
    'wild_words': WILD_WORDS_FONT_NAMES,
    'wild words': WILD_WORDS_FONT_NAMES,
    'anime_ace': ['anime_ace.ttf'],
    'anime_ace_3': ['anime_ace_3.ttf'],
    'comic_shanns': ['comic shanns 2.ttf', 'comic_shanns_2.ttf', 'comic_shanns.ttf'],
    'arial_unicode': ['Arial-Unicode-Regular.ttf', 'arial_unicode.ttf', 'ArialUnicode.ttf'],
    'noto_sans': ['NotoSansMonoCJK-VF.ttf.ttc', 'NotoSansCJK-VF.ttf.ttc', 'NotoSansCJK.ttc'],
    'msgothic': ['msgothic.ttc'],
    'msyh': ['msyh.ttc'],
}


def resolve_font_name_or_path(font_name_or_path: Optional[str] = None) -> str:
    """Resolve a font name, key, or path to a valid font file path, defaulting to Wild Words."""
    if not font_name_or_path or font_name_or_path in ('default', 'auto', 'Sans-serif'):
        return get_default_eng_font()

    # If it's already an existing file path, return it directly
    if os.path.isfile(font_name_or_path):
        return os.path.abspath(font_name_or_path)

    fonts_dir = os.path.join(BASE_PATH, 'fonts')
    direct_candidate = os.path.join(fonts_dir, font_name_or_path)
    if os.path.isfile(direct_candidate):
        return direct_candidate

    normalized = font_name_or_path.strip().lower().replace('-', '_').replace(' ', '_')
    candidates = FONT_NAME_MAP.get(normalized, [])
    for cand in candidates:
        cand_path = os.path.join(fonts_dir, cand)
        if os.path.isfile(cand_path):
            return cand_path

    # Try case-insensitive lookup in fonts_dir
    if os.path.isdir(fonts_dir):
        for fname in os.listdir(fonts_dir):
            clean_fname = fname.lower().replace('-', '_').replace(' ', '_')
            if clean_fname.startswith(normalized) or normalized in clean_fname:
                full_p = os.path.join(fonts_dir, fname)
                if os.path.isfile(full_p):
                    return full_p

    return get_default_eng_font()


def parse_font_paths(path: str, default: List[str] = None) -> List[str]:
    if path:
        parsed = path.split(',')
        parsed = list(filter(lambda p: os.path.isfile(p), parsed))
    else:
        parsed = default or []
    return parsed
