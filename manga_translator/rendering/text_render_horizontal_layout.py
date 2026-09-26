"""Horizontal text measurement and line layout."""

import re
from typing import List, Tuple

import numpy as np

from .horizontal_layout_safety import calc_paragraphs, wrap_without_hyphenation
from .text_render_horizontal_metrics import get_char_offset_x, get_string_width, select_hyphenator


def calc_horizontal(font_size: int, text: str, max_width: int, max_height: int, language: str = 'en_US', hyphenate: bool = True, allow_width_expansion: bool = True) -> Tuple[List[str], List[int]]:
    """
    Splits up a string of text into lines. Returns list of lines and their widths.
    Will go over max_height if too much text is present.
    """
    if "\n" in text or "\r" in text:
        return calc_paragraphs(
            calc_horizontal, font_size, text, max_width, max_height,
            language, hyphenate, allow_width_expansion,
        )

    max_width = max(max_width, 2 * font_size)

    whitespace_offset_x = get_char_offset_x(font_size, ' ')
    hyphen_offset_x = get_char_offset_x(font_size, '-')

    # Split text into words and precalculate each word width
    words = re.split(r'\s+', text)
    word_widths = []
    for i, word in enumerate(words):
        word_widths.append(get_string_width(font_size, word))

    max_lines = max_height // font_size + 1

    # Try to increase width usage if a height overflow is unavoidable
    if allow_width_expansion:
        while True:
            expected_size = sum(word_widths) + max((len(word_widths) - 1) * whitespace_offset_x - (max_lines - 1) * hyphen_offset_x, 0)
            max_size = max_width * max_lines
            if max_size < expected_size:
                multiplier = np.sqrt(expected_size / max_size)
                max_width *= max(multiplier, 1.05)
                max_height *= multiplier
                max_lines = max_height // font_size + 1
            else:
                break

    # Split words into syllables
    syllables = []
    hyphenator = select_hyphenator(language)
    for i, word in enumerate(words):
        new_syls = []
        if hyphenator and len(word) <= 100:
            try:
                new_syls = hyphenator.syllables(word)
            except Exception:
                new_syls = []
        if len(new_syls) == 0:
            if len(word) <= 3:
                new_syls = [word]
            else:
                new_syls = list(word)

        # # Make sure no syllable goes over max_width
        # for syl in syllables[-1]:
        #     w = get_string_width(font_size, syl)
        #     if w > max_width:
        #         max_width = w

        # Split up syllables that are too large
        normalized_syls = []
        for syl in new_syls:
            syl_width = get_string_width(font_size, syl)
            if syl_width > max_width:
                normalized_syls.extend(list(syl))
            else:
                normalized_syls.append(syl)
        syllables.append(normalized_syls)

    line_words_list = []
    line_width_list = []
    hyphenation_idx_list = []
    line_words = []
    line_width = 0
    hyphenation_idx = 0

    def break_line():
        nonlocal line_words, line_width, hyphenation_idx
        line_words_list.append(line_words)
        line_width_list.append(line_width)
        hyphenation_idx_list.append(hyphenation_idx)
        line_words = []
        line_width = 0
        hyphenation_idx = 0

    def get_present_syllables_range(line_idx, word_pos):
        while word_pos < 0:
            word_pos += len(line_words_list[line_idx])
        word_idx = line_words_list[line_idx][word_pos]
        syl_start_idx = 0
        syl_end_idx = len(syllables[word_idx])
        if line_idx > 0 and word_pos == 0 and line_words_list[line_idx - 1][-1] == word_idx:
            syl_start_idx = hyphenation_idx_list[line_idx - 1]
        if line_idx < len(line_words_list) - 1 and word_pos == len(line_words_list[line_idx]) - 1 \
            and line_words_list[line_idx + 1][0] == word_idx:
            syl_end_idx = hyphenation_idx_list[line_idx]
        return syl_start_idx, syl_end_idx

    def get_present_syllables(line_idx, word_pos):
        syl_start_idx, syl_end_idx = get_present_syllables_range(line_idx, word_pos)
        return syllables[line_words_list[line_idx][word_pos]][syl_start_idx:syl_end_idx]


    # Step 1:
    # Arrange words without hyphenating unless necessary

    i = 0
    while True:
        if i >= len(words):
            if line_width > 0:
                break_line()
            break

        current_width = whitespace_offset_x if line_width > 0 else 0

        if line_width + current_width + word_widths[i] <= max_width + hyphen_offset_x:
            line_words.append(i)
            line_width += current_width + word_widths[i]
            i += 1
        elif word_widths[i] > max_width:
            # We know no syllable can be larger than max_width
            j = 0
            hyphenation_idx = 0
            while j < len(syllables[i]):
                syl = syllables[i][j]
                syl_width = get_string_width(font_size, syl)
                if line_width + current_width + syl_width <= max_width or (line_width == 0 and current_width == 0):
                    current_width += syl_width
                    j += 1
                    hyphenation_idx = j
                else:
                    if hyphenation_idx > 0:
                        line_words.append(i)
                        line_width += current_width
                    current_width = 0
                    break_line()
            line_words.append(i)
            line_width += current_width
            i += 1
        else:
            break_line()


    # Step 2:
    # Compare two adjacent lines and try to hyphenate backwards

    # Avoid hyphenation if max_lines isn't fully used
    if hyphenate and len(line_words_list) > max_lines:
        line_idx = 0
        while line_idx < len(line_words_list) - 1:
            line_words1 = line_words_list[line_idx]
            line_words2 = line_words_list[line_idx + 1]
            left_space = max_width - line_width_list[line_idx]

            # Move syllables from below line to above
            first_word = True
            while len(line_words2) != 0:
                word_idx = line_words2[0]

                # A bit messy but were basically trying to only use the syllables on the current line
                if first_word and word_idx == line_words1[-1]:
                    syl_start_idx = hyphenation_idx_list[line_idx]
                    if line_idx < len(line_width_list) - 2 and word_idx == line_words_list[line_idx + 2][0]:
                        syl_end_idx = hyphenation_idx_list[line_idx + 1]
                    else:
                        syl_end_idx = len(syllables[word_idx])
                else:
                    left_space -= whitespace_offset_x
                    syl_start_idx = 0
                    syl_end_idx = len(syllables[word_idx]) if len(line_words2) > 1 else hyphenation_idx_list[line_idx + 1]
                first_word = False

                current_width = 0
                for i in range(syl_start_idx, syl_end_idx):
                    syl = syllables[word_idx][i]
                    syl_width = get_string_width(font_size, syl)
                    if left_space > current_width + syl_width:
                        current_width += syl_width
                    else:
                        # Splitting up word
                        if current_width > 0:
                            # We dont want very small splits
                            # if 
                            left_space -= current_width
                            line_width_list[line_idx] = max_width - left_space
                            hyphenation_idx_list[line_idx] = i
                            line_words1.append(word_idx)
                        break
                else:
                    # Whole word was brought to above line
                    left_space -= current_width
                    line_width_list[line_idx] = max_width - left_space
                    line_words1.append(word_idx)
                    line_words2.pop(0)
                    continue
                break

            if len(line_words2) == 0:
                line_words_list.pop(line_idx + 1)
                line_width_list.pop(line_idx + 1)
                hyphenation_idx_list.pop(line_idx)
            else:
                line_idx += 1

    
    # Step 3
    # Move single char syllables on the left up and those on the right down

    line_idx = 0
    while line_idx < len(line_words_list) - 1:
        line_words1 = line_words_list[line_idx]
        line_words2 = line_words_list[line_idx + 1]
        merged_word_idx = -1

        if line_words1[-1] == line_words2[0]:
            word1_text = ''.join(get_present_syllables(line_idx, -1))
            word2_text = ''.join(get_present_syllables(line_idx + 1, 0))
            word1_width = get_string_width(font_size, word1_text)
            word2_width = get_string_width(font_size, word2_text)
            if len(word2_text) == 1 or word2_width < font_size:
                merged_word_idx = line_words1[-1]
                line_words2.pop(0)
                line_width_list[line_idx] += word2_width
                line_width_list[line_idx + 1] -= word2_width + whitespace_offset_x
            elif len(word1_text) == 1 or word1_width < font_size:
                merged_word_idx = line_words1[-1]
                line_words1.pop(-1)
                line_width_list[line_idx] -= word1_width + whitespace_offset_x
                line_width_list[line_idx + 1] += word1_width

        if len(line_words1) == 0:
            line_words_list.pop(line_idx)
            line_width_list.pop(line_idx)
            hyphenation_idx_list.pop(line_idx)
        elif len(line_words2) == 0:
            line_words_list.pop(line_idx + 1)
            line_width_list.pop(line_idx + 1)
            hyphenation_idx_list.pop(line_idx)
        # We dont want all single letters to be merged
        elif line_idx >= len(line_words_list) - 1 or line_words_list[line_idx + 1] != merged_word_idx:
            line_idx += 1


    # Height fitting may pull syllables backwards until a line exceeds its
    # width. Keep the readable word/character wrapping when that happens.
    if not allow_width_expansion and hyphenate and any(width > max_width + hyphen_offset_x for width in line_width_list):
        return wrap_without_hyphenation(calc_horizontal, font_size, text, max_width, max_height, language)

    # Step 4
    # Assemble line_text_list

    use_hyphen_chars = hyphenate and hyphenator and max_width > 1.5 * font_size and len(words) > 1

    line_text_list = []
    for i, line in enumerate(line_words_list):
        line_text = ''
        for j, word_idx in enumerate(line):
            syl_start_idx, syl_end_idx = get_present_syllables_range(i, j)
            current_syllables = syllables[word_idx][syl_start_idx:syl_end_idx]
            line_text += ''.join(current_syllables)
            if len(line_text) == 0:
                continue
            if j == 0 and i > 0 and line_text_list[-1][-1] == '-' and line_text[0] == '-':
                line_text = line_text[1:]
                line_width_list[i] -= hyphen_offset_x
            if j < len(line) - 1 and len(line_text) > 0:
                line_text += ' '
            elif use_hyphen_chars and syl_end_idx != len(syllables[word_idx]) and len(words[word_idx]) > 3 and line_text[-1] != '-' \
                and not (syl_end_idx < len(syllables[word_idx]) and not re.search(r'\w', syllables[word_idx][syl_end_idx][0])):
                line_text += '-'
                # hyphen_offset was ignored in previous steps
                line_width_list[i] += hyphen_offset_x

        # print(line_text, get_string_width(font_size, line_text), line_width_list[i])
        # assert(line_width_list[i] == get_string_width(font_size, line_text))

        # Shouldn't be needed but there is apparently still a bug somewhere (See #458)
        line_width_list[i] = get_string_width(font_size, line_text)
        line_text_list.append(line_text)

    return line_text_list, line_width_list
