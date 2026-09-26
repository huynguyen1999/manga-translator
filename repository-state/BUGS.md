# Bug lessons

Record bugs when they are discovered, not only after they are fixed. Use the smallest useful entry:

## 2026-09-26 — Inpainting rejected text that should remain untouched

- Symptom: A page failed capture with “No erasing mask for detected text” even though the uncovered regions were already suppressed for review or had an unchanged translation.
- Root cause: Inpainting required mask coverage for every OCR polygon, including text that the renderer restores from the source, while unchanged translations were still sent through drawing.
- Fix: Skip mask enforcement for reviewed suppressed/unchanged regions and restore unchanged filtered text without redrawing. Strict render diagnostics now accept reviewed suppression and do not require the temporary free-text search zone after layout cleanup.
- Prevention: Keep source restoration, mask requirements, and layout workspace lifetime aligned; test pages containing suppressed text and unchanged labels.

## 2026-09-26 — Translated regions can finish without validated visible output

- Symptom: Layout could suppress a translation without review, accept a bubble-bubble overlap, or miss clipping/collisions from raster-only output, thick outlines, or post-layout transforms.
- Root cause: Validation inspected line boxes before final transforms and did not consistently inspect the renderer's actual alpha footprint or propagate every failure to region review state.
- Fix: Final validation reconstructs and transforms the segment/fallback raster with the effective outline, checks page/panel/bubble bounds and all region-pair collisions, and suppresses invalid placements with a review reason. Invalid frozen output and failed/empty compositing are also flagged. Focused render/layout coverage passed (155 tests, 14 warnings, 2 subtests).
- Prevention: Keep final-output checks on raster alpha and transformed points; do not infer visible output from a valid layout record alone.

## 2026-09-26 — Short vertical render destinations divide by zero

- Symptom: For nonempty vertical text, positive font size, and destination height smaller than that size, `put_text_vertical()` could divide by zero.
- Root cause: `num_char_y = h // font_size` could be zero and was used as a divisor.
- Fix: The renderer now guards short destinations and returns a failed placement instead. The focused vertical-render mitigation test passed.
- Prevention: Keep coverage for destinations below one glyph height and degenerate/clipped destination polygons.

## 2026-09-26 — Failed rendering retains temporary content overrides

- Symptom: A render exception leaves `_render_content_override` attached to input regions.
- Root cause: `render_page()` removes temporary overrides only on successful return, without `finally` cleanup.
- Fix: Pending. Clean temporary overrides on both success and failure.
- Prevention: Inject a rendering failure and assert region transient state is cleared before retrying with edited content.

## 2026-09-26 — Moebius web inpainting output cropped into top-left quadrant on black background

- Symptom: Running inpainting in the Moebius web UI generated an output image shrunken into the top-left quadrant with the rest of the canvas filled in black.
- Root cause: The Moebius diffusion pipeline (`RemovalSDXLPipeline_BatchMode`) automatically preprocesses square padded inputs down to 512x512 and outputs a 512x512 image. The web API applied the original high-resolution unpadding bounding box (`crop_box`) directly to the 512x512 output without upscaling it back to the padded dimensions first, causing out-of-bound crop coordinates that PIL filled with black.
- Fix: Scaled `result_sq_img` back up to the original square size (`sq_img.size`) via Lanczos interpolation before applying `crop_box`.
- Prevention: Always symmetrically inverse all coordinate scaling and letterboxing transforms applied before neural network inference.

## 2026-09-26 — Two bubble translations rendered into the same area

- Symptom: On page `8f1548b6-2436-4bbe-a99e-7269874fbaac`, region `cc93e3a1aa28432680a9408107dddbf0` overlaps region `2a48bd9d494e4693874cf94a3250e402` in the final image.
- Root cause: OCR/grouping and translation artifacts preserve the correct ID-to-text pairs. Bubble detections 7 and 10 cover nearly the same source area for the first region, while detections 8–10 overlap around the second. Per-region maximum-overlap association assigns different bubble IDs, so layout solves them in separate groups. Final validation records the exact render collision but only suppresses collisions involving `FREE_TEXT`; both regions are `BUBBLE` and remain drawable.
- Fix: Rank bubble candidates by source coverage multiplied by detector confidence, while retaining the existing minimum-coverage cutoff. This makes near-equal coverage prefer the stronger detection.
- Result: The association regression passed. An isolated layout/render preview with the corrected `bubble_7` and `bubble_8` assignments produced 25/25 layouts and no collision errors, warnings, or suppressed regions; both target translations rendered in separate bubbles.
- Prevention: Keep near-equal coverage association covered and require layout validation to report no unresolved render collisions before a preview is accepted.

## 2026-09-26 — Suppressed bubble text was restored underneath another translation

- Symptom: A drawable bubble translation overlapped original text restored from a different suppressed OCR region in the same balloon.
- Root cause: Final collision validation compared drawable translations with each other, but ignored source pixels that `render_page()` restores for suppressed regions.
- Fix: Final raster validation now treats suppressed or unchanged source text as an obstacle and suppresses any overlapping translation with a review reason.
- Prevention: Validate translated raster footprints against both drawable output and restored source geometry.

## 2026-09-26 — Free-text inpaint masks were clipped by nearby bubble geometry

- Symptom: Free-text OCR regions beside a speech bubble had no final erase-mask coverage, so later layout could not place their translations.
- Root cause: Mask growth restricted any connected text component that touched any bubble interior, even when its source region was free text and unowned by that bubble.
- Fix: Apply bubble-interior clipping only when the component belongs to bubble-associated OCR; free-text source pixels remain erasable while protected bubble edges stay hard constraints.
- Prevention: Cover mixed free-text and bubble geometry in mask-growth tests and replay pages with adjacent UI text.

## 2026-09-26 — Legacy bubble fallback enlarged calibrated source typography

- Symptom: A long translation was rendered much larger than its calibrated OCR font size after the shape-aware solver failed.
- Root cause: The legacy bubble fallback raised a known calibrated target using its adaptive translation-length estimate.
- Fix: Reuse calibrated source size as the fallback target; adaptive enlargement remains available when no calibrated size exists.
- Prevention: Test both adaptive legacy sizing and calibrated-size preservation on large bubbles.

## 2026-09-26 — Below-floor free text was flagged but still painted

- Symptom: A translated phone label was drawn at 9 px against a 25 px source estimate; diagnostics marked it for review but left it visible.
- Root cause: Final readability validation set `review_required` without suppressing raster output, and the layout contract substituted the final font size for a missing calibrated size, making its ratio appear to be 1.0.
- Fix: Preserve the OCR source size as the layout calibration fallback and suppress/restore the source when output falls below 85% of that target.
- Prevention: Build the final layout record from a region whose chosen font is below its source estimate and assert both the calibration and suppression.

## 2026-09-26 — Single-page pipeline rerun expanded to the full manga group

- Symptom: Rerunning from Page Detail queued every page in that manga instead of only the open page.
- Root cause: The Page Detail action passed both a one-page image selection and its `groupId`; the submit hook treated `groupId` as a request to rerun the entire group and discarded the selected page IDs.
- Fix: Always submit the selected `pageIds` from the rerun dialog.
- Prevention: Keep selected-page actions scoped to the explicit page IDs even when group context is available for display.

## 2026-09-26 — Page Detail viewer delayed image display with dark loading overlay

- Symptom: Opening Page Detail modal displayed a dark backdrop with "Loading image..." for several seconds before showing the translated page.
- Root cause: `PageDetailModal` only checked `image.batchPreviewUrl` for `resultPlaceholder`. When pages were opened from direct routes or Studio without an explicit `batchPreviewUrl`, `resultPlaceholder` resolved to `null`, causing `PreviewImage` to display a blocking dark overlay (`bg-zinc-950/90`) instead of utilizing cached preview/thumbnail variants. Additionally, `useGalleryPageModalRoutes` omitted image variant URLs from API payloads, and `PreviewImage` reset `resultLoaded` to false on prop updates without checking cached DOM image completeness.
- Fix: Expanded `resolveImageUrls` to resolve `resultPlaceholderUrl` falling back across batch, thumbnail, preview, cover, and folder-based variants; mapped variant URLs in `useGalleryPageModalRoutes` and `buildStudioPreviewImage`; and checked DOM `complete` status in `PreviewImage` on render updates.
- Prevention: Always provide progressive placeholder fallbacks for modal image stages and check cached image element completeness upon mount and prop updates.

## 2026-09-26 — Bright-pixel bubble fallback displaced translated regions

