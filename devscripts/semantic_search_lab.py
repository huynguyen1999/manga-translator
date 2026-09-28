"""Index local manga summaries and run two-stage embedding retrieval + cross-encoder reranking."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import shutil
import statistics
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# Running this file directly puts devscripts/, not the repository, on sys.path.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.search_embeddings import (
    CANDIDATE_LIMIT, DIMENSIONS, PROFILE, RERANK_MODEL, RERANK_REVISION,
    TEXT_MODEL, TEXT_REVISION, SearchEncoders, fingerprint, rank_results,
    rerank_candidates,
)

SUMMARY_NAMES = ("summary.txt", "summary.md", "summary.json")
CHUNKING = "tokens-512-overlap-64-v1"
BATCH_SIZE = 4


@dataclass
class MangaRecord:
    id: str
    title: str
    summary: str


def _item_fingerprint(content_hash: str) -> str:
    return fingerprint(f"{PROFILE}:{TEXT_REVISION}:{CHUNKING}:{content_hash}")


def _safe_extract(archive: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            name = item.filename.replace("\\", "/")
            member = PurePosixPath(name)
            if member.is_absolute() or ".." in member.parts or (member.parts and ":" in member.parts[0]):
                raise ValueError(f"Unsafe archive path: {item.filename}")
            if not member.parts:
                continue
            # Zip symlinks are not needed for manga archives and can escape the extraction root.
            if item.external_attr >> 16 & 0o170000 == 0o120000:
                continue
            target = destination.joinpath(*member.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)


def _summary_text(folder: Path) -> str:
    for name in SUMMARY_NAMES:
        path = next((item for item in folder.iterdir() if item.is_file() and item.name.casefold() == name), None)
        if path is None:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() != ".json":
            return content.strip()
        try:
            value = json.loads(content)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid summary JSON: {path}") from error
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            for key in ("summary", "text", "description"):
                if isinstance(value.get(key), str):
                    return value[key].strip()
        raise ValueError(f"Expected a string or summary/text/description field in {path}")
    return ""


def _is_manga_folder(folder: Path) -> bool:
    names = {path.name.casefold() for path in folder.iterdir() if path.is_file()}
    return any(name in names for name in SUMMARY_NAMES)


def _manga_folders(root: Path) -> list[Path]:
    folders = [root, *(path for path in root.rglob("*") if path.is_dir())]
    candidates = sorted((path for path in folders if path.name.casefold() != "images" and _is_manga_folder(path)),
                        key=lambda path: (len(path.parts), str(path).casefold()))
    result = []
    for folder in candidates:
        if not any(parent == folder or parent in folder.parents for parent in result):
            result.append(folder)
    return result


def _slug(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.casefold())).strip("-") or "manga"


def load_datasets(inputs: list[Path], extracted: Path) -> list[MangaRecord]:
    discovered = []
    for input_path in inputs:
        source = input_path.expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"Input does not exist: {source}")
        if source.is_file():
            if source.suffix.lower() != ".zip":
                raise ValueError(f"Expected a directory or ZIP archive: {source}")
            key = hashlib.sha256(str(source).encode()).hexdigest()[:12]
            root = extracted / f"{_slug(source.stem)}-{key}"
            _safe_extract(source, root)
            identity_root = str(source)
        else:
            root = source
            identity_root = str(source)
        for folder in _manga_folders(root):
            relative = folder.relative_to(root).as_posix() or "."
            title = folder.name if relative != "." else source.stem
            identity = f"{identity_root}:{relative}"
            summary = _summary_text(folder)
            if summary:
                discovered.append((title, identity, summary))
    counts = {}
    for title, _, _ in discovered:
        slug = _slug(title)
        counts[slug] = counts.get(slug, 0) + 1
    mangas = [MangaRecord(_slug(title) if counts[_slug(title)] == 1 else
                          f'{_slug(title)}-{hashlib.sha256(identity.encode()).hexdigest()[:8]}',
                          title, summary)
              for title, identity, summary in discovered]
    if not mangas:
        raise ValueError("No manga summaries were found in the supplied inputs")
    return mangas


def _json_write(path: Path, value) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _old_vectors(cache: Path, manifest: dict, dimension: int) -> dict[str, object]:
    if (manifest.get("profile") != PROFILE
            or manifest.get("models", {}).get("summary") != TEXT_MODEL
            or manifest.get("textRevision") != TEXT_REVISION
            or manifest.get("chunking") != CHUNKING):
        return {}
    records_path = cache / "summary_records.json"
    vectors_path = cache / "summary_vectors.npy"
    if not records_path.exists() or not vectors_path.exists():
        return {}
    import numpy as np

    records = json.loads(records_path.read_text(encoding="utf-8"))
    if not records:
        return {}
    vectors = np.load(vectors_path, mmap_mode="r", allow_pickle=False)
    if vectors.ndim != 2 or vectors.shape[1] != dimension:
        return {}
    return {row["fingerprint"]: vectors[row["vectorIndex"]]
            for row in records if row.get("fingerprint") and 0 <= row.get("vectorIndex", -1) < len(vectors)}


def _embed_entries(encoder: SearchEncoders, entries: list[dict], old: dict, dimension: int):
    import numpy as np

    unique = []
    indexes = {}
    for entry in entries:
        key = entry["fingerprint"]
        if key not in indexes:
            indexes[key] = len(unique)
            unique.append(entry)
    matrix = np.empty((len(unique), dimension), dtype=np.float32)
    pending = []
    reused = 0
    for index, entry in enumerate(unique):
        cached = old.get(entry["fingerprint"])
        if cached is None:
            pending.append((index, entry))
        else:
            matrix[index] = cached
            reused += 1
    for offset in range(0, len(pending), BATCH_SIZE):
        batch = pending[offset:offset + BATCH_SIZE]
        values = [row["text"] for _, row in batch]
        vectors = encoder.encode("summary", values)
        for (index, _), vector in zip(batch, vectors):
            matrix[index] = vector
    return matrix, indexes, len(pending), reused


def build_index(inputs: list[Path], output: Path, encoder: SearchEncoders) -> dict:
    import numpy as np

    output = output.expanduser().resolve()
    extracted = output / "extracted"
    cache = output / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    mangas = load_datasets(inputs, extracted)
    summary_entries = []
    for manga in mangas:
        if manga.summary:
            for chunk_index, chunk in enumerate(encoder.chunks(manga.summary)):
                summary_entries.append({
                    "mangaId": manga.id, "title": manga.title, "chunkIndex": chunk_index,
                    "start": chunk["start"], "end": chunk["end"], "text": chunk["text"],
                    "fingerprint": _item_fingerprint(fingerprint(chunk["text"])),
                })

    manifest_path = cache / "manifest.json"
    old_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    summary_old = _old_vectors(cache, old_manifest, DIMENSIONS["summary"])
    summary_vectors, summary_indexes, summary_embedded, summary_reused = _embed_entries(
        encoder, summary_entries, summary_old, DIMENSIONS["summary"]
    )
    for entry in summary_entries:
        entry["vectorIndex"] = summary_indexes[entry["fingerprint"]]
    manifest_path.unlink(missing_ok=True)
    for legacy_file in ("image_vectors.npy", "image_records.json"):
        (cache / legacy_file).unlink(missing_ok=True)
    np.save(cache / "summary_vectors.npy", summary_vectors)
    _json_write(cache / "summary_records.json", summary_entries)
    manifest = {
        "profile": PROFILE,
        "models": {"summary": TEXT_MODEL, "reranker": RERANK_MODEL},
        "textRevision": TEXT_REVISION,
        "rerankRevision": RERANK_REVISION,
        "chunking": CHUNKING,
        "mangaCount": len(mangas),
        "items": {"summary": {row["fingerprint"]: row["vectorIndex"] for row in summary_entries}},
    }
    _json_write(manifest_path, manifest)
    return {"manga": len(mangas), "summaryEmbedded": summary_embedded, "summaryReused": summary_reused, "output": str(output)}


def _load_index(index: Path):
    import numpy as np

    cache = index.expanduser().resolve() / "cache"
    manifest_path = cache / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No index found under {cache}; run the index command first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("profile") != PROFILE
            or manifest.get("models", {}).get("summary") != TEXT_MODEL
            or manifest.get("textRevision") != TEXT_REVISION
            or manifest.get("chunking") != CHUNKING):
        raise ValueError("Index model profile differs from this code; rebuild the index")
    summary_records = json.loads((cache / "summary_records.json").read_text(encoding="utf-8"))
    summary_vectors = np.load(cache / "summary_vectors.npy", allow_pickle=False)
    dimension = DIMENSIONS["summary"]
    if summary_vectors.ndim != 2 or summary_vectors.shape[1] != dimension or any(
            not isinstance(row.get("vectorIndex"), int) or not 0 <= row["vectorIndex"] < len(summary_vectors)
            for row in summary_records):
        raise ValueError("Index files are inconsistent; rebuild the index")
    return {"manifest": manifest, "summary_records": summary_records, "summary_vectors": summary_vectors}


def _groups(records: list[dict], vectors, query_vector) -> dict:
    vector_scores = vectors @ query_vector
    groups = {}
    for record in records:
        hit = {**record, "score": float(vector_scores[record["vectorIndex"]])}
        groups.setdefault(record["mangaId"], []).append(hit)
    for hits in groups.values():
        hits.sort(key=lambda hit: (-hit["score"], hit.get("chunkIndex", 0)))
        del hits[1:]
    return groups


def run_query(query: str, index: dict, encoder: SearchEncoders, top_k: int, min_score: float | None = None) -> dict:
    import numpy as np

    embed_started = time.perf_counter()
    query_vector = np.asarray(encoder.encode("summary", [query], query=True)[0], dtype=np.float32)
    summary_groups = _groups(index["summary_records"], index["summary_vectors"], query_vector)
    ranked_initial = rank_results(summary_groups, limit=CANDIDATE_LIMIT)
    initial_results = []
    for rank, (manga_id, score) in enumerate(ranked_initial, 1):
        hit = summary_groups[manga_id][0]
        initial_results.append({
            "id": manga_id, "title": hit["title"], "rank": rank,
            "score": float(score), "summarySimilarity": float(score),
            "excerpt": hit["text"],
        })
    embedding_ms = round((time.perf_counter() - embed_started) * 1000, 2)
    rerank_started = time.perf_counter()
    if initial_results:
        rerank_scores = encoder.rerank(query, [row["excerpt"] or "" for row in initial_results])
        results = rerank_candidates(initial_results, rerank_scores, limit=top_k, min_score=min_score)
    else:
        results = []
    rerank_ms = round((time.perf_counter() - rerank_started) * 1000, 2)
    return {
        "results": results, "initialResults": initial_results,
        "embeddingMs": embedding_ms, "rerankMs": rerank_ms,
        "elapsedMs": round(embedding_ms + rerank_ms, 2),
    }


def retrieve_passages(query: str, index: dict, encoder: SearchEncoders, top_k: int) -> list[dict]:
    import numpy as np

    query_vector = np.asarray(encoder.encode("summary", [query], query=True)[0], dtype=np.float32)
    scores = index["summary_vectors"] @ query_vector
    hits = sorted(index["summary_records"],
                  key=lambda row: (-float(scores[row["vectorIndex"]]), row["mangaId"], row["chunkIndex"]))
    return [{"rank": rank, "mangaId": row["mangaId"], "title": row["title"],
             "chunkIndex": row["chunkIndex"], "start": row["start"], "end": row["end"],
             "score": float(scores[row["vectorIndex"]]), "text": row["text"]}
            for rank, row in enumerate(hits[:top_k], 1)]


def _delta_label(delta: int, initial_rank: int) -> str:
    if delta > 0:
        return f"↑ +{delta} (from #{initial_rank})"
    if delta < 0:
        return f"↓ {delta} (from #{initial_rank})"
    return f"= #{initial_rank}"


def _print_results(query: str, payload: dict, context_chars: int = 1000) -> None:
    print(f"QUERY\n{query}")
    print(f"Latency: {payload['elapsedMs']:.1f} ms (Stage 1 embedding: {payload['embeddingMs']:.1f} ms · Stage 2 rerank: {payload['rerankMs']:.1f} ms)")
    print("\nSTAGE 2: RERANKED RESULTS (mxbai-rerank-xsmall-v1)\n" + "─" * 56)
    for row in payload["results"]:
        delta = _delta_label(row["rankDelta"], row["initialRank"])
        print(f'{row["rank"]}. {row["title"]} [{row["id"]}]  {delta}  '
              f'rerank {row["rerankScore"]:.4f} (logit {row["rerankLogit"]:.4f}) · '
              f'embed cosine {row["summarySimilarity"]:.4f}')
        if row["excerpt"]:
            excerpt = " ".join(row["excerpt"].split())
            print(f'   Summary context: "{excerpt[:context_chars]}{"…" if len(excerpt) > context_chars else ""}"')
    print(f"\nSTAGE 1: INITIAL EMBEDDING RESULTS (DEBUG — {len(payload['initialResults'])} candidates)\n" + "─" * 56)
    for row in payload["initialResults"]:
        print(f'#{row["rank"]}. {row["title"]} [{row["id"]}]  cosine {row["summarySimilarity"]:.4f}')


def write_html_report(query: str, payload: dict, destination: Path) -> None:
    reranked_cards = []
    for row in payload["results"]:
        delta = _delta_label(row["rankDelta"], row["initialRank"])
        excerpt = f'<blockquote>{html.escape(row["excerpt"])}</blockquote>' if row["excerpt"] else ""
        scores = (f'Rerank {row["rerankScore"]:.4f} (logit {row["rerankLogit"]:.4f}) · '
                  f'Embed cosine {row["summarySimilarity"]:.4f} · {html.escape(delta)}')
        reranked_cards.append(
            f'<article><div class="heading"><span>#{row["rank"]}</span>'
            f'<h3>{html.escape(row["title"])}</h3><b>{scores}</b></div>{excerpt}</article>'
        )
    initial_rows = "".join(
        f'<li><b>#{row["rank"]}</b> {html.escape(row["title"])} — <code>cosine {row["summarySimilarity"]:.4f}</code></li>'
        for row in payload["initialResults"]
    )
    document = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Semantic search · {html.escape(query)}</title><style>
body{{margin:0;background:#f5f3ef;color:#24211e;font:15px/1.5 system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:32px 20px}}header{{padding:24px;background:#fff;border-radius:14px}}h1{{font-size:14px;text-transform:uppercase;letter-spacing:.12em;color:#786f65}}header p{{font-size:22px;margin:6px 0 0}}section{{margin-top:28px}}section h2{{border-bottom:1px solid #d5d0c8;padding-bottom:9px}}article{{background:#fff;padding:18px;margin:12px 0;border-radius:12px;box-shadow:0 2px 10px #211a100c}}.heading{{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}}.heading span,.heading b{{color:#7153a4}}.heading h3{{margin:0;flex:1}}blockquote{{margin:12px 0 0;padding:10px 14px;background:#f7f6f4;border-left:3px solid #b4a1d5;white-space:pre-wrap}}details{{background:#fff;padding:16px;border-radius:12px;margin-top:20px}}
</style></head><body><main><header><h1>Query</h1><p>{html.escape(query)}</p><small>{payload["elapsedMs"]:.1f} ms total (Stage 1 embedding: {payload["embeddingMs"]:.1f} ms · Stage 2 rerank: {payload["rerankMs"]:.1f} ms)</small></header>
<section><h2>Stage 2: Reranked Results ({html.escape(RERANK_MODEL)})</h2>{"".join(reranked_cards) or "<p>No results.</p>"}</section>
<details open><summary><b>Stage 1: Initial Embedding Results — Debug ({len(payload["initialResults"])} candidates)</b></summary><ol>{initial_rows}</ol></details>
</main></body></html>'''
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")


def _metrics(runs: list[dict]) -> dict:
    latencies = [run["latencyMs"] for run in runs]
    return {
        "queries": len(runs),
        "hitAt1": sum(run["hitAt1"] for run in runs) / len(runs),
        "hitAt5": sum(run["hitAt5"] for run in runs) / len(runs),
        "mrr": statistics.mean(run["reciprocalRank"] for run in runs),
        "meanLatencyMs": statistics.mean(latencies),
        "p95LatencyMs": sorted(latencies)[math.ceil(len(latencies) * .95) - 1],
    }


def benchmark(index: dict, encoder: SearchEncoders, labels_path: Path, top_k: int, min_score: float | None = None) -> dict:
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    if not isinstance(labels, dict):
        raise ValueError("Labels file must contain a JSON object")
    queries = labels.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("Labels file must contain a nonempty queries array")
    for row in queries:
        relevant = row.get("relevantManga") or row.get("relevantGroupIds") if isinstance(row, dict) else None
        if (not isinstance(row, dict) or not isinstance(row.get("query"), str) or not row["query"].strip()
                or not isinstance(relevant, list) or not relevant):
            raise ValueError("Each query needs query and relevantManga fields")
    encoder._load("summary")
    encoder._load("rerank")
    initial_runs, reranked_runs = [], []
    for row in queries:
        payload = run_query(row["query"], index, encoder, top_k, min_score=min_score)
        relevant = set(row.get("relevantManga") or row.get("relevantGroupIds", []))
        for target, found, latency in (
            (initial_runs, payload["initialResults"][:top_k], payload["embeddingMs"]),
            (reranked_runs, payload["results"], payload["elapsedMs"]),
        ):
            ranks = [item["rank"] for item in found if item["id"] in relevant or item["title"] in relevant]
            target.append({
                "query": row["query"],
                "hitAt1": bool(ranks and min(ranks) == 1),
                "hitAt5": bool(ranks and min(ranks) <= 5),
                "reciprocalRank": 1 / min(ranks) if ranks else 0,
                "latencyMs": latency,
                "results": found,
            })
    return {
        "initial": {"metrics": _metrics(initial_runs), "runs": initial_runs},
        "reranked": {"metrics": _metrics(reranked_runs), "runs": reranked_runs},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    index_parser = commands.add_parser("index", help="Build or incrementally update the local summary index")
    index_parser.add_argument("--input", nargs="+", type=Path, required=True, help="Manga directories or ZIP archives")
    index_parser.add_argument("--output", type=Path, default=Path("devscripts/data/semantic_search"))
    index_parser.add_argument("--device", help="Torch device; defaults to CUDA, then MPS, then CPU")
    for name in ("search", "compare", "benchmark", "retrieve"):
        command = commands.add_parser(name)
        command.add_argument("--index", type=Path, default=Path("devscripts/data/semantic_search"))
        command.add_argument("--device", help="Torch device; defaults to CUDA, then MPS, then CPU")
    for name in ("search", "compare"):
        cmd = commands.choices[name]
        cmd.add_argument("--query", required=True)
        cmd.add_argument("--top-k", type=int, default=5)
        cmd.add_argument("--min-score", type=float, default=None, help="Optional minimum rerank score (0 to 1)")
        cmd.add_argument("--report", type=Path)
        cmd.add_argument("--context-chars", type=int, default=1000,
                         help="Maximum summary context characters to print (default: 1000)")
    benchmark_parser = commands.choices["benchmark"]
    benchmark_parser.add_argument("--labels", type=Path, required=True,
                                  help="JSON containing queries with relevantManga or relevantGroupIds lists")
    benchmark_parser.add_argument("--top-k", type=int, default=20)
    benchmark_parser.add_argument("--min-score", type=float, default=None)
    benchmark_parser.add_argument("--output", type=Path)
    retrieve_parser = commands.choices["retrieve"]
    retrieve_parser.add_argument("--query", required=True)
    retrieve_parser.add_argument("--top-k", type=int, default=5)
    retrieve_parser.add_argument("--output", type=Path, help="Write retrieval JSON to this file")

    args = parser.parse_args()
    try:
        encoder = SearchEncoders(device=args.device)
        if args.command == "index":
            result = build_index(args.input, args.output, encoder)
            print(f'Indexed {result["manga"]} manga summaries at {result["output"]}')
            print(f'Summary chunks: embedded {result["summaryEmbedded"]}, reused {result["summaryReused"]}')
            return
        index = _load_index(args.index)
        if args.top_k < 1:
            parser.error("--top-k must be at least 1")
        if args.command == "retrieve":
            result = {"query": args.query, "model": TEXT_MODEL,
                      "results": retrieve_passages(args.query, index, encoder, args.top_k)}
            encoded = json.dumps(result, indent=2, ensure_ascii=False)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(encoded, encoding="utf-8")
            else:
                print(encoded)
            return
        if args.command in ("search", "compare") and args.context_chars < 1:
            parser.error("--context-chars must be at least 1")
        if args.command == "benchmark":
            report = benchmark(index, encoder, args.labels, args.top_k, min_score=args.min_score)
            encoded = json.dumps(report, indent=2, ensure_ascii=False)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(encoded, encoding="utf-8")
            for stage, result in report.items():
                metrics = result["metrics"]
                print(f'{stage:8} Hit@1 {metrics["hitAt1"]:.3f}  Hit@5 {metrics["hitAt5"]:.3f}  '
                      f'MRR {metrics["mrr"]:.3f}  latency {metrics["meanLatencyMs"]:.1f} ms')
            return
        payload = run_query(args.query, index, encoder, args.top_k, min_score=args.min_score)
        _print_results(args.query, payload, args.context_chars)
        if args.report:
            write_html_report(args.query, payload, args.report)
            print(f"\nHTML report: {args.report}")
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
