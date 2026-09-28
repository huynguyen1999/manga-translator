# Layout and text-merge behavior contract

Last verified: 2026-09-28

This document records current observable behavior across OCR grouping, speech bubble fitting, free-text layout, font policies, and final raster composition. Treat the thresholds, ordering, ownership, and failure rules below as compatibility constraints. Intentional changes must update this document and its characterization tests in the same change.

## Pipeline order

1. **OCR text extraction**: OCR emits text-line quadrilaterals, font sizes, directions, and confidences. Numeric annotations undergo 3-tier contextual classification (meaningful numeric preserved without translation payload, noise numeric filtered).
2. **Text-line merge**: Pairs of compatible OCR lines form merged translation regions.
3. **Joint speech bubble and panel detection**: Neural segmentation (e.g., `ShadowB/Manga109-panel-balloon-text-yolov26-segmentation`) or CV inference detects balloon instance masks and frame/panel boundaries (`panel_detections.json`). Text regions are associated with bubbles and assigned panel constraints.
4. **Free-text coalescing**: Only remaining free-text regions are coalesced: nested duplicate winners first, then adjacent paragraph lines within the same panel boundaries.
5. **Translation**: Translation operates on those final region owners (optionally with panel-grouped hierarchical context).
6. **Layout and typesetting**: Classifies bubble versus free text, calculates region font policies and page baselines, assigns disjoint source ownership and damage scopes, searches placements (shape-aware DP line breaking + whole-block centering for bubbles; multi-stage candidate search with early acceptance fast gate for free text), validates compositor footprints, and performs page-level joint collision selection.
7. **Final raster validation & rendering**: Validates warped alpha crops against page bounds, panel masks, protected bubbles, and cross-region collisions. Suppressed regions restore source text/damage and request review.

Stage-barrier batch execution and single-page retries persist and reload saved bubble and panel geometry before text-line merge, coalescing, and layout stages. Typesetting and reprocess reruns apply the same association, coalescing, and layout rules.

## OCR text-line merge

Pair geometry is orientation-aware. Horizontal rows use their horizontal flow axis; vertical columns use their vertical flow axis.

- Directions must match.
- Flow-axis angle difference must be at most 15 degrees.
- Font-size ratio must be at most 2.0.
- A **strong** pair requires perpendicular gap at most 0.75 of the smaller font, perpendicular overlap at most 0.25, flow overlap at least 0.85, font ratio at most 1.35, and angle difference at most 12 degrees.
- A **possible** pair requires perpendicular gap at most 0.75 of the smaller font, perpendicular overlap at most 0.50, and at least one flow edge within 1.5 smaller-font units.
- Threshold comparisons are inclusive.

Accepted pairs form a graph. Connected components are split through their minimum spanning tree only when the worst edge is `possible`, its score is at least 1.0, and it exceeds the tree median by at least 0.65. Strong edges are never selected as split edges. A linked two-line component stays together.

Merged output preserves these rules:

- Horizontal lines read top-to-bottom;
- Vertical columns read right-to-left;
- Font size is the minimum member size;
- Angle is the mean member angle, snapped to zero within 3 degrees;
- Confidence is the text-area-weighted geometric mean;
- Foreground/background colors are component averages;
- Direction is the majority, with ties resolved by the most elongated member.

## Free-text coalescing

Bubble-owned regions, regions with bubble geometry, preserved annotations, and empty-text regions never participate.

### Nested duplicate winner

- Candidates are considered by descending polygon area, with input order breaking equal-area ties.
- A larger retained candidate owns a smaller candidate only when it directly covers at least 90% of the smaller polygon.
- Coverage is direct; overlap chains do not make the first region own the last.
- The winner keeps its text, font, region ID, and geometry.
- Losing IDs are appended to the winner's `source_region_ids` and `group_members`; the winner is the sole source snapshot used for placement.
- Bubble and preserved regions remain independent even when geometrically nested.

### Adjacent paragraph merge

Adjacent free-text regions merge only when all conditions hold:

- Both have the same horizontal or vertical direction;
- Both belong to the same non-page CV-inferred panel with confidence at least 0.68 (or the same detected neural panel);
- Font-size ratio is at most 1.25;
- Angle difference is at most 8 degrees;
- Perpendicular gap is at most 1.25 of the smaller font;
- Flow-axis overlap is at least 0.65 of the shorter span;
- Leading flow edges differ by at most one smaller font.

Threshold comparisons are inclusive. Horizontal paragraphs read top-to-bottom. Vertical Japanese columns read right-to-left. Merged text and raw text use newline separators, and all source geometry/IDs remain attached.

## Bubble shape-aware line breaking and centering

Speech bubble fitting computes optimal text flow within arbitrary balloon geometry:

- **Row-slot geometry**: Computes safe pixel intervals for each row using vertical prefix counts on safe bubble masks (`_build_row_slot_table`), supporting multi-interval `BandSlot` bands per vertical position (min width 8 px). Tables, placement targets, and zone profiles are cached per layout execution.
- **DP word breaking**: Dynamically places words across valid row slots. Candidate paths use backpointer tuples (`_LineBackpointer`) to prune duplicate paths without allocating `PlacedLine` objects until root candidate extraction. Explicit `HARD_LINE_BREAK` markers from pre-wrapping are preserved.
- **Word wrapping & Hyphenation rescue**:
  - Normal wrapping searches within the preferred font range.
  - Long word pressure bottleneck analysis checks word width against max usable row width.
  - Dictionary-based hyphenation rescue (`_hyphenation_variant`) splits bottleneck words at valid hyphenation breakpoints before allowing font size to drop below the consistency floor. Disabled when `no_hyphenation=True`.
  - Diagnostics record `font_policy_status` (`hyphen_rescue`, `satisfactory`, `mild`, `substantial`), introduced hyphen count, and wrapping splits.