- Symptom: On page `1790362270755-c1a677ac-2048-ENG-sugoi`, an unmatched Japanese sentence moved across a panel. On `1790397971088-149a82a2-2048-ENG-sugoi`, four of six translations were suppressed and layout took about 29 seconds; some failures had no review flag.
- Root cause: Bright-component inference ran despite saved bubble detections that did not match those OCR regions. Free-text damage scopes also used raw OCR font estimates, and empty ownership zones still entered expensive candidate search.
- Fix: Persisted detections are reattached to loaded regions; bright-component inference runs only when no saved detections exist. Scope dilation uses calibrated source geometry, empty ownership skips search, and every suppressed free-text failure requests review. Regression coverage passes. A temporary page replay took about 9 seconds but still suppressed overlapping region `f45bb050d79c413085b353c365c70b2e`; the live preview was running stale code, so that page is not verified fixed yet.
- Prevention: Cover reruns with saved-but-unmatched detections, extreme OCR estimates, overlapping source regions, and assert that each suppression has a review reason. Verify page previews against the current workspace build.

## 2026-09-26 — Stopping batches resumed after PostgreSQL restart

- Symptom: A batch still marked “Stopping…” resumed its remaining pages after the server restarted.
- Root cause: Startup reconciliation requeued processing items and changed the stopping batch to waiting.
- Fix: Both stores now delete batches with a persisted stopping state during startup recovery.
- Prevention: Honor persisted stop intent before recovering interrupted item work.

## 2026-09-25 — UI mixin filename matched pytest discovery

- Symptom: Root pytest collection imported `main_window_visual_test.py` as a test and failed because the test environment has no PySide6.
- Root cause: The extracted UI mixin filename matched pytest's `*_test.py` pattern.
- Fix: Renamed it to `main_window_visual.py` and updated the application import.
- Prevention: Avoid test-discovery filename patterns for production modules.

## 2026-09-25 — Layout solver extraction omitted math import

- Symptom: Layout candidate tests raised `NameError: math is not defined` after moving the solver into `solve_core.py`.
- Root cause: `_line_height` moved with the solver, but its `math.ceil` dependency did not.
- Fix: Import `math` in the extracted module.
- Prevention: Audit nonlocal names referenced by moved functions and run their focused tests immediately.

## 2026-09-25 — DPM-Solver sampling needs an explicit cumulative-sum axis

- Symptom: A DPM-Solver sampling smoke test raises `TypeError: cumsum() missing 1 required positional argument: dim`.
- Root cause: The existing vendored solver calls `torch.cumsum` without an axis, which the installed PyTorch API requires.
- Fix: Pending; pass `dim=0` at the unchanged timestep-order calculation. Kept separate from this extraction to avoid mixing solver behavior changes into refactoring.
- Prevention: Keep a tiny DPM-Solver sampling smoke test alongside import and helper checks.

## 2026-09-25 — Document repository bypassed the owner-resolution compatibility hook

- Symptom: Existing document persistence tests that replace `PostgresStore._document_owner` failed after moving the SQL into `DocumentRepository`.
- Root cause: The extraction called the new repository implementation directly, bypassing the facade hook used by tests and runtime callers.
- Fix: Keep document persistence calling the facade hook; the facade delegates to the repository's SQL resolver.
- Prevention: Preserve injectable compatibility seams when moving repository internals and test them through the existing public facade.

## 2026-09-25 — Extracted batch translation omitted the GPT translator registry import

- Symptom: Batch translation raised `NameError: GPT_TRANSLATORS` before offline translator dispatch, then fell back to the per-page path.
- Root cause: The extracted batch translation module kept the registry lookup but imported only the dispatch functions.
- Fix: Import the existing registry and cover the offline batch dispatch path with a focused test.
- Prevention: After moving methods, audit each referenced module global in the destination module and exercise the public batch path.

## 2026-09-25 — Linked layout preview contract used boxes outside its source canvas

- Symptom: The layout-preview contract returned `fits: false` for two linked boxes.
- Root cause: The shared API fixture used a 64×64 source image while the boxes extended to x=230 and y=90, so the layout could not place text within the image.
- Fix: Give this test a 320×180 source canvas; the request and layout behavior stay unchanged.
- Prevention: Layout fixtures must keep requested regions inside their source-image dimensions.

## 2026-09-25 — Extracted translation entry point retained a method receiver reference

- Symptom: Model-preload code in the extracted function still referenced `self`, which would fail when the helper ran.
- Root cause: The mechanical extraction changed `self.attribute` references but missed standalone `self` uses passed to `getattr`.
- Fix: Replace all method receiver references with the injected `owner`.
- Prevention: Compare the full executable AST after normalizing receiver names and run the public entry-point tests.

## 2026-09-25 — Clean-canvas renderer test skipped its renderer

- Symptom: The renderer isolation contract expected a mutating test renderer to run, but received the untouched canvas.
- Root cause: Its synthetic region had a language but no translation, so production rendering correctly filtered it out before dispatch.
- Fix: Give the synthetic region drawable translation text.
- Prevention: Renderer dispatch tests should use a region with actual renderable content.

## 2026-09-25 — Suppressed regions were not restored before rendering

- Symptom: Suppressed free-text, bubble, and untranslated review regions rendered over inpainted pixels instead of preserving the source image.
- Root cause: The render extraction narrowed the restore list to preserved/review regions, excluding ordinary regions marked `_render_suppressed`.
- Fix: Keep every suppressed region in the source-restoration list and retain the untranslated review fallback.
- Prevention: Keep render lifecycle tests for suppressed, untranslated, and drawable regions against the public `render_page` entry point.

## 2026-09-25 — Translated free-text regions were suppressed during layout

- Symptom: Regions `df4134bc052446919c6dce42e55d5141` and `2a68426d33784f49bd35efe477ebd9f4` have translations but no rendered text.
- Root cause: Free-text candidate ranking could discard the smallest viable wrap, collision search could choose a placement that blocked a neighboring region, and inferred bubble detection could mistake a white glyph outline for a bubble and leave only a narrow safe area. Final validation then suppressed invalid or unreadable placements. Later, source height was used as a scoring preference and maximum only, so successful text blocks could still be shorter than their source regions. The taller text stack was then vertically centered and rigid placement targeted the damage centroid, leaving empty space above the first row.
- Fix: Retain the smallest typography candidate, reject inferred bubble components smaller than the source text footprint, preserve placement alternatives, solve independent collision groups and backtrack in dense groups, and make source height a hard minimum for translated region bounds (capped at 1.5×), keep natural line spacing, start the stack at the top of source-height bounds, and center it horizontally on the source region. When an available target is wider than its source region, wrap against the source width and permit overflow only for an unbreakable word.
- Result: The isolated layout/render laid out all 17/17 regions with 0 suppressed, no empty rows, errors, or warnings. The first region expanded from 3 source rows to 4 natural rows (6 px visible gaps); source bounds were 186×397 px and translated bounds 229×397 px, with the widest word setting the remaining width. The second expanded from 2 rows to 3, preserving its 152 px source height (73×152 px source, 98×152 px translated bounds).
- Follow-up (2026-09-26): On page `1790356430205-f83c51d9-2048-ENG-sugoi`, the saved layout started the first row at y=154 while the source began at y=52. The isolated layout rerun starts the first row at y=59 with 6 px natural gaps; all 17 regions render with none suppressed or blank.
- Follow-up (2026-09-26): On page `1790360962376-525491c9-2048-ENG-sugoi`, region `744ed9b863f34be8b63f1aae6b0321e7` had a saved translation but no render. Its 180×268 source quad supplied `font_size=180`; the free-text candidate generator returned zero typography candidates under both the saved page-wide fallback constraint and no panel constraint. Geometry-based source-size calibration below addresses this oversized estimate.
- Prevention: Keep text-region bounds distinct from glyph bounds, enforce the source-height minimum, top-anchor the natural line stack, and center it horizontally on the source region. Verify saved-page dimensions and rendering after layout changes.

## 2026-09-25 — Pipeline rerun progress used the database pool from the translator loop

- Symptom: A rerun executed by the in-process translator failed when progress attempted to persist batch state, with an asyncpg future attached to a different event loop.
- Root cause: The translator runs pipeline callbacks on its dedicated event loop, but the rerun progress callback mutated the PostgreSQL-backed batch store directly from that loop.
- Fix: Marshal rerun progress updates back to the scheduler loop that owns the store; add a loop-bound regression test.
- Prevention: Any callback that crosses from the translator loop into scheduler or database state must return to the owning loop before awaiting those stores.

