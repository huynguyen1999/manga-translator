# Layout performance validation

Validated 2026-09-30, layout algorithm revision 6.

## Result

The seven-page replay meets the required five-page 5s median target in both isolated and background CPU execution. Every page median is below 6s, meeting the 10s target without small-text rescue. Production batch layout and typesetting reruns share this policy; direct solver defaults retain their original breadth.

Each mode used one warm-up and three measured runs per page (56 runs total). Both used OpenCV with one thread, matching the CPU executor policy. Background execution submitted seven concurrent requests with two CPU workers and one admitted layout. Queue waiting is outside layout execution. Context reconstruction, mask preparation before layout, and QA rendering are recorded separately and excluded from execution timing.

| Page | Original isolated single replay | New isolated median | Batch execution median | Batch queue median | Baseline mask |
|---|---:|---:|---:|---:|---|
| `3e2f500d` | 12.8s | 5.33s | 5.24s | 0.00s | Matches |
| `31f8b698` | 13.8s | 4.63s | 4.78s | 5.24s | Matches |
| `af3be775` | 9.1s | 2.95s | 2.98s | 10.04s | Matches |
| `4799b10a` | 5.9s | 2.86s | 2.82s | 13.01s | Reconstructed |
| `25017366` | 9.9s | 4.57s | 4.67s | 15.84s | Reconstructed |
| `491582cc` | 11.9s | 5.82s | 5.84s | 20.52s | Matches |
| `c543e93b` | 6.4s | 3.52s | 3.47s | 26.37s | Reconstructed |

Original timings are single measurements, not benchmark medians; the table is a descriptive reference rather than a paired statistical speedup. The new measured-run aggregate median is 4.57s isolated and 4.77s background execution; background queue median is 13.01s. Later pages wait behind earlier layouts, so caller wall time can exceed 10s even when execution does not.

## Changes and rationale

- Immutable font-aware glyph bitmap arrays read the native buffer once. Fully keyed page compositor crops and transformed footprints avoid repeat drawing while rechecking current domains and obstacles.
- Font queues retain first occurrence only. Bubble initial search uses two Y origins (within the agreed maximum of three), one DP path per line-count bucket, up to four refinements per font, and two validated alternatives per region. Unresolved/conflicting groups expand to prior breadth with retained preparation and candidates.
- DP rejects impossible remaining-width suffixes, uses stable bucket insertion, avoids dominated candidate construction, and reuses exact transition/spring costs. Scores and word-splitting rules are unchanged.
- Each free-text typography stage probes local offsets, then widens that same stage before accepting smaller lettering. Necessary page/domain bounds filter offset work. A local-only draft shrank an 82px caption to 11px and was rejected; final region `07be25ee` remains 82px.
- The continuation gates retain validated initial results after 5s and stop resolved, non-conflicting search after 10s. Unresolved/conflicting work may continue until the 30s search deadline. Completed valid compatible placements survive expiry; final safety validation and source restoration finish afterward.
- Reviewed rescue reaches `ceil(8 * longest_edge / 2048)` with a one-pixel minimum while respecting configured minimums, positive explicit sizes, and preserved sizing. Rescue QA survives freezing. No seven-page replay exhausted the budget or used small-text rescue; controlled tests exercise those paths.
- One background layout is admitted before acquiring a CPU worker. Interactive work and other resource capacities remain available. Queue waiting is consumed once per execution, avoiding stale values on context reuse.
- Large binary elliptical source-scope dilation was a remaining preparation hotspot (2.21s under profiling on `4799b10a`). Native double-precision correlation computes the same binary mask with a numeric-ambiguity fallback. Pixel checks cover large kernels, rotated polygons, borders, and fallback.
- Restored the existing 50% saved-panel coverage rule after the audit found unrelated stricter confidence/coverage thresholds broke panel contracts. Translation, detection execution, API response shapes, database schema, and pipeline stage order remain unchanged.

## Output review and provenance

