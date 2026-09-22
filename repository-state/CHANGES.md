# Notable changes

Record new features and large changes here. Keep implementation detail in code, tests, or dedicated documentation.

## 2026-09-23 — Source typography measurements
- Added pre-inpainting OCR-line measurements for relative size, stroke width, ink density, orientation, rotation, and OCR colors; preserved line-level metadata through merge and Pipeline Lab JSON reload. Rendering remains unchanged.

## 2026-09-23 — Fix duplicate PageDetailModal on in-flight / unfinished batch images

- Removed duplicate external modal state syncing and redundant props (`selectedImageForModal`, `onCloseExternalModal`) from `ResultGallery`.
- Made standalone image previews (in-flight batch jobs and queued items) managed exclusively by `App.tsx`, resolving an issue where clicking 'X' required two clicks due to two stacked modals.

## 2026-09-23 — Font type setting in Web Studio and Pipeline Rerun modal

- Added Font Type selection across Web Studio OptionsPanel and Pipeline Rerun dialog (`PipelineRerunDialog`), defaulting to Wild Words (`wildwords`).
- Added standard font options in `front/app/config.ts` (`wildwords`, `anime_ace`, `anime_ace_3`, `comic_shanns`, `arial_unicode`, `noto_sans`, `msgothic`, `msyh`) with persistence across local storage, studio settings, and rerun overrides.
- Added `resolve_font_name_or_path()` in `manga_translator/rendering/__init__.py` to map font keys/names to physical font files under `fonts/` with Wild Words fallback.
- Updated `server/batch_scheduler.py`'s `_config_for` to resolve `renderFont` into `font_path` for consistent rendering across batch processing and reruns.

## 2026-09-23 — Default OCR model is 48px CTC

- Kept the existing `48px_ctc` OCR backend registration and made it the default across core config, server batch/rerun fallbacks, metadata fallback, and pipeline capture.
- Added `48px_ctc` to the rerun OCR override using the shared frontend OCR options, removing the unsupported `offline` value.

## 2026-09-23 — Preset-based pipeline rerun and geometry-first translation remapping

- Added preset-based pipeline rerun subsystem (`PipelineRerunMode`: `full`, `typesetting`, `translation_typesetting`, `reprocess_text`) executed via canonical translator stages without building a duplicate pipeline runner.
- Implemented geometry-first translation remapping (`manga_translator/pipeline/translation_remap.py`) with multi-factor scoring (polygon/bbox IoU, speech bubble affinity, normalized center distance, provenance history, and OCR text similarity) supporting 1:1 matches, 1:N splits with review flags, N:1 bubble-grouped merges, and confidence metrics.
- Added atomic staged commit mechanism (`.rerun/<job_id>`) in `server/pipeline_rerun.py`, preserving existing live outputs on failure and atomically updating `final.jpg`, `text_regions.json`, and `pipeline_manifest.json` on success.
- Extended batch scheduler (`server/batch_scheduler.py`), batch store (`server/batch_store.py`), postgres store (`server/postgres_store.py`), and web API (`POST /api/results/rerun`) to support `pipeline-rerun` batches while preserving `/results/rerender` and `kind: "rerender"` backwards compatibility.
- Built interactive `PipelineRerunDialog` in frontend with preset selector, stage breakdown visualizations, and scoped setting overrides, integrated across ResultGallery and PageDetailModal.
- Added unit and integration tests across backend (`test/test_translation_remap.py`, `test/test_pipeline_rerun.py`, `test/test_rerender_batch.py`).

## 2026-09-23 — Mask generation reliability and speech-bubble residual text recovery

- Implemented `recover_bubble_residual_text()` in `manga_translator/mask_builder.py` to reliably capture Japanese punctuation (such as trailing `!?`), kana fragments, and antialiased gray text inside speech bubbles even when OCR fails or text detection bounding boxes are partial.
- Applied dual global (`gray < 220`) and adaptive Gaussian thresholding on safe bubble interior regions to extract foreground candidate ink without leaking into bubble outlines.
- Built orientation-aware text envelopes (expanding vertically for vertical text, horizontally for horizontal text, proportional to median glyph height) with connected-component feature filtering to reject artwork intrusions and hard-protect speech bubble boundaries.
- Unified mask construction order in `build_inpaint_masks()`: text mask $\to$ detector rescue $\to$ bubble residual $\to$ small gap closing $\to$ glyph-size-relative dilation $\to$ strict invariant zeroing of protected bubble outlines (`final_mask ∩ protected_bubble_edge == ∅`).
- Added QA mask metrics (`known_text_coverage`, `detector_rescue_pixels`, `bubble_residual_pixels`, `protected_edge_violations`, `residual_candidate_pixels_rejected`, `source_text_ink_coverage`).
- Persisted granular mask diagnostic layers (`mask_raw.png`, `text_mask.png`, `detector_rescue_mask.png`, `bubble_residual_mask.png`, `protected_bubble_edge.png`, `inpaint_mask.png`, `mask_final.png`, `mask_sources_overlay.png`) in `devscripts/pipeline_step_runner.py`'s `save_step_data` and Studio `MangaTranslator`.
- Added unit and regression test suite `test/test_mask_builder.py` with golden fixture verifying `!?` punctuation recovery and strict edge protection.

## 2026-09-22 — Canonical layout, mask, and rendering pipeline unification

- Created `manga_translator/mask_builder.py` with `MaskBundle`, `build_inpaint_masks`, and `build_detector_cleanup_mask`, unifying mask generation across Studio `MangaTranslator` (`_prepare_single_context`, `_complete_translation_pipeline`, `prepare`) and CLI runner `pipeline_step_runner.py capture`.
- Unified production and dev rendering through `render_page()` in `manga_translator/rendering/__init__.py`.
- Persisted detected speech bubbles to `bubble_detections.json` in batch preparation and rehydrated during translation/rendering, eliminating redundant YOLO model inferences.
- Losslessly preserved layout fields (`source_font_size`, `calibrated_font_size`, `placement_mode`, `source_region_ids`, `source_regions`, `bubble_safe_shape`) across `pipeline_lab.py` serialization boundaries.
- Set configuration defaults across CLI, Studio backend, and frontend: detection resolution 2048, box threshold 0.5, mask dilation 20, YOLOv8m bubble detection enabled, bubble padding 9, `font_size_minimum = 0` (normalized to 1 in solver), `min_text_length = 1`, `no_text_lang_skip = True`.
- Added end-to-end regression and parity tests in `test/test_pipeline_parity.py`.

## 2026-09-22 — Unified pipeline typography parity & two-stage bubble font calibration

- Implemented geometry-driven two-stage font calibration inside `manga_translator/rendering/layout/solver.py`'s `_build_region_layout_plan()`: estimates target font size directly from bubble interior area and target text word count, preventing Japanese OCR font priors from forcing excessive font sizes and hyphenation in single-pass executions.
- Saved both `source_font_size` and `calibrated_font_size` in `RegionLayout` and `manga_translator/rendering/layout/models.py` / `engine.py`.
- Replaced legacy `_fit_lobe_text()` in `/layout-preview` endpoint with canonical `layout_page()`.
- Standardized default detection resolution to 2048, box threshold to 0.5, mask dilation offset to 20, bubble detection to enabled with `yolov8m` across backend configs (`config.py`, `batch_scheduler.py`) and frontend settings (`App.tsx`, `pipelineLab.ts`).
- Added migration flag `migratedDefaultBubbleDetection` in frontend state to auto-migrate existing browser sessions.

## 2026-09-22 — Enable speech bubble detection by default across Web Studio