## 2026-09-25 — One-off AI previews were persisted as rerun batches and result folders

- Symptom: A failed debug preview left copied `ai-case-*` files and failed batch records behind.
- Root cause: The preview endpoint reused the durable batch scheduler and result-folder workflow for a one-off human review run.
- Fix: Execute the existing rerun plan synchronously against a `TemporaryDirectory`; return the rendered image and JSON artifacts, then let the context manager remove all scratch files.
- Prevention: Keep endpoint tests asserting no case folder or batch write, and a runner test asserting temporary files disappear after success.

## 2026-09-25 — Rendering extraction dropped CPU priority compatibility exports

- Symptom: The background-render test could not import `CPU_PRIORITY_BACKGROUND` from `manga_translator.manga_translator` after moving rendering orchestration.
- Root cause: The new stage module imported the CPU lane constants directly, removing their prior incidental module-level exports.
- Fix: Keep those constants imported in `manga_translator.manga_translator` and pass its `run_cpu_stage` and `render_page` bindings to the extracted stage.
- Prevention: Preserve module-level names that existing callers or tests import when extracting methods into stage modules.

## 2026-09-25 — Direct gallery page links stayed on the loading skeleton

- Symptom: Opening a saved page URL in a fresh tab showed “Loading library…” indefinitely instead of the page viewer.
- Root cause: Gallery loading initialized to `true`, while page overlay routes intentionally skip the gallery summary request that normally clears it; the skeleton returned before rendering the page modal.
- Fix: Clear the gallery loading flag on routes that skip the summary request; normal gallery loading remains unchanged.
- Prevention: Do not let overlay routes inherit a pending list-load state when that route does not request the list.

## 2026-09-25 — Direct page viewer was hidden by the empty-gallery branch

- Symptom: The detail request succeeded and opened viewer state, but an empty gallery rendered “No manga yet” without the viewer.
- Root cause: The empty-library early return ran before shared route overlay markup.
- Fix: Keep the empty-library return for ordinary gallery routes; render route-driven page, edit, and reader overlays through the shared return.
- Prevention: Early empty-state returns must not skip route-owned overlays.

## 2026-09-25 — Direct page links briefly showed an empty gallery

- Symptom: A saved page URL flashed “No manga yet” while its detail request was still resolving.
- Root cause: The ordinary gallery empty state rendered before the route's target page had opened in the modal.
- Fix: Show an opening state until the requested page is ready; add links from the viewer to its manga and Studio home.
- Prevention: Keep route-driven loading states visible until their route data is ready.

## 2026-09-25 — Mechanical extraction truncated a helper definition

- Symptom: Python compilation and layout-test collection failed on `_source_region_snapshot`.
- Root cause: The extraction script sliced the source two characters past the newline, dropping the first letter of `def`.
- Fix: Restore the missing `d`; compile and the affected tests then passed.
- Prevention: Compile mechanically extracted modules before running pytest.

## 2026-09-25 — Test imports replaced the real translator package

- Symptom: Full-suite collection imported `manga_translator` and `manga_translator.translators` as incomplete stubs; later tests saw missing exports or a mocked `MangaTranslator`.
- Root cause: Translator tests wrote package and provider mocks into `sys.modules` at module import time and left them there.
- Fix: Import the real package where possible, scope the Gemini package stubs to that test module's setup/teardown, and remove temporary imported submodules afterward.
- Prevention: Keep test-only `sys.modules` replacements scoped and restore the original module entries after use.

## 2026-09-25 — Summary OCR advanced stages between page chunks

- Symptom: Later summary pages were still detecting text while earlier pages had entered OCR or text-line merging.
- Root cause: The summary scheduler invoked the full detect/OCR/merge pipeline separately for each worker-sized page chunk.
- Fix: Run all uncached pages through one extraction call; keep worker-sized inference batches inside the pipeline so each stage completes across the manga before the next begins.
- Prevention: Preserve manga-wide stage barriers when changing summary OCR batching.

## 2026-09-25 — Route contract inspected lazy FastAPI router entries

- Symptom: Route-contract tests reported missing endpoints after `include_router()` even though the routers were registered.
- Root cause: FastAPI 0.141.1 stores included routers lazily in `app.routes`, where those entries do not expose `path` or `methods`.
- Fix: Derive route/method coverage and ordering from the generated OpenAPI paths.
- Prevention: Use the public OpenAPI schema for registered API-surface contracts instead of treating `app.routes` as a flattened list.

## 2026-09-25 — Summary repository imported a group error from the facade

- Symptom: Importing `server.postgres_store` failed while loading the extracted summary repository.
- Root cause: `GroupNotFound` was defined in `postgres_store.py`, so importing it back into a repository created an invalid dependency.
- Fix: Define group errors in `series_repository.py` and re-export them, including the `SeriesStoreError` base class, through `postgres_store.py`.
- Prevention: Keep shared domain exceptions beside their owning repository and make facades re-export every legacy exception name, including base classes.

## 2026-09-25 — Manga repository retained an old module helper reference

- Symptom: Group listing raised `NameError` while formatting the cover page timestamp.
- Root cause: The moved page formatter still referenced the old module-level `_iso` helper instead of the repository's injected formatter.
- Fix: Route timestamp formatting through `self._iso()`.
- Prevention: Check extracted functions for module globals as well as instance attributes, then run the old focused tests against the new module.

## 2026-09-25 — Summary OCR helper name changed during extraction

- Symptom: Importing `server.main` failed because the extracted summary OCR module did not export `summary_input_file`.
- Root cause: A broad helper-call rename also altered the new function's own name.
- Fix: Restore the helper name and preserve only the intended call-site substitutions.
- Prevention: Smoke-test imported helper names and run route-contract collection immediately after mechanical moves.

## 2026-09-25 — Text-grouping extraction omitted a utility import

- Symptom: Numeric-preservation checks raised `NameError` in the extracted text-grouping stage.
- Root cause: The stage body was moved mechanically, but its module-level `contains_linguistic_ocr_text` dependency was not imported into the new module.
- Fix: Import the existing helper from `manga_translator.utils`.
- Prevention: After mechanical extraction, run the original focused tests and verify each moved module's external names resolve in its own scope.

## 2026-09-25 — Extracted result routes captured the store getter

- Symptom: Pipeline-manifest tests ignored persisted stage rows, and the final-image route returned a stale local file when tests replaced `server.main._postgres`.
- Root cause: The route factory captured the original getter function during app import, while the old handlers looked up the module global on each request.
- Fix: Inject deferred callbacks for the store and replaceable helpers.
- Prevention: Preserve runtime module-global lookup behavior with lazy callbacks when extracting handlers; test both disk and store-backed result paths.

## 2026-09-25 — Editor router initialized before its helpers

- Symptom: Importing `server.main` raised `NameError` while constructing the extracted editor router.
- Root cause: The router was registered before `_get_cached_meta`, `_invalidate_meta_cache`, and related helpers were defined, and factory arguments eagerly resolved those names.
- Fix: Pass deferred callbacks for the helpers so they resolve when a request runs.
- Prevention: When registering routers before helper definitions, defer later-defined dependencies instead of evaluating them during module import.

## 2026-09-25 — CBZ export extraction missed the injected file resolver

- Symptom: File-backed CBZ export raised `NameError` after the route moved out of `server.main`.
- Root cause: One `final_file()` call was left behind while the route's file resolver was converted to an injected callback.
- Fix: Route both final-image checks through the injected `result_file` callback.
- Prevention: Search all references to every moved helper and exercise both file-backed and database-backed route paths after extraction.

## 2026-09-25 — Summary progress showed a page index, not stage completion

- Symptom: During summary OCR, the UI could show the current step but not how many pages had completed it; empty-text pages did not visibly advance a text count.
- Root cause: Summary progress stored a source-page index and pages-with-text count, but no per-stage page-completion count; cached text counts were also reset while other pages were extracted.
- Fix: Persist the number of cached and newly processed pages that passed the active image step, and show it beside the current step in Jobs and the open summary view.
- Prevention: Keep page position, stage completion, and text-bearing page counts as separate progress values.

## 2026-09-25 — Gallery cards did not expose shareable detail links

- Symptom: Copying a page-detail destination was awkward, and Command-click on a gallery navigation link also navigated the current tab.
- Root cause: Some gallery destinations used click handlers instead of anchors; Studio/Gallery links also had redundant click handlers that forced same-tab navigation on modified clicks.
- Fix: Use canonical route links, preserve in-app return state, copy the page route from Page Detail, and open the reader route in a new tab when Command/Ctrl-clicking Read. Let the Studio/Gallery links handle ordinary navigation themselves.
- Prevention: Use native links for navigable destinations and avoid additional navigation handlers on them.

