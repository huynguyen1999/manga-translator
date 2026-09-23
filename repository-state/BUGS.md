# Bug lessons

Record bugs when they are discovered, not only after they are fixed. Use the smallest useful entry:

## 2026-09-23 — Group reruns used stale page metadata for group identity

- Symptom: Saving a rerun batch warned that its item's group differed from the page's PostgreSQL group.
- Root cause: `group_pages()` omitted the relational group identity, and group-wide reruns preferred `meta.mangaGroupId`; old page metadata could therefore seed a rerun item with a stale group ID.
- Fix: Return the joined PostgreSQL group ID/title from `group_pages()` and use those canonical fields when building rerun items.
- Prevention: Derive group identity from the relational page row; metadata is a compatibility payload and may lag.

## 2026-09-23 — Bubble detector results were discarded when a page had no text regions

- Symptom: Bubble detection logged `NoneType object is not iterable` and claimed to be unavailable on pages whose `text_regions` was `None`.
- Root cause: The success log counted matched regions by iterating `ctx.text_regions` without handling `None`; its exception handler then erased successful bubble detections.
- Fix: Count over an empty list when there are no text regions, preserving the detector output.
- Prevention: Optional diagnostic metrics must tolerate empty or absent pipeline data and must not invalidate successful stage results.

## 2026-09-23 — Checkpointed batch rendering repeated the layout solver

- Symptom: Batch logs showed layout after inpainting, and pages appeared stalled while the UI said they were waiting for batch AI.
- Root cause: Layout ran after mask generation, but its checkpoint reload did not restore the saved mask. Later, rendering rebuilt a fresh `Context` and did not restore `_bubble_layout_ready`, rerunning the expensive solver. Log IDs used a shared prefix, obscuring per-page ordering, and the waiting badge described the former inpaint-before-translation flow.
- Fix: Layout reloads the persisted mask and declares that dependency; rendering trusts a completed persisted layout stage; per-page log/task IDs use unique suffixes; and the badge says pages are prepared and waiting for batch translation.
- Prevention: Restore required artifacts and ephemeral context flags from persisted stage state before downstream retries.

## 2026-09-23 — CPU lane workers were garbage-collected while idle

- Symptom: Asyncio logged `Task was destroyed but it is pending` for idle `_CpuLane._work()` tasks; CPU-backed stages could then lose their worker lane.
- Root cause: The per-loop executor cache held only a weak reference to each executor, leaving its worker tasks without a strong owner after a request completed.
- Fix: Make the event loop own its CPU executor and close both lanes during server and in-process executor shutdown.
- Prevention: Keep lane-worker lifetime tied to the event loop and test collection plus explicit shutdown.

## 2026-09-23 — Bubble detection stage depended on grouped OCR regions

- Symptom: Moving bubble detection before text grouping would produce an empty bubble artifact and skip the detector.
- Root cause: `_detect_speech_bubbles()` returned early when `ctx.text_regions` was empty, even though the detector only needs the page image.
- Fix: Run bubble inference from the image alone, then apply saved detections when text grouping runs; reload those detections for mask generation.
- Prevention: Keep bubble inference image-scoped and apply region association only after grouped text regions exist.

## 2026-09-23 — Checkpointed textless pages failed while saving final metadata

- Symptom: A batch page with no detected text failed after detection with `TypeError: 'NoneType' object is not a mapping`.
- Root cause: Reconstructed `PipelineRun` contexts do not initialize `result_documents`, but `_revert_upscale()` expanded that optional field as a mapping while finalizing textless pages.
- Fix: Treat missing result documents as an empty mapping when assembling the final documents.
- Prevention: Cover `_revert_upscale()` with a checkpoint context that has no `result_documents`.

## 2026-09-23 — Checkpointed image stages were skipped and requeued forever

- Symptom: Individual batch pages could remain on one pipeline step while their elapsed time repeatedly restarted.
- Root cause: The per-page retry body sat outside the executor callback; its context guard ran before the callback initialized that context, so the pending stage was never executed and was queued again.
- Fix: Run the retry body inside the in-process executor callback; cover that the stage executes there and advances the item.
- Prevention: Keep page context setup and stage execution together on the executor loop.

## 2026-09-23 — Checkpoint CPU work competed with interactive Studio work

- Symptom: Mask and layout checkpoint retries could occupy the CPU lane reserved for Studio interactions.
- Root cause: Their normal-priority requests were routed to the interactive lane, while the background lane remained idle.
- Fix: Run checkpoint mask/layout stages at background priority; retain normal priority for interactive and direct work.
- Prevention: Assign priority at the batch scheduler boundary, not from the stage's resource class alone.

## 2026-09-23 — Concurrent batch detail refreshes caused redundant API reads

- Symptom: Active batches emitted several `GET /batches/{id}` requests around every progress update.
- Root cause: The global batch SSE handler and each expanded `BatchCard` both fetched details when `updatedAt` changed; single-flight coalescing only joined overlapping requests, while these fast reads completed before the next trigger.
- Fix: Send changed full batch details as an SSE event, apply them to loaded/completed batches, and fetch from REST only when a user first expands a batch with missing details.
- Prevention: Do not turn summary timestamps into detail-fetch triggers; stream the changed detail payload to existing subscribers.

## 2026-09-23 — Batch reads trusted stale manifest item state

- Symptom: A batch snapshot could report an older page status or stage than the relational `batch_items` row.
- Root cause: PostgreSQL reads decoded `batches.manifest` and only overlaid page identity fields; status, stage, and item payload were read back from the same duplicated snapshot.
- Fix: Hydrate batch and item state from relational columns, persist `stage_started_at`, and keep the manifest for compatibility metadata.
- Prevention: Read mutable batch/page state from relational rows; treat manifest JSON as a compatibility payload, not the authority.

## 2026-09-23 — Grouped page stages displayed the wrong progress label

- Symptom: Pages claimed for batched detection were reported as OCR in the item status.
- Root cause: The shared grouped-stage claim path hardcoded `stage="ocr"` for every stage.
- Fix: Store the actual claimed stage and cover detection claims in the scheduler test.
- Prevention: Derive display state from the stage being claimed instead of the helper's default stage.

## 2026-09-23 — Shared detector methods were shadowed by a batch helper

- Symptom: The new batch wrapper would lose access to shared detector postprocessing methods at runtime.
- Root cause: A module-level DBNet batch helper was inserted before the end of `CommonDetector`, leaving following methods nested inside the helper.
- Fix: Moved the helper after the detector classes and added a regression check through `detect_batch()`.
- Prevention: Keep class-boundary checks and exercise shared postprocessing when adding module-level helpers.

## 2026-09-23 — Moved pipeline run imports resolved under the wrong package

- Symptom: Serialization and rerun tests failed to import `manga_translator.pipeline.rendering` after moving the run manager into `manga_translator.pipeline`.
- Root cause: Inline relative imports still treated `run.py` as if it were directly under `manga_translator`.
- Fix: Updated rendering and detection imports to ascend from the pipeline package.
- Prevention: Audit inline imports as well as top-level imports whenever a Python module moves between packages.

## 2026-09-23 — Independent pipeline stages were marked skipped by display order

- Symptom: Preparation can finish inpainting before grouped translation, but the run manifest marked translation and layout skipped; the later layout progress could not reopen its skipped record.
- Root cause: `PipelineRun._begin()` treated manifest order as a strict execution order even though stages can run on independent branches.
- Fix: Keep unvisited stages pending while work continues and mark remaining stages skipped only when the run finishes.
- Prevention: Do not infer dependency completion from stage-list position; use actual stage execution and the pipeline dependency graph.

## 2026-09-23 — Checkpoint retries reran batch preparation

