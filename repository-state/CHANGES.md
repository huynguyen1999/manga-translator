# Notable changes

Record new features and large changes here. Keep implementation detail in code, tests, or dedicated documentation.

## 2026-09-23 — Overlay speech bubbles in page detail

- Replaced the standalone bubble-mask view with a translucent bubble overlay on the original page.

## 2026-09-23 — Search existing manga before showing assignment matches

- Group assignment dialogs now show full matching manga titles only after a search. Studio suggestions come from manga already in the library, so recent titles and unfinished batches are not presented as existing groups.

## 2026-09-23 — Allow two concurrent GPU model tasks

- Raised the in-process GPU stage and shared model executor limits to two concurrent calls while retaining one shared instance per model.
- Made model-cache creation thread-safe and device-cache reclamation exclusive with active model calls.

## 2026-09-23 — Bound Apple Silicon batch detection memory

- On MPS, limit the in-process model executor and scheduler to one GPU call and run default/DBNet detection one page at a time; other devices retain the existing two-call and paired-detection behavior.
- Serialize the shared offline translator's mutable configure-and-infer operation so concurrent model lanes cannot mix request settings.

## 2026-09-23 — Bound page memory and add pipeline telemetry

- Added per-stage RSS, Python object, and MPS memory snapshots; Context and PipelineRun cleanup now release page image, mask, detector, and layout workspaces while retaining requested outputs. Mask diagnostics are freed before layout starts.
- Reused the final mask buffer for both mask references, cleaned persisted bubble detections before translation, and processed the legacy batch API in bounded preparation chunks.
- Capped MPS inpainting inputs at 1024px because the default 2048px limit left common full pages at full resolution and produced high float32 activation peaks.
- Batch completion drops translator page-history references and performs a synchronized two-pass garbage/device-cache cleanup while keeping shared models loaded.
- Added regressions for Context cleanup, batch-state clearing, and bounded legacy preparation.

## 2026-09-23 — Preserve numeric-only OCR regions

- OCR grouping now retains Unicode numeric annotations, preserves their source text and measured source font size, skips translator payloads in fast and professional modes, and records retention/policy metadata through checkpoints.
- Added regressions covering Unicode numbers, provider exclusion, mask participation, and final rendering.

## 2026-09-23 — Show queued batch stage

- Queued page rows show the next scheduled pipeline step, with distinct labels for pages waiting to start or for a batch slot.

## 2026-09-23 — Allow bounded CPU page-stage concurrency

- Derived scheduler CPU-heavy/light limits from pipeline workers and added `--cpu-stage-workers` to override the automatic heavy-stage cap of three.
- Shared one bounded background CPU pool across executor event loops; retained one interactive slot, two GPU slots, and single-threaded OpenCV.
- Added regressions for CPU overlap and process-wide capacity, scheduler limits, and bounded GPU capacity.

## 2026-09-23 — Rescue severely compressed speech-bubble text with one word break

- Preserve the normal shape-aware layout, then consider one dictionary-valid split only when a long word causes compression below 90% of the calibrated target and the split materially improves font size. Freeze the chosen lines and persist rescue diagnostics through `layout.json`.
- Added focused regressions for compression gates, single-break behavior, dictionary breakpoints, and diagnostic round trips.

## 2026-09-23 — Honor OCR minimum confidence in MangaOCR output

- `mocr` now applies the configured confidence threshold to the final OCR region after aggregating its source-region scores, matching the other OCR backends.

## 2026-09-23 — Freeze layout across stage checkpoints

- Made versioned `layout.json` authoritative for selected fonts, positions, exact line strings, bubble geometry, and input fingerprints; rendering restores it by stable region ID and paints saved lines without reflow.
- Persisted stable bubble IDs through translation checkpoints so multiple regions in one bubble retain joint collision-safe layout after restart. Solved manga dialogue now uses whole-word wrapping.
- Added a context-destruction regression comparing rendered pixels before and after hydration, plus a checkpoint test for shared bubble identity.

## 2026-09-23 — Job-wide manga stage barriers

- Changed mutable-store translation batches to finish each pipeline stage for all active pages before scheduling the next stage, with OCR → bubble detection → text grouping before translation.
- Translation now persists translated documents and queues mask generation; mask, layout, inpainting, and rendering resume as separate page checkpoints. Failed pages hold the barrier until retried or removed.
- Kept neural inference groups bounded at two pages and avoided loading source pixels during translation. Added scheduler and memory regressions for stage order, translation checkpointing, and retry dispatch.

## 2026-09-23 — Stream batch detail updates

- The batch event stream now sends changed batch details alongside summary snapshots, replacing repeated per-progress `GET /batches/{id}` calls.
- Expanded batch cards fetch detail once when needed; subsequent item progress arrives through SSE.

## 2026-09-23 — Stream manga summary job updates

- Replaced two-second Studio summary-job and active-summary polling plus Search Lab's three-second status polling with change-only SSE subscriptions.
- Fetch the full summary once when a job completes; EventSource reconnects automatically after transient disconnects.

## 2026-09-23 — Batch model inference across compatible pages

- The stage scheduler now groups up to two compatible pages for upscaling, default/DBNet text detection, and YOLO bubble detection.
- Each model batch routes through the shared inference executor; up to two local model calls can be in flight, and page outputs keep independent stage checkpoints and artifacts. Other detector backends retain their existing per-page inference path.
- Added batch mapping, stage-claim, and checkpoint persistence coverage.

## 2026-09-23 — Enforce stage resource limits

