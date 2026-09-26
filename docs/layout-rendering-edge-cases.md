# Layout and rendering gap audit

Date: 2026-09-26. This audits the nine reported gaps against the current workspace. Focused render/layout verification passed (155 tests, 14 warnings, 2 subtests); `python3 devscripts/check_line_limits.py` and `git diff --check` passed. Saved-page results are listed separately because the local preview process did not reload the current workspace.

## Confirmed gaps

| Gap | Current state | Evidence / limit |
|---|---|---|
| Bubble fallback could leave its bubble or ignore obstacles. | Resolved in the current solver. | Bubble fallback keeps bubble containment and obstacle checks hard; an infeasible fit is suppressed and flagged for review. |
| Bubble-to-bubble collisions could remain drawable. | Resolved. | Final pairwise raster collision validation suppresses one conflicting region and requests review. |
| Collision checks missed raster-only fallbacks and underestimated thick outlines. | Resolved. | Validation reconstructs fallback rasters, uses the effective outline, and compares alpha masks. |
| Rotation happened after validation and could overflow or distort text. | Resolved. | Validation checks the transformed render points and alpha footprint; out-of-page output is rejected instead of coordinate-clamped. |
| Failed free-text placement could suppress translation without review. | Resolved. | No-fit and failed-apply branches set a review reason before restoring source pixels. |
| Invalid frozen segments or failed compositing could silently paint nothing. | Resolved for validated layout/render paths. | Frozen segment bounds, missing/empty raster, failed warp, and empty output are reported and suppressed with review. |
| Font ratios were recorded without a final readability gate. | Partially resolved. | A font below 85% of calibrated source size requests review, except when size is explicit. This is a review threshold, not a guarantee that every rendered glyph is readable at export/display scale. |
| Short vertical destinations could divide by zero. | Resolved. | The vertical renderer guards a destination shorter than one glyph and returns a failed placement. |
| Explicit newlines were discarded and missing glyphs were not checked. | Resolved. | Hard line breaks are preserved; configured font faces are checked before rendering and missing glyphs request review. |

## Saved-page checks

- `1790360962376-525491c9-2048-ENG-sugoi`, region `744ed9b863f34be8b63f1aae6b0321e7`: the unpatched preview classified it as a bubble and suppressed it. A temporary diagnostic replay with the saved bubble masks attached left it as free text and rendered it at font size 39 inside the panel, with no ink overflow. This confirms the free-text fit but does not verify the unpatched live preview path, which is still running stale code.
- `1790397971088-149a82a2-2048-ENG-sugoi`: the saved manifest records 29.3 seconds in layout. A temporary replay completed in about 9 seconds, but still suppressed three bubble placements as `font_policy_infeasible` and free-text region `f45bb050d79c413085b353c365c70b2e` as `no_valid_layout`. The replay output now requests review for these failures, but several translations remain absent. Saved detections are being reattached during reruns and unmatched OCR stays free text in the current code; the running preview service has not reloaded those changes, so the page-level behavior remains unverified and unresolved.
- The remaining `no_valid_layout` occurs among overlapping OCR source regions on the second page. That is a separate placement/ownership constraint from the fixed case where panel height was incorrectly treated as a hard minimum. The solver must keep page, panel, collision, and minimum-font constraints hard; if no safe placement exists, it should retain the translation and request review.

## Remaining limits

A review flag does not make a suppressed translation visible. If no candidate satisfies page/panel bounds, collision avoidance, and the preferred readable font range, the solver currently suppresses that region and leaves a review reason. Font ratios use calibrated OCR geometry and nominal segment sizes; final visual readability can still vary with script, font metrics, raster scaling, and display size. Panel inference and OCR ownership are heuristic for broken borders and heavily overlapping source boxes.
