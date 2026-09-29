# Notable changes

Record new features and large changes here. Keep implementation detail in code, tests, or dedicated documentation.

## 2026-09-29 — Add prompt inspection devscript for synopsis, story analysis, and translation chunks

- Reason: Developers and prompt engineers needed a fast, transparent way to inspect prompt templates and structured LLM payloads (system prompts, hierarchical panel/region transcripts, story localization guides, previous chunk context, and merge consolidation prompts) across standalone sample data, local manga folders, or running server instances.
- Added `devscripts/show_prompts.py` supporting `-p {all, synopsis, summary, chunk}`, target language customization, chunk sizing, manual story ranges, merge consolidation prompt toggle, and multiple output formats (`pretty` terminal, `markdown`, `json`).
- Added unit tests in `test/test_show_prompts_script.py`.

## 2026-09-29 — Remove mxbai cross-encoder reranker in favor of standalone Typesense hybrid search

- Reason: The secondary `mixedbread-ai/mxbai-rerank-xsmall-v1` cross-encoder reranker added significant latency (~6–8 seconds per query), consumed secondary device/GPU memory, and introduced arbitrary score thresholding issues (`minScore`) without improving candidate order over Typesense's native hybrid vector + fuzzy keyword rank fusion (`rerank_hybrid_matches`). Removing the cross-encoder streamlines Search Lab to a fast, clean single-stage Typesense hybrid architecture (~50–100 ms per query).
- Removed `mixedbread-ai/mxbai-rerank-xsmall-v1` model loading, weights caching, `rerank_candidates`, and `SearchEncoders.rerank` from `server/search_embeddings.py`.
- Removed reranker properties (`reranker_available`, `reranker_default`), query reranking flow, and reranker status info from `server/search_service.py` and `server/search_api.py`.
- Updated Search Lab frontend (`front/app/components/SearchLab.tsx`, `front/app/utils/searchLabTypes.ts`, `front/app/utils/searchLab.ts`) to remove the neural reranker toggle, min score threshold input, rank delta tags, and reranker status badges, presenting Typesense hybrid results, text match scores, and vector distances directly.
- Updated benchmark runner (`server/search_benchmark_runner.py`, `server/search_benchmark.py`) and documentation (`docs/search-lab.md`) to evaluate Typesense hybrid retrieval directly.
- Cleaned up unit and integration tests in `test/test_semantic_search.py`.

## 2026-09-29 — Typesense hybrid retrieval migration and optional neural reranker gate

- Reason: Unified search in Search Lab previously relied on Qdrant for vector retrieval without native typo-tolerant lexical search and hybrid rank fusion. Migrating the storage and retrieval engine to Typesense enables single-engine hybrid search (BGE dense embeddings + fuzzy keyword search with configurable typo tolerance) with server-level and per-query feature gating for optional cross-encoder reranking.
- Updated `docker-compose.search.yml` to run pinned `typesense/typesense:27.1` on port 8108 with persistent named volume `manga-search-typesense:/data`. Replaced `qdrant-client` with `typesense==2.0.0` in `requirements-search.txt`.
- Implemented `SearchTypesense` client (`server/search_typesense.py`) managing `manga_search_v2` collection schema (`groupId`, `sourceKey`, `title`, `content`, `chunkIndex`, `start`, `end`, `fingerprint`, `profile`, `published`, `embedding` float[768]), batch document upsert, atomic publication (`published: true`), document deletion, and hybrid vector search via `multi_search.perform` to prevent URL length overflow.
- Updated `SearchService` (`server/search_service.py`) to execute unified Typesense hybrid queries with candidate grouping by `groupId` (`CANDIDATE_LIMIT = 50`), hybrid rank fusion, typo tolerance (0, 1, 2), and alpha weighting.
- Added two-level feature gate for cross-encoder reranking (`mixedbread-ai/mxbai-rerank-xsmall-v1`): server availability (`MANGA_SEARCH_RERANKER_ENABLED=1/0`, default enabled) and query-level flag (`rerank: true/false`). Reranker model is lazy-loaded only when requested, with graceful non-fatal fallback to Typesense ranking if neural inference fails.
- Updated Search Lab frontend (`front/app/components/SearchLab.tsx`, `front/app/utils/searchLab.ts`, `front/app/utils/searchLabTypes.ts`) to expose typo tolerance selector, neural reranker toggle, alpha balance slider, Stage 1 candidate diagnostics (text score, vector distance, rank), and dual-mode result cards.
- Added dual-experiment benchmark runner (`server/search_benchmark_runner.py`, `server/search_benchmark.py`) evaluating retrieval performance between Stage 1 Typesense Hybrid and Stage 2 Neural Reranking.
- Added "Select all (N)" bulk selection action in Search Lab and decoupled collection filtering into orthogonal `summary_status` (`all`, `summarized`, `not-summarized`) and `index_status` (`all`, `not-indexed`, `indexed`) dropdowns in `front/app/components/SearchLab.tsx`, `server/search_api.py`, and `server/search_store.py`, enabling immediate filtering and 1-click batch selection of unindexed manga with summaries (`ready-to-embed`).
- Updated unit and integration tests (`test/test_semantic_search.py`) with `FakeTypesense` hermetic mock, live Typesense container tests, and reranker fallback assertions.

## 2026-09-29 — Return to image detailed view on closing edit page view

- Reason: Closing the visual editor overlay previously closed all overlays back to the gallery base route, losing the page detailed comparison context.
- Updated `onClose` in `ResultGallery.tsx`'s editor properties to restore `selectedImage`, set `isModalOpen: true`, reset zoom level, and trigger `onOpenPageView(closing.folder)` to return directly to the image detailed view (`PageDetailModal` / `/gallery/pages/:folder`).
- Added unit test in `front/app/components/ResultGallery.test.ts`.

## 2026-09-29 — Preserve detector state and use saved panels in free-text layout

- Reason: Empty bubble detection results were mistaken for missing results, and saved panel geometry was bypassed when building free-text ownership constraints.
- Layout now suppresses legacy bubble inference after a completed empty detection, rerun hydration preserves empty bubble artifacts, and free-text layout receives saved panels with conservative source-coverage matching and CV fallback.
- Added focused coverage for empty detection state, empty-artifact hydration, panel selection/fallback, and ownership-zone propagation.

## 2026-09-29 — Smallest enclosing panel spatial containment for text blocks

- Reason: When nested or inset panels were detected, text block spatial assignment previously stopped on the first matching panel, causing larger macro container panels to swallow text blocks belonging to smaller sub-panels.
- Updated text block spatial assignment in `manga_translator/utils/sort.py` and `manga_translator/professional_panels.py` to associate regions with the smallest enclosing panel by bounding box area (`min(inside, key=lambda it: it[1])`).
- Added unit tests in `test/test_joint_bubble_panel_detection.py` and `test/test_professional_panels.py`.

## 2026-09-29 — Database-backed translation presets and Web Studio Preset toolbar

- Reason: Users had to repeatedly re-configure translation, OCR, inpainting, typography, colorization, and upscaling settings in Web Studio without a way to save, name, or switch between tailored configuration profiles.
- Added database migration `server/migrations/016_translation_presets.sql` creating `translation_presets` with unique name indexing and default status tracking.
- Implemented `PresetRepository` (`server/preset_repository.py`) supporting dual-mode persistence (PostgreSQL with fallback to `presets.json` in result root when running without a database).
- Added FastAPI endpoints in `server/api/routes/presets.py` and schemas in `server/api/schemas/preset.py` for listing, creating, updating, deleting, and setting default presets (`/api/presets`).
- Built Web Studio presets subsystem (`front/app/features/studio/presets/`) including `usePresets` hook, `PresetToolbar` dropdown in Tier 1 of `OptionsPanel`, "Modified" change detection with Quick Update & Revert actions, `SavePresetModal`, and `ManagePresetsModal`.
- Added backend unit tests (`test/test_preset_repository.py`, `test/test_preset_api.py`) and frontend tests (`front/app/features/studio/presets/usePresets.test.ts`).

- Reason: An unregistered 688-line LaMa implementation had no in-repo callers, and Groq/Gemini repeated key parsing and pool-selection logic.
- Removed the dormant implementation, consolidated common key/model parsing, pool metadata, and round-robin selection, and reused filesystem argument converters across both CLIs.
- Removed unused rectangle-distance helpers and the CustomOpenAI override already inherited from `ConfigGPT`.

## 2026-09-28 — Detailed review reasons and yellow review bubble UI inspection

- Reason: The review reason `"render_suppressed"` was ambiguous and did not explain why rendering failed or how to fix it. Additionally, review-required speech bubbles in the image overlay were visually indistinguishable from normal bubbles, and selecting them did not show why manual review was flagged.
- Centralized review reason code mapping and resolution in `front/app/utils/reviewReasons.ts` with descriptive labels and actionable remediation guidance for all standard codes (`text_does_not_fit`, `font_below_readability_floor`, `emergency_word_split`, `render_suppressed`, etc.) and dynamic prefixes (`no_valid_layout:*`, `missing_glyph:*`, `duplicate render ownership:*`, `final layout collides with region*`).
- Highlighted speech bubbles requiring review with yellow borders (`border-yellow-400`), warning icon badges, and yellow selection rings in `PreviewOverlay.tsx` and `EditorCanvasStage.tsx`.
- Added a dedicated yellow review status banner in `BlockInspectionCard.tsx` that surfaces the exact failure reason and actionable guidance when inspecting a flagged bubble.
- Updated backend review reason resolution in `manga_translator/rendering/layout/render_status.py` to preserve specific layout and render failure details instead of falling back to generic suppression strings.
- Added unit tests in `front/app/components/PreviewImage.test.ts`.

## 2026-09-28 — App settings for pipeline retry and settings source selection for pipeline rerun

- Reason: When retrying a failed or finished pipeline from Page Detail, users expect the retry to run with active Studio application settings (translator, OCR, font, inpainter, upscaler). When triggering a pipeline rerun, users should have explicit control over whether to apply active application settings or preserve saved page snapshot parameters.
- Updated `retryFinishedPage` (`front/app/features/batches/retryFinishedPage.ts`) and `retryFinishedImage` (`front/app/App.tsx`) to pass `currentSettings: getCurrentSettings()` as `settingsOverrides` to `/results/rerun`.
- Added `settingsSource` selection ("Current App Settings" vs "Page Snapshot Settings") to `PipelineRerunDialog` (`front/app/components/PipelineRerunDialog.tsx`) initialized with `appSettings`.
- Extracted `PRESETS` into `front/app/components/pipelineRerunPresets.ts` and mode overrides into `front/app/components/PipelineRerunOverrides.tsx`, maintaining ratcheted line limits.
- Added unit tests in `front/app/components/PipelineRerunDialog.test.ts` and updated `front/app/features/batches/retryFinishedPage.test.ts`.

## 2026-09-28 — Resilient pipeline retry and fallback for Page Detail modal

- Reason: The "Retry pipeline" action in Page Detail previously threw an error ("This image is not linked to a retryable translation batch.") for any page that was not loaded in client batch memory (e.g. from gallery or database), causing "Retry failed" to display. Additionally, `/results/rerun` failed with `NameError: name 'secrets' is not defined`.
- Extracted `retryFinishedPage` helper (`front/app/features/batches/retryFinishedPage.ts`) supporting active batch item retries and fallback to `rerunPipeline` (`/api/results/rerun`) for standalone/gallery pages.
- Added `import secrets` to `server/api/routes/pipeline_reruns.py`.
- Added stage-to-mode mapping (`resolveRerunModeForStage`) so stage retries from TimingTab map to appropriate `PipelineRerunMode` (`typesetting`, `translation_typesetting`, `reprocess_text`, `full`).
- Added unit tests in `front/app/features/batches/retryFinishedPage.test.ts`, `front/app/components/PageDetailModal.test.ts`, and `test/refactor_contract/test_api_contract.py`.

## 2026-09-28 — Prevent premature "Rerun queued" button state on pipeline rerun modal trigger

- Reason: Clicking "Rerun pipeline" in `PageDetailHeader` opens the `PipelineRerunDialog` where the user chooses stages and overrides; it does not queue a rerun until submitted. Previously, `handleRerender` immediately set `rerenderStatus('queued')`, disabling the button and displaying "Rerun queued" before submission.
- Updated `usePageDetailActions` (`front/app/features/page-detail/usePageDetailActions.ts`) to execute `onRerender(image)` without setting `rerenderStatus('queued')`.
- Added `cursor-pointer` to `PageDetailHeader` rerun button and added characterization assertions in `front/app/components/PageDetailModal.test.ts`.

## 2026-09-28 — Single-hyphen emergency split fallback for tight boundary constraints