## 2026-09-24 — Source Japanese text leaked through untranslated restore regions

- Symptom: Japanese text was pasted back over inpainted areas when a region had missing translation or was suppressed.
- Root cause: `render_page()` restored `img_rgb` source pixels for any region where translation content was empty or suppressed, re-painting Japanese characters onto the canvas.
- Fix: Constrain `restore_regions` so only regions with explicit preservation (`is_preserved_region`) or reviewed regions with explicit `_bubble_restore` can restore original pixels.
- Prevention: Never fall back to repainting raw source pixels for regular linguistic regions without an explicit preservation policy.

## 2026-09-25 — Text-box borders were inferred as narrow manga panels

- Symptom: Three translated free-text regions were detected but omitted from the final page.
- Root cause: Dark borders around vertical narration boxes became paired CV panel boundaries, leaving a usable panel width smaller than the source OCR footprint and no valid layout.
- Fix: Ignore an inferred boundary pair when its margin-adjusted span cannot fit the source footprint, retaining any valid constraint on the other axis.
- Prevention: Validate inferred panel capacity against source geometry before applying it as a hard layout boundary.

## 2026-09-24 — Short vertical Japanese lines were split from neighboring columns

- Symptom: A short vertical OCR line beside a longer Japanese column became a separate translation region.
- Root cause: Initial grouping relied on top/bottom edge alignment, then the MST split stage ranked endpoint distance, so large flow-axis offsets outweighed close column spacing and overlap.
- Fix: Use one orientation-aware pair geometry for textline grouping and MST costs; strong, highly overlapping neighbors cannot be split, while weak aligned edges remain fallback candidates. Verbose runs save pair metrics in `textline_merge_debug.json`.
- Prevention: Keep horizontal and vertical fixtures for nested short lines, flow gaps, typography mismatch, mixed directions, reading order, and noisy bridges.

## 2026-09-24 — Suppressed render regions could mix source and translated text

- Symptom: A free-text region marked for render suppression could show both its original text and a translated overlay.
- Root cause: `render_page()` restored source pixels for all regions before and after drawing, while suppressed regions could still reach the legacy renderer.
- Fix: Partition restore and drawable regions once, restore suppressed (and untranslated review-required) regions before drawing, and exclude suppressed regions from every renderer.
- Prevention: Keep regression coverage for suppressed free text/bubbles, valid review-required fallback rendering, frozen rendering, and runner/Studio pixel parity.

## 2026-09-24 — Free-text translation could cross manga panel frames

- Symptom: Translated free text could spill through a panel divider into an adjacent panel.
- Root cause: Free-text hard validation used a page-wide `panel_mask` and had no source-panel constraint, so text could pass validation in any unoccupied page area.
- Fix: Infer conservative frame-line envelopes from the source page, use panel dimensions when wrapping, clamp source-centered placements, and validate the complete paragraph box against its assigned panel.
- Prevention: Keep synthetic vertical/horizontal divider tests, whitespace-box containment checks, and a solver-level near-divider regression; bubble layout stays on its existing path.

## 2026-09-24 — Studio overlays decoded and filtered oversized content

- Symptom: Jobs thumbnails and Page Detail interactions stalled in software-rendering mode.
- Root cause: Job rows loaded full result images, Page Detail mounted hidden full-resolution sources, the app-root blur covered the full document, and SSE snapshots replaced unchanged batch objects.
- Fix: Use 160px job previews and 1600px detail variants, mount only the active viewer source, scope blur to the viewport overlay, and preserve unchanged batch and item references.
- Prevention: Keep thumbnail and viewer image tiers separate, do not assign `src` to hidden viewer states, scope overlay effects to the viewport, and compare incoming SSE data before replacing memoized objects.

## 2026-09-24 — Bulk page deletion missed the configured API route

- Symptom: Bulk-deleted pages disappeared from the gallery but returned after reload when the frontend used a direct API base URL.
- Root cause: `apiUrl()` removes `/api` for direct-base requests, but the batch-delete endpoint only accepted `/api/results/batch-delete`; the frontend also treated failed responses as success.
- Fix: Register both route forms and keep selected pages visible when the request fails.
- Prevention: Give new API endpoints both prefixed and unprefixed routes when they are called through `apiUrl()`, and do not hide persisted records after a failed delete.

## 2026-09-24 — Free-text fast path stayed disabled in production

- Symptom: Free-text regions always ran exhaustive placement search unless an environment variable was set, and pages with multiple regions never took the fast path.
- Root cause: Early acceptance was opt-in and additionally restricted to a single free-text region.
- Fix: Enable strict-gated ideal placements by default, then use exact joint collision checks to exhaustively expand colliding fast placements; retain `LAYOUT_FAST_FREE_TEXT=0` to disable the ideal stage during debugging.
- Prevention: Treat local candidate generation and early acceptance as independent controls, and validate page-level collision behavior after per-region solving.

## 2026-09-24 — Long bubble words suppressed font rescue

- Symptom: A narrow dialogue bubble rendered much smaller than the rest of the page when it contained one long word.
- Root cause: The shared adaptive font estimate capped the target by the longest word, then production layout compared the compressed result against that already-reduced target.
- Fix: Remove the word cap and compare bubble candidates against a robust page dialogue baseline; retain one dictionary-backed rescue for an 8+ letter bottleneck.
- Prevention: Keep word-fit pressure in wrapping/rescue decisions, not in the preferred page font estimate.

## 2026-09-24 — Concurrent page deletion deadlocked during order compaction

- Symptom: Deleting pages concurrently could fail with PostgreSQL `DeadlockDetectedError` in `_compact_page_order()`.
- Root cause: Delete transactions locked page rows before their manga group, while compaction locked the remaining page rows.
- Fix: Lock affected manga groups in sorted order before locking pages; retry if a page changed groups during lock acquisition.
- Prevention: Acquire group locks before page locks on every path that compacts page order.

## 2026-09-24 — Bulk page deletion repeated group compaction

- Symptom: Deleting selected pages became slow as the selection grew.
- Root cause: The client sent one delete request per page, and each request compacted the same manga group again.
- Fix: Delete selected folders in one request and compact each affected group once.
- Prevention: Batch destructive page operations that share group-level cleanup.

## 2026-09-24 — DeepL merged grouped regions into one text

- Symptom: Grouped page translation sent newline-joined regions to DeepL, so line wrapping or altered newlines could break region-to-translation mapping.
- Root cause: The DeepL adapter collapsed the query list into one string and split the translated string on newlines.
- Fix: Send the query list through the SDK multi-text API and return each ordered result separately.
- Prevention: Keep provider batch inputs and outputs as lists when the API supports per-text results.

## 2026-09-24 — Server shutdown reported a leaked semaphore

- Symptom: The server printed a `resource_tracker` warning about one leaked semaphore at shutdown.
- Root cause: The first `tqdm` progress bar created a multiprocessing `RLock`, although pipeline work uses threads.
- Fix: Configure `tqdm` with a standard thread `RLock` when the package loads.
- Prevention: Keep progress-bar synchronization aligned with the app's thread-based execution model.

## 2026-09-24 — Final translation group stayed pending

- Symptom: A 61-page batch reached 60/61 translated, then left the last page awaiting translation while both workers were free.
- Root cause: The partial-group gate compared ready translation pages with all unfinished pages, including pages already at later stages.
- Fix: Dispatch a partial group once every page still at translation stage is ready.
- Prevention: Treat the configured batch size as a maximum; later stages must not block a final partial translation group.

## 2026-09-24 — Layout stage timing summary stayed at zero

- Symptom: The layout benchmark showed nonzero page time but zero bubble and free-text stage durations.
- Root cause: The render runner populated its summary from a separate timing dictionary instead of `PageLayoutResult.timings`.
- Fix: Copy the measured stage durations from the layout result into the page timing breakdown.
- Prevention: Verify timing reports against the instrumented stage result, not an unconnected accumulator.

## 2026-09-24 — Concurrent free-text raster cache reused another font

- Symptom: Identical text, width, and size could return glyph pixels rendered with a different page's font.
- Root cause: The shared line-alpha cache key omitted the thread-local font selection.
- Fix: Include `FONT_SELECTION_KEY` in the cache key and verify two-font concurrent lookups.
- Prevention: Include all thread-local render state in shared raster-cache keys.