- Symptom: A page retried from a saved pipeline stage entered full preparation again.
- Root cause: The mutable-store scheduler always dispatched queued translation items to `_process_prepare_item`, even when the item carried a saved result folder or retry stage.
- Fix: Route checkpoint-backed items through the resume processor, which reuses the saved stage checkpoint.
- Prevention: Test retry dispatch through `_launch_available`; direct tests of the resume processor alone do not cover scheduler routing.

## 2026-09-23 — In-progress batch thumbnails requested missing final images

- Symptom: Pages still in preparation showed broken thumbnails in the Jobs drawer.
- Root cause: `TranslatingSection` built a final-result URL for every item with a result folder, including unfinished items whose final image had not been written.
- Fix: Only expose the result preview after an item reaches `finished`; in-progress rows use their source image.
- Prevention: Treat a result folder as metadata, not proof that its final artifact exists; cover preview selection for unfinished and completed states.

## 2026-09-23 — Concurrent batch claims could overwrite each other

- Symptom: Separate server workers could both read one queued batch item and overwrite scheduler progress with stale manifest snapshots.
- Root cause: PostgreSQL batch mutations used only an in-process lock and did not lock the database manifest row across read-modify-write.
- Fix: Lock the batch row with `FOR UPDATE` and persist the mutation on the same transaction/connection.
- Prevention: Keep scheduler state transitions inside row-locked PostgreSQL mutations; use stage-row claims if future workers need finer-grained scheduling.

## 2026-09-23 — Professional translation could cross failed story prerequisites

- Symptom: A professional batch could translate later pages after one page in a configured story failed preparation, shifting the story plan and potentially combining unrelated stories.
- Root cause: The scheduler removed failed pages before evaluating the professional translation barrier, then selected all remaining ready pages as one group.
- Fix: Claim complete configured story segments independently, block any segment containing a failed/completed partial prerequisite, and remap its story/archive ranges to the selected group.
- Prevention: Evaluate professional readiness against the full logical batch, including failed pages, before forming model input groups.

## 2026-09-23 — Checkpoint documents ignored the configured result root

- Symptom: `MangaTranslator.prepare()` attempted to write pipeline documents to `/Volumes/storage/data/results` during a test using a temporary result directory.
- Root cause: `save_result_documents()` used the global server result root whenever no database saver was installed, even when its caller owned a different result root.
- Fix: Accept an optional result root and pass the translator or pipeline-run root at each call site.
- Prevention: Keep sidecar document writes rooted beside the image artifacts that own them; test with a non-default result root.

## 2026-09-23 — Stale page geometry could survive bubble reassignment

- Symptom: A reused `PageGeometry` could retain a removed or replaced bubble mask.
- Root cause: `PageGeometry.matches()` treated maskless regions as wildcards and checked only the current regions, so removed assignments were not detected.
- Fix: Require the current region-to-mask identity map to equal the prepared map; cover removal and replacement in the reuse regression test.
- Prevention: Cache validation must compare the complete input identity set, including removed inputs.

## 2026-09-23 — Mask profiling treated NumPy lines as a boolean

- Symptom: Profiling failed with NumPy's ambiguous truth-value error while counting OCR lines.
- Root cause: Workload counting used boolean fallback on a NumPy array.
- Fix: Check for `None` explicitly before taking the array length.
- Prevention: Never use NumPy arrays in boolean fallback expressions; use explicit `None`/size checks.

## 2026-09-23 — Professional analysis prompt raised NameError

- Symptom: Translation failed with `name 'default' is not defined` before the analysis request.
- Root cause: A literal schema example inside an f-string used unescaped braces, so Python evaluated `default` as an expression.
- Fix: Escape the example's braces and add a focused analysis-prompt regression test.
- Prevention: Escape literal JSON/schema braces in f-string prompts; exercise prompt construction in tests.

## 2026-09-23 — Bubble-wide cleanup erased speech-bubble outlines

- Symptom: Recent pre-inpainting cleanup could erase or damage a speech-bubble outline when text sat close to the border.
- Root cause: `prepare_bubble_masks()` treated every dark pixel inside a bubble as cleanup evidence, then `build_inpaint_masks()` unioned and globally dilated that mixture before inpainting.
- Fix: Make detector segmentation the primary erase evidence, derive protected structure from dark pixels in a narrow segmentation-boundary band, grow text components only inside their owning bubble and outside protected structure, and restore protected source pixels after inpainting.
- Prevention: Keep text evidence and bubble geometry separate; never send a geometry-wide bubble cleanup mask directly to the inpainter.

## 2026-09-23 — Duplicate PageDetailModal mounted on unfinished batch image click

- Symptom: Clicking the close ('X') button on an unfinished batch item's detail view did not close the view on the first click; it required clicking 'X' a second time.
- Root cause: When `handleOpenLightbox` set `selectedImageForModal` in `App.tsx`, both `App.tsx` and `ResultGallery.tsx` rendered duplicate `PageDetailModal` instances directly on top of each other because `ResultGallery` had an effect syncing `selectedImageForModal` into its own internal modal state (`isModalOpen = true`). Clicking 'X' on the top modal unmounted `App.tsx`'s modal, exposing `ResultGallery`'s duplicate modal underneath which required a second click to close.
- Fix: Removed external modal syncing props (`selectedImageForModal`, `onCloseExternalModal`) from `ResultGallery`, keeping standalone in-flight / unfinished preview modals managed exclusively by `App.tsx` and gallery page modals managed by `ResultGallery`.
- Prevention: Modal state should have a single source of truth; never duplicate modal components across parent and child components for the same data trigger.

## 2026-09-23 — Pipeline rerun prerequisite check rejected database-backed pages

- Symptom: Reprocess-text / rerun failed with `No eligible pages found for reprocess_text rerun. Previous translations are required to preserve and remap text` even though pages had active translations.
- Root cause: `validate_rerun_prerequisites` only checked for physical `.json` files on disk (`translations.json`, `text_regions.json`), ignoring database-backed pages where text regions and translations are stored in PostgreSQL (`pages.text_regions` / `result_documents`).
- Fix: Updated `validate_rerun_prerequisites` and `load_rerun_context` to accept `database` and `record` parameters, recognize database/record text region availability flags, and allow reprocess-text to run whenever a base canvas is present (handling empty initial translations naturally).
- Prevention: Never assume result sidecars only exist as flat files on disk; always inspect database-backed models and page metadata alongside filesystem assets.

## 2026-09-23 — Rerun OCR override omitted CTC and sent an unsupported value

- Symptom: The rerun OCR selector had no `48px_ctc` option and could submit `offline`, which is not a valid `Ocr` value; backend paths also defaulted to `48px`.
- Root cause: The selector duplicated OCR choices instead of using the shared option list, and backend fallbacks were hardcoded independently from the registered CTC model.
- Fix: Use shared OCR options with `48px_ctc` selected by default and switch backend fallbacks to `Ocr.ocr48px_ctc.value`.
- Prevention: Derive user-facing OCR choices and Python fallbacks from the canonical enum/options whenever adding a model.

## 2026-09-23 — Pipeline capture pre-warmed the wrong detector

- Symptom: `pipeline_step_runner.py capture` failed before processing an image with `AttributeError: 'Detector' object has no attribute 'model'`.
- Root cause: The runner passed the text-detector enum to speech-bubble `prepare()`, which expects `BubbleDetectionConfig` and reads `.model`.
- Fix: Restored the text-detector pre-warm call to `manga_translator.detection.prepare()` and added a focused regression test covering both pre-warm calls.
- Prevention: Keep text detection and bubble detection imports/calls distinct in runner setup code.