- Reason: When narrow comic panel or slot constraints are narrower than an oversized word's fragments (e.g. word width > slot width even after split), hyphenation previously aborted and left words unhyphenated because neither half met `width <= width_limit`, causing typography candidate generation to fail with `no_valid_layout`.
- Updated `_single_hyphen_split` (`manga_translator/rendering/layout/readable_text.py`) to maintain the 1-hyphen-per-token limit while allowing emergency split fallback to the most balanced 2-fragment split when fragments exceed `width_limit`.
- Updated `_free_text_wrap_candidate` (`manga_translator/rendering/layout/free_text_typography.py`) and `explicit_break_candidate` (`manga_translator/rendering/layout/hard_line_break_layout.py`) to permit isolated single-token lines when a word/fragment exceeds `max_line_width`.
- Added unit test `test_single_hyphen_split_fallback_when_exceeding_width_limit` in `test/test_readable_text.py`.

## 2026-09-28 — Support non-LLM and offline models (Sugoi) in Professional Translation Mode

- Reason: Professional mode should still work when its main translator is a non-LLM, MT, or offline model such as Sugoi V4.0; these backends translate text blocks directly while LLM story analysis is skipped. The legacy `draft_translator` setting remains accepted for saved-config compatibility but no longer selects a separate pass.
- Updated `ProfessionalTranslator` (`manga_translator/professional_translation.py`):
  - Removed strict `GPT_TRANSLATORS` restrictions on `primary` and `draft_translator`.
  - Professional translation now uses the configured primary translator for its single translation pass.
  - Added direct text block translation dispatch (`_translate_direct` / `_dispatch_one`) for non-LLM / offline primary translators, bypassing chat prompt construction.
  - Non-LLM `analyze` returns default or manual story boundaries without raising exceptions or attempting JSON prompts.
- Removed the First Draft Engine control from Web Studio because Professional mode no longer has a separate draft pass.

## 2026-09-28 — Translate professional chunks in one context-aware pass

- Reason: A separate draft and editor request doubled LLM calls and split translation from the story-wide style guidance. A single final-translation request can apply clarity, grammar, flow, concision, meaning, voice, and terminology together.
- Professional LLM prompts now include the story analysis on every chunk and the immediately preceding chunk's Japanese and English transcript after the first chunk; chunk and story boundaries remain unchanged.
- Removed the separate editor request and draft/editor progress states. Professional audit details now show the source and final translation.

## 2026-09-28 — Layout step behavior contract and rule documentation sync

- Reason: Layout algorithms, free-text candidate footprint validation, early fast-gate acceptance, bubble DP backpointers, spiral block centering, dictionary hyphenation rescue, and joint panel constraints received refinements across recent updates that needed to be reflected in the repository state compatibility contract.
- Updated `repository-state/LAYOUT_AND_TEXT_MERGE_RULES.md` to document the complete layout pipeline order, OCR textline merge thresholds, free-text coalescing invariants, bubble shape-aware line breaking & centering, free-text ownership & fast-gate footprint validation, region font policy & consistency floors, page-level joint collision selection, suppression review semantics, and the corresponding characterization test map.

## 2026-09-28 — Batch scheduler bubble and panel detection persistence

- Reason: Batch translation jobs discarded detected panels during the batch bubble detection stage because the batch runner returned only speech bubbles, causing `panel_detections.json` to be serialized as empty `[]`.
- Updated `run_bubble_detection_batch` to preserve and return both `(bubbles, panels)` tuples for each context.
- Unpacked and forwarded `precomputed_bubbles` and `precomputed_panels` in `server/batch_stage_executor.py`, `manga_translator/bubble_detection_stage.py`, `manga_translator/pipeline/run.py`, and `manga_translator/pipeline/retry.py`.
- Added unit tests in `test/test_joint_bubble_panel_detection.py` validating batch execution and precomputed panels handling.

## 2026-09-28 — End-to-end panel detection propagation and overlay visualization fix

- Reason: Manga frame/panel bounding polygons and order badges (`#1, #2...`) were missing from the UI page detail overlay because `panel_detections.json` was not rehydrated in batch translation groups, retry/rerun stages, or cached pipeline completions, and the frontend panel loader had an inverted guard condition.
- Plumbed `panel_detections.json` serialization and deserialization across `server/batch_translation_group.py`, `manga_translator/pipeline/retry.py` (`bubble_detection`, `textline_merge`, `translation`), and `manga_translator/pipeline/completion.py`.
- Corrected the panel fetching effect guard in `front/app/components/PreviewImage.tsx` to cleanly cache loaded results, exported `normalizePanelBoxes` in `panelRegions.ts`, and updated TypeScript definitions and unit tests across frontend and backend.

## 2026-09-28 — Panel-grouped hierarchical context structuring for LLM localization and synopsis

- Reason: Raw page transcripts with linear region ordering often split coherent conversational exchanges, causing the LLM to translate speech bubbles in isolation or follow imperfect OCR reading order rather than visual scene context.
- Implemented panel-as-primary-context architecture while retaining text regions as atomic translation output units (`{"regions": [{"id": ..., "translation": ..., ...}]}`):
  - Created `manga_translator/professional_panels.py` (<310 lines) with spatial and index-based region assignment (`assign_regions_to_panels`), structured page payload builder (`build_page_panel_structure`), hierarchical analysis transcript formatting (`format_page_transcript_for_analysis`), synopsis panel grouping (`group_transcript_panel_texts`), and synopsis transcript builder (`format_synopsis_transcript`).
  - Extracted prompt templates and system prompt builders into `manga_translator/professional_prompts.py` (<220 lines), instructing LLMs across Story Analysis, First Draft Translation, Senior Editor Pass, and Manga Synopsis Generation to treat panels as primary narrative context units and reading order as an estimated guide (soft error correction), with seamless fallback to `unassigned_regions`.
  - Refactored `manga_translator/professional_translation.py` and `server/manga_summary.py` to forward panel detections and format panel-structured prompts, keeping both files well below line limit ratchets.
  - Added characterization and unit tests in `test/test_professional_panels.py` and `test/test_professional_translation.py`.

## 2026-09-28 — Compact Series detail header with inline title editing

- Reason: Having a separate standalone card below the header bar solely for editing the series title and adding manga consumed unnecessary vertical space and created visual redundancy.
- Integrated series title inline editing (click to rename with save/cancel buttons and Enter/Escape support) and the "Add manga" action button directly into the top Series detail header bar in `SeriesLibrary.tsx`, completely removing the separate title edit card for a more compact layout.

## 2026-09-28 — Compact Jobs sidebar workflow

- Reason: The Jobs sidebar's bulk-action row and tall nested page cards obscured live progress and required extra scanning.
- Moved bulk cleanup into the header's More menu, placed per-job stop beside the title, opened active batch pages by default, and condensed progress, options, thumbnails, status, and page actions into the supplied compact panel hierarchy. Existing job actions remain available in light and dark themes.

## 2026-09-28 — Exact bubble line-break DP optimization

- Reason: Saved-page profiling showed word-break DP dominated several slow layout calls, while reducing search budgets could discard higher-quality alternatives.
- Reused row/word placement options, pruned only suffixes whose words cannot fit any remaining row, jumped over post-text rows that cannot fit the next word, and precomputed hard-break lookups. Candidate paths now use backpointers instead of copying line lists, and centering validates cached offsets without allocating rejected translations.
- Validation: Saved-page regression renders remained byte-identical. On the six-page local benchmark, aggregate bubble-solver time fell from 9.05s to 7.56s in matched single runs; focused layout tests and the source line-count ratchet pass.

## 2026-09-28 — Deprecate and remove legacy single-class speech bubble detector models

- Removed old single-class speech bubble detector models (`yolov8m_seg-speech-bubble.pt`, `juithealien/manga109-segmentation-bubble`, `chiqui7/yolo11-manga-seg`) and dead evaluation code.
- Unified bubble and panel detection entirely on the multi-class joint neural segmentation model (`ShadowB/Manga109-panel-balloon-text-yolov26-segmentation`).
- Configured model checkpoint resolver (`resolve_model_checkpoint` in `manga_translator/detection/panel.py`) and frontend settings hydration to alias any remaining legacy model strings to `shadowb_manga109`.
- Streamlined `BubbleDetector` in `manga_translator/detection/bubble.py` to natively parse multi-class output masks and bounding boxes for simultaneous panel and bubble extraction.

## 2026-09-28 — Multi-class joint speech bubble and panel detection with interactive web visualization

- Integrated multi-class neural segmentation (`ShadowB/Manga109-panel-balloon-text-yolov26-segmentation`) as the default engine (`shadowb_manga109`, `manga109_yolov26`) to replace the single-class speech bubble detector, detecting speech bubbles/balloons and manga panels/frames simultaneously in a single model forward pass across backend pipeline, batch configs, and frontend defaults with local storage migration.
- Added `PanelDetection` data model and Hugging Face checkpoint resolver in `manga_translator/detection/panel.py` with Ultralytics YOLO26 head/loss shims (`Segment26`, `Proto26`, `E2ELoss`) and RTL reading-order geometric sorting (`sort_panel_detections_reading_order`).
- Extended `BubbleDetector` in `manga_translator/detection/bubble.py` to support joint inference (`detect_joint`, `dispatch_joint`, `dispatch_batch_joint`), preserving backward-compatible single-class outputs for classic callers.
- Wired detected panels (`ctx.panel_detections`) through pipeline orchestration (`manga_translator/pipeline/run.py` and `orchestrator.py`), serializing `panel_detections.json` artifacts in result folders and server API routes (`server/pipeline_repository.py`, `server/api/routes/pipeline_case_data.py`).
- Integrated detected panels into reading order region sorting (`manga_translator/utils/sort.py`) and layout panel constraints (`manga_translator/geometry/panels.py`).
- Added frontend panel detection parser (`front/app/utils/panelRegions.ts`), reusable overlay renderer (`front/app/components/PreviewOverlay.tsx` and `BlockInspectionCard.tsx`), and unified "Frames" overlay in `front/app/features/page-detail/PageDetailHeader.tsx` and `PreviewImage.tsx`, rendering manga panels (cyan boundary polygons + `#1, #2` sequence order badges) and speech bubbles together simultaneously.
- Added configurable speech bubble and frame detection confidence threshold (`bubbleConfidence`, default `0.25`, range 0.05–0.95 with step 0.05) in Web Studio Options Panel (`front/app/features/studio/options/OptionsAdvancedPipeline.tsx`), with local storage persistence and full pipeline dispatch support to filter low-confidence detections.
- Updated terminology across Web Studio and Page Detail views: "Speech bubbles" button updated to "Frames" in Page Detail header and settings tabs, and "Bubble detection" updated to "Frame detection" in Options Panel parameters and summary badges.

## 2026-09-28 — Web Studio Options Panel compact hierarchy & structured redesign

- Redesigned the Web Studio `OptionsPanel` (`front/app/components/OptionsPanel.tsx`) into a high-density, 4-tier structured layout to resolve clutter, reduce cognitive load, and eliminate grid shifting when toggling quality modes or pipeline add-ons.
- Tier 1 provides a minimal action header with live status pill, remember-settings toggle, and reset action.
- Tier 2 organizes core controls into a responsive 2-card layout: Translation & AI Intelligence (Target Language, Translation Engine, Quality Mode with seamless in-place First Draft Engine integration, and AI Batch Size stepper) and Typography & Lettering (Font Type, Text Direction, and Lettering Case).
- Tier 3 provides a unified Visual Enhancements strip for Colorization (MC2 model, colorize-only toggle, and contextual inline size/tolerance/sigma controls) and Upscaling (ESRGAN/UltraSharp/Waifu2x model, ratio, and original-size reversion).
- Tier 4 provides a collapsible Advanced Neural Pipeline accordion with live parameter summary badge and dedicated sub-cards for Text Detection & OCR, Inpainting & Bubble Geometry, and Manga Synopsis Model.
- Extracted sub-components into `front/app/features/studio/options/` (`OptionsTypes.ts`, `OptionsEnhancements.tsx`, `OptionsAdvancedPipeline.tsx`), respecting line-count limits while preserving all existing props, defaults, and contracts.

## 2026-09-28 — Enable visual editor button on completed translation job page details

- Added `onEdit` handler to `PageDetailModal` in `App.tsx` wired to `handleOpenPageEdit`, enabling the interactive visual typesetter and editor button on page detail previews opened from manga translation jobs (Jobs sidebar, batch cards, and queue items).
- Updated `PageDetailHeader` to derive editor availability when either `hasTextRegions` or `folder` is present on completed translated images.
- Set explicit `"translated"` `sourceType` for finished items in `QueueItemRow` and added characterization tests covering header edit button rendering across translated, original, and callback states.

