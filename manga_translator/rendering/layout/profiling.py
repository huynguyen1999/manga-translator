"""Per-layout profiling and content tracing for the layout solver."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ...utils import resolve_render_content
from .regions import prepare_regions as _ensure_region_identities


def _render_text(region: Any) -> str:
    return str(resolve_render_content(region) or "")


def _record_content_trace(regions: Optional[List[Any]], stage: str) -> None:
    _ensure_region_identities(regions)
    for region in regions or []:
        trace = getattr(region, "_content_trace", None)
        if trace is None:
            trace = []
            region._content_trace = trace
        mode = getattr(region, "placement_mode", None)
        mode_val = getattr(mode, "value", str(mode)) if mode is not None else None
        trace.append({
            "stage": stage,
            "region_id": str(region.region_id),
            "source_text": str(getattr(region, "text", "") or ""),
            "source_text_snapshot": str(getattr(region, "source_text_snapshot", "") or ""),
            "source_region_ids": list(getattr(region, "source_region_ids", []) or []),
            "source_lines": [str(item) for item in (getattr(region, "texts", None) or [])],
            "translated_text": str(getattr(region, "translation", "") or ""),
            "translation_policy": getattr(region, "translation_policy", None),
            "source_font_size": getattr(region, "source_font_size", None),
            "font_size": getattr(region, "font_size", None),
            "bubble_id": getattr(region, "bubble_id", None),
            "render_text": _render_text(region),
            "layout_text": getattr(region, "_layout_input_text", None),
            "placement_mode": mode_val,
            "free_text_solver_applied": bool(getattr(region, "_free_text_solver_applied", False)),
            "solver_path": getattr(region, "_solver_path", None),
            "solver_status": getattr(region, "_solver_status", None),
            "layout_lines": [
                line.get("text", "")
                for segment in (getattr(region, "layout_segments", None) or [])
                for line in (segment.get("lines", []) or [])
            ],
            "hyphenation": dict(getattr(region, "_hyphenation_diagnostics", {}) or {}),
            "has_bubble_box": getattr(region, "_bubble_box", None) is not None,
            "has_bubble_points": getattr(region, "_bubble_points", None) is not None,
            "has_free_text_zone": getattr(region, "_free_text_zone", None) is not None,
        })


@dataclass
class SolverProfileStats:
    """Fine-grained, layout-local timing and workload counters."""
    fonts_tested: int = 0
    spacing_tested: int = 0
    y_origins_tested: int = 0
    dp_invocations: int = 0
    dp_states_created: int = 0
    dp_states_pruned: int = 0
    dp_states_deduplicated: int = 0
    raw_wrappings: int = 0
    pre_score_survivors: int = 0
    refined_candidates: int = 0
    glyph_validations: int = 0
    safe_cache_hits: int = 0
    safe_cache_misses: int = 0
    band_cache_hits: int = 0
    band_cache_misses: int = 0
    free_text_crops_rendered: int = 0
    free_text_offsets_tested: int = 0
    free_text_hard_valid_hits: int = 0
    candidate_rasters_created: int = 0
    candidate_raster_cache_hits: int = 0
    free_text_typography_candidates: int = 0
    free_text_candidates_geometry_evaluated: int = 0
    free_text_ideal_attempts: int = 0
    free_text_ideal_successes: int = 0
    free_text_local_search_runs: int = 0
    free_text_local_search_attempts: int = 0
    free_text_local_search_successes: int = 0
    free_text_full_search_fallbacks: int = 0
    free_text_regions: int = 0
    free_text_full_search_runs: int = 0
    overflow_checks: int = 0
    overflow_rasterizations: int = 0
    full_qa_candidates: int = 0
    bubble_row_slot_table_builds: int = 0
    bubble_row_slot_table_cache_hits: int = 0
    joint_layout_plans: int = 0
    joint_layout_candidates: int = 0
    joint_layout_candidate_counts: List[int] = field(default_factory=list)
    joint_layout_cartesian_product: int = 0
    joint_layout_combinations_tested: int = 0
    joint_layout_collisions_rejected: int = 0
    joint_layout_winner_score: Optional[float] = None

    safe_mask_prep_ms: float = 0.0
    width_precompute_ms: float = 0.0
    row_slot_table_ms: float = 0.0
    placement_target_ms: float = 0.0
    zone_profile_ms: float = 0.0
    y_origin_seq_ms: float = 0.0
    dp_search_ms: float = 0.0
    compaction_ms: float = 0.0
    gap_classification_ms: float = 0.0
    centering_ms: float = 0.0
    x_optimization_ms: float = 0.0
    composite_penalty_ms: float = 0.0
    bbox_validation_ms: float = 0.0
    glyph_validation_ms: float = 0.0
    ft_typography_ms: float = 0.0
    ft_crops_rasterize_ms: float = 0.0
    ft_offset_search_ms: float = 0.0
    ft_coverage_ms: float = 0.0
    joint_layout_ms: float = 0.0

    # These caches live only as long as the current layout's profile.
    row_slot_tables: Dict[Tuple[Any, ...], Any] = field(default_factory=dict, repr=False)
    row_slot_max_widths: Dict[Tuple[Any, ...], int] = field(default_factory=dict, repr=False)
    placement_targets: Dict[Tuple[Any, ...], Any] = field(default_factory=dict, repr=False)
    zone_shape_profiles: Dict[Tuple[Any, ...], Any] = field(default_factory=dict, repr=False)
    safe_zone_edge_counts: Dict[int, Tuple[np.ndarray, np.ndarray, np.ndarray]] = field(default_factory=dict, repr=False)

    def clear_ephemeral_caches(self) -> None:
        self.row_slot_tables.clear()
        self.row_slot_max_widths.clear()
        self.placement_targets.clear()
        self.zone_shape_profiles.clear()
        self.safe_zone_edge_counts.clear()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workload": {
                "fonts_tested": self.fonts_tested,
                "spacing_tested": self.spacing_tested,
                "y_origins_tested": self.y_origins_tested,
                "dp_invocations": self.dp_invocations,
                "dp_states_created": self.dp_states_created,
                "dp_states_pruned": self.dp_states_pruned,
                "dp_states_deduplicated": self.dp_states_deduplicated,
                "raw_wrappings": self.raw_wrappings,
                "pre_score_survivors": self.pre_score_survivors,
                "refined_candidates": self.refined_candidates,
                "glyph_validations": self.glyph_validations,
                "safe_cache_hits": self.safe_cache_hits,
                "safe_cache_misses": self.safe_cache_misses,
                "band_cache_hits": self.band_cache_hits,
                "band_cache_misses": self.band_cache_misses,
                "free_text_crops_rendered": self.free_text_crops_rendered,
                "free_text_offsets_tested": self.free_text_offsets_tested,
                "free_text_hard_valid_hits": self.free_text_hard_valid_hits,
                "candidate_rasters_created": self.candidate_rasters_created,
                "candidate_raster_cache_hits": self.candidate_raster_cache_hits,
                "free_text_typography_candidates": self.free_text_typography_candidates,
                "free_text_candidates_geometry_evaluated": self.free_text_candidates_geometry_evaluated,
                "free_text_ideal_attempts": self.free_text_ideal_attempts,
                "free_text_ideal_successes": self.free_text_ideal_successes,
                "free_text_local_search_runs": self.free_text_local_search_runs,
                "free_text_local_search_attempts": self.free_text_local_search_attempts,
                "free_text_local_search_successes": self.free_text_local_search_successes,
                "free_text_full_search_fallbacks": self.free_text_full_search_fallbacks,
                "free_text_regions": self.free_text_regions,
                "free_text_full_search_runs": self.free_text_full_search_runs,
                "overflow_checks": self.overflow_checks,
                "overflow_rasterizations": self.overflow_rasterizations,
                "full_qa_candidates": self.full_qa_candidates,
                "bubble_row_slot_table_builds": self.bubble_row_slot_table_builds,
                "bubble_row_slot_table_cache_hits": self.bubble_row_slot_table_cache_hits,
                "joint_layout_plans": self.joint_layout_plans,
                "joint_layout_candidates": self.joint_layout_candidates,
                "joint_layout_candidate_counts": list(self.joint_layout_candidate_counts),
                "joint_layout_cartesian_product": self.joint_layout_cartesian_product,
                "joint_layout_combinations_tested": self.joint_layout_combinations_tested,
                "joint_layout_collisions_rejected": self.joint_layout_collisions_rejected,
                "joint_layout_winner_score": self.joint_layout_winner_score,
            },
            "timings_ms": {
                "safe_mask_prep": self.safe_mask_prep_ms,
                "width_precompute": self.width_precompute_ms,
                "row_slot_table": self.row_slot_table_ms,
                "placement_target": self.placement_target_ms,
                "zone_profile": self.zone_profile_ms,
                "y_origin_seq": self.y_origin_seq_ms,
                "dp_search": self.dp_search_ms,
                "compaction": self.compaction_ms,
                "gap_classification": self.gap_classification_ms,
                "centering": self.centering_ms,
                "x_optimization": self.x_optimization_ms,
                "composite_penalty": self.composite_penalty_ms,
                "bbox_validation": self.bbox_validation_ms,
                "glyph_validation": self.glyph_validation_ms,
                "ft_typography": self.ft_typography_ms,
                "ft_crops_rasterize": self.ft_crops_rasterize_ms,
                "ft_offset_search": self.ft_offset_search_ms,
                "ft_coverage": self.ft_coverage_ms,
                "joint_layout": self.joint_layout_ms,
            }
        }


_SOLVER_PROFILE: ContextVar[Optional[SolverProfileStats]] = ContextVar("solver_profile", default=None)


def get_solver_profile() -> SolverProfileStats:
    profile = _SOLVER_PROFILE.get()
    if profile is None:
        profile = SolverProfileStats()
        _SOLVER_PROFILE.set(profile)
    return profile


def reset_solver_profile() -> SolverProfileStats:
    profile = SolverProfileStats()
    _SOLVER_PROFILE.set(profile)
    return profile