- Defaulted `bubbleDetection` to `true` in `front/app/App.tsx`, `front/app/utils/pipelineLab.ts`, and `server/batch_scheduler.py`'s `_config_for()`.
- Added automatic migration `migratedDefaultBubbleDetection` in `front/app/App.tsx` and `TranslationSettings` to migrate existing saved user browser profiles to enable speech bubble detection by default, aligning Web Studio out-of-the-box typesetting with `pipeline_step_runner.py`'s high-quality shape-aware solver.

## 2026-09-22 — Align production pipeline layout and rendering with step runner

- Synchronized `MangaTranslator._run_text_rendering`, `MangaTranslator._prepare_bubble_layout`, `_prepare_single_context`, `_complete_translation_pipeline`, and `server/batch_scheduler.py` with `pipeline_step_runner.py`:
  - Transformed text case before running `layout_page` so solver metrics match the rendered strings.
  - Isolated free-text rendering (`_free_text_solver_applied`) to directly composite prepared `_bubble_box` onto `_bubble_points` instead of passing them into legacy balloon re-extraction.
  - Updated `resize_regions_to_font_size` and `render` in `manga_translator/rendering/__init__.py`, `text_render_eng.py`, and `text_render_pillow_eng.py` to preserve shape-aware solver boxes, font sizes, and exact `_bubble_points` placement.
  - Ensured `ctx.inpaint_mask = ctx.mask.copy()` and `inpaint_mask.png` persistence across `_prepare_single_context`, `_complete_translation_pipeline`, and `_process_rerender_item` for accurate inpaint footprint geometry.
  - Loaded `original_canvas.png` (or `input.png`/`input.jpg`), `inpaint_mask.png`, and `bubble_mask.png` in saved rerenders so `layout_page` and `render_saved` produce pixel-identical results to devscripts fast rendering.

## 2026-09-22 — Production layout boundary and region provenance

- Added `manga_translator/rendering/layout/` with shared placement models, deterministic bubble lobe geometry, page obstacles, free-text ownership targets, and layout integrity diagnostics.
- Routed the main pipeline through `layout_page()` after mask generation and before inpainting; it now executes the same extracted shape-aware solver as the pipeline step runner, with the existing renderer consuming the frozen placements.
- Resolved one active font for both FreeType measurement and final painting, and restored persisted `bubble_safe_shape` geometry on saved rerenders.
- Promoted stable region/source IDs and source geometry snapshots into `TextBlock`, bubble grouping, Pipeline Lab serialization, and step-runner JSON reload.

## 2026-09-22 — Preserve separate OCR regions by default

- Same-bubble OCR regions are now kept as separate translation/layout units by default across core config, bubble association, and dev capture.
- Added explicit `--bubble-grouping` opt-in; existing no-grouping aliases remain supported.
- Compatibility preparation preserves separate region IDs instead of re-merging them during legacy layout setup.

## 2026-09-22 — Natural-rhythm free-text footprint fitting

- Free-text typography now uses one clamped, font-metric-derived baseline rhythm per render; target height cannot create artificial line spacing.
- Candidate ranking measures actual glyph ink width, height, aspect, and area against a source-plus-inpaint target; crop rectangles remain diagnostics only.
- Free-text diagnostics now report source/inpaint/target bounds, ink footprint, baseline advance, visible gap, and spacing consistency while the bubble solver remains unchanged.

## 2026-09-22 — Layout solver Level-2 profiling & hierarchical search optimization

- Added `SolverProfileStats` Level-2 profiling in `devscripts/pipeline_step_runner.py` with fine-grained timing accumulators (`dp_search_ms`, `centering_ms`, `row_slot_table_ms`, `compaction_ms`, `composite_penalty_ms`, `glyph_validation_ms`, `ft_coverage_ms`, `ft_offset_search_ms`, etc.) and workload counters (fonts tested, Y origins tested, DP invocations/states/dedup, raw wrappings, candidate survivors/refinements, glyph validations, cache hits/misses, crop rasters, offset trials).
- Implemented coarse-to-fine hierarchical search in `solve_layout()`: font scale sequence tries coarse steps first and refines near optimal bounds, with coarse-to-fine Y-origin evaluation and candidate bounds checking.
- Optimized `BubbleGeometry` safe pixel and band interval caching with bounded LRU eviction (limit 512) and hit/miss tracking.
- Optimized free-text typography candidate ranking and candidate pool bounding (`_free_text_candidate_pool` capped to top 16) to accelerate joint layout selection and avoid redundant full-image rasterizations.
- Added bounded LRU caching to `_render_line_alpha()` (limit 1024) and optimized obstacle sub-window checking in `_free_text_hard_valid()`.
- Updated `--solver-report` to display structured Level-2 workload counters and top layout timings.

## 2026-09-22 — Non-bubble typesetting lifecycle instrumentation and invariants

- Added comprehensive lifecycle tracing in `devscripts/pipeline_step_runner.py` capturing `stage`, `region_id`, `placement_mode`, `solver_applied`, `layout_text`, `render_text`, and geometry state (`_bubble_box`, `_bubble_points`, `_free_text_zone`).
- Enforced hard invariants in `_validate_render_integrity`: `translation != ""` requires `_layout_input_text == _render_text(region)`, `_free_text_solver_applied == True`, layout targets exist, source geometry $\subseteq$ inpaint mask, final draw text matches layout input text, and detection of silent fallback to OCR/source text.
- Instrumented `RENDER DISPATCH` and low-level `DRAW` operations across both bubble/legacy render dispatchers and free-text direct compositing, with `find_intersecting_draw_operations` for spatial intersection analysis.
- Added resilient fallback candidate generation in `_solve_free_text_region` and expanded font size scaling down to `minimum` so that translated non-bubble text is never silently dropped without layout.

## 2026-09-22 — Non-bubble fixture correctness pass

- Free-text placement can expand beyond the source Voronoi partition into blank page space while still rejecting page, bubble, and foreign-text collisions.
- Joint conflict checks use rasterized glyph overlap instead of suppressing text when only spacing padding intersects; tall targets now gain bounded target-aware wrapping without line stretching.
- Added a regression test for the captured page covering complete translations, unsuppressed rendering, and inpaint-centroid placement.

## 2026-09-22 — Saved layout rerender for translated pages

- Added a rerender batch kind and `/api/results/rerender` endpoint for single-page, selected-page, and manga-level reruns without image uploads.
- Reused persisted translations, text regions, and per-page settings; successful jobs atomically stage the new final while failed pages keep the previous final.
- Added gallery actions and job labeling for rerunning layout/render separately from the full translation retry, plus focused success/failure coverage.

## 2026-09-22 — Non-bubble content/geometry ownership

- Persisted stable region and grouped-source IDs, synchronized editable translations by ID, and saved the exact final inpaint mask for free-text placement.
- Added per-region OCR/translation/layout/render traces plus strict render-integrity validation for solver reports.

## 2026-09-22 — Per-phase fast-render layout timing

- Expanded the fast-render summary so the layout total reports mask preparation, placement classification, bubble solving, free-text solving, fallback placement, and remaining layout time.

## 2026-09-22 — Typography-first free-text placement

- Kept the bubble solver path unchanged while replacing free-text geometry-driven wrapping with atomic-word paragraph candidates based on source typography.
- Free text now freezes line composition before placement, anchors rendered ink to its region-specific erased-mask centroid, searches minimal rigid collision-free offsets, and selects nearby regions jointly.
- Added regression coverage for unsplit words, centered damage placement, source-sized fonts, and existing bubble isolation.

## 2026-09-22 — Damage-aware free-text layout phase in pipeline step runner