- Applied canonical resource classes to in-process batch stage scheduling: GPU 2, heavy CPU 1, light CPU 2, network 2, and I/O 4 concurrent claims.
- Kept preparation pages on disk until their stage slot is available; translation groups share the bounded network lane.
- Added a scheduler regression check showing GPU stages stay within their configured capacity while heavy CPU work can run concurrently.

## 2026-09-23 — Batch OCR crops across ready pages

- The scheduler now groups up to two compatible pages at the OCR checkpoint.
- The default 48px CTC recognizer batches up to 16 crops across those pages and maps each prediction back to its page and text region; alternate OCR models retain the existing single-page execution path.
- Added scheduler and crop-to-page mapping regressions.

## 2026-09-23 — Move pipeline runs into the canonical package

- Extracted the checkpoint/run manager to `manga_translator/pipeline/run.py` as `PipelineRun`; production translation, scheduling, and reruns now import the canonical pipeline module.
- Renamed the translator's runtime run reference and kept `pipeline_lab.py` as a compatibility re-export for older callers.
- Updated run and checkpoint tests to exercise the canonical imports.

## 2026-09-23 — Canonical pipeline stage records

- Added canonical stage IDs, resource classes, dependency-based invalidation, progress-message mapping, and stable fingerprints; rerun modes now use the shared graph.
- Added PostgreSQL stage state, attempt history, and revisioned structured-document/artifact registries. Reruns persist their stage attempts and the existing pipeline timing endpoint overlays those records.
- Added a durable layout checkpoint, normalized older manifests into the current stage order, resumed batch retries from failed or selected checkpoints, exposed downstream-aware stage retry in page details, and marked unfinished database attempts interrupted at scheduler startup.
- Added stage graph, settings invalidation, rerun dependency, and checkpoint retry tests. Indexed reruns persist versioned image revisions and structured documents together, and existing result URLs serve the active revision; normal batch writes remain on the legacy path.
- Fixed pipeline sidecar fallback writes to follow the owning translator result root.
- Made checkpoint retry plans dependency-aware: translation retries reuse mask/inpainting outputs, and OCR retries regroup from saved bubble geometry before refreshing downstream stages.
- Added optional colorization to the canonical GPU stage graph and returned direct stage dependencies from the timing API so the retry preview consumes backend metadata.
- Imported normal batch checkpoint states, recognized JSON documents, and completed disk-artifact metadata into canonical PostgreSQL stage/revision tables when result folders are indexed; failed retries re-index their valid checkpoint so failure state is durable, and stale manifests cannot overwrite newer or running stage state.
- Added bounded interactive/background CPU lanes and moved production mask construction and page layout off the API event loop; OpenCV native threading is capped at one thread.

## 2026-09-23 — Stage-claimed batch preparation

- In-process batch preparation now requeues one checkpointed stage at a time and persists the next stage with the batch item before continuing; prepared pages still join the existing grouped translation and bounded render flow.
- Pipeline run manifests keep independent pending stages available until completion, instead of marking them skipped from their display order.
- Routed checkpoint-backed retries through the resume processor in the mutable-store scheduler, so retry stage metadata is honored instead of rerunning preparation.
- Deferred decoded source images until bounded per-page rendering for both fast and professional translation batches, keeping resident image memory independent of the translation group size.
- Professional translation now waits for each complete configured story segment, lets independent ready stories proceed, and blocks a story with a failed prerequisite; local story/archive ranges are remapped for each claimed group.
- PostgreSQL batch mutations lock the current manifest row and save scheduler claims in the same transaction, preventing stale concurrent workers from replacing one another's state.

## 2026-09-23 — Translation-first batch completion

- Batch preparation now stops before translation-dependent masks and inpainting. Fast groups select ready pages without waiting behind earlier preparation, then each translated page builds its mask, runs layout and inpainting, and renders with bounded per-page memory cleanup.
- Batch text rendering runs through the bounded background CPU lane, keeping CPU rasterization off the API event loop.
- Full reruns use the batch translation dispatch and the existing atomic staging workspace.
- A cached inpainted image without its final mask now triggers post-translation mask regeneration and inpainting.

## 2026-09-23 — Remove standalone Pipeline Lab

- Removed its Studio navigation, upload shortcut, route UI, run history, and control endpoints. Kept pipeline run manifests for page timing, batch resume, and rerun diagnostics.

## 2026-09-23 — Share page geometry and reduce mask work

- Reused prepared bubble geometry across mask construction and layout, stored per-bubble geometry as detached crop arrays, and retained one page-wide protected-edge mask.
- Replaced full-page-per-region mask growth and ignored-bubble dilation with crop-local operations; added mask workload timings and persisted profiling output.
- Isolated layout debug rendering behind explicit diagnostics, consolidated free-text ownership and region identity helpers, and added mask, geometry-reuse, and Studio/CLI parity regressions.

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

## 2026-09-23 — Keep checkpoint CPU stages off the interactive lane

- Routed mask-generation and layout work through the background CPU lane during checkpoint retries, preserving the separate interactive lane for Studio work.
- Made repeated same-stage progress callbacks no-op so they do not emit batch updates or trigger detail-read storms; coalesced overlapping reads and show elapsed time from persisted stage transitions.
- Reported the actual stage when a grouped page is claimed.

## 2026-09-23 — Read batch progress from PostgreSQL rows

- Persisted each batch item’s stage start time and hydrate batch/item state from relational PostgreSQL columns and payload rows instead of a potentially stale manifest snapshot.
- Added a regression proving the database’s current stage wins over stale manifest state.