## 2026-09-22 — Discrepancy between Web Studio and CLI runner in bubble typesetting and overflow

- Symptom: Web Studio rendered speech bubbles with text overflowing the bubble boundary (e.g. "THE MEAT IS DELICIOUS!") and aggressive hyphenation ("DELI-CIOUS"), while the CLI runner produced a clean, smaller font size with zero overflow and zero hyphenation on identical inputs.
- Root cause: (1) `pipeline_step_runner.py` accidentally ran `layout_page` twice (during `capture` and again during `render`), which mutated `region.font_size` down from Japanese OCR size (~50px) to an intermediate value (~24px) before the final layout pass, while Web Studio ran layout once using the raw Japanese font size as `source_font_size`, biasing candidate generation toward oversized fonts with hyphenation. (2) Bubble detection defaulted to `manga109` instead of `yolov8m` in several batch paths, and was turned off by default in older frontend settings presets (`detectionResolution: 2560`, `box_threshold: 0.45`, `mask_dilation: 30`). (3) Web Studio `/layout-preview` used legacy `_fit_lobe_text()` instead of the canonical `layout_page()` engine.
- Fix: (1) Implemented internal two-stage font calibration in `_build_region_layout_plan()` (`_estimate_adaptive_font_size` from interior bubble area and word count) so a single pass yields the optimal ~55-65% target font size without double-run hacks. (2) Stored `source_font_size` and `calibrated_font_size` in `RegionLayout`. (3) Unified all layout execution paths (CLI capture/render, batch scheduler, interactive `/layout-preview`) to call `layout_page()`. (4) Updated default settings to `bubbleDetection = true`, `model = yolov8m`, `detectionResolution = 2048`, `customBoxThreshold = 0.5`, `maskDilationOffset = 20`.
- Prevention: Always compute target font sizes geometrically from bubble interior dimensions and target text length rather than relying on Japanese OCR font priors, and keep all CLI, batch, and interactive layout entrypoints routing through the single canonical `layout_page()` solver.

## 2026-09-22 — Production text rendering bypassed shape-aware layout solver

- Symptom: Production translated manga pages rendered with jumbled, overlapping text, thick distorted borders, squished dialogue, and misaligned free-text.
- Root cause: (1) `_run_text_rendering` transformed text case after layout rather than before, causing solver measurements to mismatch rendered text. (2) Free-text regions with solved layouts were fed into legacy `dispatch_rendering`/`dispatch_eng_render` which re-expanded/re-laid them out, rather than directly compositing `_bubble_box` onto `_bubble_points`. (3) `resize_regions_to_font_size` and `render` in `manga_translator/rendering/__init__.py` overwrote solved font sizes and warped boxes to re-estimated bounding boxes instead of using the solver's `_bubble_points`. (4) `_prepare_bubble_layout` called legacy `prepare_bubbles` instead of `layout_page`. (5) `_prepare_single_context` and `_complete_translation_pipeline` did not persist/restore `ctx.inpaint_mask`, causing free-text solver targets to be missing. (6) `server/batch_scheduler.py`'s `_process_rerender_item` initialized `ctx.img_rgb` with `inpainted.copy()` rather than `original_canvas.png`, and omitted `inpaint_mask.png`/`mask_final.png`.
- Fix: Synchronized `_prepare_bubble_layout`, `_prepare_single_context`, `_complete_translation_pipeline`, and `server/batch_scheduler.py` with `pipeline_step_runner.py`: normalize text case before layout, route free text and bubble text through the shape-aware solver, preserve solved boxes/points in `resize_regions_to_font_size` and `_render_single_textblock_eng`, composite free-text boxes directly, load `original_canvas.png` and `inpaint_mask.png` across batch pipeline and saved rerenders, and run `layout_page` unconditionally.
- Prevention: Ensure the core production rendering pipeline shares the exact execution path and invariant preservation as the dev pipeline runner.

## 2026-09-22 — Free-text coverage could reward fake vertical spacing

- Symptom: Tall free-text inpaint regions selected paragraphs with excessive gaps between lines.
- Root cause: Candidate generation derived an adaptive `line_spacing` from target height, and the coverage score rewarded allocated line rectangles alongside glyph pixels.
- Fix: Clamp free-text leading to one natural typography value per render, score font/wrapping with actual glyph ink against the source-plus-inpaint footprint, and keep rectangle coverage diagnostic-only.
- Prevention: Treat font size, wrapping, and rigid placement as the coverage search dimensions; never derive free-text line advance from erased-region height.

## 2026-09-22 — Replanning could collapse grouped source provenance

- Symptom: Re-running legacy bubble preparation on an already-grouped region reduced `group_members` to one item, losing the original source-region mapping.
- Root cause: The grouping pass rebuilt IDs from the current member list instead of carrying an existing `source_region_ids`/`source_regions` chain through single-member re-entry.
- Fix: Preserve nested source IDs and geometry snapshots whenever a grouped region is prepared again, reindexing flattened records by reading order.
- Prevention: Treat grouped regions as semantic units with immutable source provenance; rerun tests after any layout re-entry.

## 2026-09-22 — Layout re-entry could re-merge explicitly separate regions

- Symptom: Bubble detection kept sibling OCR regions separate, but legacy preparation merged them again before rendering.
- Root cause: A layout re-entry could call the legacy multi-region preparation path without carrying the no-grouping policy through.
- Fix: The shared production/runner path carries the separate-region policy through preparation and fallback; grouping now requires an explicit opt-in.
- Prevention: Test grouping policy at detection, preparation, and rendering boundaries.

## 2026-09-22 — Free-text joint layout searched an unbounded Cartesian product

- Symptom: `pipeline_step_runner.py render --all --line-spacing 0.1` could stay at 100% CPU indefinitely after processing a few datasets.
- Root cause: Nine free-text regions could each produce eight layout candidates, and joint selection exhaustively evaluated all `8^9` combinations with pairwise image collision checks.
- Fix: Cap exhaustive joint selection at 4,096 combinations and use the existing greedy collision-safe fallback for larger candidate spaces.
- Prevention: Keep joint layout search bounded; add beam search only if larger pages need better global optimization.

## 2026-09-22 — Legacy result migration blocked startup on a reused conflict name

- Symptom: Server startup failed with `OSError: [Errno 66] Directory not empty` while migrating a legacy result folder.
- Root cause: The deterministic conflict destination could already exist from a prior or interrupted migration, but the migration still tried to replace it.
- Fix: Reuse an identical existing conflict folder or choose the next numbered suffix while preserving all existing folders.
- Prevention: Keep migration collision handling idempotent and cover reused conflict destinations with a regression test.

## 2026-09-22 — Saved editor regions lost render settings on reload

- Symptom: A render-only rerun could reload translated text but silently lose saved font, color, spacing, alignment, or target-language settings.
- Root cause: `deserialize_textblocks` restored only geometry, text, translation, font size, angle, and direction from editor JSON.
- Fix: Restored the persisted editor render fields and used `original_text`/stable editor IDs when rebuilding text blocks.
- Prevention: Keep editor-region serialization and deserialization fields symmetric; rerender coverage now exercises a saved editor-region payload.

## YYYY-MM-DD — Short title

- Symptom:
- Root cause:
- Fix:
- Prevention:

## 2026-09-22 — `_RegionLayoutPlan` argument mismatch triggered bubble layout fallback