- **Whole-block centering**: `_center_layout_block` in `centering.py` translates the entire placed line-block towards the `PlacementTarget` center by searching spiral offset radii (0 to `max_search_radius` step 2) sorted by squared distance, validating safe mask containment before allocating translated `PlacedLine` objects.
- **Greedy X optimization**: `_optimize_x` performs coordinate descent on line centers (`x + width / 2`) balancing slot center attraction, jitter, and curvature.

## Free-text ownership, local domain, and solver

- **Disjoint ownership**: Free-text ownership zones are disjoint and assigned by nearest source geometry (Voronoi/geodesic); ties follow input order.
- **Inpaint damage partitioning**: Shared inpaint damage is assigned to the nearest source and cannot be claimed by multiple regions.
- **Obstacle constraints**: Protected speech bubbles, pixels outside the panel mask/bounds, page margins, and other source text are hard obstacles. Foreign-text clearance is 20% of effective source font size, clamped to 2–12 px.
- **Local placement domain**: Domains expand from source or damage pixels that remain legal via a bounded geodesic wavefront (default 64 px at 2048 px page dimension) through safe panel space.
- **Empty domain suppression**: Empty ownership or domain short-circuits candidate search and suppresses the region for review (`review_required=True`).
- **Typography & wrapping**: Translated region bounds are at least the source height and capped at 1.5×; line spacing is natural; text starts at the top of bounds and centers horizontally on the source region.
- **Candidate footprint & compositor validation**:
  - Candidates are rendered to warped alpha crops (`_warped_alpha_crop`, `filter_renderable_candidates`) using text stroke metrics.
  - **Early acceptance fast gate** (`free_text_fast_gate`): An ideal candidate is accepted immediately prior to exhaustive stage/offset search if its bounding box is within page bounds, displacement distance is within `max(2.0, 0.10 * font_size)`, core damage coverage is >= 90%, and ink overflow is exactly 0.0.
  - Mixed rendered-clearance conflicts rerun only fast candidates, while exhaustive conflicts retain full search.
  - Shadow comparisons (`log_free_text_shadow_comparison`) record centroid delta, damage coverage delta, core coverage delta, footprint area delta, overflow delta, and mask IoU.

## Region font policy and readability constraints

`build_region_font_policy` computes target font sizes, compression floors, and consistency baselines:

- `preferred_size`: Target font size based on calibrated source size + configured offset (or explicit config override).
- `consistency_floor`: Minimum font size before severe penalties or hyphenation triggers (max drop 2 px or 10% below preferred size).
- `mild_compression_floor`: 80% of preferred size.
- `absolute_minimum`: Configured `font_size_minimum` (default 8–12 px).
- `page_font_baseline`: Page-wide median font baseline. Deviations incur quadratic penalties (`_WEIGHT_PAGE_FONT_OVER = 15.0`, `_WEIGHT_PAGE_FONT_UNDER = 60.0`).
- **Readability floor & review flagging**:
  - Non-preserved translated text below the absolute readability floor is suppressed.
  - Output below 85% of calibrated target is marked for review (`review_required=True`) unless the font size was explicitly configured.

## Page selection and final validation

- **Candidate choice**: Rejects actual rendered-glyph collisions, not only line-box bounding boxes.
- **Alignment modes**: Horizontal `left` and `right` align against the canvas edge; `center` and `auto` center the text. Vertical `left`, `center`, and `right` align shorter columns along the start, middle, and end of the vertical flow axis.
- **Joint selection**: Cartesian selection is capped at 4096 combinations; larger searches use the collision-safe fallback.
- **Bubble glyph checks**: Top candidates are checked in final rank order, stopping after `top_k` valid candidates.
- **Hard-rejection precedence and diagnostic labels**:
  1. `page_bounds`
  2. `empty_raster`
  3. `local_domain`
  4. `panel_bounds` or panel `panel_mask`
  5. `protected_bubble`
  6. `other_text`
  7. page `panel_mask`
- **Collision resolution**: In bubble versus free-text collisions, free text loses. For same-mode collisions, the later region loses.
- **Suppression handling**: Suppression is non-fatal; source text and inpaint damage are restored to the canvas, and the region is marked for review with structured diagnostics.

## Characterization coverage

The behavior described above is guarded by the following test suites:

- `test/test_textline_merge.py`: Pair thresholds, rejection reasons, graph grouping/splitting, reading order, and saved geometry cases.
- `test/test_paragraph_coalescing.py`: Nested winner selection, direct 90% coverage, barriers, panel evidence, adjacent thresholds, reading order, and source provenance.
- `test/test_layout_core.py`: Solver mechanics, DP row breaks, word wrapping, hyphenation rescue, multislot row geometry, and font policy calculations.
- `test/test_layout_centering.py`: Spiral centering offsets, safe-mask translation checks, and coordinate descent line centering.
- `test/test_free_text_stages.py` & `test/test_free_text_fast_conflicts.py`: Free-text solver stages, candidate footprints, early fast-gate acceptance, and conflict reruns.
- `test/test_free_text_constraints.py` & `test/test_free_text_contrast.py`: Local-domain geometry, obstacle maps, and contrast scoring.
- `test/test_panel_constraints.py` & `test/test_joint_bubble_panel_detection.py`: Neural panel segmentation, panel bounding box constraints, and joint bubble/panel pipeline propagation.
- `test/test_bubble_validation.py`: Bubble glyph validation and top-k candidate pruning.
- `test/test_render_correctness_contract.py`: Disjoint ownership, panel assignment, suppression, readability, source restoration, and final raster collisions.
- `test/test_render_mitigations.py` & `test/test_render_clearance.py`: Horizontal/vertical alignments, font scaling mitigations, and clearance bounds.