## 2026-09-28 — Bulk review approval for manga pages

- Added an "Accept all" action button to the manga detail review banner (`MangaDetailReviewBanner.tsx` and `MangaDetailHeader.tsx`) when pages in the manga need review (`needsReviewCount > 0`), allowing users to approve all pending review pages in the current manga in a single action.
- Added backend endpoint `POST /api/manga/{manga_id}/review/approve-all` (and alias `/manga/{manga_id}/review/approve-all`) powered by `MangaReviewMutationService` (`server/services/manga_review_mutation.py`), clearing `review_required` and `review_reason` from saved text regions, marking page `review_status` as `"approved"`, recording `reviewedAt` timestamps, and synchronizing batch review state across both PostgreSQL database and file-backed storage modes.
- Added frontend API helper `approveMangaReview` (`front/app/features/gallery/approveMangaReview.ts`) and action integration in `galleryMutations.ts` / `ResultGallery.tsx` / `App.tsx` with optimistic review status/count updates, page cache invalidation, and summary reloading.

## 2026-09-28 — Multi-token manga search, page count range filter, and composable gallery filter controls

- Replaced exact contiguous substring matching in manga gallery search with whitespace-separated out-of-order token matching across both PostgreSQL database queries (`regexp_split_to_array` with `bool_and(lower(title) LIKE '%' || token || '%')`) and disk/filesystem scanning/client-side view filtering (`tokens.every(token => title.toLowerCase().includes(token))`), allowing queries like `"oyako gal"` to match `"[Armadillo (Renji, Daiji)] Gal Oyako no Egui Kasegikata..."`.
- Added `minPages` and `maxPages` page-range filtering across the backend (`/results/groups` query parameters, SQL `HAVING count(*) BETWEEN $6 AND $7`, and filesystem scan counters), URL query parameters, and frontend state.
- Extracted and separated the monolithic gallery status dropdown into `GalleryFilterControls.tsx` with independent, composable filter controls:
  - Source selector: `All`, `Translated`, `Original`.
  - Composable toggle chips: `Summarized`, `Needs Review`.
  - Min / Max page range number inputs.
  - Search input, Sort selector (`Recent`, `Title`, `Pages`, `Oldest`), and a Reset Filters button.
- Updated route query parsing and synchronization to support multiple combinable status tokens (e.g. `status=translated,summarized`) and page bounds (`minPages=10&maxPages=50`).

## 2026-09-28 — In-page Jobs sidebar shown by default

- Converted `JobsDrawer` from an overlay modal rendered in a portal with a dark backdrop and app-root inert focus lock into an in-page, sticky sidebar rendered alongside the main workspace (Studio, Gallery, Search Lab).
- Defaulted `isJobsOpen` state to `true` so the Jobs panel is shown by default on page load.
- Updated `Header` with toggle state styling, `aria-expanded` attributes, and responsive layout width (`max-w-[1720px]`).
- Preserved synopsis detail preview as a standalone modal dialog, and kept existing batch and summary actions, section collapsing, and dismiss handlers.

## 2026-09-28 — Separate First Draft Engine in professional mode

- Added `draft_translator` configuration to `TranslatorConfig`, `batch_config`, and `batch_store` to allow using a distinct AI translation model for the first draft translation pass while using the main `translator` (Translation Engine) for the subsequent editor refinement pass in Professional localization mode.
- Added First Draft Engine select option in the Web Studio Options panel (`front/app/components/OptionsPanel.tsx`), with state hydration, persistence, and contract tests across settings, translation submission, and batch initialization.
- Updated `ProfessionalTranslator` (`manga_translator/professional_translation.py`) to dispatch story analysis and draft requests to `draft_translator` (defaulting to primary `translator` if unset) and editor pass requests to the primary `translator`.

## 2026-09-28 — Global Command+Arrow section navigation across Studio, Gallery, Series, and Search Lab

- Added `useShortcutNavigation` hook (`front/app/features/navigation/useShortcutNavigation.ts`) integrated with `useAppNavigation` to enable global `⌘← / ⌘→` (or `Ctrl+Arrow`) shortcut cycling across top-level views: Studio (`/studio`), Gallery (`/gallery`), Series (`/gallery?view=series`), and Search Lab (`/search-lab`).
- Excluded gallery review routes (`?status=review` / `?review=pending`) from the shortcut cycle so navigation only visits the four primary section pages, and preserved native text editing key navigation when input/textarea/select/editable elements are focused.
- Listed the section switching shortcut in the Header Keyboard Shortcuts modal (`front/app/components/Header.tsx`).

## 2026-09-28 — Integrate mxbai-rerank-xsmall-v1 cross-encoder and scope Search Lab to summaries only

- Integrated `mixedbread-ai/mxbai-rerank-xsmall-v1` (~70.8M params, pinned revision `b5c6e9da73abc3711f593f705371cdbe9e0fe422`) as Stage 2 cross-encoder reranker over the top 50 Stage 1 summary candidates in both the Web Search Lab (`/search-lab`) and `devscripts/semantic_search_lab.py` to filter and re-order candidates by fine-grained query-passage relevance, with optional `minScore` filtering.
- Removed all page-image embedding logic (`google/siglip-base-patch16-256`, `image`/`combined` modes, and page thumbnails) so Search Lab indexes and queries manga summaries exclusively, automatically cleaning up legacy `image` sources during reconciliation.
- Exposed Stage 1 `initialResults` (ordered by embedding cosine similarity), per-stage latency (`embeddingMs`, `rerankMs`), and rank-delta badges (`initialRank`, `rankDelta`, `rerankScore`, `rerankLogit`) in the API, CLI, and a collapsible Stage 1 debug panel in the UI.

## 2026-09-28 — Switch Search Lab text embedding to BGE-base-en-v1.5

- Upgraded default text embedding model from `BAAI/bge-small-en-v1.5` (384d) to `BAAI/bge-base-en-v1.5` (768d, revision `a5beb1e3e68b9ab74eb54cfd186867f64f240e1a`) and updated `PROFILE` to `bge-base-siglip-base-v1`.

## 2026-09-28 — Remove Qwen3 and GTE text embedding logic from Search Lab

- Removed `Qwen/Qwen3-Embedding-0.6B` and `thenlper/gte-base` options and branching logic from `server/search_embeddings.py` and `devscripts/semantic_search_lab.py`, standardizing text embedding on the pinned `BAAI/bge-small-en-v1.5` encoder.

## 2026-09-28 — Avoid redundant layout validation safely

- Accept an ideal free-text placement early only after its existing safety gate and final compositor validation; rerun conflicting fast placements and keep shadow comparison isolated so exhaustive output stays unchanged.
- Validate bubble glyphs in final rank order only until `top_k` valid candidates are found, retaining the existing unexplained-gap fallback. This reduces unnecessary glyph work without changing layout scoring or font exploration.
- On the verified 19-page corpus, the ideal gate accepted no candidates and free-text p50/p95 did not improve materially; leave typography generation unchanged pending a separate profiling-driven change.

## 2026-09-27 — Clear all Search Lab index data

- Add a confirmed collection-wide purge for Search Lab vectors and source records, blocked during embedding while preserving Gallery content and job history.

## 2026-09-27 — Lock text-merge and layout behavior

- Added a durable compatibility contract for OCR pair grouping, nested and adjacent free-text ownership, layout hard constraints, and final validation. Added boundary and rejection-precedence characterization tests so later changes must make behavior changes explicit.

## 2026-09-27 — Incremental per-step page progress and dismissed-job cleanup for manga summaries

- Emit batched text-extraction progress incrementally after each detection sub-batch, OCR sub-batch, and merged page, advance summary OCR stages immediately when a batched phase completes, and display `X/Y pages done` on all three extraction steps (`Detection`, `OCR`, `Merge text lines`) in the Jobs drawer so users can see live per-step progress instead of stage-end jumps.
- Clear non-terminal `jobStatus` (`queued`, `generating`, `paused`) when summary jobs are dismissed or stopped so dismissed jobs never remain stuck in `Summary queued` or block re-queuing.

## 2026-09-26 — Preserve suppressed and unchanged text during inpainting

- Prevent pages from failing on unmasked regions that will be restored for review; keep unchanged translations on their source pixels and allow strict render diagnostics after layout scratch is released.

## 2026-09-26 — Progressive image placeholder loading and header cleanup in Page Detail

- Improved image detail loading by resolving progressive placeholders across batch, thumbnail, preview, and cover variants; mapped variant URLs in direct page routes and studio previews; checked DOM completeness to avoid dark loading curtains on cached images; and streamlined the modal header breadcrumbs and removed redundant navigation buttons.

## 2026-09-26 — Widen text outlines over dark backgrounds

- White text strokes gain one pixel on dark sampled source backgrounds, with layout masks using the same width.

## 2026-09-26 — Skip a page during independent batch stages

- Jobs can skip pages in page-by-page stages; the current stage completes safely before the page leaves the batch, while shared grouped stages continue unaffected.

## 2026-09-26 — Export a saved pipeline step by manga

- Added a stdlib CLI that selects a manga, pipeline artifact, and page sort direction, then emits the saved artifact for each page as JSON.

## 2026-09-26 — Respect typography and panel bounds in layout

- Free text uses the estimated source size first, widens wrapping before shrinking when panel height is short, and may use a shorter block only when the panel requires it. Line spacing stays within 0–25%; placement prefers the source top and center while keeping the paragraph inside page, panel, bubble, and text bounds.
- Bubble text tries normal wrapping, then one legal split for an overlong word, within a preferred font floor of 90% of target or 2 px below it. Regions with no valid fit are suppressed and flagged for review instead of rendered at tiny sizes.

## 2026-09-26 — Keep unmatched text out of inferred bubbles and skip empty layout zones

- Typesetting reruns reconnect saved bubble IDs to detection masks; unmatched regions stay free text whenever saved detections exist. Free-text scope dilation uses calibrated source geometry, empty ownership zones skip candidate search, and failed placements request review.

## 2026-09-26 — Validate final translated text output

- Validate transformed raster output, including fallback crops and outlines, against page, panel, bubble, and region-collision constraints. Suppress invalid or empty frozen output with a review reason; flag below-threshold calibrated font ratios for review. Guard short vertical destinations, preserve explicit newlines, and preflight missing glyphs.

## 2026-09-25 — Keep white-outlined free text out of bubble layout

- Retain the smallest free-text wrapping candidate, reject undersized inferred bubble components, and favor vertical wrapping by widening only to fit an unbreakable word; the saved-page preview renders all regions.

## 2026-09-25 — Add read-only pipeline case JSON endpoint

- Added one-request page, settings, manifest, and stage-artifact inspection; updated `pipeline_case.py get` and its skill workflow to use the endpoint when diagnosing failed previews.
- Fixed preview setup for saved `Alignment.*` / `Direction.*` values and corrected the route's batch-error mapper binding.

## 2026-09-25 — Split remaining listed large Python modules

- Extracted existing helper and UI responsibilities from eight listed modules while preserving their original import surfaces; the MangaStudio window now uses small feature mixins.
- Focused verification: 48 core tests and 2 moved-render-helper tests passed; Python compilation and MangaStudio method-parity checks passed.

## 2026-09-25 — Extract gallery page-ordering controller

- Moved page sort actions and derived single-manga page-order state into `useGalleryPageOrdering`; retained the existing effect order and gallery prop/render behavior.
- Frontend typecheck and full `npm test` passed.

## 2026-09-25 — Extract gallery card callback adapters

- Moved the memoized handlers passed to gallery cards and row groups into `useGalleryCardActions`, preserving each existing dependency list.
- Frontend typecheck and `ResultGallery.test.ts` passed.

## 2026-09-25 — Move batch stage and memory policies beside their owners

- Extracted next-stage selection into `server/batch_stage_progression.py` and memory-pressure inference sizing into `server/batch_memory_limits.py`; `BatchScheduler` keeps the existing methods and policy thresholds.
- Added a focused check for the high-memory batch-size fallback.

## 2026-09-25 — Move server worker preparation behind runtime facade

- Moved `prepare()` resource sizing and worker selection into `server/server_preparation.py`; `server.main.prepare()` keeps its patchable runtime dependencies and signature.
- Verified the in-process and subprocess preparation branches through the existing server tests.

## 2026-09-25 — Extract PostgreSQL page and schema responsibilities

- Moved page identity, text-region, review-state, and group lookup queries into `server/page_state_repository.py`; schema lifecycle into `server/postgres_schema.py`; and untracked-result scans and document ownership into focused modules. `PostgresStore` retains its existing facade methods and is now below 500 lines.
- Verified page-state facade behavior and untracked-result indexing.

