# -*- coding: utf-8 -*-
"""
Constants used across translation modules (ChatGPT, Gemini, DeepSeek, etc.)
"""
import re

# Suspicious hallucination characters often produced by certain LLMs
SUSPICIOUS_HALLUCINATION_SYMBOLS = ('ହ', 'ି', 'ഹ')  # ['ହ', 'ି', 'ഹ']

# Regex patterns for index prefixes like <|1|>, <|2|>, etc.
RE_INDEX_TAG = re.compile(r'<\|(\d+)\|>')
RE_INDEX_LINE_PREFIX = re.compile(r'^<\|(\d+)\|>(.*)')
RE_INDEX_LINE_START = re.compile(r'^\s*<\|1\|>')
RE_CONSECUTIVE_NEWLINES = re.compile(r'\n\s*\n')
RE_THINK_TAGS = re.compile(r'(</think>)?<think>.*?</think>', flags=re.DOTALL)

# Punctuation/special character escaping for glossary regexes
REGEX_ESCAPE_CHARS = {
    '[': r'\[', ']': r'\]',
    '(': r'\(', ')': r'\)',
    '{': r'\{', '}': r'\}',
    '.': r'\.', '*': r'\*',
    '+': r'\+', '?': r'\?',
    '|': r'\|', '^': r'\^',
    '$': r'\$', '\\': r'\\',
    '/': r'\/'
}

# Small katakana to standard katakana mapping for Japanese text normalization
KATAKANA_SMALL_TO_NORMAL = {
    'ァ': 'ア', 'ィ': 'イ', 'ゥ': 'ウ', 'ェ': 'エ', 'ォ': 'オ',
    'ッ': 'ツ', 'ャ': 'ヤ', 'ュ': 'ユ', 'ョ': 'ヨ',
    'ぁ': 'あ', 'ぃ': 'い', 'ぅ': 'う', 'ぇ': 'え', 'ぉ': 'お',
    'っ': 'つ', 'ゃ': 'や', 'ゅ': 'ゆ', 'ょ': 'よ'
}

# Default safety settings for Gemini models (BLOCK_NONE)
DEFAULT_GEMINI_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
]