- **Strict bubble pipeline freeze**: Ensured `BubbleGeometry` and `solve_layout()` remain 100% untouched and invariant. Bubble geometry flows into the obstacle map as a read-only hard constraint with protected safety halos (`bubble → obstacle map → free-text`).
- **`FreeTextZone` architecture & inpaint damage claiming**: Added `FreeTextZone` dataclass and `build_free_text_ownership_zones()` with `_extract_region_damage_masks()` to claim disjoint inpaint/erased footprints (`ctx.text_mask`, `ctx.mask_raw`, `ctx.mask`), computing normalized distance-transform weight maps ($W(p) \ge 1.0$) and extracting core damage masks.
- **Visual, ink, and block damage coverage metrics**: Implemented `_candidate_cropped_visual_masks()` and `_measure_damage_coverage_crop()` evaluating weighted damage coverage $C_{\text{damage}} = 0.15 C_{\text{ink}} + 0.35 C_{\text{visual}} + 0.50 C_{\text{block}}$ and core coverage against coverable damage.
- **Region-specific damage target**: Added `FreeTextDamageTarget` with an erased-mask centroid, bounds, and source centroid so each free-text region owns its actual inpaint footprint.
- **Typography-first placement**: Generates whole-word paragraph candidates independently of page geometry, freezes the selected lines, then searches minimal rigid offsets around the rendered-ink/damage centroid alignment; coverage and hard collision diagnostics remain available in `--solver-report`.
- **High-performance crop-based mask rasterization & territory bounding**: Pre-calculated damage coverage weight denominators, bounded expansion search territory locally around source bounding boxes, isolated visual mask rasterization and dilation to tight local sub-bounding-box crops ($H_{\text{crop}} \times W_{\text{crop}}$), and cached/reused rasterized base glyph masks across candidate translation offsets $(dx, dy)$, eliminating full-image allocations and speeding up layout execution by $>95\%$.
- **Diagnostics & rich visualization**: Updated `create_free_text_layout_debug()` with an 8-color overlay (Blue source, Magenta damage, Bright Core, Green zone, Red bubble, Yellow halo, Cyan visual mask, White block) and detailed coverage/alternative metrics in `--solver-report`.
- **Unit test suite**: Added tests verifying distance transform core weights, damage-aware placement concealing erased footprints, strict zero bubble/halo intrusion, and verified zero regressions across existing bubble tests.

## 2026-09-22 — Isolated free-text placement in the pipeline step runner

- Added explicit `BUBBLE` / `FREE_TEXT` dispatch after bubble association; the existing bubble solver and renderer remain isolated.
- Added read-only bubble halos, disjoint multi-source ownership zones, source-footprint-first expansion, source-sized font search, hard collision validation, and whole-word wrapping for free text.
- Added placement diagnostics (`--solver-report`) plus `free_text_layout_debug.png`; JSON reload now restores bubble assignments before classification.
- Fixed the shared glyph-cache lifetime so switching fonts cannot reuse glyphs from the previous face.

## 2026-09-22 — Typography diagnostics & font-metrics correctness subsystem in pipeline step runner