There were zero execution/render errors and zero final layout diagnostic errors across the 56 replays. All seven final isolated/background RGB hashes match. Source ownership IDs have no additions or removals against the baseline. Search changes are reviewed separately from exact raster reuse: final run 3 differs from baseline in 46 regions, including 26 font sizes, 16 wrappings, 43 bounds, and 46 line-position sets. These counts overlap.

The longer paragraph `0bc29086` on `31f8b698` uses 17px rather than baseline 13px, with changed wrapping and placement. The caption `07be25ee` retains 82px and its larger placement. On `491582cc`, three baseline sizes change 18→17, 15→13, and 16→11; its saved collision is a dedicated regression fixture, and final renders have no collision diagnostics. Unresolved regions retain ownership and follow source-restoration policy rather than introducing unsafe lettering.

Four historical masks match the baseline fingerprints (`3e2f500d`, `31f8b698`, `af3be775`, `491582cc`). Masks for `4799b10a`, `25017366`, and `c543e93b` were reconstructed from saved geometry without detector masks and do not match. Source geometry, translations, styles, bubble geometry, font and image-shape fingerprints match; settings/revision fingerprints differ. These three comparisons cannot establish exact baseline equivalence.

QA renders use temporary Telea inpainting rather than the historical neural-inpainted background. Original characters/cleanup artifacts can remain; full-image equality to historical final JPEGs is not claimed. The pixel-identical guarantees apply to glyph/compositor reuse, binary dilation, and matching isolated/background current renders. Saved result directories were not mutated; reruns regenerate revision-6 layouts.

## Supplemental cases

One warm-up plus three measurements per case, OpenCV one thread, direct queue wait 0. All ownership, containment, collision and applicable source-restoration checks pass.

| Case | Execution median |
|---|---:|
| conflicting regions | 0.269s |
| connected bubbles | 0.144s |
| irregular bubble | 0.070s |
| long paragraph | 0.854s |
| ordinary bubble | 0.075s |
| rotated free text | 0.160s |

The conflict fixture suppresses its second placement; shared source ownership does not require duplicate restoration. An engine-level forced-deadline test separately verifies retained valid lettering and pixel restoration for an unresolved independent source region.

## Remaining slower work

`3e2f500d` remains 5.33s isolated (5.24s background), with caption `07be25ee` taking about 1.48s in run 3. `491582cc` remains 5.82s (5.84s background); regions `bb632e2a` and `fd0862e3` take about 0.63s and 0.49s. Both remain below the exceptional 10s target. The long-paragraph region on `31f8b698` takes about 1.39s, down from the diagnosed 7.4s single replay.

## Evidence and reproduction

- Baseline: `/tmp/layout-seven-baseline.json` (single replays).
- Final seven-page report: `/tmp/layout-seven-complete-validation.json`.
- Supplemental report: `/tmp/layout-synthetic-complete-validation.json`.
- Final images: `/tmp/isolated_layout_page/<folder>/run-3.png` and `/tmp/background_cpu_stage/<folder>/run-3.png`.
- Profiling diagnosis: `/tmp/layout-4799.pstats` and `/tmp/layout-4799-profile.json`; instrumented timings are not benchmark latency.

```sh
rtk proxy venv/bin/python devscripts/layout_page_benchmark.py --warmups 1 --repeats 3 --output /tmp/layout-seven-complete-validation.json
rtk proxy venv/bin/python devscripts/layout_synthetic_benchmark.py --output /tmp/layout-synthetic-complete-validation.json
rtk proxy python3 devscripts/check_line_limits.py
```

The replay requires the captured `/tmp/layout-case-*.json`, source files under `/Users/ice-h/Downloads/slow`, and historical masks under `/tmp/layout-preview-current/results`. It runs offline; the full HTTP/persisted batch scheduler was not exercised. Scheduler/resource/rerun contracts are covered by the affected test suite.

## Contract verification

Affected layout, raster, rescue/deadline, panel, scheduler, rerun, CPU admission and refactor contracts: **298 passed**. The application line-count ratchet passes without changing its baseline (546 baseline files, 500-line new-file cap). Scoped diff whitespace checks pass. The full working-tree diff still has unrelated `.gitignore` CRLF whitespace at line 54, which was preserved.
