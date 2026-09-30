---
name: manga-pipeline-case
description: Run, inspect, and compare Manga Image Translator pipeline cases from local images/folders or saved studio page URLs, automatically running the full pipeline into ./devscripts/data on first run and reusing saved JSON data for separate-directory stage reruns on subsequent runs.
---

# Manga pipeline case

Use this skill when the user specifies local image(s)/folder(s) and a target stage to focus on (for example, `layout` on `~/Downloads/slow`), or when the user supplies a saved studio page/result URL to reproduce a detection, OCR, speech-bubble, translation, mask, layout, inpainting, or rendering issue.

## Workflow A: Local images or folders + target stage (`./devscripts/data`)

1. **Check whether the images have been run before** in `./devscripts/data`:
   ```bash
   python devscripts/pipeline_case.py check <image-or-folder> --stage <stage>
   ```
   Supported stages: `input` (`full`), `detection`, `ocr`, `bubble_detection`, `textline_merge`, `translation`, `mask_generation`, `layout` (`typesetting`), `inpainting`, `rendering`.
   The JSON output reports `allRunBefore`, `recommendedAction` (`run_full_pipeline` or `rerun_stage`), `baselineDir`, and `lastRunJson` for each image.

2. **First run (when images have NOT been run before)**:
   Run `pipeline_case.py run` to execute the full pipeline from scratch and save the baseline JSON (`case.json`, `detection.json`, `ocr.json`, `bubble_detections.json`, `panel_detections.json`, `text_regions_merged.json`, `translations.json`, `layout.json`, `text_regions.json`, `regions.json`, `profiling.json`, `config.json`, `meta.json`) and related stage images (`input.png`, `img_rgb.png`, `mask_raw.png`, `mask_final.png`, `inpaint_mask.png`, `bubble_mask.png`, `protected_bubble_edge.png`, `inpainted.png`, `rendered.png`, `final.jpg`) in `./devscripts/data/<case>`:
   ```bash
   python devscripts/pipeline_case.py run <image-or-folder> --stage <stage> --output /tmp/pipeline-baseline.json
   ```

3. **Subsequent runs (when images HAVE been run before, or after implementing a code change)**:
   Re-check the saved state with `check`, then run only from the target stage onward. This reuses the last run's saved JSON data (plus baseline images/masks from `./devscripts/data/<case>`) and writes the new state (`case.json`, updated stage `.json` files, `rendered.png`, `final.jpg`, and `comparison.json`) into a **separate directory** so the original baseline data in `./devscripts/data/<case>` is never overwritten:
   ```bash
   python devscripts/pipeline_case.py run <image-or-folder> --stage <stage> --output-dir ./devscripts/data/runs/<run-name> --output /tmp/pipeline-rerun.json
   ```
   - If `--output-dir` is omitted, `pipeline_case.py` automatically creates a separate timestamped directory under `./devscripts/data/runs/<timestamp>_<stage>/<case>`.
   - To reuse the original baseline JSON instead of the most recent stage rerun JSON, pass `--from-baseline` (or `--from-run <dir>` to reuse a specific run).
   - To test setting or artifact overrides without modifying saved files, pass `--patch /tmp/pipeline-patch.json`.

4. **Compare old and new states**:
   Inspect the generated `comparison.json` inside the rerun directory, or compare any two saved runs directly:
   ```bash
   python devscripts/pipeline_case.py compare ./devscripts/data/<case> ./devscripts/data/runs/<run-name>/<case>
   ```
   Report region-level changes (font size, line wrapping, bounds, review flags, rendered image hash, and solver workload metrics). Keep the original `./devscripts/data/<case>` baseline untouched for future comparisons.

## Workflow B: Saved studio page or result URL

1. **Get saved page details**:
   ```bash
   python devscripts/pipeline_case.py get '<page-url>' --output /tmp/pipeline-case.json
   ```
   This calls the read-only `GET /api/pipeline-cases/{page-ref}/data` endpoint and returns page metadata, settings, pipeline manifest, and stage artifacts with stable prompt IDs (`detection_1`, `ocr_region_1`, `speech_bubble_1`, `text_region_1`).

2. **Preview a stage rerun in temporary files**:
   Choose the earliest stage that must be rerun (`input`, `detection`, `bubble_detection`, `translation`, or `layout`). Prepare an optional `--patch /tmp/pipeline-patch.json` containing `settingsOverrides` and/or replacement `artifacts`, then run:
   ```bash
   python devscripts/pipeline_case.py rerun '<page-url>' --from <stage> --patch /tmp/pipeline-patch.json --output /tmp/pipeline-rerun.json --render-output /tmp/pipeline-preview.jpg
   ```
   URL reruns use `/api/pipeline-cases/preview`, which executes in a temporary copy without modifying the saved page or database records. Never call `/api/results/rerun` for debugging.
