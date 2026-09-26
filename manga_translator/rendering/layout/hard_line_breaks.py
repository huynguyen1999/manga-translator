"""Tokens and wrapping helpers for preserved paragraph breaks."""

HARD_LINE_BREAK = "\0"


def split_layout_words(text):
    paragraphs = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    words = []
    for index, paragraph in enumerate(paragraphs):
        words.extend(paragraph.split())
        if index < len(paragraphs) - 1:
            words.append(HARD_LINE_BREAK)
    return words


def precompute_widths(words, font_size):
    try:
        from manga_translator.rendering import text_render

        widths = [0 if word == HARD_LINE_BREAK else int(text_render.get_string_width(font_size, word)) for word in words]
        return widths, int(text_render.get_string_width(font_size, " "))
    except Exception:
        widths = [0 if word == HARD_LINE_BREAK else int(len(word) * font_size * 0.6) for word in words]
        return widths, int(font_size * 0.3)


def next_break_index(words, start, end):
    return next((index for index in range(start, end) if words[index] == HARD_LINE_BREAK), end)


def skip_forced_break(words, index, dp, memo, key, state):
    if words[index] != HARD_LINE_BREAK:
        return False
    memo[key] = dp(index + 1, *state)
    return True
