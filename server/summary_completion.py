"""Provider completion helpers for manga synopsis generation."""

from __future__ import annotations

import json
from typing import Any

from manga_translator.professional_prompts import build_synopsis_system_prompt


def _short_diagnostic(value: Any) -> str:
    return str(value).replace("\n", " ")[:500]


def summary_error_details(exc: BaseException) -> str:
    detail = str(exc).strip() or type(exc).__name__
    status_code = getattr(exc, "status_code", None)
    if not isinstance(status_code, int):
        status_code = getattr(exc, "code", None)
    if isinstance(status_code, int) and f"status_code={status_code}" not in detail:
        detail += f" status_code={status_code}"
    api_message = getattr(exc, "message", None)
    body = getattr(exc, "body", None)
    if not api_message and body is not None:
        error_body = body.get("error", body) if isinstance(body, dict) else body
        api_message = error_body.get("message") if isinstance(error_body, dict) else None
        if not api_message and error_body:
            api_message = (
                json.dumps(error_body, ensure_ascii=False, default=str)
                if isinstance(error_body, dict)
                else str(error_body)
            )
    if api_message:
        api_message = str(api_message).replace("\n", " ")[:1000]
        if api_message not in detail:
            detail += f" api_error={api_message}"
    api_status = getattr(exc, "status", None)
    if api_status:
        detail += f" api_status={api_status}"
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    request_id = (
        getattr(exc, "request_id", None)
        or headers.get("x-goog-request-id")
        or headers.get("x-request-id")
    )
    if request_id:
        detail += f" request_id={request_id}"
    request = getattr(exc, "request", None)
    method = getattr(request, "method", None)
    url = getattr(request, "url", None)
    if method and url:
        detail += f" request={method} {str(url).split('?', 1)[0]}"

    causes = []
    cause = exc.__cause__ or exc.__context__
    while cause is not None:
        cause_detail = type(cause).__name__
        if str(cause).strip():
            cause_detail += f": {str(cause).strip()}"
        causes.append(cause_detail)
        cause = cause.__cause__ or cause.__context__
    if causes:
        detail += f" cause={' -> '.join(causes)}"
    return detail


async def _summary_completion(
    client,
    language: str,
    text: str,
    merge: bool,
    model: str,
    provider: str,
) -> str:
    instruction = (
        "Combine the supplied partial summaries into one spoiler-inclusive synopsis. Deduplicate overlapping "
        "events and recurring explanations, preserve causal order within each story, keep every detail attached "
        "to the correct story, and never combine unrelated stories or invent continuity between them."
        if merge
        else "Create a detailed, spoiler-inclusive synopsis from the supplied original manga dialogue and narration."
    )
    request_options = {"temperature": 0.5, "max_tokens": 8192}
    if provider == "deepseek":
        request_options["extra_body"] = {"thinking": {"type": "disabled"}}
    message = None
    if provider == "gemini":
        from google.genai import types

        from manga_translator.translators.constants import DEFAULT_GEMINI_SAFETY_SETTINGS

        response = await client.aio.models.generate_content(
            model=model,
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=build_synopsis_system_prompt(instruction, language),
                temperature=0.5,
                max_output_tokens=8192,
                safety_settings=DEFAULT_GEMINI_SAFETY_SETTINGS,
            ),
        )
        status_code = 200  # google-genai raises APIError for every non-200 response.
        choices = getattr(response, "candidates", None) or []
        choice = choices[0] if choices else None
        try:
            content = response.text
        except Exception:
            content = None
        response_id = getattr(response, "response_id", None)
        response_model = getattr(response, "model_version", None)
        prompt_feedback = getattr(response, "prompt_feedback", None)
        prompt_block_reason = getattr(prompt_feedback, "block_reason", None)
        prompt_ratings = getattr(prompt_feedback, "safety_ratings", None) or []
        finish_reason = getattr(choice, "finish_reason", None)
        finish_reason_name = str(finish_reason).rsplit(".", 1)[-1] if finish_reason is not None else None
        candidate_ratings = getattr(choice, "safety_ratings", None) or []
        blocked_finish_reasons = {
            "SAFETY", "PROHIBITED_CONTENT", "RECITATION", "LANGUAGE",
            "BLOCKLIST", "SPII", "IMAGE_SAFETY", "CONTENT_BLOCKED",
        }
        prompt_blocked = (
            prompt_block_reason is not None
            and str(prompt_block_reason).rsplit(".", 1)[-1]
            not in {"BLOCK_REASON_UNSPECIFIED", "UNSPECIFIED", "NONE"}
        ) or any(getattr(rating, "blocked", False) for rating in prompt_ratings)
        candidate_blocked = (
            finish_reason_name in blocked_finish_reasons
            or any(getattr(rating, "blocked", False) for rating in candidate_ratings)
        )
        if prompt_blocked or candidate_blocked:
            diagnostics = ["status_code=200"]
            diagnostics.extend(
                f"{name}={value}"
                for name, value in (
                    ("response_id", response_id),
                    ("response_model", response_model),
                    ("prompt_block_reason", prompt_block_reason),
                    ("finish_reason", finish_reason),
                    ("finish_message", getattr(choice, "finish_message", None)),
                    ("prompt_safety_ratings", prompt_ratings or None),
                    ("candidate_safety_ratings", candidate_ratings or None),
                )
                if value is not None
            )
            prefix = "Gemini returned no text" if not isinstance(content, str) or not content.strip() else "Gemini returned blocked content"
            raise RuntimeError(f"{prefix} ({', '.join(diagnostics)})")
    else:
        completion = client.with_options(timeout=90.0).chat.completions
        request_options.update({
            "model": model,
            "messages": [
                {"role": "system", "content": build_synopsis_system_prompt(instruction, language)},
                {"role": "user", "content": text},
            ],
        })
        raw_completion = getattr(completion, "with_raw_response", None)
        if raw_completion is not None:
            raw_response = await raw_completion.create(**request_options)
            status_code = getattr(raw_response, "status_code", None)
            response = raw_response.parse()
        else:
            status_code = None
            response = await completion.create(**request_options)
        choices = getattr(response, "choices", None) or []
        choice = choices[0] if choices else None
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        response_id = getattr(response, "id", None) or getattr(response, "_request_id", None)
        response_model = getattr(response, "model", None)
    if not isinstance(content, str) or not content.strip():
        diagnostics = [f"status_code={status_code}"] if status_code is not None else []
        diagnostics.extend(
            f"{name}={value}"
            for name, value in (
                ("response_id", response_id),
                ("response_model", response_model),
                ("choices", len(choices)),
                ("finish_reason", getattr(choice, "finish_reason", None)),
                ("finish_message", getattr(choice, "finish_message", None)),
            )
            if value is not None
        )
        if provider == "gemini":
            for field in ("prompt_feedback", "safety_ratings"):
                value = getattr(response, field, None) or getattr(choice, field, None)
                if value:
                    diagnostics.append(f"{field}={_short_diagnostic(value)}")
        elif choice is not None and message is None:
            diagnostics.append("message=missing")
        elif message is not None:
            diagnostics.append(f"content_type={type(content).__name__}")
            refusal = getattr(message, "refusal", None)
            if refusal:
                diagnostics.append(f"refusal={_short_diagnostic(refusal)}")
        api_error = getattr(response, "error", None)
        if api_error:
            diagnostics.append(f"api_error={_short_diagnostic(api_error)}")
        detail = f"{provider.title()} returned no text"
        if diagnostics:
            detail += f" ({', '.join(diagnostics)})"
        raise RuntimeError(detail)
    return content.strip()