## 2026-09-25 — Extract FastAPI application factory

- Moved middleware and router registration into `server/app.py:create_app`, preserving route order and `server.main` compatibility exports.
- Verified route methods/paths, route ordering, public imports, and server result API behavior.

## 2026-09-25 — Extract server lifecycle

- Moved storage selection, scheduler startup, and shutdown into `server/app_lifecycle.py`; `server.main.lifespan` keeps the same runtime-module dependencies and patch points.

## 2026-09-25 — Add application line-count ratchet

- Added a baseline-backed CI check that prevents existing application files from growing and caps new files at 500 lines; model, vendored, generated, and test code is excluded.
- Recorded explicit shrink-only caps and reasons for the oversized modules already extracted during this migration.

## 2026-09-25 — Split pipeline rerun responsibilities

- Moved rerun planning, prerequisite checks, checkpoint hydration, stage execution, and artifact commits into focused modules while preserving `server.pipeline_rerun` imports and temporary-case test seams.
- Verified pipeline rerun and pipeline-case behavior: 24 tests passed.

## 2026-09-25 — Extract PostgreSQL batch persistence

- Moved the batch manifest and page-reservation SQL write into `server/postgres_batch_persistence.py`; retained the adapter method as a patchable delegate and reduced `PostgresBatchStore` below 500 lines.
- Verified store and scheduler behavior: 69 tests passed.

## 2026-09-25 — Move manual endpoint into API routes

- Moved the unchanged `/manual` handler into `server/api/routes/manual.py` and added its method/path to the route contract.

## 2026-09-25 — Extract pipeline serialization

- Moved region serialization and restoration helpers into `manga_translator/pipeline/serialization.py`; existing imports from `pipeline.run` remain available.
- Verified pipeline, layout, and persistence contracts: 93 passed, 1 skipped, 33 subtests passed.

## 2026-09-25 — Extract vertical text rendering

- Moved vertical text measurement and rasterization into `manga_translator/rendering/text_render_vertical.py`; existing `text_render` access remains available through a lazy facade.
- Fixed the clean-canvas renderer test fixture to include translation text so its fake renderer is actually reached.

## 2026-09-25 — Extract horizontal text rendering

- Moved horizontal text measurement/wrapping, glyph rasterization, and candidate selection/composition into focused modules; `text_render_horizontal.py` and existing `text_render` imports remain compatibility facades.

## 2026-09-25 — Extract prepared-page lifecycle

- Moved `MangaTranslator.prepare()` implementation, including its existing memory-pressure fallback, to `manga_translator/pipeline/preparation.py`; retained the public method as a delegating facade.

## 2026-09-25 — Extract pipeline stage retry execution

- Moved `PipelineRun.retry_stage()` implementation to `manga_translator/pipeline/retry.py`; retained the method signature and the `pipeline.run` dependency patch points used by tests and callers.

## 2026-09-25 — Separate translation remap geometry

- Moved polygon, bbox, centroid, overlap, and pair scoring helpers to `translation_remap_geometry.py`; `translation_remap.py` keeps the same helper imports and matching pass order.

## 2026-09-25 — Extract prepared-page render lifecycle

- Moved render-run restoration, completion, timing persistence, and failure cleanup into `manga_translator/pipeline/page_render.py`; retained `MangaTranslator.render()` as the public method.
- Added a focused test for the compatibility facade.

## 2026-09-25 — Extract single-image pipeline entry point

- Moved single-image lifecycle orchestration and context-history updates into `manga_translator/pipeline/entrypoint.py`; retained the public `translate()` method and `save_jpeg` test seam.

## 2026-09-25 — Extract batch translation workflow

- Moved bounded image preparation, aggregate translation/rendering, history updates, and output cleanup into `manga_translator/pipeline/batch/workflow.py`; retained the public `MangaTranslator` methods.

## 2026-09-25 — Move gallery composition into its feature

- Moved `ResultGallery` into `front/app/features/gallery/ResultGallery.tsx` and retained the original component import path as a re-export.
- Frontend tests, typecheck, and production build passed.

## 2026-09-25 — Extract gallery filter state

- Move sort, status, search, and selected-manga filter state and handlers into `useGalleryFilters`; keep gallery page correction and pagination behavior in `ResultGallery`.
- Frontend tests and typecheck passed.

## 2026-09-25 — Move Page Detail into its feature

- Move the modal coordinator beside the existing Page Detail components and preserve the original import path and exports.
- Verified Page Detail tests and typecheck.

## 2026-09-25 — Extract Page Detail actions

- Move download/copy, retry/rerender, navigation, and keyboard behavior into `usePageDetailActions`; retain the modal callback and interaction behavior.
- Verified Page Detail and artifact tests, typecheck, and diff checks.

## 2026-09-25 — Extract Page Detail sidebar resizing

- Move drag listeners, animation-frame updates, width state, and cleanup into `usePageDetailSidebarResize`; `PageDetailModal` is now under 500 lines.
- Verified Page Detail tests, typecheck, production build, and diff checks.

## 2026-09-25 — Extract summary task lifecycle

- Moved scheduled summary-job error handling and controller cleanup into `server/summary_task.py`; the server wrapper keeps the same scheduler interface and callbacks.
- Verified summary tests: 51 passed.

## 2026-09-25 — Extract Studio state restoration

- Move the IndexedDB snapshot merge into `applyRestoredStudioState`, retaining current-over-restored status precedence, ordering, selection unions, ref hydration, and hydration completion; add a focused contract to the frontend test command.

## 2026-09-25 — Extract batch text extraction

- Move the existing batch detection, OCR, text-line merge, bubble detection, and progress sequence into `manga_translator/pipeline/batch/text_extraction.py`; keep `MangaTranslator.extract_text_batch()` as the compatibility method and characterize the stage order.

## 2026-09-25 — Consolidate manga reader actions

- Move reader close-position capture and reader-to-editor handoff into the existing `useMangaReaderActions` feature hook; add its existing contract test to the frontend test command.

## 2026-09-25 — Extract gallery editor-save reconciliation

- Move gallery cache, selection, review-filter, and overlay updates from the editor modal callback into `front/app/features/gallery/editorSave.ts`; cover normal and review-only outcomes.

## 2026-09-25 — Extract Page Detail artifact loading

- Move bubble-mask discovery and pipeline/translation artifact fetch lifecycles into the Page Detail feature hook, retaining fallback paths, original-page filtering, and cancellation behavior; add fetch-contract coverage.

## 2026-09-25 — Extract model lifecycle operations

- Move MPS cache coordination, exclusive per-engine unload dispatch, and periodic TTL cleanup into `manga_translator/pipeline/lifecycle.py`; retain the existing translator methods and inject their current unload, executor, clock, and sleep bindings.
- Add lifecycle coverage for shared-executor cache safety, exclusive OCR unload, and TTL timestamp removal. Focused adjacent tests pass (31 passed).

## 2026-09-25 — Extract gallery no-results state

- Share the existing no-results markup and reset action between card and row views through `GalleryStatusStates`; register its markup contract in the frontend test command.
- The full frontend test command and typecheck pass; Vite emits its existing `EMFILE` watcher warnings.

## 2026-09-25 — Extract batch detail loading

- Move per-batch detail request deduplication and stale-server translator reconciliation into `loadBatchDetails`; keep App's callback and state ownership unchanged.
- Add focused concurrency/optimistic-state coverage; focused test and frontend typecheck pass.

## 2026-09-25 — Extract gallery fallback summary loading

- Move the existing fallback groups request and loading state into `useGalleryFallbackSummaries`; preserve request gating, invalid-payload handling, and error behavior.
- Add a focused URL/payload contract; the full frontend test command and typecheck pass (with existing Vite watcher warnings).

## 2026-09-25 — Extract editor document loading

- Move editor background fallback selection, saved-region loading, starter-block construction, and existing geometry/font normalization into `loadEditorDocument`; keep modal state and history reset timing unchanged.
- Add an image-source and metadata contract; the full frontend test command and typecheck pass (with existing Vite watcher warnings).

## 2026-09-25 — Extract editor drag handling

- Move pointer move/resize state and existing geometry into `useEditorDrag`; preserve selection, segment editing, history checkpoints, and history-free drag updates.
- Add movement, resize-anchor, and minimum-size contracts; the full frontend test command and typecheck pass (with existing Vite watcher warnings).

## 2026-09-25 — Extract editor save and export actions

- Move PNG/JSON download and save lifecycle into `useEditorExports`; preserve image rendering, file encoding, request timing, dirty/history reset, and `onSave` behavior.
- Keep the same endpoint and payload in `saveEditorEdits`; add request/error contracts and reduce `MangaEditorModal` below 500 lines.
- Full frontend test command and typecheck pass, with the existing Vite watcher warnings.

## 2026-09-25 — Extract batch dispatch

- Move runnable-batch selection, task launch, and done-callback cleanup into `server/batch_dispatch.py`; preserve `BatchScheduler._launch_available()` and dispatch ordering.
- `test/test_batch_scheduler.py` passes (40 tests).

## 2026-09-25 — Extract PostgreSQL result snapshots

- Move file-backed page snapshot construction into `server/result_snapshot.py`; preserve `PostgresStore._page_snapshot` and existing helper imports/patch points.
- `test/test_postgres_store.py` passes (26 tests).

## 2026-09-25 — Extract pipeline progress handling

- Move progress-hook dispatch, pipeline checkpoint/cleanup ordering, and default logger-hook mapping into `manga_translator/pipeline/progress.py`; preserve `MangaTranslator` methods as delegates.
- Add focused observer-order and logger-mapping coverage. Pipeline and memory tests pass (109 passed, 1 skipped).

## 2026-09-25 — Extract manga editor history

- Move editor history stacks, commit/checkpoint behavior, undo/redo, resets, and shortcut handling into `useEditorHistory`; preserve history caps and load/save reset timing.
- Add focused history transition tests. The frontend test script and typecheck pass.

## 2026-09-25 — Extract gallery keyboard handling

- Move modal Escape dismissal and selected-manga Escape clearing into `useGalleryKeyboard`, preserving dismissal precedence and selection conditions; remove the unreferenced duplicate page-modal key handler.
- Add branch contracts for dismissal priority and selection clearing. The complete frontend tests and typecheck pass.

## 2026-09-25 — Extract gallery status presentation

- Move the existing loading skeleton, empty-library state, and cleared-review state into `GalleryStatusStates.tsx`; preserve the markup, state-selection conditions, and callback behavior.
- Add static markup contracts for both loading modes and the empty states. The full frontend test script and typecheck pass.

## 2026-09-25 — Extract executor registration route

- Move `/register` into `server/api/routes/instances.py`; preserve the route, nonce validation, client-IP assignment, and `server.main.register_instance` compatibility name.
- Add route characterization for rejected and accepted nonces. Route, public-import, and server-logging checks pass (13 tests).

## 2026-09-25 — Extract result artifact helpers

- Move detection JSON and thumbnail synthesis into `server/result_artifacts.py`; keep the `server.main` helper names and route wiring unchanged.
- `test/test_server_results.py` and public import contracts pass (15 tests).

## 2026-09-25 — Extract translator rendering stage

- Move the existing text-rendering coordinator into `manga_translator/rendering_stage.py`; retain `_run_text_rendering()` and module-level CPU/render imports as compatibility surfaces.
- No rendering or layout formulas changed. The render-focused Python run reports 71 passed and 9 failures in renderer/layout expectations; the background CPU lane contract passes.

## 2026-09-25 — Extract startup batch restoration

- Move server batch hydration, IndexedDB cleanup, active upload restoration, and upload resumption into `front/app/features/batches/restoreServerBatches.ts`.
- Preserve its original initialization-effect start point and latest optimistic-delete ref; cover ordering, filtering, state merge, and resume dispatch.

## 2026-09-25 — Extract Studio preview image construction

- Move lightbox `FinishedImage` construction, source/folder resolution, and settings merge into `front/app/features/studio/buildPreviewImage.ts`.
- Keep App modal selection and retry state unchanged; add construction contracts to the refactor suite.

## 2026-09-25 — Extract gallery page assignment actions

- Move selected-page reassignment and loaded gallery state updates into `front/app/features/gallery/movePages.ts`.
- Cover metadata callback payload, target ordering, local state, and modal/selection cleanup.

## 2026-09-25 — Extract gallery rename actions

- Move title persistence handoff, read-progress key migration, route updates, and loaded gallery state migration into `front/app/features/gallery/renameActions.ts`.
- Cover the rename callback, storage migration, route ID, and local image/group/selection updates.

## 2026-09-25 — Extract gallery page-order actions

