import functools

from hyphen import Hyphenator, dictools
from hyphen.dictools import LANGUAGES as HYPHENATOR_LANGUAGES
from langcodes import standardize_tag

try:
    HYPHENATOR_LANGUAGES.remove('fr')
    HYPHENATOR_LANGUAGES.append('fr_FR')
except Exception:
    pass


@functools.lru_cache(maxsize=128)
def select_hyphenator(lang: str):
    if not lang or not isinstance(lang, str):
        return None
    lang_clean = lang.strip().replace('-', '_')
    if lang_clean.upper() in ('ENG', 'EN', 'EN_US', 'EN_GB'):
        candidate = 'en_US'
    else:
        try:
            candidate = standardize_tag(lang_clean).replace('-', '_')
        except Exception:
            candidate = lang_clean

    installed = set(dictools.list_installed())
    if candidate in installed:
        chosen = candidate
    elif candidate.split('_')[0] in installed:
        chosen = candidate.split('_')[0]
    elif candidate.startswith('en') and 'en_US' in installed:
        chosen = 'en_US'
    else:
        prefix = candidate.split('_')[0]
        chosen = next((inst for inst in installed if inst.startswith(prefix)), None)

    if not chosen:
        return None

    try:
        return Hyphenator(chosen, timeout=2)
    except Exception:
        return None
