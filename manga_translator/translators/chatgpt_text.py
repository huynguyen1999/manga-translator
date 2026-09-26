from .constants import KATAKANA_SMALL_TO_NORMAL


def levenshtein_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def normalize_japanese(text: str) -> str:
    result = ""
    for char in text:
        if char in KATAKANA_SMALL_TO_NORMAL:
            char = KATAKANA_SMALL_TO_NORMAL[char]
        if 0x30A0 <= ord(char) <= 0x30FF:
            result += chr(ord(char) - 0x60)
        else:
            result += char
    return result


def japanese_levenshtein_distance(s1: str, s2: str) -> int:
    return levenshtein_distance(normalize_japanese(s1), normalize_japanese(s2))


def normalize_term(term: str) -> str:
    term = re.sub(r'[^\w\s]', '', term).lower()
    return normalize_japanese(term)


def partial_match(text: str, term: str) -> bool:
    return normalize_term(term) in normalize_term(text)


def is_japanese_similar(text: str, term: str, threshold: int = 2) -> bool:
    normalized_text = normalize_term(text)
    normalized_term = normalize_term(term)
    if len(normalized_term) <= 2:
        threshold = 0
    elif len(normalized_term) <= 4:
        threshold = 1
    return japanese_levenshtein_distance(normalized_text, normalized_term) <= threshold


def is_general_similar(text: str, term: str, threshold: int = 2) -> bool:
    normalized_text = normalize_term(text)
    normalized_term = normalize_term(term)
    threshold = max(0, min(len(normalized_term) // 8, 3))

    if len(normalized_text) > len(normalized_term) * 5:
        min_distance = float('inf')
        if len(normalized_term) <= 8:
            window_size = len(normalized_term)
        elif len(normalized_term) <= 16:
            window_size = len(normalized_term) + 1
        else:
            window_size = len(normalized_term) + 2
        for i in range(max(0, len(normalized_text) - window_size + 1)):
            window = normalized_text[i:i + window_size]
            min_distance = min(min_distance, levenshtein_distance(window, normalized_term))
        return min_distance <= threshold

    return levenshtein_distance(normalized_text, normalized_term) <= threshold
