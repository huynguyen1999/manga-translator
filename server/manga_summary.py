"""Manga synopsis helpers with a file-backed compatibility path."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from server.image_variants import final_file
from typing import Any, Iterable

try:
    import openai
except ImportError:  # pragma: no cover - requirements include openai in production
    openai = None


SUMMARY_DIR = ".summaries"
DEFAULT_SUMMARY_MODEL = "deepseek-flash"

DEFAULT_SUMMARY_CHUNK_LIMITS: dict[str, int] = {
    "deepseek": 64_000,
    "gemini": 120_000,
    "groq": 3_500,
}
DEFAULT_SUMMARY_CHUNK_LIMIT = 3_500


def get_summary_chunk_limit(provider: str | None, model: str | None = None) -> int:
    """Return the max input token limit per chunk for a given summary provider."""
    normalized_provider = (provider or "").strip().lower()
    env_key = f"SUMMARY_CHUNK_LIMIT_{normalized_provider.upper()}"
    env_val = os.getenv(env_key, "").strip()
    if env_val.isdigit() and int(env_val) > 0:
        return int(env_val)

    env_default = os.getenv("SUMMARY_CHUNK_LIMIT_DEFAULT", "").strip()
    if env_default.isdigit() and int(env_default) > 0:
        default_limit = int(env_default)
    else:
        default_limit = DEFAULT_SUMMARY_CHUNK_LIMIT

    return DEFAULT_SUMMARY_CHUNK_LIMITS.get(normalized_provider, default_limit)


def summary_error_details(exc: BaseException) -> str:
    detail = str(exc).strip() or type(exc).__name__
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


def _natural_key(value: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def _clean_title(title: str) -> str:
    return (title or "").strip() or "Ungrouped"


def summary_path(result_root: Path, title: str) -> Path:
    key = hashlib.sha256(_clean_title(title).encode("utf-8")).hexdigest()
    return result_root / SUMMARY_DIR / f"{key}.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return default


def _page_meta(page_path: Path) -> dict[str, Any]:
    value = _read_json(page_path / "meta.json", {})
    return value if isinstance(value, dict) else {}


def _page_order(meta: dict[str, Any]) -> int | None:
    try:
        order = int(meta.get("pageOrder"))
    except (TypeError, ValueError):
        return None
    return order if order > 0 else None


def group_pages(result_root: Path, title: str) -> list[dict[str, Any]]:
    clean_title = _clean_title(title)
    pages: list[dict[str, Any]] = []
    if not result_root.is_dir():
        return pages
    for page_path in result_root.iterdir():
        if not page_path.is_dir() or final_file(page_path) is None:
            continue
        meta = _page_meta(page_path)
        page_title = _clean_title(str(meta.get("mangaTitle") or "Ungrouped"))
        if page_title != clean_title:
            continue
        pages.append({
            "folder": page_path.name,
            "path": page_path,
            "name": str(meta.get("originalName")) if meta.get("originalName") and meta.get("originalName") != "Unknown" else f"{page_path.name}.png",
            "meta": meta,
        })
    pages.sort(
        key=lambda page: (
            _page_order(page["meta"]) is None,
            _page_order(page["meta"]) or 0,
            _natural_key(page["name"]),
            page["folder"],
        )
    )
    return pages


def region_texts(regions: Any) -> list[str]:
    if not isinstance(regions, list):
        return []
    texts: list[str] = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        text = region.get("original_text")
        if not isinstance(text, str) or not text.strip():
            text = region.get("text_raw")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return texts


def read_page_text(page: dict[str, Any]) -> list[str]:
    texts = region_texts(page.get("textRegions"))
    if texts:
        return texts
    path = page.get("path")
    if isinstance(path, Path):
        regions_path = path / "text_regions.json"
        if regions_path.is_file():
            return region_texts(_read_json(regions_path, []))
    return []


def is_page_text_extracted(page: dict[str, Any], has_group_text: bool = False) -> bool:
    """Return True if text detection/OCR was already performed or cached for this page."""
    texts = read_page_text(page)
    if texts:
        return True

    if has_group_text:
        meta = page.get("meta") or {}
        source_type = str(page.get("sourceType") or meta.get("sourceType") or "").lower()
        if source_type == "translated":
            return True
        path = page.get("path")
        if isinstance(path, Path) and (path / "text_regions.json").is_file():
            return True
        if page.get("hasRegions") is True:
            return True

    return False


def source_snapshot(pages: Iterable[dict[str, Any]]) -> dict[str, Any]:
    pages_list = list(pages)
    has_group_text = any(bool(read_page_text(page)) for page in pages_list)
    entries = []
    fingerprint_entries = []
    for page in pages_list:
        texts = read_page_text(page)
        extracted = is_page_text_extracted(page, has_group_text=has_group_text)
        entries.append({
            "folder": page["folder"],
            "name": page["name"],
            "texts": texts,
            "missing": not extracted,
        })
        fingerprint_entries.append({
            "name": page["name"],
            "texts": texts,
        })
    encoded = json.dumps(fingerprint_entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return {
        "entries": entries,
        "fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "texts": [text for entry in entries for text in entry["texts"]],
        "missing": [entry for entry in entries if entry["missing"]],
    }


def load_summary(result_root: Path, title: str) -> dict[str, Any] | None:
    value = _read_json(summary_path(result_root, title), None)
    return value if isinstance(value, dict) else None


def save_summary(result_root: Path, title: str, value: dict[str, Any]) -> None:
    path = summary_path(result_root, title)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


import datetime as dt

def update_summary_job(
    result_root: Path,
    title: str,
    status: str,
    error: str | None = None,
    stage: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    current_page: int | None = None,
    page_count: int | None = None,
    pages_with_text: int | None = None,
    extraction_required: bool | None = None,
    provider: str | None = None,
    model: str | None = None,
    refresh_text: bool | None = None,
    regenerate: bool | None = None,
    stage_passed_count: int | None = None,
) -> None:
    value = load_summary(result_root, title) or {}
    value.update({
        "mangaTitle": _clean_title(title),
        "jobStatus": status,
        "jobError": error,
        "jobUpdatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    if status in {"queued", "generating", "paused"}:
        value["jobDismissed"] = False
    if stage is not None:
        value["jobStage"] = stage
    if progress is not None:
        value["jobProgress"] = max(0, min(100, progress))
    if message is not None:
        value["jobMessage"] = message
    if current_page is not None:
        value["jobCurrentPage"] = max(0, current_page)
    if stage_passed_count is not None:
        value["jobStagePassedCount"] = max(0, stage_passed_count)
    if page_count is not None:
        value["jobPageCount"] = max(0, page_count)
    if pages_with_text is not None:
        value["jobPagesWithText"] = max(0, pages_with_text)
    if extraction_required is not None:
        value["jobExtractionRequired"] = extraction_required
    if provider is not None:
        value["provider"] = provider
    if model is not None:
        value["model"] = model
    if refresh_text is not None:
        value["jobRefreshText"] = refresh_text
    if regenerate is not None:
        value["jobRegenerate"] = regenerate
    save_summary(result_root, title, value)


def dismiss_summary_job(result_root: Path, title: str) -> None:
    value = load_summary(result_root, title) or {"mangaTitle": _clean_title(title)}
    value["jobDismissed"] = True
    save_summary(result_root, title, value)


def reconcile_summary_jobs(result_root: Path) -> None:
    if not result_root.is_dir():
        return
    summary_dir = result_root / SUMMARY_DIR
    if not summary_dir.is_dir():
        return
    for path in summary_dir.glob("*.json"):
        value = _read_json(path, None)
        if not isinstance(value, dict):
            continue
        status = value.get("jobStatus")
        if status == "generating":
            if value.get("summary"):
                value["jobStatus"] = "ready"
                value["jobStage"] = "complete"
                value["jobProgress"] = 100
                value["jobMessage"] = None
            else:
                value["jobStatus"] = "queued"
                value["jobProgress"] = 0
                value["jobMessage"] = "Waiting for an available worker"
                value["jobStage"] = (
                    "detecting" if value.get("jobExtractionRequired", True) else "concatenating"
                )
            value["jobUpdatedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
            save_summary(result_root, value.get("mangaTitle") or path.stem, value)
        elif status == "queued":
            if value.get("summary") and not value.get("jobRegenerate") and not value.get("jobRefreshText"):
                value["jobStatus"] = "ready"
                value["jobStage"] = "complete"
                value["jobProgress"] = 100
                value["jobMessage"] = None
                value["jobUpdatedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
                save_summary(result_root, value.get("mangaTitle") or path.stem, value)


def list_runnable_summary_jobs(result_root: Path) -> list[dict[str, Any]]:
    if not result_root.is_dir():
        return []
    summary_dir = result_root / SUMMARY_DIR
    if not summary_dir.is_dir():
        return []
    runnable: list[dict[str, Any]] = []
    for path in summary_dir.glob("*.json"):
        value = _read_json(path, None)
        if not isinstance(value, dict) or value.get("jobDismissed"):
            continue
        if value.get("jobStatus") != "queued":
            continue
        if value.get("summary") and not value.get("jobRegenerate") and not value.get("jobRefreshText"):
            continue
        record = {
            "id": f"summary:{value.get('groupId') or value.get('mangaTitle') or path.stem}",
            "kind": "summary",
            "groupId": value.get("groupId"),
            "title": value.get("mangaTitle") or "Ungrouped",
            "status": "queued",
            "provider": value.get("provider"),
            "model": value.get("model"),
            "updatedAt": value.get("jobUpdatedAt") or value.get("generatedAt"),
            "jobStage": value.get("jobStage"),
            "jobProgress": value.get("jobProgress"),
            "jobMessage": value.get("jobMessage"),
            "jobError": value.get("jobError"),
            "jobCurrentPage": value.get("jobCurrentPage"),
            "jobStagePassedCount": value.get("jobStagePassedCount"),
            "jobPageCount": value.get("jobPageCount"),
            "jobPagesWithText": value.get("jobPagesWithText"),
            "jobExtractionRequired": value.get("jobExtractionRequired"),
            "jobRefreshText": bool(value.get("jobRefreshText", False)),
            "jobRegenerate": bool(value.get("jobRegenerate", False)),
            "summaryAvailable": bool(value.get("summary")),
        }
        runnable.append(record)

    runnable.sort(key=lambda item: (str(item.get("updatedAt") or ""), item.get("title") or ""))
    return runnable


def list_summary_jobs(result_root: Path, completed_limit: int = 20) -> list[dict[str, Any]]:
    if not result_root.is_dir():
        return []

    active: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    for path in (result_root / SUMMARY_DIR).glob("*.json"):
        value = _read_json(path, None)
        if not isinstance(value, dict) or value.get("jobDismissed"):
            continue
        status = value.get("jobStatus")
        if status is None and value.get("summary"):
            status = "ready"
        if status not in {"queued", "generating", "paused", "ready", "error"}:
            continue
        record = {
            "id": f"summary:{value.get('groupId') or value.get('mangaTitle') or path.stem}",
            "kind": "summary",
            "groupId": value.get("groupId"),
            "title": value.get("mangaTitle") or "Ungrouped",
            "status": status,
            "provider": value.get("provider"),
            "model": value.get("model"),
            "updatedAt": value.get("jobUpdatedAt") or value.get("generatedAt"),
            "jobStage": value.get("jobStage"),
            "jobProgress": value.get("jobProgress"),
            "jobMessage": value.get("jobMessage"),
            "jobError": value.get("jobError"),
            "jobCurrentPage": value.get("jobCurrentPage"),
            "jobStagePassedCount": value.get("jobStagePassedCount"),
            "jobPageCount": value.get("jobPageCount"),
            "jobPagesWithText": value.get("jobPagesWithText"),
            "jobExtractionRequired": value.get("jobExtractionRequired"),
            "jobRefreshText": bool(value.get("jobRefreshText", False)),
            "jobRegenerate": bool(value.get("jobRegenerate", False)),
            "stale": bool(value.get("stale", False)),
            "summaryAvailable": bool(value.get("summary")),
        }
        (completed if status == "ready" else active).append(record)

    def newest(record: dict[str, Any]) -> str:
        return str(record.get("updatedAt") or "")

    active.sort(key=newest, reverse=True)
    completed.sort(key=newest, reverse=True)
    return active + completed[:max(0, completed_limit)]


def remove_summary(result_root: Path, title: str) -> None:
    summary_path(result_root, title).unlink(missing_ok=True)


def rename_summary(result_root: Path, old_title: str, new_title: str) -> None:
    old_path = summary_path(result_root, old_title)
    if not old_path.is_file():
        return
    new_path = summary_path(result_root, new_title)
    if new_path.is_file() and new_path != old_path:
        old_path.unlink(missing_ok=True)
        return
    value = load_summary(result_root, old_title)
    if value is not None:
        value["mangaTitle"] = _clean_title(new_title)
        save_summary(result_root, new_title, value)
    old_path.unlink(missing_ok=True)


def synopsis_status(
    result_root: Path,
    title: str,
    pages: list[dict[str, Any]] | None = None,
    saved: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_title = _clean_title(title)
    pages = pages if pages is not None else group_pages(result_root, clean_title)
    snapshot = source_snapshot(pages)
    if saved is None:
        saved = load_summary(result_root, clean_title)
    job_status = saved.get("jobStatus") if saved else None
    if job_status is None and saved and saved.get("summary"):
        job_status = "ready"
    return {
        "mangaTitle": clean_title,
        "summary": saved.get("summary") if saved else None,
        "provider": saved.get("provider") if saved else None,
        "model": saved.get("model") if saved else None,
        "language": saved.get("language") if saved else None,
        "generatedAt": saved.get("generatedAt") if saved else None,
        "sourceFingerprint": saved.get("sourceFingerprint") if saved else snapshot["fingerprint"],
        "stale": bool(
            saved
            and saved.get("summary")
            and saved.get("sourceFingerprint")
            and saved.get("sourceFingerprint") != snapshot["fingerprint"]
        ),
        "jobStatus": job_status,
        "jobError": saved.get("jobError") if saved else None,
        "jobUpdatedAt": saved.get("jobUpdatedAt") if saved else None,
        "jobStage": saved.get("jobStage") if saved else None,
        "jobProgress": saved.get("jobProgress") if saved else None,
        "jobMessage": saved.get("jobMessage") if saved else None,
        "jobCurrentPage": saved.get("jobCurrentPage") if saved else None,
        "jobStagePassedCount": saved.get("jobStagePassedCount") if saved else None,
        "jobPageCount": saved.get("jobPageCount") if saved else None,
        "jobPagesWithText": saved.get("jobPagesWithText") if saved else None,
        "jobExtractionRequired": (
            saved.get("jobExtractionRequired")
            if saved and "jobExtractionRequired" in saved
            else bool(snapshot["missing"])
        ),
        "jobDismissed": bool(saved.get("jobDismissed")) if saved else False,
        "pageCount": len(pages),
        "textPageCount": sum(bool(entry["texts"]) for entry in snapshot["entries"]),
        "missingPages": [entry["name"] for entry in snapshot["missing"]],
        "skippedPages": saved.get("skippedPages", []) if saved else [],
        "ocrErrors": saved.get("ocrErrors", {}) if saved else {},
    }


def transcript_pages(snapshot: dict[str, Any]) -> list[str]:
    return [
        f"[Page {entry['name']}]\n" + "\n".join(entry["texts"])
        for entry in snapshot["entries"]
        if entry["texts"]
    ]


def chunk_transcript(pages: list[str], count_tokens, limit: int = 3500) -> list[str]:
    """Keep page boundaries where possible and split only oversized pages."""
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for page in pages:
        page_tokens = count_tokens(page)
        if current and current_tokens + page_tokens > limit:
            chunks.append("\n\n".join(current))
            current, current_tokens = [], 0
        if page_tokens <= limit:
            current.append(page)
            current_tokens += page_tokens
            continue
        lines = page.splitlines()
        piece: list[str] = []
        piece_tokens = 0
        for line in lines:
            line_tokens = count_tokens(line)
            if piece and piece_tokens + line_tokens > limit:
                chunks.append("\n".join(piece))
                piece, piece_tokens = [], 0
            piece.append(line)
            piece_tokens += line_tokens
        if piece:
            chunks.append("\n".join(piece))
    if current:
        chunks.append("\n\n".join(current))
    return chunks


async def generate_synopsis(
    transcript: list[str],
    language: str,
    count_tokens,
    model: str | None = None,
    chunk_limit: int | None = None,
) -> str:
    if openai is None:
        raise RuntimeError("The OpenAI client is not installed")

    provider, resolved_model, api_key, base_url = resolve_summary_model(model)

    if chunk_limit is None or chunk_limit <= 0:
        chunk_limit = get_summary_chunk_limit(provider, resolved_model)

    chunks = chunk_transcript(transcript, count_tokens, limit=chunk_limit)
    if not chunks:
        raise ValueError("No original text was found in this manga")

    client = openai.AsyncOpenAI(
        api_key=api_key or "unused",
        base_url=base_url,
        max_retries=0,
    )
    try:
        concurrency = asyncio.Semaphore(4)

        async def complete_chunks(chunk_list, merge):
            async def complete(chunk):
                async with concurrency:
                    return await _summary_completion(client, language, chunk, merge, resolved_model, provider)

            results = await asyncio.gather(
                *(complete(chunk) for chunk in chunk_list), return_exceptions=True
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            return results

        partials = await complete_chunks(chunks, merge=False)
        while len(partials) > 1:
            merged_chunks = chunk_transcript(partials, count_tokens, limit=chunk_limit)
            partials = await complete_chunks(merged_chunks, merge=True)
        return partials[0].strip()
    finally:
        close = getattr(client, "close", None)
        if close:
            result = close()
            if hasattr(result, "__await__"):
                await result


def resolve_summary_model(model: str | None) -> tuple[str, str, str, str]:
    """Resolve a UI model value to provider, model, key, and OpenAI-compatible URL."""
    from manga_translator.translators import keys

    raw = (model or "").strip()
    provider, separator, requested_model = raw.partition(":")
    if not separator:
        if raw.casefold() in {"deepseek", "groq", "gemini", "google", "google-gemini"}:
            requested_model = ""
        else:
            provider, requested_model = "deepseek", raw
    provider = {
        "google": "gemini",
        "google-gemini": "gemini",
    }.get(provider.casefold(), provider.casefold())

    if provider == "deepseek" and requested_model.casefold() in {"deepseek-chat", "deepseek-reasoner"}:
        requested_model = DEFAULT_SUMMARY_MODEL

    defaults = {
        "deepseek": (DEFAULT_SUMMARY_MODEL, keys.DEEPSEEK_API_KEY, keys.DEEPSEEK_API_BASE),
        "groq": (
            keys.GROQ_MODEL,
            getattr(keys, "GROQ_API_KEY", ""),
            "https://api.groq.com/openai/v1",
        ),
        "gemini": (
            keys.GEMINI_MODEL,
            keys.GEMINI_API_KEY,
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        ),
    }
    if provider not in defaults:
        raise ValueError(f"Unsupported synopsis provider: {provider}")

    default_model, api_key, base_url = defaults[provider]
    return provider, requested_model or default_model, api_key, base_url


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
    request_options = {
        "temperature": 0.5,
        "max_tokens": 8192,
    }
    if provider == "deepseek":
        request_options["extra_body"] = {"thinking": {"type": "disabled"}}
    response = await client.with_options(timeout=90.0).chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    f"{instruction} Write all prose in English, regardless of target language code {language}. "
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
                    "Use natural, professional, reader-friendly wording instead of raw OCR phrasing, profanity, "
                    "slurs, crude expressions, or unnecessarily graphic wording. Preserve the intended meaning, "
                    "plot relevance, consent, threat, and severity without repeating vulgar language verbatim. "
                    "Exclude additional details unrelated to the plot, publication information, credits, "
                    "author or editor notes, afterword notes, advertisements, and front-cover or back-cover "
                    "text. State each event, fact, interpretation, and relationship change once in its most "
                    "appropriate section. Consolidate repeated source material; keep overview sections high-level "
                    "without duplicating detailed story sections; and avoid repeated sentence openings, conclusions, "
                    "section restarts, cyclic recaps, and recurring paragraph patterns. Before responding, silently "
                    "remove duplicated events and paragraphs. End immediately after ENTITIES AND CONCEPTS; never "
                    "restart or continue the synopsis. Use only source-supported facts and do not mention OCR or "
                    "these instructions."
                ),
            },
            {"role": "user", "content": text},
        ],
        **request_options,
    )
    choices = getattr(response, "choices", None) or []
    choice = choices[0] if choices else None
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        finish_reason = getattr(choice, "finish_reason", None)
        detail = f"{provider.title()} returned no text"
        if finish_reason:
            detail += f" (finish reason: {finish_reason})"
        raise RuntimeError(detail)
    return content.strip()
