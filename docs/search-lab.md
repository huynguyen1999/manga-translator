# Search Lab

Search English descriptions across manga summaries with two-stage retrieval:
Stage 1 dense embedding retrieval followed by Stage 2 cross-encoder reranking.
The Lab lives at `/search-lab` alongside Gallery. It uses the
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
Set `QDRANT_URL` only if it runs elsewhere. The encoder and reranker run in the native macOS
backend so MPS is available; running that backend in Docker uses CPU instead.
The supported local configuration is one API process, as in `run-macos-web.sh`.

The first embedding or search downloads the pinned BGE-base (`BAAI/bge-base-en-v1.5`) and
Mixedbread reranker (`mixedbread-ai/mxbai-rerank-xsmall-v1`, ~70.8M parameters)
weights into the ignored `models/search` directory. `MANGA_SEARCH_MODEL_CACHE`
can override this location. Once downloaded, inference is local: summaries
are never uploaded. The Lab reports MPS or CPU and any setup error.
Translation and Gallery remain available when Qdrant is unavailable.

## Try it

1. Select a small set of manga. Selection persists across list pages.
2. Choose **Embed selected**. Existing unchanged summary vectors are reused.
3. Missing or outdated summaries are skipped and reported.
   Generate summaries using the existing Gallery action, then resume the job.
4. Search completed items immediately, even while indexing continues. Choose
   all indexed manga or only the current selection.
5. Optionally set **Min rerank score (0–1)** to filter out low-relevance candidates,
   and expand **Initial Embedding Results — Stage 1 Debug** to inspect the raw
   embedding cosine similarity rankings before cross-encoder reranking.

Cancellation takes effect between embedding batches. Completed items remain
searchable. Interrupted, cancelled, partial, and failed jobs can be resumed;
restarting the application marks unfinished jobs interrupted instead of starting
heavy work automatically. To refresh content changed since a completed job,
select the manga and choose **Embed selected** again.

## What scores mean

1. **Stage 1 (Initial Embedding Cosine)**: `BAAI/bge-base-en-v1.5` retrieves up to 50
   distinct candidate manga, scored by the highest cosine similarity among each
   manga's 512-token summary chunks (64-token overlap).
2. **Stage 2 (Cross-Encoder Reranker)**: `mixedbread-ai/mxbai-rerank-xsmall-v1`
   jointly scores each `(query, best_summary_chunk)` pair, producing a raw
   logit (`rerankLogit`) and a normalized sigmoid score (`rerankScore` in `[0, 1]`).
3. **Rank Delta**: Each reranked result card shows how many positions the reranker
   moved the candidate relative to Stage 1 (`↑ +N (was #M)`, `↓ -N (was #M)`, or `= #M`).
   The collapsible **Initial Embedding Results — Stage 1 Debug** panel lists all
   Stage 1 candidates in their original embedding cosine order.

Outdated summary vectors remain searchable with warnings until manual refresh. Excerpts
come from the indexed summary snapshot. Deleted records are
excluded and their vector points reconciled. Content-based point IDs, staged
summary writes, and PostgreSQL checkpoint validation make retries idempotent.

## API

Both `/search/...` and `/api/search/...` routes are available:

- `GET /status`: availability, model versions/device (`summary` and `reranker`), recent jobs, timing/memory.
- `GET /manga?search=&status=all&offset=0&limit=25`: manga and summary coverage (filter by summary status: `all`, `summarized`, `not-summarized`).
- `POST /jobs` with `{ "groupIds": ["stable-manga-id"] }`: begin summary embedding.
- `GET /jobs`, `POST /jobs/{id}/cancel`, `POST /jobs/{id}/resume`: job lifecycle.
- `DELETE /index`: purge all indexed vectors and source checkpoints.
- `POST /query` with `{ "query": "a traveler finds a friend", "groupIds": null, "limit": 20, "minScore": null }`.

`groupIds: null` searches all indexed manga; an empty list is rejected.
Responses include `results` (Stage 2 reranked results with `rank`, `initialRank`,
`rankDelta`, `rerankScore`, `rerankLogit`, `summarySimilarity`, `excerpt`, and `coverage`),
`initialResults` (Stage 1 embedding candidates in cosine order for debugging),
and per-stage timings (`embeddingMs`, `rerankMs`, `elapsedMs`).

## Verification and relevance evaluation

```sh
PYTHONWARNINGS=ignore venv/bin/python -m unittest discover -s test -p test_semantic_search.py
RUN_SEARCH_INTEGRATION=1 PYTHONWARNINGS=ignore venv/bin/python -m unittest discover -s test -p test_semantic_search.py
```

Integration checks use a temporary schema in `TEST_DATABASE_URL` and Qdrant's
in-memory test implementation. They remove only their schema. They cover real
checkpoint SQL, grouped vector retrieval, cross-encoder reranking, `minScore` filtering,
reuse, replacement, deletion, missing summaries, cancellation/resume, and interrupted writes.

Set `SEARCH_TEST_QDRANT_URL=http://127.0.0.1:6333` to run those tests against
the actual server in a unique disposable collection. Add `RUN_SEARCH_MODELS=1`
to exercise the pinned real encoder and reranker on synthetic fixtures as well. With cached
weights, `HF_HUB_OFFLINE=1` keeps this check entirely offline.

For relevance, embed 10–20 representative manga and label at least 20 queries
with known matching manga. Supply a JSON file:

```json
{
  "groupIds": ["actual-selected-manga-id"],
  "queries": [{
    "query": "your short English story or scene description",
    "relevantGroupIds": ["actual-relevant-manga-id"]
  }]
}
```

```sh
venv/bin/python -m server.search_benchmark labels.json --output search-report.json
```

This read-only evaluation reports hit rate at five, reciprocal rank, per-query results,
median/p95 latency, model revisions, and runtime diagnostics for both Stage 1 (`initial`)
and Stage 2 (`reranked`).
