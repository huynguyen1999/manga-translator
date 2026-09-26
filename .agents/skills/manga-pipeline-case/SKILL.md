---
name: manga-pipeline-case
description: Inspect a saved Manga Image Translator page and reproduce a reported detection, OCR, speech-bubble, translation, layout, or rendering defect in an isolated copy.
---

# Manga pipeline case

Use this skill when the user supplies a saved studio page or result URL and reports a bad region, bubble, detection, translation, or rendered result. Ask for the visible region ID only if the reported area cannot otherwise be identified.

1. Get the saved page details with `python devscripts/pipeline_case.py get '<page-url>' --output /tmp/pipeline-case.json`. This calls the read-only `GET /api/pipeline-cases/{page-ref}/data` endpoint, which returns page metadata, settings, pipeline manifest, and all available stage artifacts in one JSON response without running layout or rendering. Use that JSON to identify the failed stage and find the reported ID. Detection, OCR, speech-bubble, and text-region artifacts include stable prompt IDs. Use this command for structured page data; leave studio interaction to the human visual review.
2. Choose the earliest stage that must be rerun: `input` reruns the full pipeline, `detection` reruns detection/OCR and downstream work while reusing saved bubble data, `bubble_detection` reruns the full pipeline, `translation` reruns translation and typesetting, and `layout` reruns typesetting/rendering.
3. Apply a proposed correction to a JSON patch file when needed. It may contain `settingsOverrides` and replacement JSON documents under `artifacts` (for example, `text_regions.json` or `bubble_detections.json`). Then run `python devscripts/pipeline_case.py rerun '<page-url>' --from <stage> --patch /tmp/pipeline-patch.json --output /tmp/pipeline-rerun.json --render-output /tmp/pipeline-preview.jpg`. The command runs the selected stages synchronously in a temporary directory, reads saved database documents, and writes only the requested review image and JSON output. Temporary source files and patched artifacts are deleted automatically. Inspect the returned stage artifacts and render image, then report what changed and any remaining issue.

Reruns use `/api/pipeline-cases/preview`, which runs against a temporary file copy and returns stage artifacts plus the rendered image to the command. It does not create a page, case folder, or batch record in the database. If preview fails, use the read-only JSON endpoint above to inspect the saved inputs and stage artifacts; do not rerun in place. Never use `/api/results/rerun` for debugging: that endpoint operates on the saved page. Do not edit the original page or its database documents. A rerun applies to the page; region IDs identify the problem but do not scope execution to one region. The review image is the only retained render output; choose a temporary path and delete it after human review when it is no longer needed.

If a temporary preview fails, use `get` to capture the saved page data for diagnosis, then report the failure and relevant artifact state. If the needed artifact is missing or the requested stage cannot be resumed, report that clearly and stop. Never fall back to an in-place rerun.