- Move drag reorder and sort-save behavior into `front/app/features/gallery/pageOrderActions.ts`, preserving optimistic ordering and rollback behavior.
- Add focused contracts for successful saves, failures, and unchanged sort order; frontend typecheck passes.

## 2026-09-25 — Extract gallery summary job controls

- Move pause, resume, and stop handlers into `useMangaSummaryActions`; keep the same server requests and modal state updates.
- Extend the action contract to cover each endpoint and resulting job state.

## 2026-09-25 — Extract gallery bulk deletion actions

- Move selected-page and selected-manga deletion handlers into `front/app/features/gallery/bulkDeletionActions.ts`; keep `ResultGallery` presentation and state ownership.
- Replace placeholder filtering checks with assertions against the actual deletion callbacks and local state updates.

## 2026-09-25 — Extract gallery group-title aggregation

- Move assignment-modal group entry aggregation into `front/app/utils/groupTitles.ts`, preserving title precedence, metadata, and sorting.
- Remove the unused title-check helper from `App.tsx`.

## 2026-09-25 — Extract rendering font lookup

- Move font name resolution and default font selection into `manga_translator/rendering/fonts.py`; keep the existing package-level import names.

## 2026-09-25 — Extract rendering artifact serialization

- Move safe-mask and rendered-box PNG codecs into `manga_translator/rendering/serialization.py`; keep their existing `bubble_layout` imports available.

## 2026-09-25 — Extract bubble grouping

- Move OCR-region association and source-region snapshots into `manga_translator/rendering/grouping.py`; keep `bubble_layout` compatibility names.

## 2026-09-25 — Extract bubble line breaking

- Move semantic break costs, safe line slots, dynamic line fitting, centering, and positioned rendering into `manga_translator/rendering/line_breaking.py`; keep `bubble_layout` imports.

## 2026-09-25 — Extract bubble lobe geometry

- Move connected-bubble peak detection, lobe partitioning, geometry profiles, and adaptive font estimates into `manga_translator/rendering/lobes.py`; keep the previous import names.

## 2026-09-25 — Extract bubble candidate fitting

- Move multi-lobe text fitting, geometry measurements, boundary checks, and candidate scoring into `manga_translator/rendering/candidates.py`; keep the existing `bubble_layout` names.

## 2026-09-25 — Extract hyphenator selection

- Move dictionary selection and the French dictionary alias into `manga_translator/rendering/hyphenation.py`; keep `text_render.select_hyphenator` available.

## 2026-09-25 — Extract server batch event handling

- Move the server-owned batch event subscription and callback logic out of `App.tsx`, preserving batch state ownership and subscription ordering.
- Cover completion updates, cache invalidation, summary refresh, and duplicate-snapshot suppression in the existing batch sync test.

## 2026-09-25 — Extract HTTP middleware

- Move request correlation logging and original-manga import serialization into `server/api/middleware.py`; retain middleware registration order and compatibility imports in `server.main`.
- Add a lock characterization test and keep the existing request-ID response tests.

## 2026-09-25 — Extract batch translation mode selection

- Move professional/fast/concurrent mode selection and per-page memory fallback into `manga_translator/pipeline/batch/runner.py`; keep `MangaTranslator.translate_batch_contexts()` as the compatibility facade.
- Add facade tests for standard dispatch, professional dispatch, and fallback. Translation, scheduler, memory, and pipeline contract tests pass.

## 2026-09-25 — Add saved-page AI inspection commands

- Add `pipeline_case.py get` to resolve a page URL and export its settings, pipeline manifest, and stage JSON. Reruns now execute on temporary files, return a review image, and leave source pages and database batch/page records unchanged; scratch files are cleaned automatically.
- Run rerun progress callbacks on the scheduler event loop so PostgreSQL batch state stays on its owning loop.
- Expose detection, speech-bubble, and text-region IDs in image overlays for issue reports.

## 2026-09-25 — Extract gallery group derivation

- Move summary, cached-page, session-page, review-count, and sort derivation into `front/app/utils/resultGallery.ts`; retain the `ResultGallery` re-export and keep rendering unchanged.
- The full frontend test suite, typecheck, and production build pass.

## 2026-09-25 — Extract gallery series actions

- Move series loading, create/add requests, selected-title ordering, and toast timing into `useGallerySeriesActions`; keep gallery selection, deletion, and modal presentation in `ResultGallery`.
- The frontend test suite and typecheck pass.

## 2026-09-25 — Extract summary generation service

- Move manga-summary job orchestration into `server/summary_generation.py` and manga-wide OCR scheduling into `server/summary_ocr_execution.py`; keep `server.main._generate_manga_summary` as the runtime-injected compatibility entry point.
- Summary OCR, job lifecycle, and refactor-contract checks pass (57 tests).

## 2026-09-25 — Extract server worker runtime

- Move worker environment setup, resource sizing, process startup, and supervision into `server/worker_runtime.py`; retain `server.main` wrappers and `prepare()` behavior.
- Focused worker/runtime checks pass (13 tests), plus Python compilation and `git diff --check`.

## 2026-09-25 — Extract manga row group presentation

- Move the row/accordion group markup into `MangaRowGroup.tsx`; `ResultGallery` continues to own its state and callbacks.
- Add a static render contract for expanded, collapsed, rename, loading, and retry states.

## 2026-09-25 — Share gallery page selection bar

- Replace the duplicate bulk page action bars in manga detail and row views with `GalleryBulkSelectionBar`; each view still supplies its own selected-page rerender callback.
- Typecheck and focused ResultGallery tests pass.

## 2026-09-25 — Extract gallery download actions

- Move single-image and CBZ download requests/browser downloads into `front/app/features/gallery/downloads.ts`; `ResultGallery` retains status, error, and caller behavior.
- Typecheck and focused ResultGallery tests pass.

## 2026-09-25 — Extract manga series selection dock

- Move the floating selected-manga actions and series-creation toast into `MangaSeriesSelectionDock.tsx`; the gallery keeps selection and series-modal state.
- Typecheck and focused ResultGallery tests pass.

## 2026-09-25 — Extract manga editor inspector

- Move canvas and toolbar presentation into `EditorCanvasStage.tsx` and `EditorToolbar.tsx`; split the inspector into `EditorInspector.tsx` and text, style, and layout tab components. Gesture handlers, selection/history state, and save actions stay in `MangaEditorModal`.
- Frontend typecheck and full test suite pass.

## 2026-09-25 — Extract editor text-region deserialization

- Move saved text-region mapping into `front/app/features/editor/serialization.ts`; keep fallback loading and editor state behavior in `MangaEditorModal`.
- The focused editor geometry/serialization checks and frontend typecheck pass.

## 2026-09-25 — Extract editor review footer

- Move the review summary, original-text choices, and draft/approval buttons into `EditorReviewFooter.tsx`; keep save logic and review-state updates in `MangaEditorModal`.
- The full frontend test suite and typecheck pass.

## 2026-09-25 — Extract Page Detail timing panel

- Move timing and stage-retry presentation into `TimingTab.tsx`; keep timing resolution, manifest loading, and retry queueing behavior unchanged.
- Frontend typecheck and full test suite pass.

## 2026-09-25 — Extract Page Detail tabs

- Move localization, story analysis, and step settings markup into dedicated Page Detail tab components; move their shared audit shape into `ProfessionalAudit.ts` while keeping modal state, tab selection, and retry behavior in place.
- The complete frontend test suite and TypeScript typecheck pass.

## 2026-09-25 — Extract Page Detail header

- Move the image mode, inspection, navigation actions, and page action controls into `PageDetailHeader.tsx`; keep state, callbacks, sidebar, and image-stage ownership in `PageDetailModal`.
- The full frontend test suite and typecheck pass.

## 2026-09-25 — Extract manga detail header and page grid

- Move the single-manga header/action banner and page reordering grid into `front/app/features/gallery/MangaDetailHeader.tsx` and `MangaPagesGrid.tsx`, preserving markup, classes, event handlers, and state ownership.
- The complete frontend test suite and TypeScript typecheck pass.

## 2026-09-25 — Extract translation completion and model execution

- Move post-translation completion, final upscale reversion/editor artifact serialization, and MPS fallback/serialization to `pipeline/completion.py`, `pipeline/finalization.py`, and `pipeline/model_execution.py`; preserve `MangaTranslator` methods, module-level lock aliases, and injected patch points.
- AST comparisons confirmed the moved finalization and MPS bodies are unchanged apart from receiver/dependency bindings and relative-import scope. Isolated impacted checks passed (15 tests); broader adjacent suites expose stale fixtures missing `logger`/`release_runtime` or using the old bubble-detector constructor.

## 2026-09-25 — Extract single-page translation orchestration

- Move the single-page translation flow into `manga_translator/pipeline/single_page.py`; preserve `_translate` and inject the existing logging, CPU-lane, persistence, and dictionary bindings.
- Verify memory-lane, pipeline timing/checkpoint, OCR, batch context, and translation completeness behavior with 53 passing tests.

## 2026-09-25 — Extract translator batch coordination

- Move bounded batch orchestration, concurrent per-page translation, and text dispatch into `manga_translator/pipeline/batch/`; preserve all `MangaTranslator` method signatures and docstrings.
- Verify batch context, professional story grouping, translation dispatch, completeness, pipeline order, and public imports with 47 passing tests.

## 2026-09-25 — Extract batch claim operations

- Move atomic item/group reservation and status mutation into `server/batch_claims.py`; preserve the existing scheduler claim methods.
- Verify claim races, paused/stopping batches, stage barriers, and compatibility through 62 passing focused tests.

## 2026-09-25 — Extract batch group selection

- Move stage-order resolution, translation readiness, queued-item selection, and story-plan remapping into `server/batch_group_selection.py`; retain the `BatchScheduler` methods.
- Verify batch barrier, story-plan mapping, and scheduler contracts with the existing focused tests.

## 2026-09-25 — Extract batch configuration mapping

- Move persisted batch setting conversion into `server/batch_config.py`; keep `BatchScheduler._config_for` and all existing defaults intact.
- Verify config compatibility through the scheduler and pipeline rerun tests.

## 2026-09-25 — Extract batch translation group execution

- Move stage-barrier translation, progress fanout, checkpoint persistence, and translation-resource cleanup into `server/batch_translation_group.py`; preserve the `BatchScheduler` method and progress behavior.
- Verify batch scheduler, batch image context, pipeline rerun, and rerender behavior with 54 passing tests.

## 2026-09-25 — Extract checkpointed batch preparation

- Move checkpoint initialization, single-stage retry, completion, failure persistence, and cleanup into `server/batch_checkpointed_prepare.py`; retain `BatchScheduler._process_checkpointed_prepare_item`.
- Verify scheduler, rerun, and rerender behavior through the existing focused suite.

## 2026-09-25 — Extract batch pipeline rerun lifecycle

- Move rerun planning, staged execution/commit, progress persistence, failure recording, and cleanup into `server/batch_pipeline_rerun.py`; retain the scheduler entry point and rerender delegation.
- Verify success and rollback behavior through the existing pipeline-rerun and rerender tests.

## 2026-09-25 — Extract batch item lifecycle

- Move per-item translation, checkpoint resume, completion/failure persistence, and executor cleanup into `server/batch_item_runner.py`; keep `BatchScheduler._process_item` and its call sites unchanged.
- Verify scheduler, pipeline-rerun, and rerender behavior through the existing 50-test focused suite.

## 2026-09-25 — Extract pre-translation orchestration

- Move colorization, upscaling, detection, OCR, textline merge, and pre-translation setup into `manga_translator/pipeline/orchestrator.py`; preserve `MangaTranslator._translate_until_translation` and its stage order.
- Add a characterization test for the pre-translation stage sequence.

## 2026-09-25 — Extract translator context and output paths

- Move image-context tracking, result metadata assembly, and intermediate output-path selection into `manga_translator/pipeline/context.py`; keep the `MangaTranslator` methods as delegates.
- Verify context creation/restoration, result path placement, rendered metadata, and pipeline timing contracts.

## 2026-09-25 — Extract manga mutation repository

- Move page ordering, metadata/group updates, and result deletion SQL into `server/manga_mutation_repository.py`; keep `PostgresStore` methods and page-order helpers as compatibility delegates.
- Verify store facade queries, page reservations, batch scheduling, and import/route contracts.

## 2026-09-25 — Extract batch job mutations

- Move batch status transitions, retry validation/rescheduling, page removal, and drain-before-delete into `server/batch_mutations.py`; preserve the `BatchScheduler` methods and wake/compaction behavior.
- Add facade-level coverage for active-item scoping, config invalidation, status updates, retry/remove operations, and deferred page-order compaction.

## 2026-09-25 — Extract summary status persistence