- **Font-Metrics Correctness System (`typediag`)**: Added a dedicated `typediag` CLI subcommand to [`devscripts/pipeline_step_runner.py`](file:///Users/ice-h/Source/manga-image-translator/devscripts/pipeline_step_runner.py) implementing a 10-phase diagnostic pipeline to validate typography rendering invariants independently of the bubble layout solver.
- **`FontContext` Dataclass (Phase 3 & 7)**: Introduced an immutable typography state container (`FontContext.resolve()`) capturing font path, face index, pixel size, stroke width, letter spacing, ascender, descender, units per EM, and FreeType line height, separating requested font size from ink height ($H_{\text{ink}} = \text{ascender} - \text{descender}$) and line height ($H_{\text{line}}$).
- **Layout Bypass Diagnostic (`render-test`, Phase 1 & 6)**: Renders fixed reference strings directly through FreeType rasterization with ascender, baseline, and descender guide lines, glyph bounding box overlays, and baseline consistency verification.
- **Font Path & Determinism Trace (`trace`, Phase 2 & 4)**: Traces every character in a target string to verify font face resolution, horizontal advance, bitmap dimensions, bearings, fallback status, and deterministic byte-for-byte reproducibility.
- **Scaling Audit (`scale-audit`, Phase 5)**: Uses AST/source pattern analysis and runtime character evaluations to guarantee no per-character dynamic scaling or height normalization exists.
- **Glyph Cache Completeness Audit (`cache-audit`, Phase 8)**: Audits FreeType `get_char_glyph` LRU cache keys for font-face parameters and documents cache invalidation invariants across font switches.
- **Dataset Font Consistency Validator (`validate`, Phase 9)**: Evaluates all text regions across sample datasets in `devscripts/data` for single-face, single-size, zero-fallback typography compliance.
- **Visual Regression Fixtures (`fixtures`, Phase 10)**: Generates and verifies golden reference typography renders with automated RMSE/pixel-difference regression checks.
- **Render-pass font consistency**: The fast runner now passes the same resolved default/custom font to placement and final dispatch, emits face/size/fallback metrics in `--solver-report`, and keys cached glyphs by the active font-selection tuple while retaining same-font cache entries.

## 2026-09-22 — Layout solver performance & memory optimization in pipeline step runner

- **Precomputed reusable row-slot table**: Added `_build_row_slot_table()` to compute valid horizontal `BandSlot` intervals once per font size $S$, avoiding repeated `band_intervals()` numpy slice checks across $y$-origin trials.
- **Staged candidate generation & pruning**: Decoupled DP candidate generation from heavy vertical compaction, gap classification, centering, and X-refinement by performing lightweight pre-scoring across line-count diversity buckets before expensive geometric operations.
- **Bounded cache lifetimes**: Added `clear_ephemeral_caches()` on `BubbleGeometry` to flush safe masks and band slot caches between font sizes while preserving the static Euclidean Distance Transform (`dist`).
- **Translation feasibility bounding**: Optimized `_center_layout_block()` with precomputed safe bounding box translation feasibility bounds ($[\min_{dx}, \max_{dx}] \times [\min_{dy}, \max_{dy}]$) and coarse-to-fine step progression.
- **Early branch pruning & code cleanup**: Added word-width feasibility bounding and removed duplicate/unreachable return blocks in `solve_layout()`.

## 2026-09-22 — Continuous paragraph rhythm invariant and bidirectional vertical compaction pass

- **Structural continuous rhythm invariant**: Replaced soft blank-row penalty tuning with an ideal vertical lattice $y_i^* = y_0 + i H$ where internal row skipping requires a concrete geometric obstruction (safe cross section $< W_{\min}$, mask disconnection, or crossing a detected `LobeGraph` neck).
- **Bidirectional vertical compaction pass**: Added `_compact_vertical_rhythm()` performing continuous/pixel Y local search ($\pm H$) with upward "gravity" and downward spring-chain relaxation ($E = \sum (y_{i+1} - y_i - H)^2$) validated against `BubbleGeometry.band_intervals()`.
- **Explicit gap reason classification**: Implemented `_classify_adjacent_gaps()` classifying adjacent line gaps into `NORMAL`, `LOCAL_GEOMETRY_ADJUSTMENT`, `LOBE_NECK`, `DISCONNECTED_SAFE_REGION`, `SOURCE_PARAGRAPH_BREAK`, and `UNEXPLAINED`.
- **Strict candidate validation & multi-stage fallback**: Invalidate any candidate containing an internal gap $> 1.4H$ with reason `UNEXPLAINED`, falling back to zero-gap alternatives, alternate line breaks, or font size reduction ($S-1$).
- **Pipeline reordering**: Vertical compaction runs prior to horizontal line refinement (`_optimize_x()`) and whole-block recentering (`_center_layout_block()`).
- **Diagnostics & solver reports**: Added gap breakdown (`gap_details`, `gap_reasons`, `max_gap_h`) to `LayoutCandidate.qa` and solver reports with visual indicators (`✓` / `❌`).

## 2026-09-22 — Keep shape-aware paragraphs vertically continuous

- Removed optional blank-row selection from the paragraph DP; usable rows must carry text after placement starts, while unavailable bands remain geometry-forced traversals.
- Added a nonlinear paragraph-spring penalty based on actual line-to-line distance and regressions for continuous rows and irregular lower lobes.

## 2026-09-22 — K-best paragraph shaping and zone-conforming layout solver in pipeline step runner

- **ZoneShapeProfile**: Added `ZoneShapeProfile` and `compute_zone_shape_profile()` calculating safe zone width profiles $W(y)$, zone aspect ratio $AR_{\text{zone}}$, usable area, line capacity $N_{\max} \approx H/L$, and capacity center.
- **K-best DP word breaking by line count**: Refactored `_dp_word_break_rows()` to produce diverse candidate wrappings partitioned into structural line-count buckets (e.g. 3, 4, 5, 6 lines) rather than discarding valid alternative paragraph shapes early.
- **Vertical utilization scoring**: Added $R_v = H_{\text{text}} / H_{\text{usable}}$ evaluation in `_composite_penalty()` with quadratic penalties outside the optimal $0.55 \le R_v \le 0.80$ band.
- **Aspect ratio & silhouette matching**: Added $P_{\text{aspect}} = \lambda_{\text{aspect}} |\log(AR_{\text{text}}/AR_{\text{zone}})|^2$ and silhouette width envelope matching against $W(y)$, naturally selecting taller paragraphs for tall bubbles and wider paragraphs for wide bubbles without hardcoded rules.
- **Contextual orphan & punctuation attachment**: Enhanced `normalize_words()` to attach floating punctuation (e.g. `?!`, `...`) to preceding word tokens and updated `_line_break_cost()` to permit single-word lines (e.g. `A`) when conforming to narrow bubble sections.
- **Render performance & multi-region candidate caching**: Cached full-frame candidate glyph masks, bounding boxes, and dilated reserved areas once per candidate before joint layout combinatorial search in `_choose_joint_layout()`, and truncated `solve_layout()` results to `valid_candidates[:top_k]`. Reduced batch rendering latency by ~10x.
- **Unpickler & single-stream concurrency stability**: Added module-level class resolution for `__main__` and `devscripts.pipeline_step_runner` in `CompatUnpickler`, and set default rendering batch worker concurrency to 1 for Apple Silicon MPS / GPU thread stability.

## 2026-09-22 — Object-centric safe-zone text placement architecture in pipeline step runner

- **PlacementTarget & text-capacity centers**: Introduced `PlacementTarget` and `compute_placement_target()` calculating capacity-weighted vertical centers $C_y = \frac{\sum y W(y)}{\sum W(y)}$ and horizontal centers $C_x = \frac{\sum x H(x)}{\sum H(x)}$ across safe eroded masks, preventing irregular bubble tails or pinched necks from distorting optimal text placement.
- **Decoupled Y-sampling**: Replaced mid-centroid first-line Y binding in `_y_origin_sequence()` with uniform candidate starting range sampling so that the DP focuses purely on word distribution and paragraph shape.
- **Whole-block recentering with lattice search**: Added `_center_layout_block()` to translate finished multi-line text blocks towards their `PlacementTarget` centers with local spiral/lattice search and safe-mask validity checking.
- **Decoupled block vs line placement**: Refined `_optimize_x()` to align line centers relative to the established global block center without pulling the entire paragraph away.
- **Explicit whitespace balance scoring**: Added $P_{\text{balance}} = \lambda_v V^2 + \lambda_h H^2$ to `_composite_penalty()` measuring free-space capacity above, below, left, and right in the safe mask.
- **Context-aware source weighting**: Scaled down source centroid attraction for single-region bubbles to prioritize visual centering, while preserving strong source geometry guidance for multi-region partitioned zones.
- **Centering diagnostics & visual QA**: Added `block_center_dx`, `block_center_dy`, `top_free_ratio`, `bottom_free_ratio`, `vertical_balance`, and `horizontal_balance` to QA metrics and `--solver-report`/`--solver-diagnose`, and updated `create_placement_zones_visualization()` to draw target center markers (+), rendered block centroids (×), and block bounding boxes.

## 2026-09-22 — Sugoi model translation support in pipeline step runner

- Added `--sugoi` (alias `--use-sugoi`) and `--translator` (`--target-lang` / `-l`) CLI options to `devscripts/pipeline_step_runner.py capture` to allow translating manga pages directly during capture (e.g. using the offline Sugoi V4.0 model for Japanese to English).
- Extended `execute_capture` and `_capture_single_image` to pre-warm the translator model and run text translation prior to bubble geometry layout preparation.

## 2026-09-22 — Intra-region visual coherence and spatial continuity in single-region layout solver
 
- **DP state spatial continuity**: Extended `_dp_word_break_rows` state to memoize `(wi, ri, prev_slot_left, prev_slot_right, started)` and track the preceding placed line center and slot bounds.
- **Inter-line transition penalties**: Introduced `_transition_cost` scoring normalized line-center displacement $d_x = |C_i - C_{i-1}|/S$ (quadratic penalty), horizontal slot overlap ratio $r = |I_{prev} \cap I_{curr}| / \min(|I_{prev}|, |I_{curr}|)$, and large branch-switch penalties.
- **Intelligent context-aware row skipping**: Differentiated pre-placement (free), intra-block (penalized only when the skipped row could have fit the next word via `_row_can_fit_word`), and post-placement (free).
- **Candidate compactness & global X coherence**: Added vertical block gap ratio penalty $G = (H_{span} - H_{text})/H_{span}$ and normalized excess center variance penalty $\sigma_x^2$ to `_composite_penalty()`.
- **Source profile pattern prior**: Strengthened source similarity scoring with relative line-center shape pattern matching against `OriginalLayoutProfile.lines`.
- **Diagnostics & visualization**: Added inter-line connector vectors in `create_placement_zones_visualization()` and per-transition metric logging in `--solver-report`/`--solver-diagnose`.
 
## 2026-09-22 — Joint placement for separate regions in one speech bubble

- Kept ungrouped OCR regions separate while laying them out jointly inside their shared bubble, using source-derived preferred territories, top-K candidates, reservation-mask spacing, and relative-position/gap scoring.
- Added a regression test covering vertically separated source regions.

## 2026-09-22 — Option to disable bubble region grouping in pipeline step runner

- Added `--no-bubble-grouping` (aliases `--no-group-regions-by-bubbles`, `--disable-bubble-grouping`) to `devscripts/pipeline_step_runner.py capture` to allow keeping text regions separate instead of merging all OCR regions inside the same speech bubble into one multi-line unit.
- Added `group_regions: bool = True` to `BubbleDetectionConfig` in `manga_translator/config.py` and `group: bool = True` to `group_regions_by_bubbles()` in `manga_translator/rendering/bubble_layout.py`.
- When disabled, individual text regions maintain their separate text and bounding lines while each still associating with its matching speech bubble detection mask.

## 2026-09-22 — Bubble lobe graphs in the pipeline step runner

- Added mask normalization, distance-transform peak detection, duplicate/noise suppression, watershed lobe partitioning, false-lobe merging, neck analysis, and `LobeGraph` serialization to `bubbles.json`.
- Extended `bubbles_overlay.png` with lobe boundaries, centers, and neck markers; added regression coverage for connected two-lobe masks.

## 2026-09-22 — Pipeline runner NumPy unpickle compatibility, solver font search & performance optimizations

- **Full solver font size exploration**: Passed `font_size_min=minimum` to `solve_layout()`, preventing false `requires_compression` failures when text requires modest scaling down from target font size, ensuring 100% of speech bubble regions across all sample datasets are placed and rendered with legible lettering.
- **NumPy 2.x / 1.x pickle compatibility**: Added `CompatUnpickler` to dynamically bridge serialized NumPy module differences (`numpy._core` <-> `numpy.core`), eliminating `No module named 'numpy._core.numeric'` errors when loading captured `step_data.pkl` datasets across NumPy versions.
- **Reentrant rendering lock**: Changed `_RENDER_LOCK` in `manga_translator/rendering/__init__.py` to `threading.RLock()`, preventing re-entrant deadlocks across nested layout preparation and render dispatch.
- **Shape-aware layout performance optimizations**: Cropped `BubbleGeometry` distance transforms to active mask bounding boxes, vectorized `band_intervals` using `np.all(safe[y1:y2], axis=0)` and array diffs, and replaced scalar X-coordinate grid searches with analytical quadratic minimization in `_optimize_x`, accelerating text fitting from ~3.5s per region down to sub-100ms.

## 2026-09-22 — Batch deletion for multiple selected images and multiple manga groups

- **Multi-image deletion in gallery**: Added bulk deletion support when selecting multiple images/pages across both single manga views and accordion/row views in `ResultGallery`. Users can delete all selected pages at once with a confirmation modal (`confirmDeleteSelectedPages`) and atomic gallery state/server cleanup via `deleteFinishedImages`.
- **Multi-manga deletion in gallery**: Added bulk delete action to the floating selection dock when multiple manga cards are selected. Prompts with a confirmation modal showing the selected manga titles (`confirmDeleteSelectedMangas`) and removes the manga groups in batch via `deleteMangaGroups`.
- **Multi-file removal in upload staging**: Added "Remove Selected" action button in `ImageHandlingArea` to remove multiple selected staging files before translation without needing to clear the entire form or remove files individually.


- **Wild Words font resolution**: Added `get_default_eng_font()` in `manga_translator/rendering/__init__.py` to prioritize Wild Words variants (`Wild Words.ttf`, `CC Wild Words Roman.ttf`, `CCWildWords-Roman.ttf`, `wild_words.ttf`, etc.) located in `fonts/` as the default English lettering font.
- **Graceful fallback**: When Wild Words font files are not installed in `fonts/`, rendering automatically falls back to bundled comic/manga fonts (`anime_ace.ttf` / `comic shanns 2.ttf`).
- **Unified defaults**: Updated `dispatch_eng_render`, `server/main.py`, and `devscripts/pipeline_step_runner.py` to use `get_default_eng_font()` when no specific font path is provided.

- **X-centering coordinate fix**: `_optimize_x`/`_x_cost` now optimize *line centers* (x + width/2) against slot centers instead of comparing left edges with centers, which visibly misaligned lines of different widths.
- **OCR hyphen-split normalization (Phase 2)**: New `normalize_words()` stage before layout repairs OCR line-break hyphen artifacts (`GOT- TEN` → `GOTTEN`, `SCRATCH- ES.` → `SCRATCHES.`) using `/usr/share/dict` dictionary evidence plus a compound-prefix blocklist (`TWENTY-ONE`, `SELF-DEFENSE`, `X-RAY` stay intact); split proper names (`HA- RUTO`) join when both fragments are non-words. Words are then atomic for the DP — hyphenation is fallback-only, never introduced.
- **Row + interval topology (Phase 6)**: Replaced the flattened `BandSlot` list with `RowGeometry` (one row per line-height Y, each holding its disjoint safe intervals); the DP can now skip rows and understands multi-lobe same-Y regions instead of treating successive intervals as successive textual lines.
- **Linguistic line-break penalties (Phase 5)**: DP line costs penalize orphan short words, single-function-word lines, and breaks before articles/prepositions/auxiliaries/pronouns/conjunctions, keeping pairs like `LET ME` / `MAY HAVE` / `LOOK AT` together when geometry allows. Slot-fill pressure reduced from weight 10 to 2 so geometry gates ("allowed here?") instead of dictating contour-following fills.
- **Composite objective (Phase 7–9)**: `(-font_size, aesthetic)` replaced by a single penalty: font deviation from target (smaller fonts must earn their win via a monotone pruning bound), glyph-ink centroid placement, occupancy band (whitespace is a design feature, not a failure), silhouette smoothness (second derivative of line widths), fill variance/jitter/raggedness, and hyphen/orphan terms. Hard validity remains in the glyph-pixel validators.
- **Original layout profile (Phase 3/7/8)**: `build_original_layout_profile()` derives ground truth from captured OCR line quads (line count, per-line centers/widths, ink-weighted text centroid via rasterization, block bbox, occupancy) — no extra model — and the score includes a source-similarity term (centroid, line count, font size, bbox). Centering targets the original typesetter's ink centroid when available, else the mask centroid.
- **Visual QA reporting (Phase 12)**: `--solver-diagnose` and `--solver-report` now print penalty, occupancy, center error vs original, source similarity, original line count/font, and per-region `status`.
- **Translation compression fallback marker (Phase 13)**: Regions the solver cannot place at minimum font size are flagged `requires_compression` (`region._solver_status`) so the translation stage can shorten the text and retry instead of shrinking lettering further.

## 2026-09-21 — Robust image format decoding and client-side retry for manga uploads

- **Expanded image format decoding**: Configured Pillow with `Image.MAX_IMAGE_PIXELS = None` (preventing `DecompressionBombError` on large manga scans and long-strip webtoons) and `ImageFile.LOAD_TRUNCATED_IMAGES = True`, added EXIF transposition, alpha transparency detection, and expanded `SUPPORTED_IMPORT_FORMATS` in `server/main.py` to support TIFF, GIF, AVIF, MPO, JPEG2000, TGA, PPM, etc.
- **Client-side upload batch retrying**: Updated `retryTranslationItem` and `handleStudioMangaUpload` in `front/app/App.tsx` to handle retrying `manga-upload` batches in the browser without attempting invalid server batch lookup calls.

## 2026-09-21 — English page pipeline capture and fast placement/fitting test dev scripts

- **English page capture utility (`capture`)**: Added `devscripts/pipeline_step_runner.py` and `devscripts/capture_step_data.py` to run text detection, OCR, speech bubble segmentation, text-line merge, mask refinement, and inpainting on English manga pages (without translation) and persist step data (`input.png`, `img_rgb.png`, `inpainted.png`, `mask_final.png`, `regions.json`, `step_data.pkl`, `config.json`, `meta.json`) under `devscripts/data/<sample_name>/`.
- **Speech bubble ink cleanup & edge preservation**: Enhanced `prepare_bubble_masks()` in `bubble_layout.py` with configurable padding erosion ($9\text{px}$ default) to strictly protect the bubble outline border from inpainting, combined with ink dilation and zeroing of boundary edge bands across `text_mask` and `mask_final` so that interior text is 100% erased while the speech bubble border line remains crisp and untouched.
- **Bubble padding CLI argument**: Added `--bubble-padding` flag (default $9\text{px}$) and `BubbleDetectionConfig.padding` to configure the distance from bubble contours to inpainting regions.
- **High-accuracy OCR & MPS execution**: Switched default capture OCR to the high-accuracy `48px` Transformer model (with optional `--ocr` switch) and sequenced bubble layout preparation before mask refinement with single-stream MPS execution.
- **Fast placement & text fit utility (`render`)**: Added `devscripts/fast_render.py` and `render` mode to load captured step data and execute only speech bubble layout, medial-axis/SDF calculations, text wrapping/fitting, canvas composition, and font rendering in milliseconds without re-running upstream detection/OCR/inpainting models.
- **Concurrent multi-image batching**: Both commands support processing multiple image files, globs, or sample directories concurrently with timing breakdown summaries.
- **Speech bubble detection persistence & visualization**: Added dedicated `bubbles.json` / `bubble_detections.json` export with bounding boxes, area, and contour polygons, as well as `bubbles_overlay.png` visual inspection overlays highlighting detected bubbles directly on the source pages.
- **MPS and GPU hardware acceleration default**: Enabled automatic device detection prioritizing Apple Silicon MPS (`mps`) on macOS and CUDA on Linux/Windows by default across `capture` and `render` commands (with `--cpu` / `--no-gpu` override available), accelerating text detection, OCR, bubble segmentation, and inpainting 3x over CPU.
- **Unit test suite**: Added `test/test_pipeline_step_runner.py` covering region serialization/deserialization, step data saving/loading, JSON editing sync, and fast placement/render execution.

## 2026-09-21 — Per-page retry on missing draft region IDs; fix spurious `substantial_editor_rewrite`

- **Retry on missing IDs**: When the draft LLM omits region IDs in its JSON response, `localize_story` now retries the failed chunk one page at a time instead of immediately falling back to raw Japanese passthrough for the entire batch.
- **`draft_failed` sentinel**: Any region that ultimately falls back to Japanese passthrough is marked `draft_failed=True` so downstream stages can detect the degraded state.
- **Gated similarity check**: `edit_story` skips the `substantial_editor_rewrite` SequenceMatcher check when `draft_failed` is set, preventing spurious flags from cross-language draft/final comparisons.



- **Native LLM JSON mode**: Enabled `response_format={"type": "json_object"}` (OpenAI, DeepSeek, Groq, OpenRouter, Custom OpenAI) and `response_mime_type="application/json"` (Gemini) when executing professional translation tasks via `_professional_json_mode`. Automatically catches and recovers if an endpoint rejects the parameter.
- **Uncapped token limits**: Uncapped `max_tokens` / `max_completion_tokens` during professional translation (previously halved to `_MAX_TOKENS // 2`), preventing reasoning/thinking models from exhausting tokens before emitting JSON.
- **Resilient JSON parsing**: Upgraded `_json_object` in `professional_translation.py` to handle unclosed `<think>` tags, unclosed markdown code fences, and to trigger `_recover_partial_objects` even when trailing closing braces are cut off.
- **Provider fallback on JSON failure**: If the primary AI translator returns unparseable JSON on its retry attempts, `_json_request` falls back to DeepSeek to salvage the translation instead of aborting directly to original Japanese text.

## 2026-09-21 — Professional-mode memory optimization

- **Deferred image loading**: professional-mode `_process_translation_group` no longer decodes every page image before translation. Each page's PIL image is loaded in `_render_item` immediately before rendering and released immediately after, capping image memory at ≤ `_RENDER_SEMAPHORE_SIZE` decoded pages at any time regardless of job size (`server/batch_scheduler.py`).
- **Semaphore-bounded render concurrency**: professional-mode renders are now bounded by `_RENDER_SEMAPHORE_SIZE = 2` (module constant). Fast mode is unchanged. Prevents multiple render canvases, mask arrays, and large PIL images from existing simultaneously (`server/batch_scheduler.py`).
- **Bounded rolling translation context**: `previous` strings in `localize_story` and `edit_story` are truncated to 8 000 characters after each chunk, matching the slice already applied when building the API prompt. API behavior is unchanged (`manga_translator/professional_translation.py`).
- **Reduced analysis temporary strings**: In the windowed analysis path, `page_blocks`, `page_text`, each per-window prompt, and `partials` are set to `None` immediately after use — before the next API call — so only the current window and its results exist in memory at once (`manga_translator/professional_translation.py`).
- **New helpers**: `manga_translator/mem_probe.py` (tracemalloc + optional psutil RSS probe for tests/devscripts), `devscripts/mem_probe_run.py` (standalone synthetic measurement script), `test/test_memory_optimization.py` (mocked invariant tests: no pre-load, one Image.open per page, peak concurrency ≤ semaphore, previous length bounded).

## 2026-09-21 — Tabbed resizable sidebar in PageDetailModal

- Replaced the flat scrolling sidebar in `PageDetailModal` with a four-tab layout: **Pipeline Timing**, **Professional Localization**, **Story Analysis**, and **Step Settings**.
- Pipeline Timing tab shows started-at / ended-at / total-duration overview plus per-stage breakdown. Step Settings tab now includes the Translation Details card (engine, model, languages) at the top.
- Sidebar defaults to 480 px (1.5× the previous 320 px) and is drag-resizable from its left edge (range 280–700 px) via mouse event handlers.

## 2026-09-21 — Speech bubble text space utilization and font sizing optimization

- Optimized speech bubble text layout to comfortably fill ~55–65% of available bubble interior with word-length-aware adaptive font sizing.
- Expanded layout candidate generation and scoring in `text_render.py` to reward height fill and area utilization, eliminating 1-word column collapses in tall vertical speech bubbles.
- Aligned candidate text line height calculations with FreeType ascender/descender metrics ($1.15 \times \text{size}$).
- Composited prepared `_bubble_box` directly in `render()` to maintain properly centered layout coordinates.
- Added space utilization and multi-line wrapping unit tests in `test/test_bubble_layout.py`.

## 2026-09-21 — 2D free-space mask typesetting layout engine

- Implemented arbitrary 2D safe-region mask layout avoiding hardcoded lobe heuristics (`2 lobes`, `3 lobes`, `left/right`, `circle vs rectangle`).
- Integrated Euclidean distance transforms (SDF) for medial axis calculation, center trajectories $C(y)$, and line slot boundary clearance.
- Added 2D spatial mask moments ($C_M, \Sigma_M$) and text block inertia tensors ($C_T, \Sigma_T$) for centroid and aspect ratio alignment.
- Implemented DP line breaking and candidate layout scoring combining raggedness, fill ratio, moment alignment, linguistic break penalties, lobe capacity balance, and SDF boundary clearance.
- Refined plateau and collinear lobe suppression to preserve continuous stadium/pill bubbles while cleanly isolating stepped arms and multi-pinched lobes.
- Added comprehensive unit test coverage in `test/test_bubble_layout.py` verifying moment calculations, SDF clearance validation, and multi-lobe continuous paragraph flow.

## 2026-09-21 — Render review-flagged text regions with persistent review status

- Rendered translated dialogue onto canvas across backend rendering and web studio editor/preview even when flagged for review (e.g. layout/overflow fitting fallback, multi-lobe fallback, filter triggers, low confidence).
- Preserved `review_required: true`, review reasons, and "Needs editing" status badges in editor and metadata so flagged regions remain flagged for subsequent review and approval.

## 2026-09-21 — Capacity-balanced connected-bubble layout

- Balanced word allocation by measured safe line-slot capacity and centered each segment within its own stepped or oval lobe at a shared fixed font size.
- Validated rendered glyphs against per-group safe shapes, preserved originals when no safe fit exists, and persisted those shapes for matching editor reflow with conservative legacy review.

## 2026-09-21 — Lettering case options (UPPERCASE, lowercase, original)

- Added configurable lettering case transformations across all pipeline entry points: CLI flags (`--uppercase`, `--lowercase`, `--letter-case upper|lower|none|original`), Web Studio `OptionsPanel` ("Lettering Case" dropdown), Pipeline Lab controls, and server `batch_scheduler.py`.
- Added `transform_text_case(text: str)` helper to `RenderConfig` supporting string alias normalization (`letter_case`, `letterCase`, `text_case` -> `upper`, `lower`, `none`) and integrated casing into standard translation, professional localization, and typesetting renderers.
- Removed hardcoded `.upper()` in `seg_eng()` ([`manga_translator/rendering/text_render_eng.py`](file:///Users/ice-h/Source/manga-image-translator/manga_translator/rendering/text_render_eng.py)) to allow `manga2eng` and `manga2eng_pillow` to preserve mixed case and lowercase formatting.
- Added frontend state persistence via `localStorage`, step settings resolution in `PageDetailModal`, and unit test coverage across config validation, rendering case preservation, and frontend UI components.

## 2026-09-21 — Direct image loading & manga detail drag edge auto-scrolling

- Updated studio file ingest in [`front/app/App.tsx`](file:///Users/ice-h/Source/manga-image-translator/front/app/App.tsx) to load loose image files directly into the active cards list without displaying the intermediate "Arrange original files before loading" staging view.
- Added viewport edge auto-scrolling during manga page reordering drag in [`front/app/components/ResultGallery.tsx`](file:///Users/ice-h/Source/manga-image-translator/front/app/components/ResultGallery.tsx) with proximity-accelerated `requestAnimationFrame` vertical scrolling when dragging near top/bottom edges.
- Added unit tests in [`front/app/components/ResultGallery.test.ts`](file:///Users/ice-h/Source/manga-image-translator/front/app/components/ResultGallery.test.ts) verifying drag auto-scroll speed calculations.

## 2026-09-21 — Original regions confidence display & OCR minimum confidence setting

- Updated "Original regions" overlay in [`front/app/components/PreviewImage.tsx`](file:///Users/ice-h/Source/manga-image-translator/front/app/components/PreviewImage.tsx) to render clean, color-coded polygon outlines by confidence tier (emerald $\ge 85\%$, amber $65\%-84\%$, rose $< 65\%$) with interactive hover highlights and floating tooltips, eliminating clutter from overlapping badges on vertical textlines.
- Added `confidence` property to `Quadrilateral` and `TextBlock` in backend utils and serialized confidence in pipeline lab endpoints.
- Added `OCR Min Confidence` setting (`customOcrProb` / `ocrMinConfidence`) in the frontend options panel under Text Detection & OCR, persisting with user settings and passing through batch translations, Pipeline Lab, and manifest step metadata.
- Configured backend OCR pipeline and batch scheduler to map `customOcrProb` to `OcrConfig.prob` to filter OCR recognition candidates meeting the required confidence threshold.
- Added frontend and backend unit tests verifying confidence parsing, confidence tier styles, UI tooltips, and OCR threshold settings mapping.

## 2026-09-21 — Raw detector regions displayed in page details

- Changed the Original regions overlay to load persisted `detection.json` independently from OCR-derived editable bubbles.
- OCR-skipped detector polygons now remain visible and counted without entering translation or editor data.

## 2026-09-21 — Reactive translation batch result image synchronization
- Implemented automatic batch detail rehydration in `subscribeServerBatches` SSE handler when loaded batches update or finish translating, keeping item statuses, folders, and `resultUrl` synchronized live.
- Added queued re-fetch handling to `loadTranslationBatchDetails` in [`front/app/App.tsx`](file:///Users/ice-h/Source/manga-image-translator/front/app/App.tsx) to prevent dropped completion states during in-flight network requests.
- Enhanced `BatchCard` in [`front/app/components/TranslatingSection.tsx`](file:///Users/ice-h/Source/manga-image-translator/front/app/components/TranslatingSection.tsx) to automatically re-fetch batch details upon expanding when items are incomplete.
- Added file name sanitization and pass-through in `handleOpenLightbox` and `ItemRow` so completed batch images open immediately into the translated result view.
- Added automated unit test coverage in [`front/app/utils/batchSync.test.ts`](file:///Users/ice-h/Source/manga-image-translator/front/app/utils/batchSync.test.ts).

## 2026-09-21 — Comprehensive pipeline step settings in translation details
- Added full pipeline step configuration persistence (`ocr`, `useMocrMerge`, `renderer`, `renderAlignment`, `renderFont`, `bubbleModel`, `bubbleConfidence`, `bubbleMaskThreshold`, `inpaintingPrecision`, `upscaler`, `upscaleRatio`, `revertUpscaling`) to backend `meta.json` in [`manga_translator/manga_translator.py`](file:///Users/ice-h/Source/manga-image-translator/manga_translator/manga_translator.py).
- Added step settings resolution (`resolvePipelineStepSettings`) and human-readable label formatters in [`front/app/components/PageDetailModal.tsx`](file:///Users/ice-h/Source/manga-image-translator/front/app/components/PageDetailModal.tsx) to support both nested manifest configs and flat result settings.
- Enhanced the `PageDetailModal` sidebar with categorized step settings cards (Detection & OCR, Speech Bubbles, Inpainting & Mask, Rendering & Typesetting, Colorization & Upscaling) alongside engine, model, languages, timestamps, and process timing.
- Added comprehensive unit tests in [`front/app/components/PageDetailModal.test.ts`](file:///Users/ice-h/Source/manga-image-translator/front/app/components/PageDetailModal.test.ts).

## 2026-09-20 — Immediate persistence of text detection bounding boxes

- Updated [`MangaTranslator._translate`](file:///Users/ice-h/Source/manga-image-translator/manga_translator/manga_translator.py) and `_translate_legacy` to persist raw detection bounding boxes (`detection.json`) and OCR textlines (`ocr.json`) to `ctx.result_documents` and disk/database storage immediately upon stage completion.
- Prevents raw segmentation bounding boxes from being lost or obscured by downstream multi-line bubble grouping or post-translation filtering.
- Added automated unit test in [`test/test_save_detection_immediately.py`](file:///Users/ice-h/Source/manga-image-translator/test/test_save_detection_immediately.py).

## 2026-09-20 — Manga OCR model preparation in docker_prepare.py

- Updated `ModelMangaOCR` to download the `kha-white/manga-ocr-base` Hugging Face model snapshot (`config.json`, `pytorch_model.bin`, `preprocessor_config.json`, `tokenizer_config.json`, `vocab.txt`, `special_tokens_map.json`) directly into `./models/ocr/` and verify local presence.
- Updated `docker_prepare.py` to support `mocr` preparation via `--models ocr.mocr` (as well as `mocr`, `ocr.manga_ocr`, or default all-models preparation) with non-invasive module import and error resilience.
- Added comprehensive unit tests in `test/test_docker_prepare_mocr.py`.

## 2026-09-20 — Repository memory established

- Added shared repository-state guidance for coding agents.
- Added living notes for current architecture, bug lessons, and notable changes.

## 2026-09-20 — Bubble-aware batch pipeline reordered

- Grouped detected-bubble text before page-count-based AI batching.
- Moved legacy/bubble mask union and inpainting before batch translation.
- Moved translated-text placement after translation and added original-pixel restoration for failed units.
- Persisted the legacy, bubble, and merged masks for each prepared result.

## 2026-09-20 — Linked layout for connected speech bubbles

- Added mask-aware lobe detection and shared-font text flow for connected bubble shapes.
- Persisted linked layout segments and added canonical backend editor reflow.
- Made linked segments independently movable while keeping translation and styling grouped.

## 2026-09-20 — Geometric centering and adaptive font sizing for speech bubbles

- Anchored bubble text boxes on the interior distance-transform medial peak rather than bounding-box centers.
- Added adaptive target font sizing derived from bubble interior area and inscribed radius to fill ~45–55% of the bubble interior.
- Implemented adaptive per-lobe font sizing across connected multi-lobe bubbles: main/primary lobe preserves full page-level target font size (~20–24px) while smaller lobes scale adaptively, preventing multi-lobe bubbles from shrinking down to tiny bottlenecked font sizes.
- Added punctuation-aware split scoring ('.', '!', '?', ',', ';', '—') to preserve clause and sentence structure across lobes.
- Propagated segment-level `font_size` through backend layout preview, rendering compositor, and frontend interactive studio.
- Added safe centered rectangle expansion across wide aspect ratios [0.35–2.6] around interior distance peaks.

## 2026-09-20 — Shape decomposition and variable-width line-slot typesetting

- Implemented `build_geometry_profile` for speech bubble interiors: calculates row-by-row horizontal spans (`left`, `right`, `width`, `center`), 1D Gaussian smoothing to suppress pixel jitter, derivative tracking (`d_width`, `d_center`), distance transform peaks, and neck detection.
- Implemented `analyze_semantic_breakpoints` with tiered grammatical penalty scoring (sentence boundaries = 0, clause boundaries = 5, conjunctions/prepositions = 15, normal word boundaries = 30) for linguistically optimal text splits across bubble lobes.
- Implemented `build_line_slots` and `dp_break_lines_for_lobe`: variable-width horizontal line slots across natural lobe contours with DP word wrapping, lobe medial-axis anchoring, horizontal wobble dampening, and orphan short-word penalties.
- Implemented `render_positioned_lines` for pixel-accurate FreeType character rendering of irregularly positioned lines with stroke/border into RGBA canvas segments.
- Upgraded `_fit_lobe_text` to prefer uniform font sizing across connected lobes, with emergency per-lobe adaptive scaling only when required to preserve main lobe readability.
- Added full unit test coverage in `test/test_bubble_layout.py` including asymmetric multi-lobed bubble semantic allocation, geometry profiling, and semantic breakpoint scoring.

## 2026-09-20 — Thread-safe bubble detector shared model execution

- Integrated `manga_translator/detection/bubble.py` with `SharedModelExecutor` and `model_cache`.
- Added `@model_operation` hooks (`prepare`, `dispatch`, `unload`) and serialized bubble detector YOLO execution on the dedicated model worker thread.
- Routed bubble detection calls in `MangaTranslator` through `_mps_call` with automatic CPU fallback and MPS lock serialization, eliminating Metal command buffer race conditions during multi-worker server operation.

## 2026-09-20 — Best-effort text rendering for review-flagged regions

- Enabled best-effort layout calculation and rendering for all regions with available translations, preventing translation suppression when `uncertain_boundary`, `uncertain_cleanup`, or tight fits occur.
- Retained inpainting masks and canvas rendering for translated units so dialogue is visible immediately on the screen, while keeping review metadata for editor inspection.
## 2026-09-20 — Page-level and bubble font size consistency

- Established page-level baseline font sizing across dialogue bubbles in `prepare_bubbles` and `resize_regions_to_font_size` to maintain visual consistency across speech bubbles on the page.
- Relaxed bubble boundary clipping in `_find_horizontal_placement`: when text exceeds narrow bubble width at target font sizes, text is centered on the bubble center with multi-line wrapping in the preferred range ($\ge 85\%$ of target) instead of severely downscaling to tiny font sizes.
- Updated `_estimate_adaptive_font_size` aspect ratio cap to `max(bh, bw) * 0.38` to prevent tall narrow bubbles from being artificially capped.
- Increased downstream `downscale_constraint` default to 0.85 in FreeType and Pillow English renderers to preserve target font sizes.

## 2026-09-20 — Text segmentation model comparison tool

- Added `devscripts/evaluate_text_segmentation.py` to evaluate and benchmark `a-b-c-x-y-z/Manga-Text-Segmentation-2025` (Unet++ EfficientNetv2) against baseline `detect-20241225.ckpt` (DBNet ResNet-34).
- Added multi-metric evaluation (IoU, Dice/F1, pixel agreement, relative precision/recall, coverage percentages, and latency benchmarking) supporting CPU, MPS, and CUDA.
- Generates side-by-side composite comparison grids, difference maps, binary masks, overlays, whiteout clean images, JSON metrics, and interactive HTML visual reports.
- Added comprehensive unit tests in `test/test_text_segmentation_evaluator.py`.

## 2026-09-20 — Frontend OCR model selection

- Added OCR model selection to the web studio and legacy web form, with saved settings and batch configuration support.
# 2026-09-21 — Professional manga localization mode

- Added an opt-in Japanese-to-English professional workflow with OCR-based story analysis, automatic story segmentation and overrides, sequential contextual drafting, DeepSeek refusal fallback, independent editing, deterministic region validation, and review flags.
- Added story/chunk progress labels to the batch UI and a page-detail audit panel for the persisted first draft and editor pass.
- Separated the professional working-draft and editor roles so the editor independently rechecks the Japanese instead of receiving an already-polished draft; fixed DeepSeek's system-role override path.
- Added a Story analysis panel to page details so the saved analyst summary and story guidance are visible alongside the draft/editor audit.
- Added web studio controls for translation quality and optional story page ranges; fast mode remains the default.
- Persisted per-page source, draft, final text, confidence, review reasons, story analysis, and provider/model provenance in `professional_translation.json`.
## 2026-09-21

- Added an archive-aware Professional translation submission flow with story boundary editing, merge-all-pages override, accessible range controls, structured `storyPlan` persistence, and story-local draft/editor request chunks controlled by `translationBatchSize`.
- Redesigned the "Arrange stories" modal UX with a color-coded thumbnail filmstrip, interactive gap split triggers (`+`), discrete draggable boundary handles with pointer-capture and arrow-key stepping, live tooltips, story row swatches, and inline split removal.
- Added a right-side high-resolution Page Preview & Cut-off Inspector panel in the Arrange Stories view, featuring single-page inspection with navigation controls, active thumbnail highlighting, and a 2-up side-by-side transition comparison for verifying narrative boundary cut-offs.

## 2026-09-21 — Review editor resolution workflow

- Kept unresolved review translations out of the editor preview/export, surfaced human-readable review reasons and guidance, and moved review resolution plus draft-save/approval actions into a responsive footer.

## 2026-09-21 — Shape-safe editor lettering

- Persisted the backend-validated lettering raster for each linked lobe and reused it in editor preview, reflow, and image export, preventing browser font metrics from moving glyphs outside the bubble.

## 2026-09-23 — Constrained text erasure and protected bubble outlines

- Removed bubble-wide dark-ink cleanup and page-wide final dilation from canonical inpaint mask composition.
- Kept detector segmentation pixels as OCR-independent erase evidence, added per-component constrained growth, narrow source-image outline protection, and post-inpaint protected-pixel restoration.
- Added detector-missed-text regression coverage and protected-edge retention metrics.

## 2026-09-23 — Structured linguistic register analysis

- Added character speech profiles and story-level honorific, language-feature, and localization-convention output to professional story analysis; the editor receives these through the existing story guide, and the Story Analysis panel displays them.
- Added stage-specific system guidance: analysis uses compact neutral descriptions, while draft/editor requests preserve the source's meaning and explicitness; the editor is instructed to apply the structured language profile and story conventions.
