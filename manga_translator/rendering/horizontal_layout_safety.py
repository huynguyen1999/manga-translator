"""Boundary-preserving wrappers for horizontal text layout."""


def calc_paragraphs(calc, font_size, text, width, height, language, hyphenate, expand):
    paragraphs = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    results = [
        calc(font_size, part, width, height, language, hyphenate, expand)
        if part.strip() else ([""], [0])
        for part in paragraphs
    ]
    return ([line for lines, _ in results for line in lines],
            [width for _, widths in results for width in widths])


def wrap_without_hyphenation(calc, font_size, text, width, height, language):
    return calc(font_size, text, width, height, language, False, False)