- Move PostgreSQL and file-backed summary status reads and job updates into `server/summary_status.py`; preserve `server.main` helper names and runtime-selected result root.
- Verify summary jobs, queue, memory-reclaim, and public-import contracts.

## 2026-09-25 — Extract PostgreSQL result ingestion

- Move file-backed result snapshot upserts, page/document/artifact synchronization, and manifest indexing into `server/result_indexing.py`; keep `PostgresStore.sync_result_folder()` as the existing facade entry point.
- PostgreSQL store tests and public-import contracts pass.

## 2026-09-25 — Move reading progress operations behind MangaRepository

- Delegate `PostgresStore.get_progress()` and `save_progress()` to the existing manga repository while preserving the store methods and response data.
- PostgreSQL store tests and public-import contracts pass.

## 2026-09-25 — Extract batch inference group selection

- Move compatible OCR and page inference group selection to `server/batch_inference_groups.py`; keep the existing `BatchScheduler` methods as delegates.

## 2026-09-25 — Align batch grouping contract tests

- Update stale assertions to cover supported MangaOCR batching and page batching with one GPU call slot; scheduler behavior is unchanged.

## 2026-09-25 — Extract batch stage execution helpers

- Move standard page preparation plus checkpointed OCR and model-stage group execution into `server/batch_stage_executor.py`; retain the `BatchScheduler` method entry points and injected runtime dependencies.
- Verify page preparation, OCR checkpoint handling, model dispatch/retry, resource release, and manifest stage transition.

## 2026-09-25 — Extract file-backed result metadata and queries

- Move metadata caching, atomic metadata writes, page-order updates, review/source classification, and the result/manga-group scans into `server/result_metadata.py` and `server/result_queries.py`; keep the `server.main` helper imports intact.
- Verify file-backed results, Postgres compatibility, original-import behavior, and route/public-import contracts.

## 2026-09-25 — Extract summary job controls

- Move active summary task, pause/resume state, and queued OCR task tracking into `server/summary_jobs.py`; `server.main.SummaryJobController` remains import-compatible.

## 2026-09-25 — Extract original manga import helpers

- Move upload validation, archive scanning, normalized page iteration, atomic filesystem staging, and preview warmup into `server/original_import.py`; keep the existing `server.main` helper names and route wiring.
- Add archive upload coverage for hidden-file filtering, natural page ordering, and alpha-preserving normalization.

## 2026-09-25 — Extract batch resource management

- Move resource capacity defaults, stage-to-resource mapping, GPU concurrency caps, semaphore construction, and acquire/release logging into `server/batch_resources.py`; preserve `BatchScheduler` attributes, methods, and `stage_resource_limits` import.
- Verify capacity, GPU cap, and checkpointed stage scheduling with focused scheduler tests.

## 2026-09-25 — Extract image preprocessing stages

- Move colorization, single-page upscaling, and batch upscaling dispatch into `manga_translator/image_preprocessing_stage.py`; preserve the `MangaTranslator` methods and batch validation.

## 2026-09-25 — Extract inpainting stage

- Move compatible batch validation, missing-mask checks, device sizing, dispatch, and protected-edge restoration into `manga_translator/inpainting_stage.py`; keep the `MangaTranslator` single/batch method surfaces.
- Add coverage through the existing translation-completeness and pipeline-stage tests.

## 2026-09-25 — Extract bubble detection stage

- Move single-page bubble detection, region grouping/ID preservation, artifact logging, compatible batch dispatch, and fallback behavior into `manga_translator/bubble_detection_stage.py`; preserve the `MangaTranslator` methods and their injected detector patch surface.
- Verify bubble layout and pipeline-stage behavior with the focused tests.

## 2026-09-25 — Extract OCR pipeline stage

- Move single-page OCR, compatible page batching, and OCR textline finalization into `manga_translator/ocr_stage.py`; retain the decorated `MangaTranslator` methods as compatibility entry points.
- Add contracts for dispatch arguments, environment restoration, textline finalization, and mixed-backend fallback.

## 2026-09-25 — Extract manga reader actions

- Move reader opening, series-member navigation, and adjacent-member image preloading into `front/app/features/gallery/useMangaReaderActions.ts`; preserve the route callback and existing modal state.
- Add a contract for the reader route arguments and loaded manga data.

## 2026-09-25 — Extract manga gallery data loader

- Move route-driven group fetching, page-cache reads/writes, and stale-response suppression into `front/app/features/gallery/useMangaSummaries.ts`; preserve App-owned state and the existing gallery props.
- Add a contract for query parameters, page totals, loading transitions, and cache contents.

## 2026-09-25 — Extract gallery manga-title loader

- Move paginated title fetching, title normalization, and in-flight request deduplication into `front/app/features/gallery/useServerMangaGroupTitles.ts`; preserve preload and modal-triggered loading.
- Add a contract for page offsets, title filtering, and shared concurrent requests.

## 2026-09-25 — Extract gallery synopsis actions

- Move synopsis loading/generation, job subscription updates, and copy behavior into `front/app/features/gallery/useMangaSummaryActions.ts`; keep gallery modal state and UI props unchanged.
- Add a contract for summary lookup paths and generation request payloads.

## 2026-09-25 — Extract translation batch upload lifecycle

- Move upload resumption, cross-tab deduplication, progress updates, stale-batch cleanup, and failure handling into `front/app/features/upload/translationBatchUpload.ts`; preserve App state and upload-ref ownership.
- Add contracts for failed-batch state and duplicate/stale upload handling.

## 2026-09-25 — Extract Studio color actions

- Move file/batch color exclusion and automatic color detection into `front/app/features/upload/useStudioColorActions.ts`; preserve the four-file detection concurrency and queue item patch request.
- Add a contract for single/bulk color toggles and queued-page API updates.

## 2026-09-25 — Extract Studio file-selection actions

- Move individual/bulk file removal, range selection, select-all, and deselection into `front/app/features/upload/studioFileSelection.ts`; preserve status-based selection rules and cleanup of color, folder, result-URL, and status state.
- Add a contract for removal cleanup, selected-file clearing, and anchor reset behavior.

## 2026-09-25 — Extract gallery mutation actions

- Move manga title updates, page restoration, gallery clearing, image/group deletion, page ordering, and optimistic image replacement into `front/app/features/gallery/galleryMutations.ts`; keep state ownership and `ResultGallery` callback props unchanged.
- Add contracts for delete/update/order request payloads, local collection/count changes, and gallery cache invalidation.

## 2026-09-25 — Extract original-manga upload workflow

- Move original-manga import, upload-batch lifecycle, duplicate-title recovery, validation, and modal actions into `front/app/features/upload/useStudioMangaUpload.ts`; preserve App-owned modal state, shared upload tracking, and form reset behavior.
- Add contracts for multipart request metadata/progress, unsupported files, accepted-file modal setup, upload-batch handoff, title validation, and modal close behavior.

## 2026-09-25 — Extract translation submission controller

- Move Studio translation selection, group confirmation, batch construction, and upload handoff into `front/app/features/translation/translationSubmission.ts`; retain App's modal state and upload lifecycle callbacks.
- Add a contract covering page selection/removal, group/story metadata, color flags, and persistence before upload resume.

## 2026-09-25 — Extract batch action callbacks

- Move batch title, pause/resume, retry, priority, translator, manual-review, dismiss, and removal callbacks into `front/app/features/batches/useBatchActions.ts`; keep state and upload refs owned by `App` and preserve the existing `JobsDrawer` props.
- Keep each callback's request, optimistic update, error, and retry behavior unchanged.

## 2026-09-25 — Extract summary job actions

- Move summary dismiss, retry, pause, resume, and stop callbacks into `front/app/features/summaries/useSummaryJobActions.ts`; preserve API calls and local status updates.
- Add action contracts for request paths and local job-state changes.

## 2026-09-25 — Extract app navigation controller

- Move route parsing, legacy redirects, overlay history, gallery query updates, and page/manga/series navigation callbacks into `front/app/features/navigation/useAppNavigation.ts`.
- Preserve the existing route helpers, callback arguments, navigation state, and route-state contract coverage.

## 2026-09-25 — Extract translation settings controller

- Move translation option state, remembered-setting hydration, current-setting construction, and persistence into `front/app/features/translation/useTranslationSettings.ts`.
- Keep App's existing hydration and persistence effect positions; add a contract for migration defaults, range clamping, and saved settings.

## 2026-09-25 — Extract Studio file intake

- Move image/archive intake, pending archive ordering, queue commit/removal, and input/drop handlers into `front/app/features/upload/useStudioFileIntake.ts`.
- Keep App's paste listener and cross-feature file/status state in place; add a contract for loose-image loading, selection, title, color-check dispatch, and drop order.

## 2026-09-25 — Extract PostgreSQL batch-store adapter

- Move `PostgresBatchStore` to `server/postgres_batch_store.py` and shared JSON, page-order, and safe-folder helpers to `server/postgres_common.py`; re-export the adapter and helpers from `server.postgres_store` for compatibility.
- Preserve the adapter and helper ASTs exactly; update the route contract to inspect the generated OpenAPI surface under the installed FastAPI 0.141.1 lazy-router representation.

## 2026-09-25 — Extract summary persistence repository

- Move summary payload and job persistence methods to `server/summary_repository.py`; preserve all `PostgresStore` method names and signatures as facade delegates.
- Move group conflict/not-found exception definitions beside the series/group repositories and re-export them from `server.postgres_store` unchanged.

## 2026-09-25 — Extract pipeline-state and artifact persistence

- Move stage state, document revision, and artifact revision operations to `server/pipeline_repository.py`; keep the `PostgresStore` methods and document-type mapping available through the existing facade.
- Preserve the public facade import of `SeriesStoreError` and add contract coverage for repository delegation.

## 2026-09-25 — Extract manga and page query operations

- Move page-to-response mapping, group/result listing, page details, group-page loading, and export page selection to `server/manga_repository.py`.
- Keep the existing `PostgresStore` methods as compatibility delegates; retain query ordering, response fields, and file selection behavior.

## 2026-09-25 — Extract virtual document persistence

- Move virtual JSON sidecar save/load/delete operations to `server/document_repository.py`; retain the `PostgresStore` methods as delegates and keep structured documents on their existing revision paths.

## 2026-09-25 — Extract text-detection stage calls

- Move single-page and batch detector dispatch to `manga_translator/detection_stage.py`; keep `MangaTranslator` entry points and injected dispatch functions compatible.
- Add a focused batch-dispatch and settings-guard check; leave detector implementation and pipeline order unchanged.

## 2026-09-25 — Extract summary OCR helpers

- Move saved-language/config resolution, region repair, OCR execution, persistence, and batch OCR handling to `server/summary_ocr.py`.
- Keep the previous helper names in `server.main` as delegates so the summary routes, job flow, and patch points remain stable.

## 2026-09-25 — Manage Search Lab embeddings

- Add indexed/not-indexed collection filters, explicit per-manga index status, and removal of one manga's Search Lab vectors and source records without touching its Gallery content.
- Block removal while an embedding job is active.

## 2026-09-25 — Extract text-translation pipeline stage

- Move the existing stage body to `manga_translator/translation_stage.py`; keep `MangaTranslator._run_text_translation()` as the compatible entry point and preserve the implementation AST exactly.
- The focused none-translator check covers preserved annotations and stage metadata.

## 2026-09-25 — Extract text-grouping pipeline stage

- Move the existing textline merge and region-retention body to `manga_translator/text_grouping_stage.py`; preserve the `MangaTranslator` method and injectable module globals used by existing tests/callers.
- The text-grouping implementation AST is unchanged; numeric-preservation and detection-document persistence checks cover its existing entry points.

## 2026-09-25 — Extract translation post-processing stage

- Move the existing post-translation normalization, dictionary, and retry body to `manga_translator/translation_postprocessing.py`; preserve `MangaTranslator._apply_post_translation_processing()` and its docstring.
- Keep the extracted implementation unchanged and cover the existing entry point with a preserved-annotation check.

## 2026-09-25 — Extract page translation completeness retries

- Move region validation, retry, manual-review fallback, and `TranslationFailure` to `translation_retry.py` and `translation_errors.py`; keep the existing `MangaTranslator` method and exception import paths.
- Translation-completeness, numeric-preservation, and provider-path regressions pass.

## 2026-09-25 — Extract translation provider dispatch

- Move ChatGPT history injection and regular model-executor dispatch to `manga_translator/translation_dispatch.py`; keep `MangaTranslator._dispatch_with_context()` as the compatible entry point.
- Both provider paths and adjacent translation-context/completeness tests pass.

## 2026-09-25 — Extract previous-page translation context builder

- Move the history selection and formatting logic to `manga_translator/translation_context.py`; retain `MangaTranslator._build_prev_context()` as the existing call surface.
- Context, translation-validation, and translation-completeness tests pass.

