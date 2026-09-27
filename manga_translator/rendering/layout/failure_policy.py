"""Keep original content visible when no safe translated layout exists."""


def should_restore_source(region, active_bubble_ids=None):
    # Render ownership is resolved by the caller before restoring shared source pixels.
    attempts = getattr(region, "_free_text_attempt_qa", {}).get("placement_attempts", [])
    if attempts and attempts[-1].get("failure_stage") in {"empty_ownership", "source_overlaps_other_regions"}:
        return False
    bubble_id = getattr(region, "bubble_id", None)
    if (
        bubble_id
        and active_bubble_ids
        and bubble_id in active_bubble_ids
        and getattr(region, "_solver_status", None) == "font_policy_infeasible"
    ):
        return False
    return True


def free_text_failure_reason(region):
    attempts = getattr(region, "_free_text_attempt_qa", {}).get("placement_attempts", [])
    if not attempts:
        return "no_valid_layout"
    attempt = attempts[-1]
    if attempt.get("failure_stage"):
        return "no_valid_layout: " + attempt["failure_stage"]
    reasons = attempt.get("rejections", {})
    return "no_valid_layout: " + (", ".join(sorted(reasons, key=reasons.get, reverse=True)[:2]) or "no renderable candidate")
