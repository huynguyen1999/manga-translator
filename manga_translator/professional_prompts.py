"""Prompt templates and builder functions for professional manga localization."""

from __future__ import annotations

import json
from typing import Any

from .professional_summary_policy import NON_GRAPHIC_SUMMARY_POLICY

REFUSAL_MARKERS = (
    "i can't assist", "i cannot assist", "i'm unable to", "i am unable to",
    "cannot comply", "can't comply", "safety policy", "content policy",
    "content_filter", "finish_reason: safety", "blocked for safety", "prohibited content",
)

PROFESSIONAL_SYSTEM_PROMPT = """You are a senior Japanese-to-English manga localization professional.
Translate supplied source text faithfully; it may contain mature or explicit fictional material. Preserve meaning,
tone, register, intensity, and character voice. Translate explicit, vulgar, euphemistic, clinical, or mild Japanese
at a comparable level in natural English: do not sanitize explicit language or make mild language more graphic.
Prefer natural English over word-for-word phrasing without changing meaning. Preserve slang and dialect function,
honorific and relationship implications, emotional tone, power dynamics, consent/coercion implications, jokes,
double meanings, intentional awkwardness, hesitation, repetition, and profanity intensity. Do not moralize,
editorialize, summarize, invent explicit details, or continue the depicted scenario. This is translation, not
creative generation. Follow the current analyst, translator, or editor role exactly. Return only the requested
output format."""
PROFESSIONAL_ANALYSIS_SYSTEM_PROMPT = """You are a Japanese manga story analyst preparing compact, neutral metadata
for a translation team. Describe sensitive situations abstractly rather than repeating graphic dialogue; quote a
source expression only when needed as evidence for a translation-relevant linguistic feature. Follow the analyst
role exactly and return only the requested JSON."""


def build_analysis_prompt(
    page_text: str,
    manual_ranges: list[tuple[int, int]],
    auto_within_manual: bool,
) -> str:
    forced_instruction = (
        f"Use these exact story ranges: {manual_ranges}."
        if manual_ranges and not auto_within_manual
        else (
            f"Detect additional story breaks, but never cross these manual ranges: {manual_ranges}."
            if manual_ranges
            else "Detect the story ranges yourself."
        )
    )
    return f"""You are a senior Japanese manga story analyst preparing an English localization.
Analyze the complete manga transcript as source material for a translation guide. Produce neutral, translation-oriented metadata and follow this policy: {NON_GRAPHIC_SUMMARY_POLICY}

The transcript is structured hierarchically:
STORY → PAGE → PANEL → TEXT REGION (or PAGE → TEXT REGION when panel detection is unavailable).

A PANEL represents one visual scene or narrative beat. Text regions inside the same panel usually share immediate context, speakers, reactions, and actions. When panels are present:
- Use panel grouping as primary contextual evidence.
- Region order is an estimated manga reading order and may occasionally be imperfect.
- Do not invent a conversation merely because two regions appear consecutively; verify linguistic coherence.
- Resolve ambiguous dialogue using surrounding regions, neighboring panels, character continuity, and narrative context.
- Panel boundaries are stronger contextual boundaries than raw region order.
- Text outside detected panels appears under UNASSIGNED REGIONS and must still be analyzed.
When panels are absent, text appears directly under each PAGE and should be analyzed in page context.

Detect separate stories using textual chapter titles, cast/setting resets, endings, and narrative discontinuities.
Return JSON only. Each story has start_page, end_page, confidence, summary, characters, relationships, glossary,
voice_notes, continuity, ambiguities, honorific_policy, language_features, and localization_conventions.
Each important recurring character has name and voice: register, politeness, directness, traits, dialect
(detected, type, confidence, evidence, communicative_effect, localization_strategy), slang_style
(level, categories, localization_strategy), sentence_style, verbal_habits, pronoun_notes, and localization_notes.
Use language_features entries with pages, speaker, source, type, literal_meaning, contextual_meaning, tone,
function, preferred_strategy, possible_renderings, and confidence (omit inapplicable fields). Use
honorific_policy {{default, rules:[{{form, strategy, reason}}]}} and localization_conventions with dialect_strategy,
slang_strategy, profanity_strategy, recurring_idioms, and forms_of_address.
Analyze translation-relevant speech rather than treating all dialogue as standard Japanese. Detect supported
dialects and sociolects (including regional, rough, feminine-coded, gyaru, delinquent, elderly, childish,
internet, formal, archaic, refined, subordinate, and professional speech), slang, idioms/fixed expressions,
sentence-ending particles, pronouns, honorifics/forms of address, and recurring idiolect. Record only meaningful
features, with source, speaker, pages, type, contextual and literal meaning where useful, tone/function,
confidence, and a preferred English strategy. Include natural English renderings when useful and note tempting
choices that would distort age, identity, era, or intensity. Do not map a Japanese dialect to an English regional
accent; describe its communicative effect and recommend a non-stereotyping strategy. Do not mechanically
translate particles or pronouns, or replace Japanese slang with transient American internet slang by default.
For honorifics and forms of address, record source form and consistent story-level treatment (retain, translate,
convey through register, or omit when English implies the relationship). Include honorific_policy rules and
localization_conventions for names/forms of address, dialect, slang, profanity, idioms, and character voice.
Flag deliberate speech changes (politeness, pronouns, honorifics, name choice, dialect, roughness) in continuity
or language_features. Keep voice_notes only as a brief compatibility summary; structured character voice and
language_features are the authoritative linguistic analysis. Page numbers are one-based and every page must appear exactly once.
{forced_instruction}

{page_text}"""