- Symptom: Speech bubble text layout broke, rendering massive text overflowing and spilling out of speech bubbles across the page.
- Root cause: `_select_free_text_joint_candidates` instantiated `_RegionLayoutPlan` with partial arguments (`region`, `candidates`, `source_profile`), but `_RegionLayoutPlan` dataclass fields lacked default values. The resulting `TypeError` was caught in `execute_fast_render_batch`, triggering an emergency fallback of all page regions to naive legacy bounding-box placement.
- Fix: Added sensible defaults to `_RegionLayoutPlan` dataclass fields and passed complete region typography attributes (`text`, `fg`, `bg`, `line_spacing`, `language`, `source_profile`) in `_select_free_text_joint_candidates`.
- Prevention: Ensure dataclasses instantiated in fallback or secondary branches have default values; verify that layout tests and render benchmarks assert `fallback_ms == 0` and `_solver_path == "solver"`.

## 2026-09-22 — Free-text wrapping followed page geometry

- Symptom: Floating translations could split ordinary words (`IN-` / `STED`), grow far beyond the source lettering, and land away from the erased text footprint.
- Root cause: The free-text branch reused bubble row slots, vertical compaction, and geometry-driven candidate scoring; placement and typography were not separate stages.
- Fix: Generate whole-word paragraph candidates from the source typography, freeze the selected lines, then center rendered ink on each region's damage centroid and search only rigid offsets around that anchor.
- Prevention: Keep `FREE_TEXT` out of `BubbleGeometry`/`solve_layout()`; test atomic words, damage-centroid centering, rigid line preservation, and hard collision displacement.

## 2026-09-22 — Non-bubble ownership drifted from content and erasure

- Symptom: A non-bubble result could render a partial translation or center against unrelated erased pixels after regions were grouped, reordered, or filtered.
- Root cause: Region edits were synchronized positionally, free-text ownership treated one shared page mask as one unscoped target, and the Voronoi ownership partition was incorrectly reused as a hard placement boundary. Joint free-text selection also rejected disjoint glyphs when only their safety padding touched.
- Fix: Persist stable region/source IDs, trace content through layout/render, sync JSON by ID, scope each region's target to the exact inpaint mask near its source footprint, allow translated glyphs to expand into blank page space, and detect joint conflicts from actual glyph pixels.
- Prevention: Keep translation ownership ID-based, treat ownership as damage assignment rather than a typography fence, and require the final inpaint mask plus layout text before strict solver-report rendering. The captured `devscripts/data/input` page is now a regression fixture.

## 2026-09-22 — JSON reload dropped bubble assignments

- Symptom: JSON-only pipeline samples lost their speech-bubble association and could be routed through the non-bubble renderer on reload.
- Root cause: `load_step_data()` reconstructed bubble detections but did not reattach their masks to deserialized text regions.
- Fix: Re-associate reconstructed detections with regions before placement classification.
- Prevention: Treat region-to-bubble assignment as reload-time state, not only pickle state; keep JSON fallback coverage.

## 2026-09-22 — Font switches reused cached glyphs from the previous face

- Symptom: Bubble layout tests could shift after the runner rendered free text with a different font first.
- Root cause: `get_char_glyph()` cached by character/size/direction while `set_font()` changed the active face without including the selected face in the key.
- Fix: Key the glyph cache by the active font-selection tuple and clear it only when that tuple changes.
- Prevention: Keep font selection and glyph-cache identity coupled; retain both same-font cache-retention and font-switch regression tests.

## 2026-09-22 — Runner measured with one font and rendered with another

- Symptom: A fast render could fit text using the configured/default English font, then rasterize it with the fallback font chain.
- Root cause: Shape-aware placement selected `get_default_eng_font()`, but the later dispatch received `MangaTranslator.font_path=None` and reset `text_render` to fallback fonts.
- Fix: Resolve one active font before placement and pass it to both the translator and final renderer; `--solver-report` now reports resolved faces, sizes, fallbacks, and metrics.
- Prevention: Treat the resolved font path as render-pass state; never let measurement and rasterization independently choose a font.

## 2026-09-22 — Shape-aware solver selected blank rows inside paragraphs

- Symptom: Rendered translations could contain one or more blank rows between otherwise continuous lines of text.
- Root cause: The row DP still treated every usable row as optional, so shape scoring could select a sparse subset and `_center_layout_block()` could only translate the already-gapped paragraph.
- Fix: Row discovery now retains unavailable bands instead of truncating; usable rows must carry text after paragraph start, and actual line-to-line deformation receives a spring penalty.
- Prevention: Keep paragraph word breaking continuous, reserve empty-row traversal for geometry-forced bands, and test irregular bubbles with lower lobes.

## 2026-09-22 — Pipeline runner render command latency and multi-thread MPS deadlock

- Symptom: Running `python devscripts/pipeline_step_runner.py render` took over 65 seconds per page or hung indefinitely when processing sample datasets.
- Root cause: (1) `solve_layout` returned all valid candidate wrappings (32+ candidates) when `top_k > 1` instead of slicing to `top_k`. In `_choose_joint_layout`, evaluating the Cartesian product across multiple regions ($32 \times 32 = 1024$ combinations) repeatedly re-rasterized full-resolution glyph masks and computed full-page morphological dilations thousands of times in Python. (2) `CompatUnpickler` failed when unpickling `step_data.pkl` because `LobeGraph` was serialized with `__main__` as its module path, triggering a fallback that re-read and re-computed everything from raw image files. (3) `execute_fast_render_batch` defaulted to `concurrency=4` threads; on Apple Silicon MPS / multi-stage text rendering, concurrent worker threads serialized and deadlocked across `MangaTranslator` event loops and `_RENDER_LOCK`.
- Fix: (1) Truncated `solve_layout` return value to `valid_candidates[:top_k]`; (2) Cached rasterized candidate glyph masks and bounding boxes once per candidate before joint layout optimization; (3) Added `__main__` and `devscripts.pipeline_step_runner` class resolution in `CompatUnpickler`; (4) Defaulted `render` concurrency to `1` for single-stream GPU/MPS stability.
- Prevention: Always cache candidate geometry masks before Cartesian product combinatorial optimization, ensure unpicklers resolve top-level CLI classes, and default GPU/MPS rendering concurrency to 1.

## 2026-09-22 — Speech bubbles rendered empty due to artificial preferred font size lower bound in solver

- Symptom: Running `python devscripts/pipeline_step_runner.py render` produced `rendered.png` images with empty speech bubbles for datasets like `Page-02` and `shot-mub8j6l3`.
- Root cause: `apply_shape_aware_bubble_layout` computed `preferred_min = max(int(np.ceil(target * 0.85)), target - 3, minimum)` and passed `font_size_min=preferred_min` to `solve_layout()`. When text required a slightly smaller font size (e.g. size 19 when target was 24), the solver aborted early and marked the region as `requires_compression` instead of exploring down to `minimum`. The legacy `prepare_bubbles` fallback then skipped these regions because `_bubble_mask` was unset.
- Fix: Passed `font_size_min=minimum` directly to `solve_layout()`. Because `_composite_penalty` already has a monotonic `p_font` penalty favoring larger font sizes near the target, allowing the solver to check down to `minimum` enables finding the optimal valid layout without artificial failures.
- Prevention: Allow the layout solver search bounds to span the full valid range `[minimum, target]`; rely on composite objective penalties to balance typography quality rather than hard truncating the candidate search space.

## 2026-09-22 — Watershed lobe seeds were initially blocked by background markers

- Symptom: Synthetic connected bubble masks reported lobe centers but watershed output left most interior pixels unlabeled.
- Root cause: The watershed marker image initialized every pixel as the background marker instead of leaving the cleaned mask as unknown.
- Fix: Initialize markers to zero, mark only outside-mask pixels as background, then seed the candidate centers.
- Prevention: Keep watershed background, unknown, and foreground marker states distinct; retain the connected-mask regression test.

## 2026-09-22 — Pipeline runner step data unpickle failed on NumPy cross-version imports and render lock deadlocks