## 2026-09-25 — Extract translation validation

- Move repetition, target-language, per-translation, and retry validation to `manga_translator/translation_validation.py`; retain the existing async `MangaTranslator` methods as delegates and preserve validation/retry behavior.
- Focused validation and translation-completeness tests pass.

## 2026-09-25 — Extract gallery header and dialogs

- Move the header controls plus create/add-series, move-to-manga, delete-confirmation, and saved synopsis dialog markup into gallery feature components while keeping parent state/actions, classes, and interaction flow unchanged.
- Frontend tests and TypeScript typecheck pass.

## 2026-09-25 — Extract Gallery page modal route synchronization

- Move route-driven page view/edit modal lookup and loading into `front/app/features/gallery/useGalleryPageModalRoutes.ts`; preserve the existing state setters, API mapping, and cancellation behavior.
- Frontend tests and TypeScript typecheck pass.

## 2026-09-25 — Extract gallery row cards and read-progress helper

- Move the memoized row-card grid into `front/app/features/gallery/RowGroupCards.tsx` and the local-storage read-progress wrapper into `front/app/utils/resultGallery.ts`, preserving markup, styling, callbacks, and fallback behavior.
- Frontend tests and TypeScript typecheck pass.

## 2026-09-25 — Add selectable text embedding models to semantic search lab

- Keep BGE as default and add pinned Qwen3-Embedding-0.6B and thenlper/gte-base choices with their required pooling and query formatting.
- Store model identity in the index manifest and support independent model-specific indexes for A/B evaluation. Combined output displays cosine scores per modality while retaining RRF for ranking.
- Add retrieval-only `retrieve` command that returns ranked summary chunks, cosine scores, and source offsets as JSON without running a generator.

## 2026-09-25 — Extract series persistence repository

- Move series queries and mutations to `server/series_repository.py`; keep `PostgresStore` methods as compatibility delegates and re-export the existing series exceptions.
- Preserve the SQL/method bodies exactly apart from delegating shared helpers; API and non-database regressions pass. The live Postgres integration test could not connect to its configured localhost database in the sandbox.

## 2026-09-25 — Extract original imports and internal translation routes

- Move the multipart original-manga import endpoint into `server/api/routes/result_import.py`, preserving route aliases, validation, archive iteration, persistence, preview warmup, and the `server.main` handler import.
- Move both internal batch translation endpoints into `server/api/routes/internal_translation.py`, retaining the lazy translator import and handler names.
- Extend route and public-import contracts; run original manga import and broader API sync regressions.

## 2026-09-25 — Extract direct translation routes

- Move direct JSON, byte, image, form, and streaming translation handlers into `server/api/routes/translation.py`; keep route metadata, transforms, indexing, and `server.main` imports intact.
- Add route-method contracts and verify the broader backend API, editor, summary, result-file, and pipeline regressions.

## 2026-09-25 — Extract pipeline rerun routes

- Move rerun and legacy rerender endpoints into `server/api/routes/pipeline_reruns.py`, keeping batch creation, eligibility checks, route aliases, and `server.main` handler imports unchanged.
- Add a request-level contract for the shared missing-page-selection error; rerun and batch-scheduler tests pass.

## 2026-09-25 — Extract result-file and pipeline-manifest routes

- Move pipeline manifest and result-file GET/HEAD handlers into `server/api/routes/result_files.py`, retaining the existing helper and handler import surfaces.
- Extend path/import contracts and verify filesystem and persisted-artifact behavior through route, result, and pipeline-timing tests.

## 2026-09-25 — Extract editor HTTP routes

- Move layout preview, editor save, and page-review handlers into `server/api/routes/editor.py`, retaining `server.main` handler names and response behavior.
- Extend route and public-import contracts; editor API sync and refactor contract tests pass.

## 2026-09-25 — Support summary-only semantic lab indexing

- Add `index --mode summary|image|all`; skipped modality caches are retained so either modality can be indexed later.
- CLI search defaults to five results and prints up to 1,000 characters of matching summary context.

## 2026-09-25 — Extract summary API routes

- Move summary status, configuration, job-list, event-stream, create, pause, resume, stop, and dismiss handlers into `server/api/routes/summaries.py`; retain their `server.main` handler names and response behavior.
- Extend route and public-import contracts; summary job, queue, and refactor contract tests pass.

## 2026-09-25 — Extract manga and result-page routes

- Move manga page ordering, metadata/group mutations, result details, and result deletion endpoints into `server/api/routes`, preserving handler imports, route paths, registration order, and existing responses.
- Extend route and public-import contracts; the API sync and refactor contract tests pass.

## 2026-09-25 — Batch summary image extraction

- Process summary OCR pages in bounded worker-sized groups, batching compatible detection and OCR stages before per-page text merging and persistence.
- Fall back to single-page extraction when a batch fails.

## 2026-09-25 — Show per-step summary page progress

- Show the active summary image step and the number of pages completed in it, including cached pages and pages with no detected text.

## 2026-09-25 — Start behavior-preserving module extraction

- Move Gallery page/manga cards, helpers, selection state, drag auto-scroll, and on-demand page loading into feature modules, along with editor canvas/geometry and Page Detail panels; keep existing component import surfaces. Move batch UI helpers, health, batch, CBZ export, manga-list/page, reader-progress, series, and translation-batch routes, and summary, manga, series, batch, editor, and pipeline request schemas into their feature or `server/api` modules without changing route or validation behavior. Add refactor contract checks for compatibility imports, routes, batch and reading-progress responses, and pipeline order.

## 2026-09-25 — Add frontend flow contracts for refactoring

- Add route registration and upload, gallery, batch-stream, and summary-control behavior contracts under `front/app/refactor-contract`; run them through the existing frontend test command.
- Add the behavior-preserving extraction protocol and layout-algorithm constraints to the root `AGENTS.md`.

## 2026-09-25 — Extract translation batch item rows

- Move the job thumbnail and page action row into `front/app/features/batches/BatchItemRow.tsx`; retain the existing imports, status labels, controls, classes, preview behavior, and callbacks.
- Add a static-render contract for queued, awaiting-translation, and failed rows; existing translation section checks and frontend typecheck pass.

## 2026-09-25 — Extract translation batch cards and page virtualization

- Move `BatchCard`, its action buttons, and the focus-aware virtualized page list into `front/app/features/batches`; keep `BatchCard` importable through `TranslatingSection` for existing callers.
- Add static-render contracts for batch card content and list accessibility/windowing; targeted component tests and typecheck pass.

## 2026-09-25 — Improve gallery detail links

- Page and manga detail destinations use native links for browser open-in-new-tab and copy-link behavior; Page Detail can copy its canonical page URL.

## 2026-09-25 — Skip waiting pages in active batches

- Jobs page rows can skip queued pages and pages waiting for translation; active stage work continues without interruption.

## 2026-09-24 — Prevent source Japanese text leaks during canvas restoration

- Constrained `render_page()` restore paths so only explicitly preserved regions or reviewed regions with explicit restore geometry can copy source pixels over the inpainted canvas.

## 2026-09-24 — Add devscript to export server manga datasets for semantic search lab

- Added `devscripts/export_manga_for_search.py` to pull manga pages and AI summaries from the running server and format them into the required directory structure for `semantic_search_lab.py`.

## 2026-09-24 — Keep free-text translations inside their manga panel

- Infer conservative axis-aligned panel bounds from frame lines and constrain free-text wrapping and placement to those bounds.

## 2026-09-24 — Group neighboring Japanese text columns by shared geometry

- Use orientation-aware overlap and spacing metrics in both textline grouping stages; verbose runs persist pair diagnostics.

## 2026-09-24 — Add devscript for manga panel detection and instance segmentation

- Added `devscripts/detect_panels.py` supporting:
  - `leoxs22` (`leoxs22/manga-panel-detector-yolo26n`): lightweight YOLO26n bounding box detector for panels and text.
  - `shadowb` (`ShadowB/Manga109-panel-balloon-text-yolov26-segmentation`): YOLO26s instance segmentation model for panels/frames, speech balloons, and text regions with high-resolution polygon masks.
- Features automatic Hugging Face model downloads, Ultralytics serialization compatibility shims (`Segment26`, `Proto26`, `E2ELoss`), reading-order sorting (RTL/LTR), visual overlays, panel cropping, and structured JSON export.


## 2026-09-24 — Separate source restoration from translated rendering

- Restore suppressed regions once before drawing and keep them out of legacy and frozen renderers.

## 2026-09-24 — Reduce Studio overlay and page-viewer work

- Jobs use batch-sized thumbnails and window large expanded page lists; Page Detail uses reader-sized variants until zoom requires full resolution, and unchanged SSE batches retain their references.

## 2026-09-24 — Improve mobile reader page selection

- Give touch readers a dedicated page jump row with a larger numeric input and tap target.

## 2026-09-24 — Reduce repeated layout search work

- Enable strict ideal free-text placement by default, expand exhaustive search only for colliding fast placements, and use exact prefix-based row-slot, DP word-width, compaction, and whitespace-count calculations.

## 2026-09-24 — Preserve dialogue font consistency in bubble layout

- Use a page-level dialogue font baseline for bubble targets so a single long word cannot silently shrink its whole region; keep one dictionary-backed hyphenation rescue for severe compression.

## 2026-09-24 — Remove legacy API-root frontend

- Removed the embedded translator page at `/`; the API server remains available on port 8000, with OpenAPI docs at `/docs` and the separate Studio on port 6868.

## 2026-09-24 — Search existing manga when moving pages

- Added a title filter to Gallery's Move to Manga dialog.

## 2026-09-24 — Batch DeepL translations across pages

- DeepL now receives grouped manga text as native multi-text requests, splitting only at the API body-size limit, and returns ordered per-region translations.

## 2026-09-24 — Download original manga CBZ archives

- Added an original-page CBZ option for manga groups, using retained source images and an `_original.cbz` filename.

## 2026-09-24 — Make memory profiling verbose-only

- Memory snapshots now log at DEBUG; server console level can be set with `LOG_LEVEL` in `.env` or `--log-level`.

## 2026-09-24 — Reduce layout solver work without changing fallback selection

- Scoped solver profiles to each layout, keyed line-alpha masks by font selection, and reused immutable candidate rasters for placement and overflow checks.
- Deferred free-text QA, reused page-local geometry and row-slot results, and added feasibility checks before hyphenation rescue.
- Added strict, disabled-by-default free-text early acceptance, shadow comparison, and a repeated layout benchmark with workload counters.

## 2026-09-24 — Show batch stage progress

- Job cards now show the current pipeline step and how many pages have passed it, using the existing per-page stage records.

## 2026-09-24 — Add local semantic-search experiment harness

- Added a developer CLI and thin notebook frontend for indexing manga ZIPs/directories, reusing BGE/SigLIP vectors incrementally, comparing exact image/text/RRF retrieval, generating standalone HTML evidence, and benchmarking labeled queries.
- Search encoders now prefer CUDA, then MPS, then CPU, with an optional explicit device.

## 2026-09-24 — Add TTL and memory-pressure eviction for shared models

- Added shared active-use/last-use tracking, backend unload calls, LRU eviction at 60% process-RAM pressure, full idle cleanup at 72%, and a 120-second server default (`--models-ttl 0` keeps entries indefinitely).
- Deduplicated bubble model instances across inference settings, removed detector source-image copies, streamed 48px OCR/MangaOCR crops in bounded chunks, and reduced scheduler page groups as RSS rises.

## 2026-09-24 — Log actual GPU inference batch sizes

- Added bubble detector page-count logs, inpainting tensor batch/device logs, and backend-specific upscaler logs that distinguish tensor batching, tiled calls, and NCNN-Vulkan directory processing.

## 2026-09-24 — Run page layout concurrently

- Isolated FreeType font state per worker thread, keyed text-wrapping cache entries by font selection, and removed the page-wide render lock from layout.

## 2026-09-24 — Reduce software-rendering work in Studio overlays

- Moved Jobs and Page Detail into a shared overlay portal, keeping the blurred/tinted background while scrolling only the overlay surface.
- Isolated and memoized the Page Detail image stage, retained decoded view sources, coalesced image and sidebar geometry updates, and memoized/contained Jobs cards and rows.
- Added a development-only `?renderPerf=1` probe for frame pacing, p95 frame time, click response, long tasks, React commits, Chromium long-frame layout/render timing, and acceptance thresholds.

## 2026-09-24 — Context-aware 3-state OCR numeric preservation and noise filtering