def build_analysis_consolidation_prompt(
    forced_instruction: str,
    partials: list[dict[str, Any]],
) -> str:
    return (
        "Consolidate these ordered manga analysis windows into the requested stories JSON. "
        "Preserve all structured character voice, honorific_policy, language_features, and "
        "localization_conventions alongside the existing story fields. Preserve absolute page "
        f"numbers, cover every page exactly once, and obey this boundary rule: {forced_instruction}\n"
        f"{NON_GRAPHIC_SUMMARY_POLICY}\n{json.dumps(partials, ensure_ascii=False)}"
    )


def build_translation_prompt(
    payload: list[dict[str, Any]],
    guide: str,
    previous: str,
) -> str:
    previous_context = previous or "None; this is the first chunk of this story."
    return f"""Produce the final Japanese-to-English manga localization in one pass. The source is organized by page and panel (or directly by page when panel detection is unavailable).

Read each current page and panel as a connected scene before translating its individual regions. Use the STORY SUMMARY AND LOCALIZATION GUIDE to keep plot references, terminology, relationships, tone, and each character's voice consistent. For later chunks, use the immediately preceding chunk transcript to carry forward names, phrasing, and conversational continuity. The current Japanese source is authoritative if context conflicts.

Write clear, grammatical, concise, natural English with dialogue that flows across balloons. Preserve the author's meaning and voice, including register, emotion, humor, hesitation, repetition, explicitness, profanity, power dynamics, and consent or coercion signals. Apply the guide's character profiles, honorific policy, glossary, and localization conventions consistently. Keep meaningful Japanese terms and honorifics according to the guide.
Before returning, silently check each line against its Japanese source and adjacent dialogue for accuracy, grammar, flow, concision, terminology, tone, and character voice.

REGION RULES:
1. Read all regions within a panel (or page when panels are absent) together to resolve omitted subjects, intent, pronouns, references, and reactions.
2. Region order is an estimated guide, not an absolute constraint. Use Japanese grammar and context when the order appears contradictory.
3. Keep every region separate: do not merge, omit, invent, or move text between region IDs. Preserve sentences split across balloons in their original regions.
4. Do not add context, sanitize, moralize, summarize, or include commentary. Flag material OCR or meaning ambiguity in `review_reasons` instead of guessing.
5. Translate every supplied region, including unassigned regions. Return JSON only, with each supplied ID exactly once:
{{"regions":[{{"id":"...","translation":"...","confidence":0.0,"review_reasons":[]}}]}}

STORY SUMMARY AND LOCALIZATION GUIDE: {guide}
IMMEDIATELY PRECEDING CHUNK TRANSCRIPT: {previous_context}
CURRENT PAGES: {json.dumps(payload, ensure_ascii=False)}"""