- Symptom: `python devscripts/pipeline_step_runner.py render` failed with `Failed to load step_data.pkl (No module named 'numpy._core.numeric'), falling back to disk files`, and subsequent layout/rendering calls deadlocked or ran slowly.
- Root cause: (1) `step_data.pkl` serialized NumPy arrays under NumPy 2.x (which references `numpy._core.numeric`); when unpickling across environments with differing NumPy module paths, standard `pickle.load` failed. (2) `_RENDER_LOCK` was initialized as a non-reentrant `threading.Lock()`, causing deadlocks when nested functions (`apply_shape_aware_bubble_layout` calling `prepare_bubbles` or `dispatch_rendering`) acquired the lock on the same thread. (3) `BubbleGeometry` and `band_intervals` processed full-page arrays without bounding-box cropping or vectorized row reductions, causing CPU-bound rendering latency.
- Fix: (1) Added `CompatUnpickler` in `pipeline_step_runner.py` to dynamically remap `numpy._core` <-> `numpy.core` across NumPy 1.x and 2.x; (2) Changed `_RENDER_LOCK` in `manga_translator/rendering/__init__.py` to `threading.RLock()`; (3) Cropped `BubbleGeometry` to active mask bounding boxes, vectorized `band_intervals` using `np.all(safe[y1:y2], axis=0)`, and replaced scalar coordinate search with analytical quadratic minimization in `_optimize_x`.
- Prevention: Always use `threading.RLock()` for multi-stage rendering pipelines that share font/context state, use `CompatUnpickler` for serialized NumPy arrays, and crop geometry operations to region bounding boxes.

## 2026-09-21 — Bubble solver X-centering compared left edges against slot centers

- Symptom: Rendered multi-line bubble text looked subtly misaligned — lines of different widths were offset even when their computed X positions seemed correct, and the `hyphenate` flag passed into `solve_layout()` had no effect on output.
- Root cause: `_optimize_x()` stored `(L, R_w, slot.center)` bounds but `_x_cost()` computed `(x - c)^2` using the text's *left edge* `x` against the slot *center* `c`; jitter/curvature terms likewise mixed left edges with centers. Separately, OCR hyphen splits (`GOT- TEN`) were fed straight into the layout DP, so the solver arranged around broken words the OCR pipeline had already split.
- Fix: All `_x_cost` terms now operate on line centers (`x + width/2`); a `normalize_words()` stage joins dictionary-verified hyphen splits before layout while preserving true compounds (`TWENTY-ONE`, `SELF-DEFENSE`, `X-RAY`) via a compound-prefix blocklist.
- Prevention: Whenever an optimizer aligns an object against a reference point, assert both quantities are in the same coordinate space (edge vs center); normalize OCR text at the OCR/translation boundary, not inside the layout solver.

## 2026-09-21 — Manga upload failed on valid image files with "Invalid image: filename"

- Symptom: Uploading manga pages (e.g. `Page-01.png`, `Page-02.png`) failed with `Invalid image: Page-01.png` for all items, and clicking "Retry" failed with 404.
- Root cause: (1) `_validate_original_upload` in `server/main.py` enforced a restrictive `SUPPORTED_IMPORT_FORMATS` whitelist, lacked `Image.MAX_IMAGE_PIXELS = None` (raising `DecompressionBombError` on large manga scans and webtoons), lacked `ImageFile.LOAD_TRUNCATED_IMAGES = True`, and suppressed underlying exception details; (2) `retryTranslationItem` in the frontend only targeted server batches and failed when attempting to retry client-side `manga-upload` batches before import completion.
- Fix: Configured `ImageFile.LOAD_TRUNCATED_IMAGES = True`, `Image.MAX_IMAGE_PIXELS = None`, EXIF auto-transposition, robust RGBA/RGB conversion, expanded format whitelist (TIFF, GIF, AVIF, MPO, JPEG2000, TGA, PPM, etc.), and logging in `server/main.py`. Added support for retrying `manga-upload` batches in `App.tsx`.
- Prevention: Ensure image upload validation permits all Pillow-decodable formats, unsets decompression bomb caps for high-res manga images, and test client-side upload retry flows.

## 2026-09-21 — Speech bubble text ink remained on inpainted pages due to tight dilation threshold and CTC OCR omission

- Symptom: `inpainted.png` in `devscripts/data/` for samples like `shot-mub8hgnn` and `shot-mub8g7mv` left intact text lines inside detected speech bubbles (e.g. "WORKING", "HOW DARE HE ACCUSE ME...").
- Root cause: (1) In `prepare_bubble_masks()`, ink connected components inside the bubble interior were only retained if they overlapped a tight $7\text{px}$ dilation around discrete `region.lines` polygons (`nearby = cv2.dilate(selected, np.ones((7, 7)))`). Any words or multi-line text separated by wider line-spacing were discarded from the bubble cleanup mask. (2) `pipeline_step_runner.py` previously defaulted to `48px_ctc` OCR with a confidence threshold that skipped low-prob English lines, and lacked `_prepare_bubble_layout` in the capture sequence.
- Fix: Updated `prepare_bubble_masks` to use a $25\text{px}$ dilation along with the convex hull of region lines for ink component matching within speech bubble interiors. Changed `pipeline_step_runner.py` default OCR to the high-accuracy `48px` Transformer model, set `concurrency=1` for single-stream MPS safety, and added `_prepare_bubble_layout` prior to mask refinement.
- Prevention: Include convex hull ink expansion in bubble mask preparation and verify dark-pixel erasure across all speech bubbles in pipeline unit tests.

## 2026-09-21 — English OCR regions filtered out during dev pipeline capture leaving text on inpainted images

- Symptom: Running pipeline capture on English manga pages produced `inpainted.png` with original text still visible, `mask_final.png` with 0 or near-0 pixels masked, and an unexpected `rendered.png` file in the capture stage directory.
- Root cause: (1) `_run_textline_merge` in `MangaTranslator` filters out detected text if `tag_distance(region.source_lang, target_lang) == 0` unless `config.translator.no_text_lang_skip = True`. When processing English pages with `target_lang="ENG"`, all recognized English textlines were treated as redundant and filtered out. Without active text regions, mask refinement produced empty masks, so inpainting skipped erasing text. (2) `_capture_single_image` ran an initial baseline render call and wrote `rendered.png` during capture instead of reserving text placement and rendering exclusively for the subsequent fast render command.
- Fix: Set `config.translator.no_text_lang_skip = True` and `config.ocr.min_text_length = 1` during the capture phase, and removed the baseline rendering step and `rendered.png` output from the capture stage.
- Prevention: Ensure capture utilities explicitly disable target-language skipping when ingesting pre-translated or English source pages.

## 2026-09-21 — Draft failure cascades spurious `substantial_editor_rewrite` flag

- Symptom: Pro Localization panel shows `unresolved_translation: Missing region ids: [...]` AND `substantial_editor_rewrite` on the same region, even though the editor pass succeeded.
- Root cause: (1) Draft LLM returned JSON that omitted some region IDs; `_regions()` raised `ValueError("Missing region ids: ...")` which fell back to raw Japanese text stored in `item["draft"]`. (2) `edit_story` then compared Japanese draft vs. English final via SequenceMatcher — a cross-language comparison always scores near zero and unconditionally appended `substantial_editor_rewrite`.
- Fix: In `localize_story`, catch `Missing region ids` `ValueError` specifically and retry with per-page sub-batches before falling back; mark `item["draft_failed"] = True` on any Japanese passthrough. In `edit_story`, skip the similarity check entirely when `draft_failed` is set.
- Prevention: Do not run SequenceMatcher draft/final comparison when the draft is known to be raw source text from a fallback path.