## 2026-09-24 — Concurrent layout reports mixed page counters

- Symptom: Solver workload totals could include work from another page layout running at the same time.
- Root cause: Layout workers incremented one process-global profile object.
- Fix: Scope the solver profile to the current layout context and reset it at `layout_page()` entry.
- Prevention: Keep per-page diagnostics in the same execution context as the page work they measure.

## 2026-09-24 — Candidate raster extraction missed stroke radius

- Symptom: The first candidate-raster parity check failed when constructing the stroke dilation kernel.
- Root cause: The extracted raster helper no longer initialized the local dilation radius.
- Fix: Restore the radius calculation and cover page-clipped masks at 100 placement offsets.
- Prevention: Run exact mask-parity checks whenever shared raster logic is extracted.

## 2026-09-24 — Unpadded image names shifted page labels

- Symptom: `1.jpg`, `2.jpg`, and `10.jpg` could receive page numbers in the wrong order, breaking relevant-page labels.
- Root cause: Page numbers were assigned after lexicographic path sorting.
- Fix: Sort numeric filename segments naturally before assigning page sequence numbers.
- Prevention: Use numeric-aware ordering whenever filenames represent ordered pages.

## 2026-09-24 — Initialization I/O was capped below configured workers

- Symptom: Page initialization could not use more than four workers.
- Root cause: The shared I/O-stage semaphore used a fixed limit of four.
- Fix: Derive the I/O-stage limit from the configured pipeline worker count.
- Prevention: Keep stage capacities aligned with the worker budget when a stage should scale with workers.

## 2026-09-24 — Active pipeline pages were mislabeled as translating

- Symptom: Pages showed “Translating” during initialization and other non-translation stages.
- Root cause: The page row mapped every `processing` status to “Translating”, ignoring its separate current-stage label.
- Fix: Label the broad active status “Processing” and keep the actual stage beside it.
- Prevention: Keep umbrella lifecycle labels distinct from specific pipeline-stage labels.

## 2026-09-24 — Shared model cache retained idle weights and duplicate bubble wrappers

- Symptom: Server memory stayed elevated after batches and could grow when bubble inference settings changed.
- Root cause: Each translator tracked model use independently despite sharing one executor cache; unload functions usually popped entries without calling backend unload hooks; the default detector kept strong global references; bubble cache identity included inference-only settings.
- Fix: Track active users and last use in the shared executor, unload idle LRU entries on TTL or RAM pressure, run backend unload hooks, weaken detector fallbacks, and key bubble models only by checkpoint/device. Stream OCR crop microbatches and reduce page group size as RSS rises.
- Prevention: Put lifetime metadata beside the shared cache and keep inference arguments out of model identity; ensure unload checks exercise the actual model object and its external references.

## 2026-09-24 — Layout pages were serialized by the render lock

- Symptom: Batch pages reached the layout stage but only one page used CPU at a time.
- Root cause: The page solver held a process-wide render lock while mutating shared FreeType font selection and face state.
- Fix: Give each thread its own font faces and selection, key layout caches by font selection, and remove the broad lock from page layout.
- Prevention: Keep layout state page-local and only lock genuinely shared render operations.

## 2026-09-24 — MangaOCR pages were dispatched serially

- Symptom: MangaOCR pages in one batch entered separate OCR inference calls.
- Root cause: The scheduler grouped only the 48px backends, and `dispatch_batch()` looped through MangaOCR pages; its existing crop batching covered only one page per call.
- Fix: Group compatible MangaOCR pages up to `--workers`, interleave their crops, and batch both MangaOCR generation and auxiliary confidence/color inference across pages.
- Prevention: Keep scheduler eligibility and backend dispatch batching in sync; verify every GPU OCR backend maps crop outputs back to their source pages.

## 2026-09-24 — Completed batch pages were detached from their manga

- Symptom: A completed translation batch had saved result pages, but the manga detail showed only the page already assigned to its group.
- Root cause: The gallery lists pages by manga-group membership; batch records keep result folders for finished items, but there was no detail-page action to reassign saved results when their group metadata differed.
- Fix: Restore finished result folders from terminal translation batches matching the manga title or group ID into the open manga group.
- Prevention: Keep batch completion and manga membership as separate states, and provide a recovery action that relinks existing results without rerunning translation.

## 2026-09-24 — Manga detail kept a stale page list after batch completion

- Symptom: A completed translation batch could leave an already-open manga detail showing its previously cached pages.
- Root cause: Batch completion refreshed manga counts, but only rerender completion invalidated the gallery's cached per-manga page lists.
- Fix: Invalidate and reload gallery pages when either a translation batch or rerender reaches completion.
- Prevention: Refresh both group summaries and page lists after jobs that create or replace result pages.

## 2026-09-24 — GPU stages were rerouted to CPU under contention

- Symptom: A GPU-capable page stage ran on CPU when another page held the GPU slot.
- Root cause: The scheduler treated GPU-slot contention as a reason to bypass the shared model executor and load a separate CPU model.
- Fix: Keep the stage on its configured device and wait for the shared GPU slot; remove the CPU-only cache bypass.
- Prevention: Accelerator contention must queue model work, not change its device. CPU fallback remains limited to an explicit backend unsupported-operation path.

## 2026-09-24 — Recommended 48px OCR stayed page-serial

- Symptom: Studio's recommended 48px OCR pages ran in separate model calls even though the scheduler grouped only CTC OCR.
- Root cause: `_find_ocr_group()` excluded the UI-default recognizer, and `dispatch_batch()` serialized every backend except CTC.
- Fix: Pool compatible 48px OCR crops from as many ready pages as `--workers` into shared model calls, while retaining each page's probability threshold and output regions.
- Prevention: Keep page batch size wired to startup worker count and verify scheduler grouping and model dispatch for the UI-default backend.

## 2026-09-24 — Bubble and inpainting stages stayed page-serial

- Symptom: Speech-bubble detection and inpainting called their GPU model once per page even though their model paths can accept multiple pages.
- Root cause: A single GPU concurrency slot disabled all scheduler page groups, and LaMa/AOT inpainting only built batch-size-one tensors. Reloaded inpainting checkpoints also dropped the saved protected bubble edge and translated regions from runtime context.
- Fix: Keep safe bubble batches enabled at one GPU slot; add two-page default AOT tensors with padding capped at 25%, checkpoint-group AOT inpainting, and reload persisted translations and protected edges. LaMa Large starts at batch size one for memory, while LaMa MPE remains single-page because its positional encoding reads only the first batch item.
- Prevention: Treat active GPU calls and items per model call as separate limits; checkpointed batched stages must restore all artifacts consumed by their model and postprocessing.

## 2026-09-24 — Professional batches stalled behind completed pages

- Symptom: Prepared pages stayed at “Waiting for batch translation” when another page in the same professional story segment had completed.
- Root cause: Professional story scheduling rejected a whole segment if any page was terminal, including textless pages that correctly completed before translation.
- Fix: Exclude completed pages from the selected story group and remap story/archive ranges across skipped pages; failed pages continue to block the batch.
- Prevention: Cover professional story barriers with completed textless pages both inside and at the edge of a segment.

## 2026-09-23 — Recent titles appeared as existing manga groups

- Symptom: A title with no manga in the library, such as `6`, was labeled as an existing group in the assignment dialog.
- Root cause: The Studio merged saved recent titles and unfinished batch titles with library groups, and the dialog treated any exact title match as existing.
- Fix: Build Studio assignment options from library manga only; show matching names after a search and render each full title.
- Prevention: Keep persisted manga and recent or in-progress titles separate when presenting existing-group choices.

## 2026-09-23 — Pipeline release left page arrays reachable

- Symptom: RSS could remain high after a page stage or grow across legacy batch preparation even after checkpoints were saved.
- Root cause: `PipelineRun.release_runtime()` detached only its own context reference, `Context.cleanup_intermediate()` did not cover mask/layout workspace fields, and `translate_batch()` prepared every page before translating. AOT inpainting also ran full-resolution float32 on MPS when the configured 2048px limit exceeded a normal page, creating a large transient activation peak that stage-boundary telemetry missed.
- Fix: Clear page-owned buffers explicitly at run release, persist then drop bubble detector masks before translation, free mask diagnostics before layout, reuse the final mask alias, and prepare legacy calls in bounded chunks. Cap MPS inpainting at 1024px and clear batch-scoped translator history at completion.
- Prevention: Keep Context cleanup lists aligned with new stage-owned pixel fields, retain bounded preparation, and account for inference peaks as well as stage-boundary memory when reviewing MPS workloads.