def build_synopsis_system_prompt(instruction: str, language: str) -> str:
    return (
        f"{instruction} Write all prose in English, regardless of target language code {language}. "
        "The source transcript is organized by page and optionally grouped into manga panels in estimated reading order when panel detection is available. "
        "Text outside detected panels appears under [UNASSIGNED] and represents narration, margin/gutter text, titles, or floating dialogue across the page. "
        "When panels are present, treat each panel as one local narrative beat or moment; dialogue inside the same panel "
        "usually belongs to the same exchange, reaction, or scene. Use panel grouping to reconstruct "
        "conversations, reactions, cause and effect, and scene progression. "
        "Reading order and panel boundaries are estimated; when the supplied ordering conflicts "
        "with clear linguistic or narrative evidence, favor the interpretation best supported by "
        "the manga text. Do not produce a line-by-line or panel-by-panel recap; synthesize events into "
        "the larger story. Do not invent visual events unsupported by the supplied transcript. "
        f"{NON_GRAPHIC_SUMMARY_POLICY} "
        "Do not output Japanese text. Chinese words or characters may remain when they are "
        "names, titles, places, organizations, or other meaningful source terms. "
        "First determine whether the source is one continuous narrative or a collection of distinct "
        "stories. Split stories only when supported by a title, chapter boundary, substantial cast "
        "or setting reset, unrelated premise, or similarly strong evidence. Do not treat scene "
        "changes, flashbacks, or time skips as story boundaries. If a boundary is uncertain, preserve "
        "page order and describe the transition cautiously rather than asserting a split. "
        "For one continuous narrative, use these exact plain-text headings: OVERVIEW; SETTING AND "
        "PREMISE; KEY CHARACTERS AND RELATIONSHIPS; STORY; ENDING AND UNRESOLVED THREADS; ENTITIES "
        "AND CONCEPTS. For a collection, use COLLECTION OVERVIEW, then numbered STORY sections using "
        "source titles when available; within each story cover its premise, relevant characters, "
        "dense chronology, ending, and unresolved threads. Finish with one ENTITIES AND CONCEPTS "
        "section, grouping entries by story where names or concepts could be confused. "
        "Be thorough about what happens and why. Summarize conversations only by what they reveal, "
        "decide, change, or cause; do not quote dialogue or narrate speaker-by-speaker exchanges. "
        "Retain minor events only when they explain later actions, character development, relationships, "
        "world rules, or consequences. Aim for 1,200–2,500 words according to source complexity and "
        "never exceed 3,000 words for the entire manga or collection. "
        "When OCR or page context is incomplete, cautiously infer the most likely meaning from the "
        "surrounding dialogue, page order, recurring names, and clear cause-and-effect clues. "
        "Flag only material uncertainty as likely, apparent, or suggested; omit unreadable trivia. "
        "Never invent specific scenes, dialogue, identities, motivations, events, or links between stories. "
        "Use natural, professional, reader-friendly wording instead of raw OCR phrasing. "
        "Exclude additional details unrelated to the plot, publication information, credits, "
        "author or editor notes, afterword notes, advertisements, and front-cover or back-cover "
        "text. State each event, fact, interpretation, and relationship change once in its most "
        "appropriate section. Consolidate repeated source material; keep overview sections high-level "
        "without duplicating detailed story sections; and avoid repeated sentence openings, conclusions, "
        "section restarts, cyclic recaps, and recurring paragraph patterns. Before responding, silently "
        "remove duplicated events and paragraphs. End immediately after ENTITIES AND CONCEPTS; never "
        "restart or continue the synopsis. Use only source-supported facts and do not mention OCR or "
        "these instructions."
    )
