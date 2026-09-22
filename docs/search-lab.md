# Search Lab

Search English descriptions across manga summaries and **original input pages**.
The Lab lives at `/search-lab` alongside Gallery and Pipeline Lab. It uses the
existing PostgreSQL database for jobs and source checkpoints, local PyTorch
models for inference, and a separate Qdrant service for vectors.

## Local setup

Run from the repository root, with the normal application dependencies installed:

```sh
venv/bin/python -m pip install -r requirements-search.txt
venv/bin/python -m server.db_cli migrate
docker compose -f docker-compose.search.yml up -d
./run-macos-web.sh
```

Restart an already-running backend after installing this change. Qdrant is bound
to `127.0.0.1:6333`; its `manga-search-vectors` volume survives container restarts.
Set `QDRANT_URL` only if it runs elsewhere. The encoder runs in the native macOS
backend so MPS is available; running that backend in Docker uses CPU instead.
The supported local configuration is one API process, as in `run-macos-web.sh`.

The first embedding or search downloads the pinned BGE-small and SigLIP-base
weights into the ignored `models/search` directory. `MANGA_SEARCH_MODEL_CACHE`
can override this location. Once downloaded, inference is local: summaries and
images are never uploaded. The Lab reports MPS or CPU and any setup error.
Translation and Gallery remain available when Qdrant is unavailable.

## Try it

1. Select a small set of manga. Selection persists across list pages.
2. Choose **Embed selected**. Existing unchanged vectors are reused.
3. Missing originals and missing/outdated summaries are skipped and reported.
   Generate summaries using the existing Gallery action, then resume the job.
4. Search completed items immediately, even while indexing continues. Choose
   all indexed manga or only the current selection.
5. Compare **Summary**, **Images**, and **Combined**. Queries stay in the field
   when modes change. Images/Combined accept up to 64 model tokens; longer
   queries return a validation message instead of being truncated.

Cancellation takes effect between embedding batches. Completed items remain
searchable. Interrupted, cancelled, partial, and failed jobs can be resumed;
restarting the application marks unfinished jobs interrupted instead of starting
heavy work automatically. To refresh content changed since a completed job,
select the manga and choose **Embed selected** again.

## What scores mean

Summary cosine similarity is the best matching chunk of the saved synopsis.
Image cosine similarity is the best matching original page. These model scores
are not confidence percentages and should not be compared across modalities.
Unavailable evidence is shown as **Not indexed**, never as zero.

Each modality retrieves up to 100 distinct manga, grouped before fusion.
Combined ranking sums `1 / (60 + rank)` from the two rankings; missing ranks
contribute zero. Its displayed score is a ranking score, not a similarity.
The top 20 manga are returned by default, with at most three matching original
pages. Supporting evidence fetched afterward does not change fusion ranks.

Summary windows cover the full text with 64-token overlap. Whole-page images
are resized and white-padded to 256×256 without cropping; tiny panel details
and long webtoon strips may retrieve poorly. This is a baseline to evaluate,
not a claim that generic visual embeddings understand all manga scenes.

Outdated vectors remain searchable with warnings until manual refresh. Excerpts
come from the indexed summary snapshot. Image previews show the current original
and are labeled accordingly when outdated. File size/mtime detects image changes
in coverage; manual embedding rechecks the actual file hash. Deleted records are
excluded and their vector points reconciled. Content-based point IDs, staged
summary writes, and PostgreSQL checkpoint validation make retries idempotent.

## API

Both `/search/...` and `/api/search/...` routes are available:

- `GET /status`: availability, model versions/device, recent jobs, timing/memory.
- `GET /manga?search=&status=all&offset=0&limit=25`: manga and source coverage (filter by summary status: `all`, `summarized`, `not-summarized`).
- `POST /jobs` with `{ "groupIds": ["stable-manga-id"] }`: begin embedding.
- `GET /jobs`, `POST /jobs/{id}/cancel`, `POST /jobs/{id}/resume`: job lifecycle.
- `POST /query` with `{ "query": "a traveler finds a friend", "mode": "combined", "groupIds": null, "limit": 20 }`.

`groupIds: null` searches all indexed manga; an empty list is rejected. Modes
are `summary`, `image`, and `combined`. Results include rank, manga ID/title,
nullable summary/image similarities, nullable combined score, indexed excerpt,
original-page evidence, navigation links, and coverage/outdated indicators.

## Verification and relevance evaluation

```sh
PYTHONWARNINGS=ignore venv/bin/python -m unittest discover -s test -p test_semantic_search.py
RUN_SEARCH_INTEGRATION=1 PYTHONWARNINGS=ignore venv/bin/python -m unittest discover -s test -p test_semantic_search.py
```

Integration checks use a temporary schema in `TEST_DATABASE_URL` and Qdrant's
in-memory test implementation. They remove only their schema. They cover real
checkpoint SQL, grouped vector retrieval, reuse, replacement, filtering,
deletion, missing originals, cancellation/resume, and interrupted writes.

Set `SEARCH_TEST_QDRANT_URL=http://127.0.0.1:6333` to run those tests against
the actual server in a unique disposable collection. Add `RUN_SEARCH_MODELS=1`
to exercise the pinned real encoders on synthetic fixtures as well. With cached
weights, `HF_HUB_OFFLINE=1` keeps this check entirely offline.

The implementation was checked on MPS with both normalized output dimensions,
real PostgreSQL and Qdrant, and all three search modes. Tiny-fixture queries took
72–612 ms in one validation run; these are smoke-test observations, not library
performance estimates. Production UI checks covered pagination/selection,
query-length errors, responsive layout, and labeled-fixture rank/score rendering.

For relevance, embed 10–20 representative manga and label at least 20 queries
with known matching manga and optional pages. Supply a JSON file:

```json
{
  "groupIds": ["actual-selected-manga-id"],
  "queries": [{
    "query": "your short English story or scene description",
    "relevantGroupIds": ["actual-relevant-manga-id"],
    "relevantPageIds": ["optional-actual-page-id"]
  }]
}
```

```sh
venv/bin/python -m server.search_benchmark labels.json --output search-report.json
```

This read-only evaluation reports hit rate at five, reciprocal rank, page recall
among the first five manga, per-query results, median/p95 latency, model revisions,
and runtime diagnostics. Run once for cold-start behavior and again for warm
latency. Timing includes evidence and coverage hydration. No fabricated labels
or unmeasured 100,000-page performance guarantees are supplied.