- Symptom: Review badge shows "Invalid draft response: Model did not return a JSON object · editor_failed: Invalid editing response: Model did not return a JSON object · low_confidence_story_boundary", and original Japanese is rendered on the page.
- Root cause: (1) Remote LLM requests lacked native API JSON mode (`response_format` / `response_mime_type`) and relied solely on prompt text; (2) Reasoning/thinking models consumed half-sized token budgets (`_MAX_TOKENS // 2`) before emitting JSON, leaving unclosed `<think>` tags and unclosed code fences that broke `_json_object`; (3) Missing closing braces caused `_json_object` to raise before calling `_recover_partial_objects`; (4) `localize_story` fell back to raw Japanese on draft failure, which editor fell back to on editor failure, and no secondary provider fallback was attempted on JSON errors.
- Fix: Set `_professional_json_mode` on translators to pass native `response_format={"type": "json_object"}` (or `response_mime_type="application/json"` for Gemini) and uncap `max_tokens`; updated `_json_object` to strip unclosed `<think>` tags, unclosed markdown fences, and attempt partial recovery even when outer closing braces are missing; added DeepSeek fallback on JSON errors in `_json_request`.
- Prevention: Regression test unclosed thinking tags, unclosed fences, cut-off JSON, and DeepSeek fallback in `test_professional_translation.py`.

## 2026-09-21 — LLM output-token truncation causes entire translation chunk to fail

- Symptom: Professional translation pages render with original Japanese text; review badge shows "Invalid Draft Response Expecting ',' Delimiter Line 1 Column N" and "Editor Failed Invalid Editing Response…".
- Root cause: Large batches produce JSON responses that exceed the model's output-token limit. Python's `json.loads` raises `JSONDecodeError` on the incomplete JSON, `_json_request` retries once then raises, and the except block falls back to Japanese source text for *every* region in the chunk.
- Fix: Added `_recover_partial_objects()` which character-walks the raw response to extract complete inner objects before the cut-off, tags the result `_partial=True`, and returns it. `_json_object` calls it on `JSONDecodeError`. `_regions` detects `_partial` and fills missing IDs with the best available fallback (draft → japanese) plus a `"response_truncated"` review reason instead of raising. UI `REVIEW_REASON_COPY` maps `response_truncated` to a human-readable label.
- Prevention: Reduce batch size if truncation is frequent. Test `_json_object` with a string that has no closing braces and assert partial recovery returns the complete early objects.


## 2026-09-21 — Merged text-region confidence uses page-wide normalization

- Symptom: Confidence persisted for merged text regions does not represent the confidence of that region alone.
- Root cause: `textline_merge.dispatch` divides the region's area-weighted log probability by the sum of areas for every detected textline on the page.
- Fix: Normalize by the sum of areas in the current merged region, producing its area-weighted geometric mean confidence.
- Prevention: Test merged confidence with two regions and assert that adding an unrelated page region does not change either region's score.

## 2026-09-21 — Speech bubble text under-utilized available space and left large blank areas

- Symptom: Translated text in single-lobe and tall speech bubbles shrunk to small font sizes and sat in small top-anchored rectangles, leaving >50% of the bubble interior empty.
- Root cause: (1) `_estimate_adaptive_font_size` targeted a conservative 48% area with a high character denominator; (2) `_evaluate_bubble_layout_candidates` capped `max_font` too low and allowed narrow width factors ($0.45$) exempt from single-word penalties for tall bubbles; (3) `_score_layout_candidate` and `_binary_search_font_size` calculated text height using nominal font size rather than FreeType's $1.15 \times \text{size}$ ascender/descender line height, causing oversized candidates to trigger false `text_does_not_fit` errors; (4) `render()` ignored `_bubble_box` on single-lobe bubbles and re-rendered into clipped sub-boxes.
- Fix: Increased adaptive area target to ~58% with word-length-aware capping, broadened candidate `max_font` and width factors, penalized 1-word lines across all ratios, accounted for FreeType line heights in candidate scoring, and composited `_bubble_box` directly during final rendering.
- Prevention: Add unit tests verifying target font scaling, area fill percentages, and multi-line wrapping in tall bubbles.

## 2026-09-21 — Broad stadium/pill bubbles fragmented into separate lobes by distance plateau peaks

- Symptom: A single continuous broad speech bubble (e.g. 260px wide, 500px tall stadium shape) split a translated sentence across two artificial lobes with an awkward mid-clause break.
- Root cause: Distance transform on elongated stadium, pill, or cylindrical shapes produces multiple local maxima along the longitudinal medial axis even when width is completely uniform and there is no narrowing neck or lateral step.
- Fix: Checked interior span between distance peaks ($min(spans) \ge 0.70 \times min(endpoints)$) and lateral center shift ($dx < 0.5 \times max(r)$), suppressing collinear plateau peaks within the same continuous body while cleanly retaining true stepped arms and pinched necks.
- Prevention: Add unit tests covering single continuous stadium shapes, stepped diagonal bubbles, and 3-lobe vertical pinched shapes to verify correct lobe decomposition.

## 2026-09-21 — manga2eng renderer unconditionally forced text to uppercase

- Symptom: English typesetting using `manga2eng` or `manga2eng_pillow` always rendered all text in uppercase even when lowercase or original mixed case was desired.
- Root cause: `seg_eng()` in `manga_translator/rendering/text_render_eng.py` called `text.strip().upper()`, discarding original casing and preventing case options from taking effect.
- Fix: Changed `seg_eng()` to `text.strip()` and delegated casing transformations to `config.render.transform_text_case()`.
- Prevention: Add unit tests verifying that segmentation and rendering preserve lowercase and mixed-case input when uppercase conversion is disabled.

## 2026-09-21 — Bubble clauses reordered and translated titles overlapped

- Symptom: A clause rendered in reverse vertical order with a large internal gap, while expanded title translations layered over one another.
- Root cause: Every distance-transform peak was treated as a separate bubble lobe and ordered by Japanese OCR proximity; non-bubble placement then retried without collision obstacles to preserve font size.
- Fix: Require a verified mask neck before splitting lobes, order English compartments top-to-bottom, keep non-bubble obstacles hard, and restore the original when no collision-free placement exists.
- Prevention: Cover touching multi-compartment bubbles with misleading OCR order and impossible overlapping title regions in rendering regressions.

## 2026-09-21 — Professional editor pass duplicated the first draft

- Symptom: Completed professional pages showed identical first-draft and editor-pass text even though the editor provider was called.
- Root cause: The first-pass prompt already requested polished, idiomatic human localization, leaving the second pass little to revise; DeepSeek also read its class-level system template directly, bypassing the temporary professional role override.
- Fix: Recast the first pass as an accurate working draft, require the editor to independently compare and rewrite it, and override/restore DeepSeek's direct system-template path during professional requests.
- Prevention: Keep draft and editor prompts role-distinct and test their captured prompts for explicit separation.

## 2026-09-21 — Translation batch sidebar showed original image instead of result upon completion

- Symptom: When a translation job finished in the translation batch sidebar, clicking on a batch page image opened the viewer with the original image instead of the translated result until manually refreshing the browser.
- Root cause: (1) `subscribeServerBatches` SSE handler received `ServerBatchSummary[]` without items and merged them into `translationBatches`, preserving stale in-memory items with `queued` status and missing `resultUrl`/`folder` without auto-refreshing detailed batch items; (2) `loadTranslationBatchDetails` discarded duplicate calls when an in-flight fetch was pending without queuing a post-completion fetch; (3) `BatchCard` skipped detail refreshes on expansion because `hasDetails` was initialized to true on batch upload; (4) `ItemRow` defaulted to original image preview source when items lacked `finished` status.
- Fix: Deliver changed full batch details through `subscribeServerBatches`, fetch details only when a batch is expanded without them, and pass accurate file names to `onOpenLightbox`.
- Prevention: Keep summary and item detail updates on the same SSE stream; cover batch item lifecycle transitions with automated tests.

