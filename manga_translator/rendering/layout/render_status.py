"""Review state for regions that cannot produce translated pixels."""

from ...utils import resolve_render_content


def flag_review(region, reason="render_suppressed"):
    region.review_required = True
    region.review_reason = getattr(region, "review_reason", None) or reason


def frozen_render_state(region, segments):
    content = resolve_render_content(region)
    if content.strip() and not segments and not getattr(region, "_render_suppressed", False):
        region._render_suppressed = True
    if content.strip() and getattr(region, "_render_suppressed", False):
        flag_review(region, "invalid_frozen_layout" if not segments else "render_suppressed")
    state = "FROZEN" if segments and not getattr(region, "_render_suppressed", False) else "UNSOLVED"
    return state, content


def flag_suppressed_review(region):
    if getattr(region, "_render_suppressed", False):
        flag_review(region)
