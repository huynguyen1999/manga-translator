# Layout and text-merge behavior contract

Last verified: 2026-09-27

This document records current observable behavior. Treat the thresholds, ordering, ownership, and failure rules below as compatibility constraints. Intentional changes must update this document and its characterization tests in the same change.

## Pipeline order

1. OCR emits text-line quadrilaterals.
2. Text-line merge forms translation regions from compatible lines.
3. Saved or newly detected speech bubbles are associated with regions.
4. Only remaining free-text regions are coalesced: nested duplicates first, then adjacent paragraph lines.
5. Translation operates on those final region owners.
6. Layout classifies bubble versus free text, assigns source ownership, selects placements, and validates the final raster.

Stage-barrier execution may run bubble detection before text-line merge, but text-line merge must reload that saved bubble geometry before free-text coalescing. Typesetting and reprocess reruns must apply the same association and coalescing rules.

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

- horizontal lines read top-to-bottom;
- vertical columns read right-to-left;
- font size is the minimum member size;
- angle is the mean member angle, snapped to zero within 3 degrees;
- confidence is the text-area-weighted geometric mean;
- foreground/background colors are component averages;
- direction is the majority, with ties resolved by the most elongated member.

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

- both have the same horizontal or vertical direction;
- both belong to the same non-page CV-inferred panel with confidence at least 0.68;
- font-size ratio is at most 1.25;
- angle difference is at most 8 degrees;
- perpendicular gap is at most 1.25 of the smaller font;
- flow-axis overlap is at least 0.65 of the shorter span;
- leading flow edges differ by at most one smaller font.

Threshold comparisons are inclusive. Horizontal paragraphs read top-to-bottom. Vertical Japanese columns read right-to-left. Merged text and raw text use newline separators, and all source geometry/IDs remain attached.

## Free-text ownership and placement

- Free-text ownership zones are disjoint and assigned by nearest source geometry; ties follow input order.
- Shared inpaint damage is assigned to the nearest source and cannot be claimed by multiple regions.
- Protected speech bubbles and pixels outside the panel mask are globally unavailable.
- Other source text is an obstacle; ownership partitions damage but does not require translated glyphs to stay inside the original source box.
- Local placement domains start from source or damage pixels that remain legal, then expand through safe panel space by a bounded geodesic wavefront.
- The default geodesic radius scales from 64 px at a 2048 px page dimension.
- Foreign-text clearance is 20% of effective source font size, clamped to 2–12 px.
- Panel bounds, panel masks, margins, protected bubbles, foreign text, page bounds, and the local domain are hard constraints.
- Empty ownership/domain short-circuits candidate search and suppresses the region for review.

Hard-rejection precedence and diagnostic labels are:

1. `page_bounds`
2. `empty_raster`
3. `local_domain`
4. `panel_bounds` or panel `panel_mask`
5. `protected_bubble`
6. `other_text`
7. page `panel_mask`

## Page selection and final validation

- Candidate choice rejects actual rendered-glyph collisions, not only line-box overlap.
- Horizontal `left` and `right` alignment place the rendered content against the corresponding canvas edge; `center` and `auto` center it.
- For vertical text, `left`, `center`, and `right` align shorter columns to the start, middle, and end of the vertical flow axis respectively.
- Joint Cartesian selection is capped at 4096 combinations; larger searches use the existing collision-safe fallback.
- Candidate scoring preserves source relative order and spacing, page font consistency, centering, and candidate penalty.
- Each source region ID has exactly one render owner.
- Non-preserved translated text below the absolute readability floor is suppressed. Output below 85% of the calibrated/source target is marked for review unless the font size was explicitly configured.
- Final raster validation suppresses output that leaves the page, local domain, panel, or bubble; overlaps a protected bubble or restored source text; fails rasterization; or collides with another rendered region.
- In a bubble/free-text collision, free text loses. For same-mode collisions, the later region loses.
- Suppression is non-fatal: the source is restored and the region is marked for review with a reason.

## Characterization coverage

- `test/test_textline_merge.py`: pair thresholds, rejection reasons, graph grouping/splitting, reading order, and saved geometry cases.
- `test/test_paragraph_coalescing.py`: nested winner selection, direct 90% coverage, barriers, panel evidence, adjacent thresholds, reading order, and source provenance.
- `test/test_free_text_constraints.py`: local-domain geometry and hard-rejection precedence/labels.
- `test/test_layout_core.py`, `test/test_panel_constraints.py`, and `test/test_render_correctness_contract.py`: disjoint ownership, panel assignment, suppression, readability, source restoration, and final raster collisions.
- `test/test_render_mitigations.py`: horizontal left/center/auto/right alignment and vertical flow-axis alignment.