## 2026-09-23 — Paired MPS detection raised batch memory peaks

- Symptom: Two-page text detection reached 5.86 GB of MPS driver memory in the second repeated batch, compared with 2.51 GB for fresh single-page detection; batch cleanup returned live tensor memory near its prior level.
- Root cause: The default detector pads paired pages into one 2048px model tensor, increasing the MPS working set. The shared in-process executor also allowed two local model calls, which could compound peaks, though the recorded repeated nine-page runs themselves had one GPU slot.
- Earlier fix: On MPS, cap shared model calls and scheduler GPU work at one, and route default/DBNet detection through the single-page checkpoint path.
- Symptom (2026-09-24): `python server/main.py --workers 2` aborted with macOS malloc heap-corruption detection after enabling two MPS model calls and paired detection.
- Updated policy (2026-09-24): Keep concurrent accelerator calls serialized, but batch compatible default/DBNet detection pages into one MPS call up to `--workers`. This supersedes the earlier single-page scheduler gate; the recorded crash and memory spike remain relevant when assessing future failures.
- Fix (2026-09-24): Run default and DBConvNeXt MPS detector batches with FP16 autocast and inference mode to reduce feature-map activation memory while preserving one call for all pages. Keep output maps in FP32 for detection postprocessing, and restrict CPU fallback to explicit unsupported-operation errors so MPS memory failures remain visible on the selected device.
- Prevention: Keep scheduler and executor concurrency limits separate from per-call page batch size; use lower-precision inference to control MPS activation peaks and never interpret out-of-memory as a missing GPU kernel.

## 2026-09-23 — Concurrent offline translation mixed per-request settings

- Symptom: Concurrent requests through the shared offline translator could both return the second request's settings.
- Root cause: `SugoiTranslator` is cached as one mutable instance, and concurrent `parse_args()` calls overwrote its request-specific configuration during translation.
- Fix: Serialize the complete offline translator operation while leaving other local model calls concurrent.
- Prevention: Protect mutable settings for cached singleton models across the full configure-and-infer operation.

## 2026-09-23 — Numeric OCR annotations were discarded

- Symptom: OCR regions such as `(48)` disappeared from the translated page.
- Root cause: Text grouping used translation eligibility (`is_valuable_text`) as its retention rule, and that helper intentionally excludes digits.
- Fix: Retain Unicode letters and numbers independently, mark number-only regions for source preservation, and omit them from fast and professional translation requests.
- Prevention: Test punctuation-only rejection, numeric retention, mixed linguistic numbers, provider payloads, checkpoint round trips, masking, source-sized layout, and rendering separately.

## 2026-09-23 — Batch stage timers included resource wait time

- Symptom: A page's stage timer advanced while that stage was waiting for a scheduler resource slot.
- Root cause: Claiming an item marked its step started before the worker acquired the stage semaphore.
- Fix: Clear the timer when claiming and start it after resource acquisition, when stage execution begins; leave queued/reserved steps untimed.
- Prevention: Keep the timer transition on the execution side of resource acquisition.

## 2026-09-23 — Checkpointed OCR treated empty detection as a failure

- Symptom: Pages with no detected textlines failed their OCR checkpoint instead of completing as textless pages.
- Root cause: Checkpoint retry raised when OCR input textlines were empty, and grouped OCR treated the same state as a batch error.
- Fix: Persist an empty OCR result and use the existing textless completion path for empty pages in both retry flows.
- Prevention: Cover empty detector output in direct and grouped checkpoint retry tests.

## 2026-09-23 — A single long dialogue word could force severe font shrinkage

- Symptom: Shape-aware bubble layout reduced an otherwise readable line to tiny text when one word could not fit any safe row.
- Root cause: The canonical solver intentionally treats normalized words as atomic and had no controlled rescue path for dictionary-valid breaks.
- Fix: After a compressed bubble result, try one dictionary-valid split of the bottleneck word near the calibrated target and keep it only for a material font-size gain.
- Prevention: Keep the normal solve first and test healthy layouts, non-bottleneck paragraphs, and `no_hyphenation` as hard gates.

## 2026-09-23 — MangaOCR returned regions below the OCR confidence threshold

- Symptom: The `mocr` backend could emit text for regions below `OCR Min Confidence`.
- Root cause: Low-confidence predictions were skipped before confidence aggregation, but auxiliary MangaOCR text was later attached and every merged region was returned.
- Fix: Aggregate confidence across all source regions and apply the threshold before returning each OCR region.
- Prevention: Apply confidence gates to the final text regions that leave a backend, after model-specific merging.

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

- Symptom: Layout quality changed after a checkpoint; render retries could fail with `Frozen layout is stale` on unchanged pages.
- Root cause: Text-line merge assigns `region_id` after construction, leaving provenance empty. Layout fills provenance and may choose a new font size before fingerprinting, while rendering fingerprinted the raw translation checkpoint first; equivalent stage inputs therefore hashed differently. Repeated hydration also needed the source font size retained.
- Fix: Canonicalize region identities, provenance, and source font size before fingerprinting; keep the source size in `layout.json` and across hydration. Frozen lines render directly, and solved dialogue uses whole-word wrapping.
- Prevention: Test checkpoint fingerprints from the same translation document before and after layout, including IDs assigned after region construction, then verify repeat hydration and pixel parity.

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

## 2026-09-24 — Checkpointed batch finalization lost metadata or failed on textless pages

- Symptom: Completed checkpointed pages could be indexed under `Ungrouped`; textless pages also failed after detection when `result_documents` was missing.
- Root cause: Normal checkpointed rendering and textless completion bypassed the direct translation path that writes `meta.json`; separately, reconstructed `PipelineRun` contexts did not initialize `result_documents` before `_revert_upscale()` expanded it.
- Fix: Share the result-metadata builder across direct and checkpointed finalization, persist `meta.json` for rendered and textless pages, and treat missing result documents as empty when assembling documents.
- Prevention: Cover checkpointed render metadata with a manga group ID and textless finalization with a context that has no `result_documents`.

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

## 2026-09-23 — CPU-heavy page stages were serialized by two independent limits

- Symptom: Layout and mask generation ran one page at a time even when multiple pipeline workers were available.
- Root cause: The batch scheduler fixed CPU-heavy slots at one, and each event-loop CPU lane also had one consumer; independently increasing per-worker consumers could multiply process-wide CPU work.
- Fix: Derive the scheduler's CPU-heavy limit from `--workers` and one configurable process-wide background pool; keep one separate interactive slot and GPU capacity at one.
- Prevention: Configure the scheduler and shared CPU pool from the same budget, and keep a cross-event-loop capacity regression test.

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

## 2026-09-23 — Pipeline timing overview showed wall-clock duration

- Symptom: Total duration did not match the sum of the displayed processing stages.
- Root cause: The timing resolver preferred the page-level duration or started-to-finished interval over the stage durations.
- Fix: Sum available stage durations first, retaining existing metadata and timestamp fallbacks when stage timing is absent.
- Prevention: Keep timing resolution based on recorded processing stages when they are available.

## 2026-09-24 — Speech bubble preview was invisible on white balloons

- Symptom: Selecting Speech bubbles showed no visible regions on manga pages with white balloon interiors.
- Root cause: The viewer screen-blended a filled white cleanup mask over the original page and never loaded the saved detection polygons.
- Fix: Load `bubble_detections.json` and draw its saved polygons in an image-aligned SVG overlay, with visible loading and missing-data states.
- Prevention: Render saved detection geometry against the source image so white regions remain visible as outlines.

## 2026-09-24 — PreviewImage changed hook order when image controls appeared

- Symptom: Switching a preview from its single-image fallback to the comparison viewer could trigger React's hook-order error.
- Root cause: The newly added active-image measurement effect was declared after the single-image early return.
- Fix: Reuse the existing unconditional resize effect and track Hold Peek changes there.
- Prevention: Keep all hooks above conditional returns; test previews when result or region data appears after mount.

## 2026-09-24 — Job thumbnail nested an image retry button

- Symptom: Jobs rendered a nested `<button>` and emitted a React hydration warning when a result thumbnail was loading or failed.
- Root cause: `PreviewImage` always showed its retry control, including when embedded inside the thumbnail's own button.
- Fix: Show the retry control only in comparison previews; the thumbnail still opens the full viewer, where retry is available.
- Prevention: Keep preview thumbnails non-interactive and check the server-rendered Jobs list for nested controls.