## 2026-09-21 — 48px OCR dropped detected text from the cleanup mask

- Symptom: A translated region rendered, but one source-Japanese column remained visible, making text detection appear incomplete.
- Root cause: Detection produced 22 boxes, but 48px OCR discarded one box at its confidence gate before text-line merging and mask refinement; mocr retained all 22 through its separate recognition path.
- Fix: `pipeline_step_runner.py` now keeps the detector mask pixels inside all detector polygons and unions that fallback with the refined OCR mask, so OCR-dropped boxes are still erased without masking unrelated page areas.
- Prevention: Preserve detector geometry for masking independently of OCR acceptance, and regression-test detection-to-OCR box counts for partially recognized multi-column regions.

## 2026-09-20 — Bubble translations disappeared after batch preparation

- Symptom: The final image could leave detected speech bubbles blank or render text using the original OCR placement.
- Root cause: Batch rendering rebuilt `Context` from saved OCR/translation JSON, which intentionally excludes private bubble masks; the final path skipped bubble detection and therefore skipped bubble layout.
- Fix: Re-run bubble detection once when an enabled pipeline context has not carried detection state into rendering.
- Prevention: Any state needed after context serialization must either be persisted or explicitly rehydrated before layout/rendering.

## 2026-09-22 — Separate text regions competed for the same bubble center

- Symptom: When a bubble contained multiple ungrouped OCR regions, translated blocks could overlap or collapse toward the bubble center.
- Root cause: Each region independently searched the complete shared bubble interior, so the solver had no knowledge of sibling regions or their original vertical relationship.
- Fix: `pipeline_step_runner.py` now builds source-anchored preferred territories, generates a small candidate set per region, and selects a joint layout with dilated reservation-mask collision rejection and source-gap/order penalties.
- Prevention: Keep multi-region bubble placement joint; test separated top/bottom source regions and verify the rendered blocks retain breathing room.
## 2026-09-20 — Rendered output could contain empty speech bubbles

- Symptom: batch output showed the inpainted page with no translated lettering.
- Root cause: regions marked for review were always excluded from rendering, even when batch preprocessing had already erased their source pixels.
- Fix: retain the pre-inpaint canvas and restore original pixels for failed translation or placement units before rendering successful units.
- Prevention: keep cleanup reversible across batch serialization and test both successful rendering and region-level restoration.

## 2026-09-20 — Valid English translations restored as Japanese

- Symptom: the editor contained English translations, but the final image showed the original Japanese lettering.
- Root cause: detector-backed bubbles inherited vertical OCR geometry and overly conservative boundary checks; layout height also underestimated FreeType ascenders/descenders, so valid English was marked `text_does_not_fit` and restored.
- Fix: let detector geometry drive horizontal translated layout, search narrower bubble widths, and include glyph overflow in height estimates.
- Prevention: test a single vertical Japanese OCR region with a valid English translation and require a non-empty rendered text box.

## 2026-09-20 — Connected speech bubbles restored despite valid English

- Symptom: English was present in editor metadata, but a connected two- or three-lobe bubble rendered its original Japanese.
- Root cause: bubble placement tested only one rectangle at the connected mask's bounding-box center, which can lie in a narrow neck between lobes.
- Fix: detect broad interior peaks, derive safe per-lobe rectangles, flow the translation across them at one shared font size, and reuse that layout in rendering and editing.
- Prevention: cover connected lobes, narrow necks, editor reflow, and final rendering with regression tests.

## 2026-09-21 — Stepped speech bubbles wasted most of their usable interior

- Symptom: English text occupied only the lower half of an L-shaped speech bubble despite ample empty space in its raised arm.
- Root cause: a stepped connected shape was reduced first to one inscribed rectangle and then to one continuous text segment; this exposed only 45% of its safe interior and let wrapping, rather than the two visual masses, decide the text split.
- Fix: use the complete safe mask and conservatively split sustained upper/lower center shifts into separate segments, retaining neck-based partitions for connected oval lobes. Allocate words by measured safe line-slot capacity, center each text block within its lobe, and preserve the original when fixed-size glyphs cannot fit safely.
- Prevention: test stepped diagonal lobes and connected multi-oval masks, requiring semantic balanced splits, local centering, complete text coverage, and no glyphs outside the safe mask.

## 2026-09-21 — Editor lettering escaped validated bubble lobes

- Symptom: after reflow, text crossed a stepped bubble's outline even though the backend layout passed its safe-mask check.
- Root cause: the backend validated FreeType glyph pixels, but the editor redrew only their line coordinates with a different browser font and metrics; saved/exported pixels therefore differed from the validated pixels.
- Fix: persist and return the backend-rendered RGBA segment image, then use it for linked-lobe preview and export. Hide linked segments without a validated image until reflow finishes.
- Prevention: assert preview raster alpha remains inside the saved group mask and keep final/editor lettering sourced from the same rendered pixels.

## 2026-09-20 — Multi-lobe bubble alignment distorted by neck geometry and bounding-box centers

- Symptom: Multi-lobe speech bubble text had uneven left/right padding, appeared squashed or warped, or had glyphs clipped at rectangular edges.
- Root cause: (1) Whole-lobe centroids and bounding-box centers shifted toward connecting necks; (2) Inscribed rects differed in dimensions from variable-width line slots, leading to perspective warping or canvas clipping.
- Fix: Calculated optical center from the actual occupied text lines balancing left and right whitespace clearance, constrained line slots to safe lobe rects, and rendered FreeType glyphs directly at 1:1 scale without homography distortion.
- Prevention: Include test cases for asymmetric multi-lobe shapes verifying uniform font sizing, optical center offsets, and non-clipped segment interiors.

## 2026-09-20 — Metal command buffer assertion abort during multi-worker server inference

- Symptom: Server with `--workers 2` crashed on macOS with `failed assertion _status < MTLCommandBufferStatusCommitted at line 322 in -[IOGPUMetalCommandBuffer setCurrentCommandEncoder:]` and reported leaked semaphores at shutdown.
- Root cause: PyTorch MPS (Metal) backend is not thread-safe for concurrent command encoding across CPU threads. While detectors, OCR, inpainters, and translators executed on the dedicated `SharedModelExecutor` thread, YOLO speech-bubble detection was executed via `asyncio.to_thread` on arbitrary worker threads concurrently with other Metal GPU operations.
- Fix: Integrated bubble detection into `SharedModelExecutor` via `get_model_cache('bubble_detector', ...)`, `@model_operation async def prepare/dispatch/unload`, and routed bubble detection calls in `MangaTranslator` through `_mps_call`.
- Prevention: Ensure all neural network inference (including auxiliary YOLO models) is routed through `SharedModelExecutor` and `_mps_call` so GPU kernels are never encoded concurrently across threads.

## 2026-09-20 — 'NoneType' object has no attribute 'ctx' during textless image preparation

- Symptom: Batch translation of pages without detected text (e.g. splash pages or textless art) failed with `'NoneType' object has no attribute 'ctx'`.
- Root cause: In `prepare()`, when `_translate_until_translation` detected no text regions, it short-circuited through `_revert_upscale` and emitted `finished: True`, which invoked `release_runtime()` and cleared `self._pipeline_run = None`. Returning to `prepare()`, an unguarded `self._pipeline_run.ctx = ctx` raised an AttributeError.
- Fix: Guarded `self._pipeline_run.ctx = ctx` with `if self._pipeline_run is not None:`.
- Prevention: Guard all lifecycle-dependent references to `_pipeline_run` across pipeline stages and test textless page pre-processing.

