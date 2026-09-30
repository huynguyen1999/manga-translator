"""Shared Gemini response diagnostics for translation and synopsis requests."""

from __future__ import annotations

from typing import Any


class GeminiBlockedResponse(RuntimeError):
    """Gemini returned a prompt or candidate policy block; do not retry it."""


_BLOCKED_FINISH_REASONS = {
    "SAFETY", "PROHIBITED_CONTENT", "RECITATION", "LANGUAGE",
    "BLOCKLIST", "SPII", "IMAGE_SAFETY", "CONTENT_BLOCKED",
}


def inspect_gemini_response(response: Any) -> tuple[bool, list[str]]:
    """Return whether Gemini blocked a response and the available diagnostics."""
    candidates = getattr(response, "candidates", None) or []
    candidate = candidates[0] if candidates else None
    prompt_feedback = getattr(response, "prompt_feedback", None)
    prompt_reason = getattr(prompt_feedback, "block_reason", None)
    prompt_ratings = getattr(prompt_feedback, "safety_ratings", None) or []
    finish_reason = getattr(candidate, "finish_reason", None)
    finish_reason_name = (
        getattr(finish_reason, "name", None)
        or str(finish_reason).rsplit(".", 1)[-1]
        if finish_reason is not None
        else None
    )
    candidate_ratings = getattr(candidate, "safety_ratings", None) or []

    prompt_blocked = (
        prompt_reason is not None
        and (getattr(prompt_reason, "name", None) or str(prompt_reason).rsplit(".", 1)[-1])
        not in {"BLOCK_REASON_UNSPECIFIED", "UNSPECIFIED", "NONE"}
    ) or any(getattr(rating, "blocked", False) for rating in prompt_ratings)
    candidate_blocked = (
        finish_reason_name in _BLOCKED_FINISH_REASONS
        or any(getattr(rating, "blocked", False) for rating in candidate_ratings)
    )

    diagnostics = []
    for name, value in (
        ("prompt_block_reason", prompt_reason),
        ("finish_reason", finish_reason),
        ("finish_message", getattr(candidate, "finish_message", None)),
        ("prompt_safety_ratings", prompt_ratings or None),
        ("candidate_safety_ratings", candidate_ratings or None),
    ):
        if value is not None:
            diagnostics.append(f"{name}={str(value).replace(chr(10), ' ')[:500]}")
    return prompt_blocked or candidate_blocked, diagnostics