## 2026-09-24 — Text at an image edge produced an empty mask crop

- Symptom: Mask generation failed with `'NoneType' object is not subscriptable` inside `refine_mask`.
- Root cause: `extend_rect` treated image dimensions as inclusive coordinates and subtracted one, making a crop zero-sized for small components on the last row or column; OpenCV returns `None` for `bitwise_not` on an empty array.
- Fix: Cap crop width and height by the remaining image dimensions without subtracting one.
- Prevention: Cover mask crops that touch each image edge, including one-pixel components.

## 2026-09-24 — Terminal failed batches skipped memory reclamation

- Symptom: Removing a failed page did not reduce retained translation/device memory.
- Root cause: Several checkpointed and grouped failure paths marked a batch terminal but skipped the history cleanup and device-cache reclaim used by successful batch completion.
- Fix: Run the same cleanup when processing, translation, or rerun paths finish in an error state.
- Prevention: Every terminal batch transition must release page context and reclaim unused device cache while its worker is still owned.

## 2026-09-24 — Rendering could rediscover source text and source typography

- Symptom: Japanese could reappear beside a grouped translation, valid dialogue could shrink to a few pixels, preserved numbers could drift, and free text could move away from its source footprint.
- Root cause: Layout and rendering resolved content independently; review-required untranslated group members were restored even when a rendered group owned them; the source-first policy trusted the minimum OCR line size without checking source geometry; and separating cleanup from glyph coverage also removed the placement anchor.
- Fix: Snapshot immutable source facts, use one strict render-content resolver, exclude owned group members from restoration, calibrate implausibly tiny positive OCR sizes against line thickness, keep preserved numeric sizing exact, freeze content/ownership checksums, score free-text placement with block overlap while cleanup remains independent, and invalidate old layout artifacts by algorithm revision.
- Prevention: Keep deterministic regressions for missing translations, `(48)`, grouped-member restoration, invalid tiny OCR size, provenance retries, source-anchored free text, unknown-name hyphenation, duplicate ownership, cleanup underlays, and stale layout rejection.
- Reproduced during the 2026-09-25 Sugoi server run on two dense pages from `~/Downloads/bruh`: all pipeline stages completed, but final renders visibly overlap English text and retain source text; see `/private/tmp/mt-refactor-final-e2e/data/results/`.
# 2026-09-25 — Local semantic search combined mode crashes

- Symptom: `semantic_search_lab.py search` with its default `combined` mode raised `KeyError: 'combined'` in encoder lookup.
- Root cause: `run_query()` treated ranking modes as encoder modalities; combined retrieval must encode both summary and image query vectors.
- Fix: expand combined mode to the summary and image modalities before encoding. For a summary-only index, query with `--mode text` to avoid unnecessary image-model loading.
- Prevention: keep ranking mode names separate from encoder modality names when adding or changing search modes.
# 2026-09-25 — Resumed synopsis jobs lost their provider

- Symptom: A queued Gemini synopsis resumed with a 400 saying Gemini's model name was unsupported by DeepSeek.
- Root cause: The scheduler rebuilt requests from the persisted model name but omitted the separately stored provider, so model resolution defaulted to DeepSeek.
- Fix: Rebuild the request with `provider:model`; keep the legacy model-only fallback for old jobs.
- Prevention: Test scheduler resumes with multiple providers, not only DeepSeek.

# 2026-09-25 — Layout retry test used an immutable placeholder region

- Symptom: The layout retry lane test failed while serializing its context, before it could assert the CPU lane.
- Root cause: Its built-in `object()` placeholder could not accept the `_translation_incomplete` annotation used by frozen-layout serialization.
- Fix: Use a mutable `SimpleNamespace` region in the test fixture.
- Prevention: Use a mutable region-shaped fixture when exercising serializers that annotate translation state.

## 2026-09-25 — Extracted document repository omitted UUID import

- Symptom: Checkpointed batches failed when saving documents for a folder without an owner.
- Root cause: `DocumentRepository.save_documents()` creates a pipeline-run ID with `uuid.uuid4()`, but the extracted module did not import `uuid`.
- Fix: Import `uuid` in `server/document_repository.py`.
- Prevention: Exercise the unowned-folder persistence branch after extracting repository code.

## 2026-09-25 — Gallery alphabetical sort reset across pages

- Symptom: Choosing A→Z was treated as newest-first after route parsing; pagination then dropped the sort and showed a different slice.
- Root cause: `parseAppPath()` accepted the other gallery sort values but omitted `alpha-asc`.
- Fix: Recognize `alpha-asc` and cover parsing with a route regression assertion.
- Prevention: Keep every `GallerySort` value round-tripping through route parsing and pagination.

## 2026-09-25 — Page Detail overlays obscured the artwork

- Symptom: Dense pages were covered by always-visible region IDs, IDs could not be copied for detector and speech-bubble regions, and reopening the speech-bubble view refetched its geometry.
- Root cause: Overlay labels rendered unconditionally, only translated text regions had an inspector, and the bubble-loading effect fetched on every view toggle.
- Fix: Reveal IDs and details on selection, add copy-ID controls to each region inspector, and keep successfully loaded speech-bubble geometry in component state across toggles. Show image retry only after the load fails.
- Prevention: Keep inspection metadata progressive and avoid refetching immutable per-page geometry when a display mode changes.

## 2026-09-25 — Solver line-breaking extraction dropped a compatibility export

- Symptom: `devscripts.pipeline_step_runner` failed to import `compute_zone_shape_profile` from `layout.solver` after line-breaking code moved.
- Root cause: The runner imports that geometry helper through the solver module, and the extraction removed its incidental re-export.
- Fix: Keep importing `compute_zone_shape_profile` in `solver.py` alongside the moved helper compatibility imports.
- Prevention: Search all solver imports before moving symbols and run the pipeline-runner collection/tests after layout extractions.

## 2026-09-25 — Free-text extraction omitted a solver compatibility export

- Symptom: `devscripts.pipeline_step_runner` failed during test collection because it could not import `_free_text_words` from `layout.solver`.
- Root cause: The helper moved to `free_text_typography.py`, but the solver compatibility imports initially omitted it.
- Fix: Re-export `_free_text_words` and the other moved typography helpers from `solver.py`.
- Prevention: Compare moved names against repository imports and verify module collection immediately after extraction.

## 2026-09-25 — Bubble-zone extraction bypassed the solver estimator patch point

- Symptom: The page font-baseline test returned 20 instead of the expected 27 after monkeypatching `solver._estimate_adaptive_font_size`.
- Root cause: `_page_dialogue_font_baseline()` moved to `bubble_zones.py`, so it resolved the estimator from that module instead of the existing solver symbol.
- Fix: Keep the small baseline helper in `solver.py`; grouping and zone partitioning remain extracted.
- Prevention: Check tests for monkeypatches of module globals before moving functions that resolve those globals.

## 2026-09-25 — Bubble-zone extraction removed adjacent joint-layout exports

- Symptom: Layout test collection failed because `solver._candidate_data` was no longer available.
- Root cause: The mechanical extraction range included the joint-layout imports immediately following `partition_bubble_zones()`.
- Fix: Restore the joint-layout compatibility imports in `solver.py`.
- Prevention: Verify extraction boundaries against neighboring imports as well as neighboring function definitions.
## 2026-09-25 — Pipeline serialization extraction omitted restoration helpers

- Symptom: Imports from `pipeline.run` failed during test collection after moving the serialization functions.
- Root cause: The extraction range stopped at `ACTIVE_RUNS`, while text restoration helpers appeared later in the module after result-document saving.
- Fix: Move both restoration helpers into `pipeline/serialization.py` and re-export all serialization helpers from `pipeline.run`.
- Prevention: Identify the full symbol set by definition and caller search, rather than using the first neighboring module-level constant as an extraction boundary; run import and round-trip tests.
## 2026-09-25 — Page Detail action extraction left a preview URL import behind

- Symptom: Typecheck failed because the Page Detail preview JSX still referenced `apiUrl` after its import was removed with the moved download action.
- Root cause: The moved handler and the modal presentation both used the same module import.
- Fix: Keep `apiUrl` imported by the modal for preview URL construction.
- Prevention: Search every moved dependency across the complete source file before deleting its import.
## 2026-09-25 — Page Detail resize extraction kept the old modal ref

- Symptom: The Page Detail test failed to transform the modal because `modalContainerRef` was declared twice.
- Root cause: The resize hook returned the ref, but the modal’s original local declaration remained.
- Fix: Remove the local declaration and use the ref returned by `usePageDetailSidebarResize`.
- Prevention: When moving hook-owned refs, remove their original declarations and search every use before verifying.