## 2026-09-21 — Review editor hid its decision path

- Symptom: Review-flagged text covered the preserved original, while the reason and save/approve actions were clipped or unclear in the editor.
- Root cause: The editor rendered flagged translations before reviewer resolution and placed review actions in an overflowing header/inspector layout.
- Fix: Keep unresolved flagged regions out of the preview/export, map review reasons to readable guidance, and move resolution and save controls into a full-width footer.
- Prevention: Keep the browser review fixture covering original-pixel preservation, reason visibility, draft save, and approval enablement at the target viewport.

## 2026-09-20 — Detector enum validation error with 'manga_text_detector'

- Symptom: Translation requests containing `detector.detector = "manga_text_detector"` failed with `1 validation error for Config detector.detector Input should be 'default', 'dbconvnext', 'ctd', 'craft', 'paddle' or 'none'`.
- Root cause: `Detector` enum lacked `_missing_` alias mapping for legacy/alternate detector names (such as `manga_text_detector`, `comic_text_detector`, `paddle_rust`, etc.).
- Fix: Added `_missing_` classmethod to `Detector` (and other configuration enums) to normalize and map aliases and case variants to canonical enum members, and updated `get_detector` / `unload` to accept and coerce string inputs.
- Prevention: Implement `_missing_` mapping on configuration enums for backward compatibility with external clients and legacy options.

## 2026-09-23 — Checkpoint cache retained every processed page

- Symptom: Long manga batches released decoded images but kept every `PipelineRun` manifest and document set in the process-wide active-run cache.
- Root cause: OCR and single-page checkpoint stages cleared only `ctx` and `translator`, leaving the run registered in `ACTIVE_RUNS` after its checkpoint was persisted.
- Fix: Release each run after its stage checkpoint so later stages reload the saved documents on demand.
- Prevention: Keep image and checkpoint runtime state bounded to the current page group.

## 2026-09-23 — Scheduler checkpoint fields lost before worker execution

- Symptom: A selected-stage retry loaded its deleted input file, and normal page processing repeated detection while resetting the page timer; legacy items with a saved initialization checkpoint also failed to resume.
- Root cause: `BatchStore._to_dto()` omitted scheduler-control fields `retryFromStage` and `pipelineStage`, so workers lost the requested resume point and treated checkpointed pages as new. Existing items already saved at initialization also needed their next pending stage recovered from the saved manifest.
- Fix: Preserve both fields in the batch item DTO and resume existing initialization checkpoints at their first pending stage.
- Prevention: Keep scheduler-control fields in the DTO and cover the request-to-worker path.

## 2026-09-23 — Layout preview worker missed its NumPy import

- Symptom: Layout preview requests failed after layout completed with `NameError: name 'np' is not defined` while checking the rendered box.
- Root cause: The thread worker imported OpenCV locally but referenced NumPy without importing it.
- Fix: Import NumPy in the layout worker.
- Prevention: Keep endpoint tests that execute the worker through its final response construction.

## 2026-09-23 — Stage retry reran unrelated downstream work

- Symptom: Retrying from Translation repeated mask generation and inpainting even though their inputs had not changed.
- Root cause: Checkpoint retry followed the displayed stage list as a linear suffix instead of using the canonical dependency graph.
- Fix: Retry now selects the dependency closure; OCR regrouping reuses saved bubble geometry, and text-grouping changes invalidate mask and layout.
- Prevention: Test retries from Translation and OCR against the dependency graph, including preserved independent artifacts.

## 2026-09-23 — Normal batch checkpoints were absent from canonical stage tables

- Symptom: Normal batch pages lacked canonical stage/artifact records, and later checkpoint updates could leave `get_document()` returning stale structured JSON.
- Root cause: Result indexing only imported JSON sidecars present on disk; checkpoints could be virtual rows on a temporary `pipeline_runs` record, while later page-owned saves continued writing recognized JSON to the compatibility table.
- Fix: Import virtual checkpoint documents before removing the temporary run, route page-owned structured JSON through revisioned rows, register completed disk artifacts, seed stage state/history from newer terminal checkpoints, and re-index an existing page after a retry fails so the failed attempt is durable.
- Prevention: Keep tests that index virtual run documents, update a checkpoint, and fail a page retry, verifying canonical stage state, active structured output, and artifact references.

## 2026-09-23 — Mask and layout work blocked the API event loop

- Symptom: Expensive mask construction and shape-aware layout could delay unrelated FastAPI and Studio requests while a page was processing.
- Root cause: CPU-heavy OpenCV and geometry functions ran synchronously inside async pipeline methods; awaiting an async wrapper did not move that work off the event loop.
- Fix: Added separate bounded interactive and background CPU lanes, limited OpenCV native threads to one, and routed production mask/layout work plus editor previews through the lanes.
- Prevention: Keep executor tests for event-loop responsiveness, single-worker background limits, and interactive progress while a background task occupies its slot.

## 2026-09-23 — Fast translation groups retained every decoded page image

- Symptom: Large fast-translation groups could keep one decoded source image per page resident during text translation.
- Root cause: Deferred loading was enabled only for professional mode even though context-based fast translation consumes OCR regions and settings, not source pixels.
- Fix: Keep source paths on the contexts for both translation modes and decode each page only inside the semaphore-bounded render worker.
- Prevention: Test fast and professional groups for zero decoded inputs during translation, one open per rendered page, and the render concurrency ceiling.

## 2026-09-23 — Translation-dependent mask work ran before translation

- Symptom: Batch preparation built masks and inpainted pages before translated text was available, and a slow earlier page could hold ready fast-translation pages behind it.
- Root cause: Mask generation and inpainting were included in the pre-translation stage list, while fast-group selection stopped at the first unready batch item.
- Fix: Stop preparation at bubble geometry, form fast groups from ready pages in batch order, and build masks, layout, and inpainting after translation. Clear heavy per-page render state when each bounded render worker finishes.
- Prevention: Cover preparation stopping at the translation barrier, ready-page grouping around a delayed page, and render-state cleanup.

## 2026-09-23 — Full pipeline rerun bypassed batch translation dispatch

- Symptom: A full rerun could route translation outside the shared serialized model executor and write intermediate output into a new live page folder.
- Root cause: The rerun path used the public `translate()` wrapper instead of the batch's underlying translation dispatch and did not route output through its staging directory.
- Fix: Reuse the batch translation dispatch inside the atomic rerun staging workspace and restore translator state after the run.
- Prevention: Verify rerun translation dispatch and that a failed or staged rerun leaves live page output untouched.

## 2026-09-23 — Batch rendering blocked the API event loop

- Symptom: CPU-heavy final text rendering could stall unrelated API and Studio work while a batch page finished.
- Root cause: The async rendering wrapper called its CPU renderer directly on the event-loop thread.
- Fix: Run page rendering through the existing bounded CPU lanes and mark batch page renders for the background lane.
- Prevention: Verify batch render contexts select the background lane and keep API-facing CPU work off the event loop.

## 2026-09-23 — Cached inpainted image had no matching erasing mask

- Symptom: A page failed inpainting with `No erasing mask for detected text` even though its result folder contained an inpainted image.
- Root cause: Post-translation mask generation was skipped when an inpainted image existed, even if the corresponding final mask was missing or stale.
- Fix: Treat a missing mask as an incomplete artifact pair: discard the cached inpainted image, rebuild the translation-dependent mask, and rerun inpainting.
- Prevention: Cover the cached-image/no-mask state and require mask generation before inpainting.
