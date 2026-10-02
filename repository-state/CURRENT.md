# Current state

Last reviewed: 2026-10-02

Manga Image Translator is a Python translation and image-rendering pipeline with a React Router web studio. Detailed feature and extraction notes from the previous snapshot are in [CURRENT_DETAILS.md](CURRENT_DETAILS.md); consult that file only when the relevant subsystem needs them.

## Architecture and contracts

- The pipeline covers detection, OCR, bubble grouping, translation, masking, layout, inpainting, and rendering. Batch checkpoints persist stage outputs, and reruns resume from saved artifacts.
- Keep public imports and signatures stable during behavior-preserving extractions. Preserve stage order, candidate ordering, and error behavior. See [AGENTS.md](../AGENTS.md).
- OCR grouping, free-text coalescing, layout ownership, hard placement constraints, and final raster validation are contractual. Update [LAYOUT_AND_TEXT_MERGE_RULES.md](LAYOUT_AND_TEXT_MERGE_RULES.md) and characterization tests together when changing them.
- Layout performance limits and validated measurements are in [LAYOUT_PERFORMANCE.md](LAYOUT_PERFORMANCE.md). Do not change placement quality rules as part of performance-only work.
- `server.main` and existing translation/rendering modules retain compatibility surfaces for extracted implementations.
- Original manga uploads stage files under `BATCH_ROOT/manga-import-jobs` and use a separate durable FIFO queue in PostgreSQL or filesystem mode. One import runs at a time; completion follows gallery indexing. Failed imports keep staged files for retry, while translation and summary schedulers remain independent.

## Working memory

- Read this page before substantial changes; open [CURRENT_DETAILS.md](CURRENT_DETAILS.md) only for relevant feature or module history.
- Search [BUGS.md](BUGS.md) and [CHANGES.md](CHANGES.md) with `rg` when matching history matters; read the relevant entries only.
- Record discovered bugs and their prevention lessons in [BUGS.md](BUGS.md).
- Record features and large behavior, architecture, dependency, storage, API, or workflow changes in [CHANGES.md](CHANGES.md).
- Preserve unrelated working-tree changes. Check `git status` before editing.

## Common checks

- Python: `pytest`
- Frontend: `cd front && npm test`
- Frontend types: `cd front && npm run typecheck`
- Frontend build: `cd front && npm run build`
- Line-count ratchet: `python3 devscripts/check_line_limits.py`
- Saved-page and local stage-focused pipeline diagnosis: see `devscripts/pipeline_case.py` (`check`, `run`, `compare`, `get`, `rerun`) and `.agents/skills/manga-pipeline-case/SKILL.md`.

See [README.md](../README.md), [CONTEXT.md](../CONTEXT.md), [PRODUCT.md](../PRODUCT.md), and [DESIGN.md](../DESIGN.md) for setup, domain language, product purpose, and UI conventions.

## Initial-layout preparation optimization

Native glyph bitmap copying and batched bubble row-slot extraction are implemented. Glyph-only and combined variants each preserve exact snapshots, semantic QA and decoded RGB across 36 retained-page fresh-process comparisons. Focused glyph contracts passed 191 tests; row/core contracts passed 44 tests. Final combined contracts, isolated commit rendering checks and performance acceptance remain pending. The restored `.venv` uses Python 3.13 with updated NumPy/OpenCV; the first 2026-10-01 attempt failed all three CPU preflights. A retry passed the start screen but stopped after 26/90 processes when one control sample exceeded the 40% CPU limit. Partial eligible-pair cohort improvement was 12.6%, without acceptance certification; observed matched outputs remain exact but frozen-reference QA and two bubble-geometry input fingerprints differ under the updated environment. An expanded 37-image, 370-process survey preserved compared layout/region/RGB outputs across all pairs and repetitions. Jointly eligible pairs show 8.2% lower cohort runtime; 35 noisy samples, a 6.194 s page median and eight unchanged baseline validation-error pages prevent acceptance. Evidence: `.scratch/initial-layout-implementation/expanded/performance-1/report.md`. No accepted speed claim or implementation commit yet.

## PP-OCRv6 Small Manga CPU OCR

`Ocr.ppocrv6` (`"ppocrv6"`, UI label `"PP-OCRv6 Small Manga"`) provides CPU-optimized ONNX Runtime recognition (`Kellenok/PP-OCRv6_manga` `manga_rec_v0.2.onnx` + `ppocrv6_dict.txt`). Because the SVTRv2 neck applies global self-attention across width without a padding mask, `ModelPPOCRv6` avoids cross-width zero-padding and runs unpadded exact-width groups across a bounded intra-page `ThreadPoolExecutor` with Otsu foreground/background color estimation, while scheduling `ocr` as `ResourceClass.CPU_HEAVY` on CPU stage lanes without acquiring GPU/MPS model slots.

## Asynchronous Batch Input Prefetching

`prefetch_input_stream` (`manga_translator.pipeline.batch.prefetch`) provides asynchronous CPU decoding and normalization of batch images via `asyncio.to_thread` with a bounded queue (`max_prefetch=2`). In `translate_batch` (`manga_translator.pipeline.batch.workflow`), each image immediately advances to GPU detection and page preparation upon completing the CPU input stage, while subsequent batch images are concurrently read and decoded on CPU in the background.