- Implemented 3-tier OCR numeric classification (`MEANINGFUL_NUMERIC`, `POSSIBLE_NUMERIC`, `NOISE_NUMERIC`).
- Numeric regions with semantic units, brackets, date/time formats, multi-digits, speech bubble membership, or spatial proximity to adjacent text regions are classified as `MEANINGFUL_NUMERIC` and preserved directly.
- Standalone digits with lower confidence default to `POSSIBLE_NUMERIC` (preserved on canvas with review flag enabled) rather than silent deletion.
- Pure noise and low-confidence isolated artifacts are filtered out as `NOISE_NUMERIC`.

## 2026-09-24 — Batch MPS text detection by worker count

- Removed the MPS single-page scheduler gate. Compatible default/DBNet pages now share one detector call up to the configured `--workers` count while accelerator calls remain serialized.

## 2026-09-24 — Reduce memory for batched MPS text detection

- Run default and DBConvNeXt detectors with MPS FP16 autocast and inference mode to reduce full-resolution activation memory without splitting the page batch. Keep detector outputs in FP32 for postprocessing, and do not retry MPS memory errors on CPU.

## 2026-09-24 — Batch MangaOCR crops across pages

- Include MangaOCR in worker-sized OCR page groups and batch its text-generation crops and auxiliary confidence/color crops across pages. Verbose mode no longer forces OCR page groups to run serially.

## 2026-09-24 — Render saved speech-bubble regions

- Page detail now outlines persisted bubble polygons in source-image coordinates and reports loading or missing region data.

## 2026-09-24 — Restore completed batch pages to a manga

- Manga detail can reassign existing result pages from terminal translation batches matching its title or group ID, without uploading or translating them again.
- Completed translation and rerender batches invalidate cached manga page lists so newly saved or replaced pages appear in an already-open detail view.

## 2026-09-24 — Keep GPU-capable stages on the selected accelerator

- Scheduler GPU stages now wait for the shared accelerator slot instead of rerunning on CPU when another page is using it; removed the fallback-only CPU model cache.
- Compatible page groups for 48px OCR, CTC, bubble detection, and default AOT inpainting now scale to the startup `--workers` count. OCR crop batches expand as needed to include at least one crop from each grouped page.
- Compatible default text-detection pages also share a model call on non-MPS GPUs, even with one active GPU slot. MPS detection stays page-serial due to measured driver-memory growth and a native heap-corruption failure.
- LaMa Large starts at batch size one for activation memory; LaMa MPE stays single-page because its positional-encoding path assumes batch size one; inpainting batches cap padding overhead at 25%.

## 2026-09-24 — Serialize GPU stages and use CPU for queued pages

- Keep accelerator-backed model calls to one at a time. With multiple in-process workers, pages that find the GPU stage occupied can use an isolated CPU model cache and a CPU-heavy slot instead of waiting on GPU execution.

## 2026-09-24 — Keep MPS inference serial after native crash

- Reverted two-call MPS inference and paired text detection after a native heap-corruption abort; other devices retain two GPU slots.

## 2026-09-24 — Keep two CPU stages available for two pipelines

- Preserve two background CPU slots when `--workers=2`, allowing two pages to run CPU-heavy layout or mask generation concurrently even when auto-reserving a core would reduce the calculated limit to one.
- An explicit `--cpu-stage-workers` value still controls the shared background pool.

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

## 2026-09-24 — Render correctness and source-first typography

- Unified post-translation content resolution so only preserved source text or a non-empty translation can render; frozen layouts now carry content checksums, source ownership, cleanup masks, and region QA.
- Made bubble and free-text typography source-first with geometric validation for implausibly tiny OCR sizes, added bounded fallback hyphen splits for unknown long names, hard final collision validation, source-footprint placement anchoring, and region-local residual cleanup independent of English glyph coverage.
- Prevented review restoration from repainting source members already owned by a grouped render, and added deterministic contract regressions for numeric preservation, Japanese source leaks, ownership-aware restoration, immutable provenance, free-text sizing/anchoring, hyphen rescue, duplicate ownership, and cleanup.
- Versioned the layout algorithm fingerprint so existing bad `layout.json` artifacts are rejected and rebuilt automatically during rendering.

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

## 2026-09-25 — Make Page Detail overlays easier to inspect

- Hide region IDs until selection, show inspection details on click, and add copy-ID controls across translated text, original detector regions, and speech-bubble detections.
- Reuse loaded speech-bubble detections when switching views and show image retry only after a failed load.

## 2026-09-25 — Keep gallery reader lifecycle in its feature

- Moved reader return-position capture and scroll/highlight effects into gallery reader hooks without changing navigation or scroll behavior.

## 2026-09-25 — Continue frontend feature extraction

- Moved gallery group assembly, automatic page loading, and reader image selection into `useGalleryMangaGroups`, preserving existing memo and effect dependencies.
- Moved gallery filtering and count derivation into `useGalleryGroupViews`, preserving existing search and status-filter behavior.
- Moved gallery summary state and route-driven loading into `useGalleryData`, keeping the route effect at its existing hook position.
- Moved Studio screen markup into `features/studio/StudioWorkspace.tsx`; its conditionals, styles, and callbacks are unchanged.

## 2026-09-25 — Move manga group mutations behind the service boundary

- Moved file-backed and PostgreSQL group deletion into `MangaMutationService`; the route keeps its response and error mapping.

## 2026-09-25 — Separate reader page rendering

- Moved the memoized page item, URL fallback helpers, and reader display types into `features/reader/PageItem.tsx`; the existing modal path re-exports its reader helper/type surface.
- Moved page-width and single-page-fit controls into `features/reader/ReaderWidthMenu.tsx`, preserving the existing controls and callbacks.
- Moved reader image preload windows and series-member navigation helpers into `features/reader/readerUtils.ts`, keeping the existing modal exports.
- Extracted the reader's infinite-scroll and single-page content into `features/reader/ReaderPageStage.tsx`, preserving the modal's refs, callbacks, and DOM structure.
- Moved App navigation/title, Studio IndexedDB, and settings persistence effects into `features/studio/useAppPersistence.ts` with their original ordering and dependencies.
- Moved pipeline rerun dialog state, request execution, and resulting batch updates into `features/batches/usePipelineRerunActions.ts`, retaining the existing App callbacks.
- Moved Studio preview image construction and lightbox state into `features/studio/useStudioLightbox.ts`, preserving the existing settings payload and callbacks.
- Moved textless checkpoint finalization into `server/batch_checkpointed_prepare.py`, retaining the scheduler method used by callers and tests.
- Added `MangaMutationService` for shared PostgreSQL/file-backed page ordering and manga title updates; route URLs, status mapping, and response bodies remain unchanged.

## 2026-09-25 — Separate gallery modal composition

- Moved the gallery's delete, move, summary, page detail, editor, reader, and series overlay JSX into `GalleryOverlays`; modal state, callbacks, order, and styling remain with the gallery coordinator.

## 2026-09-25 — Extract queue item row

- Moved the existing per-page queue row component into `QueueItemRow.tsx`; visual markup, download URL selection, and event behavior are unchanged.

## 2026-09-25 — Extract Studio image card

- Moved the existing translated-image card into `ImageHandlingCard.tsx`; selection, progress, download, color controls, and visual markup are passed through unchanged.

## 2026-09-25 — Extract pending upload ordering panel

- Moved the existing pending-file reorder list and drag auto-scroll lifecycle into `PendingFilesOrderPanel.tsx`; ordering callbacks and panel markup remain unchanged.

## 2026-09-25 — Extract summary job row

- Moved the summary job card and its progress/actions into `SummaryJobRow.tsx`; grouping, callback arguments, and rendered controls remain unchanged.

## 2026-09-25 — Extract series creation modal

- Moved series creation modal JSX into `CreateSeriesModal.tsx`; selection, ordering, validation, and submission remain controlled by `SeriesLibrary`.

## 2026-09-25 — Extract gallery library presentation

- Moved the card and row library views into `GalleryLibraryView`, preserving card props, bulk actions, filter reset behavior, and pagination ownership.

## 2026-09-25 — Move Studio startup restoration into its feature

- Moved settings hydration, saved Studio state restoration, and initial batch recovery into `useStudioInitialization`; kept effect registration position and the existing callbacks.

## 2026-09-25 — Separate layout diagnostics from solver decisions

- Moved solver profiling and content tracing into `rendering/layout/profiling.py`; the solver keeps the same compatibility imports and layout decisions.
- Moved bubble row geometry and DP line breaking into `rendering/layout/line_breaking.py`, preserving the solver helper names and parameters.
- Moved paragraph-gap classification and vertical rhythm compaction into `rendering/layout/paragraph_flow.py`, preserving solver and pipeline-runner imports.
- Moved source-line font calibration and original typography profile construction into `rendering/layout/source_profile.py`, preserving the established helper names.
- Moved font-size policy calculations into `rendering/layout/font_policy.py`; solver helper names, constants, and behavior remain unchanged.
- Moved free-text candidate generation, wrapping, ink metrics, and typography scoring helpers into `rendering/layout/free_text_typography.py`, retaining the solver helper imports.
- Moved page-level joint candidate selection and collision geometry into `rendering/layout/joint_layout.py`; candidate scoring and search order are unchanged.
- Moved free-text damage coverage, panel constraints, overflow checks, and offset search helpers into `rendering/layout/free_text_search.py`, retaining the solver helper imports.
- Moved free-text candidate search orchestration and result materialization into `rendering/layout/free_text_solver.py`, retaining the solver entry points.
- Moved bubble candidate bounding-box and glyph-pixel checks into `rendering/layout/validation.py`, retaining the solver helper imports.
- Moved shared-bubble grouping and source-owned zone partitioning into `rendering/layout/bubble_zones.py`; the page font baseline helper stays in the solver to preserve its estimator patch point.
- Moved bubble glyph-mask construction and composite candidate scoring into `rendering/layout/scoring.py`, preserving the formulas and constants.
- Moved placement-zone debug visualization into the existing `rendering/layout/debug.py` module; the solver helper name remains available.
- Moved bubble block centering and horizontal alignment into `rendering/layout/centering.py`, preserving the existing search sequence.
- Moved OCR hyphen-split repair into `rendering/layout/text_normalization.py`, preserving the solver helper names and decisions.
- Moved page-level bubble/free-text orchestration into `rendering/layout/orchestration.py`; `solver.apply_shape_aware_bubble_layout` retains its signature and dispatch behavior.
## 2026-09-25 — Extract translation story preview parts

- Moved image thumbnails, large page previews, and the story split handle into `TranslationSubmitParts.tsx`; modal state, callbacks, and styles remain unchanged.

## 2026-09-25 — Give free-text translations more vertical room

- Allow free-text layout candidates up to 1.5× the source region's height. Preserve distinct typography and placement alternatives so colliding regions can be selected together safely.
## 2026-09-25 — Split remaining large Python modules

- Extracted window feature mixins, diffusion model groups, DPM-Solver utilities, quadrilateral geometry, layout search, rendering placement helpers, and translator text/client helpers while retaining original import surfaces.

## 2026-09-26 — Keep original manga uploads out of the image scheduler

- Original manga uploads now finish through the gallery import endpoint without creating a translation batch; gallery refresh failures no longer mark an already completed import as failed.

## 2026-09-26 — Preserve source typography through bubble reruns

- Bubble fitting now handles 7-letter hyphenation, allows a half-target review fallback before suppression, keeps multi-lobe fallbacks within the preferred font floor, and retains source fonts and region identity in editor reruns.

## 2026-09-26 — Calibrate oversized vertical OCR text for free-text layout

- Multi-glyph OCR boxes now provide a realistic source font estimate so translated paragraphs can shrink and wrap into the available free-text region.

## 2026-09-26 — Preserve calibrated typography and recover nearby free-text layouts

- Legacy bubble fitting now respects calibrated source sizes. Free-text mask growth no longer clips unassociated text to a neighboring bubble, and final validation prevents drawable text from crossing source text that will be restored.
- Final readability validation now suppresses and restores translations below 85% of their calibrated source font instead of only flagging them.
## 2026-09-28 — Reduce repeated free-text layout preparation

- Reused source ownership maps for damage assignment, limited scope dilation to its padded source region, and shared ink measurements between scoring and candidate diagnostics. The measured maps and masks preserve their existing ownership and placement semantics.

## 2026-09-29 — Highlight only review-required bubbles in Page Detail

- Translated results open with only review-required bubbles highlighted; the full Bubbles overlay remains opt-in. The same behavior applies in Translation Jobs because they use Page Detail.

## 2026-09-29 — Download original images from batch rows

- Batch item rows now expose the original upload alongside the translated image download.

## 2026-09-29 — Default fresh studio users to ALL CAPS lettering

- New users now start with ALL CAPS lettering; previously remembered settings still override the default.
