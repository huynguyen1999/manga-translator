# Search Lab

Search English descriptions across manga summaries with Typesense hybrid retrieval
(fuzzy lexical search + BGE vector search + rank fusion).
The Lab lives at `/search-lab` alongside Gallery. It uses the
existing PostgreSQL database for jobs and source checkpoints, local PyTorch
models for embeddings, and a separate Typesense service for indexed chunks.

## Architecture

```text
BGE embedding
      ↓
Typesense
 ├─ fuzzy lexical search (num_typos: 0, 1, 2)
 ├─ vector search (768-dim BGE dense vectors)
 └─ hybrid fusion / rerank_hybrid_matches
      ↓
top candidate manga (grouped by groupId)
      ↓
final results
```

Typesense acts as the single search and retrieval engine. The application generates
embeddings via `BAAI/bge-base-en-v1.5` and indexes them into Typesense's `manga_search_v2` collection.
Queries execute as a single hybrid request with typo tolerance and vector similarity fused
into a combined rank.

## Local setup

Run from the repository root, with the normal application dependencies installed:

```sh
venv/bin/python -m pip install -r requirements-search.txt
venv/bin/python -m server.db_cli migrate
docker compose -f docker-compose.search.yml up -d
./run-macos-web.sh
```

Restart an already-running backend after installing this change. Typesense is bound
to `127.0.0.1:8108`; its `manga-search-typesense` volume survives container restarts.
Configure connection settings if Typesense runs elsewhere:
- `TYPESENSE_HOST` (default `127.0.0.1`)
- `TYPESENSE_PORT` (default `8108`)
- `TYPESENSE_PROTOCOL` (default `http`)
- `TYPESENSE_API_KEY` (default `xyz`)
- `TYPESENSE_COLLECTION` (default `manga_search_v2`)

The encoder runs in the native macOS backend so MPS is available.
The first embedding or search downloads the pinned BGE-base (`BAAI/bge-base-en-v1.5`)
weights into the ignored `models/search` directory. `MANGA_SEARCH_MODEL_CACHE`
can override this location. Translation and Gallery remain available when Typesense is unavailable.

## Try it

1. Select a small set of manga in Search Lab. Selection persists across list pages.
2. Choose **Embed selected**. Existing unchanged summary vectors are reused.
3. Missing summaries are skipped and reported.
   Generate summaries using the existing Gallery action, then resume the job.
4. Search completed items immediately, even while indexing continues. Choose
   all indexed manga or only the current selection.
5. Select **Typo tolerance** (2 typos, 1 typo, or Off).
6. Adjust **Advanced Retrieval Settings** (Hybrid semantic/keyword balance and `rerank_hybrid_matches`).
7. Expand **Typesense Hybrid Candidates** to inspect raw retrieval rankings, text match score, and vector distance.

Cancellation takes effect between embedding batches. Completed items remain
searchable. Interrupted, cancelled, partial, and failed jobs can be resumed.

## What scores mean

1. **Typesense Hybrid Score**: Typesense combines fuzzy Damerau–Levenshtein lexical matching
   and BGE vector cosine similarity (`embedding:([vector], alpha: 0.7)`), grouping by manga (`groupId`)
   and returning distinct candidate manga with `score`, `textMatch`, and `vectorDistance`.
2. **Text Match**: Lexical score indicating exact and typo-tolerant keyword presence in the excerpt.
3. **Vector Distance**: Cosine distance in embedding space between the query and the chunk passage.

Outdated summary vectors remain searchable with warnings until manual refresh. Excerpts
come from the indexed summary snapshot. Deleted records are
reconciled automatically.

## API

Both `/search/...` and `/api/search/...` routes are available:

- `GET /status`: availability, retrieval engine (`typesense`), model versions/device, recent jobs, timing/memory.
- `GET /manga?search=&status=all&offset=0&limit=25`: manga and summary coverage (filter by summary status: `all`, `summarized`, `not-summarized`, `indexed`, `not-indexed`).
- `POST /jobs` with `{ "groupIds": ["stable-manga-id"] }`: begin summary embedding.
- `GET /jobs`, `POST /jobs/{id}/cancel`, `POST /jobs/{id}/resume`: job lifecycle.
- `DELETE /index`: purge all indexed vectors and source checkpoints.
- `POST /query`:

```json
{
  "query": "a traveler finds a friend",
  "groupIds": null,
  "limit": 20,
  "typoTolerance": 2,
  "alpha": 0.7,
  "hybridRerank": true
}
```

Responses include:
- `retrieval`: metadata on engine (`typesense`), mode (`hybrid`), `candidateLimit`, `alpha`, `typoTolerance`, and `hybridRerank`.
- `initialResults`: candidate manga in Typesense rank order for diagnostics.
- `results`: final ranked manga.
- `timings`: `{ "embeddingMs": ..., "retrievalMs": ..., "elapsedMs": ... }`.

## Verification and relevance evaluation

```sh
PYTHONWARNINGS=ignore venv/bin/python -m unittest discover -s test -p test_semantic_search.py
RUN_SEARCH_INTEGRATION=1 PYTHONWARNINGS=ignore venv/bin/python -m unittest discover -s test -p test_semantic_search.py
```

Set `SEARCH_TEST_TYPESENSE_URL=http://127.0.0.1:8108` to run integration checks directly against
a running Typesense container. Add `RUN_SEARCH_MODELS=1` to exercise the pinned real BGE encoder on synthetic fixtures as well.

For relevance evaluation, embed 10–20 representative manga and supply a labeled queries file:

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

This benchmark evaluates Typesense hybrid retrieval and reports Candidate Recall@50, Hit@1, Hit@5, MRR, and per-stage latencies (embedding, retrieval, total).