## 2026-09-25 — Reader page extraction omitted preload helper import

- Symptom: Frontend typecheck failed because single-page preloading still called `getImageUrl` from `MangaReaderModal` after the helper moved.
- Root cause: `getImageUrl` was used by both the extracted page component and the parent modal's detached preload effect.
- Fix: Export `getImageUrl` from the reader feature and import it into the modal.
- Prevention: Search callers outside an extracted component before moving its private helpers.

## 2026-09-25 — App-factory import cleanup removed artifact helpers

- Symptom: Four server result tests raised `NameError` for the bbox and thumbnail artifact helpers.
- Root cause: Removing route-factory imports by a broad text range also removed the adjacent `server.result_artifacts` compatibility imports.
- Fix: Restore the artifact imports and narrow the cleanup to explicit route-factory blocks.
- Prevention: Use AST-aware import edits or verify all retained wrapper dependencies after import cleanup.

## 2026-09-25 — Rerun execution extraction omitted the mode enum

- Symptom: Pipeline rerun jobs failed at execution with `NameError: PipelineRerunMode`.
- Root cause: The moved execution module imported the plan dataclass but not the enum referenced by stage branches.
- Fix: Import the enum from the plan module.
- Prevention: Search all extracted function bodies for referenced module globals and run the full rerun lifecycle tests after each move.

## 2026-09-25 — Batch persistence extraction dropped a shared JSON helper

- Symptom: PostgreSQL batch review updates raised `NameError: _json_dump`.
- Root cause: The helper moved with manifest writes but remains used by review-update methods in `PostgresBatchStore`.
- Fix: Keep `_json_dump` imported in the compatibility store module as well.
- Prevention: After extracting a method, search the original module for every dependency still used by sibling methods.

## 2026-09-25 — Job image viewer hid the batch thumbnail while loading the detail image

- Symptom: Opening a finished job image could leave the viewer showing only “Loading image…” for several seconds.
- Root cause: The viewer waited for its larger reader/detail image and did not use the batch thumbnail already shown in the job row.
- Fix: Keep the batch thumbnail visible behind the loading status until the detail image loads.
- Prevention: Reuse already-loaded image variants as placeholders when a larger viewer asset loads asynchronously.

## 2026-09-25 — Quadrilateral distance lost its helper after extraction

- Symptom: OCR textline merging failed with `NameError: name 'dist' is not defined`.
- Root cause: `Quadrilateral.distance_impl()` moved out of `generic.py`, where `dist` was implicitly imported, without carrying that dependency into the extracted module.
- Fix: Import the existing `dist` helper into `generic_quadrilateral.py`.
- Prevention: Check extracted method bodies for module-level globals supplied by wildcard imports in the original module.

## 2026-09-25 — Pipeline case preview returned HTTP 500 before layout

- Symptom: `POST /api/pipeline-cases/preview` failed while preparing a saved page.
- Root cause: Persisted settings contained enum-qualified strings (`Alignment.auto`, `Direction.auto`) rejected by `Config.parse_raw`; the resulting validation error was then obscured because the router looked up `_batch_http_error` on the `__main__` runtime module.
- Fix: Normalize the legacy setting values and bind the existing `batch_http_error` handler directly. Added a read-only JSON endpoint and switched case inspection to one request for diagnosis without rerunning the pipeline.
- Prevention: Exercise saved-page settings through the same config conversion used by previews and keep preview error mapping independent of runtime-module globals.

## 2026-09-25 — Pipeline case preview used a stale artifact allowlist name

- Symptom: The temporary preview returned HTTP 500 before running any stages.
- Root cause: The preview route still referenced the allowlist's former private name after the constant moved to `pipeline_case_data.py`.
- Fix: Use the shared `PIPELINE_CASE_DOCUMENTS` export from the new route module.
- Prevention: Search all references when moving shared constants and inspect the server trace after the first live request.

## 2026-09-26 — Original manga import submitted a synthetic pipeline batch

- Symptom: A gallery upload could be reported as failed after its pages were already imported if the follow-up batch request failed; the request also woke the image batch scheduler unnecessarily.
- Root cause: The client followed the dedicated original-import request with a synthetic completed batch submission; that unrelated request was treated as part of upload success.
- Fix: End upload after the original-import response, remove its temporary upload state, and refresh the gallery independently.
- Prevention: Keep gallery-only imports out of translation batch submission and pipeline scheduling.

## 2026-09-26 — Manga group listing repeated its totals aggregation

- Symptom: `GET /results/groups` computed the same active-group totals separately from the requested page.
- Root cause: The PostgreSQL path ran two near-identical group aggregations on every request.
- Fix: Compute totals from the shared grouped CTE and left-join them to the requested page, including when the page is empty.
- Prevention: Keep pagination totals in the page query's shared aggregation and check offsets beyond the final page.

## 2026-09-26 — Panel-constrained free text skipped readable font sizes

- Symptom: A translated paragraph was suppressed even though a smaller, readable layout fit inside its panel.
- Root cause: The panel candidate shortlist kept several wraps at nearby large sizes, then jumped to the absolute minimum and dropped intermediate sizes.
- Fix: Keep page and panel candidates ordered by source-font proximity; stop at the first valid size, while retaining lower-size alternatives for joint collision solving.
- Prevention: Cover short panels with many fallback sizes and verify the chosen block remains inside the detected frame.

## 2026-09-26 — Bubble layout silently shrank overlong words

- Symptom: A single word could shrink a bubble far below the target, while a 7-letter word such as “already!” missed the hyphenation rescue. The legacy multi-lobe path also computed its minimum from the adaptive font before resetting its target to the OCR font.
- Root cause: The fitter used the stricter combined 90%/2px floor and only considered words of at least 8 letters; the multi-lobe target reset left a stale preferred floor.
- Fix: Use the preferred floor, allow legal 7-letter splits, then allow a review-marked fallback down to half the target before suppressing. Recompute the legacy lobe floor after its target is reduced to the OCR source size.
- Result: On page `1790363787103-ff26829c-2048-ENG-sugoi`, region `e56acd4dd80743df9e11c112a096fd6b` fits two whole-word lines at 28px. Region `c37704da399941408682eaa6f7a72467` stays at 24px and is review-flagged because its single long token cannot fit the preferred range after one split attempt; it is no longer reduced to 6px.
- Prevention: Cover the 7-letter split, preferred floor, bounded fallback, and multi-lobe target reset in layout regression tests.

## 2026-09-26 — Vertical OCR size suppressed a translated free-text region

- Symptom: Region `744ed9b863f34be8b63f1aae6b0321e7` was saved as `no_valid_layout` despite a translation and an open page area.
- Root cause: The OCR font field described a 180×268 multi-glyph text box, not a 180px English glyph; using it directly skipped realistic source sizing.
- Fix: Calibrate extreme OCR sizes from source text area per character. A full saved-page replay estimates 55px from its source geometry and places the translation at 39px in four whole-word lines, without suppression or review.
- Prevention: Exercise the actual text, box, and geometry-derived font through free-text layout, not typography candidate generation alone.

## 2026-09-26 — Typesetting reruns lost source font metadata

- Symptom: A rerun treated the last rendered editor font as the source size and could change or suppress translations on the next render.
- Root cause: Minimal editor artifacts omitted `source_font_size` and stable region IDs; the frozen layout also recomputed outline width from normalized colors.
- Fix: Persist source size and IDs, backfill legacy artifacts from matching translation records, and save the effective outline width with the frozen layout.
- Prevention: Test legacy artifact hydration and pixel-identical frozen rerenders.
## 2026-09-26 — Page detail image could stay on “Loading sharper image…” forever

- Symptom: The sharper result never appeared and the loading overlay remained indefinitely.
- Root cause: `PreviewImage` only ended loading after an image `load` or `error` event; a stalled request can emit neither.
- Fix: After 15 seconds, switch the existing overlay to its error state so Retry is available.
- Prevention: Give asynchronous image loading feedback a terminal state when the browser request can remain pending.

## 2026-09-26 — Direct inpainting validation assumed render suppression metadata

- Symptom: A `TextBlock` without `_render_suppressed` raised `AttributeError` during inpainting validation.
- Root cause: The validation path read optional layout metadata as a required attribute.
- Fix: Read suppression state with `getattr(..., False)` so direct and partially populated blocks remain valid.
- Prevention: Keep inpainting preflight covered with standalone `TextBlock` fixtures.
