"""Index local manga archives and compare exact text/image semantic retrieval."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
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
    DIMENSIONS, IMAGE_MODEL, IMAGE_REVISION, PROFILE, TEXT_MODEL_OPTIONS,
    SearchEncoders, fingerprint, rank_results,
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
SUMMARY_NAMES = ("summary.txt", "summary.md", "summary.json")
PREPROCESSING = "exif-rgb-white-pad-256-lanczos-v1"
CHUNKING = "tokens-512-overlap-64-v1"
BATCH_SIZE = 4


@dataclass
class PageRecord:
    number: int
    path: Path


@dataclass
class MangaRecord:
    id: str
    title: str
    summary: str
    pages: list[PageRecord]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _item_fingerprint(modality: str, content_hash: str, encoder: SearchEncoders) -> str:
    revision = TEXT_MODEL_OPTIONS[encoder.text_model][1] if modality == "summary" else IMAGE_REVISION
    preprocessing = CHUNKING if modality == "summary" else PREPROCESSING
    return fingerprint(f"{PROFILE}:{revision}:{preprocessing}:{content_hash}")


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


def _page_paths(folder: Path) -> list[Path]:
    paths = [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS]
    image_dir = folder / "images"
    if image_dir.is_dir():
        paths.extend(path for path in image_dir.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)

    def natural_key(path: Path):
        value = str(path.relative_to(folder))
        parts = tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                      for part in re.split(r"(\d+)", value))
        return parts, value.casefold(), value

    return sorted(set(paths), key=natural_key)


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
    return any(name in names for name in SUMMARY_NAMES) or bool(_page_paths(folder))


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
            pages = [PageRecord(number, path) for number, path in enumerate(_page_paths(folder), 1)]
            if summary or pages:
                discovered.append((title, identity, summary, pages))
    counts = {}
    for title, _, _, _ in discovered:
        slug = _slug(title)
        counts[slug] = counts.get(slug, 0) + 1
    mangas = [MangaRecord(_slug(title) if counts[_slug(title)] == 1 else
                          f'{_slug(title)}-{hashlib.sha256(identity.encode()).hexdigest()[:8]}',
                          title, summary, pages)
              for title, identity, summary, pages in discovered]
    if not mangas:
        raise ValueError("No manga summaries or page images were found in the supplied inputs")
    return mangas


def _json_write(path: Path, value) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _old_vectors(cache: Path, manifest: dict, modality: str, dimension: int, encoder: SearchEncoders) -> dict[str, object]:
    text_name, text_revision, _ = TEXT_MODEL_OPTIONS[encoder.text_model]
    expected = {"profile": PROFILE, "models": {"summary": text_name, "image": IMAGE_MODEL},
                "textRevision": text_revision, "imageRevision": IMAGE_REVISION,
                "preprocessing": PREPROCESSING, "chunking": CHUNKING}
    if any(manifest.get(key) != value for key, value in expected.items()):
        return {}
    records_path = cache / f"{modality}_records.json"
    vectors_path = cache / f"{modality}_vectors.npy"
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


def _embed_entries(encoder: SearchEncoders, modality: str, entries: list[dict], old: dict,
                   dimension: int):
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
        values = [row["text"] if modality == "summary" else str(row["path"]) for _, row in batch]
        vectors = encoder.encode(modality, values)
        for (index, _), vector in zip(batch, vectors):
            matrix[index] = vector
    return matrix, indexes, len(pending), reused


def build_index(inputs: list[Path], output: Path, encoder: SearchEncoders, mode: str = "all") -> dict:
    import numpy as np

    output = output.expanduser().resolve()
    extracted = output / "extracted"
    cache = output / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    mangas = load_datasets(inputs, extracted)
    summary_entries, image_entries = [], []
    for manga in mangas:
        if mode in ("summary", "all") and manga.summary:
            for chunk_index, chunk in enumerate(encoder.chunks(manga.summary)):
                summary_entries.append({"mangaId": manga.id, "sourceId": manga.source_id, "title": manga.title, "chunkIndex": chunk_index,
                    "start": chunk["start"], "end": chunk["end"], "text": chunk["text"],
                    "fingerprint": _item_fingerprint("summary", fingerprint(chunk["text"]), encoder)})
        for page in manga.pages if mode in ("image", "all") else ():
            image_entries.append({"mangaId": manga.id, "sourceId": manga.source_id, "title": manga.title, "page": page.number,
                "path": str(page.path), "region": None,
                "fingerprint": _item_fingerprint("image", _sha256_file(page.path), encoder)})

    manifest_path = cache / "manifest.json"
    old_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    def preserve(modality: str):
        if modality == "summary" and old_manifest.get("textRevision") != TEXT_MODEL_OPTIONS[encoder.text_model][1]:
            return [], np.empty((0, encoder.text_dimension), dtype=np.float32)
        records_path, vectors_path = cache / f"{modality}_records.json", cache / f"{modality}_vectors.npy"
        if records_path.exists() and vectors_path.exists():
            return json.loads(records_path.read_text(encoding="utf-8")), np.load(vectors_path, allow_pickle=False)
        return [], np.empty((0, encoder.text_dimension if modality == "summary" else DIMENSIONS[modality]), dtype=np.float32)

    if mode in ("summary", "all"):
        summary_old = _old_vectors(cache, old_manifest, "summary", encoder.text_dimension, encoder)
        summary_vectors, summary_indexes, summary_embedded, summary_reused = _embed_entries(
            encoder, "summary", summary_entries, summary_old, encoder.text_dimension)
    else:
        summary_entries, summary_vectors = preserve("summary")
        summary_embedded = summary_reused = 0
        summary_indexes = {}
    if mode in ("image", "all"):
        image_old = _old_vectors(cache, old_manifest, "image", DIMENSIONS["image"], encoder)
        image_vectors, image_indexes, image_embedded, image_reused = _embed_entries(
            encoder, "image", image_entries, image_old, DIMENSIONS["image"])
    else:
        image_entries, image_vectors = preserve("image")
        image_embedded = image_reused = 0
        image_indexes = {}

    if summary_indexes:
        for entry in summary_entries:
            entry["vectorIndex"] = summary_indexes[entry["fingerprint"]]
    if image_indexes:
        for entry in image_entries:
            entry["vectorIndex"] = image_indexes[entry["fingerprint"]]
    manifest_path.unlink(missing_ok=True)
    np.save(cache / "summary_vectors.npy", summary_vectors)
    np.save(cache / "image_vectors.npy", image_vectors)
    _json_write(cache / "summary_records.json", summary_entries)
    _json_write(cache / "image_records.json", image_entries)
    manifest = {
        "profile": PROFILE,
        "models": {"summary": TEXT_MODEL_OPTIONS[encoder.text_model][0], "image": IMAGE_MODEL},
        "textRevision": TEXT_MODEL_OPTIONS[encoder.text_model][1],
        "imageRevision": IMAGE_REVISION,
        "preprocessing": PREPROCESSING,
        "chunking": CHUNKING,
        "mangaCount": len(mangas),
        "items": {
            "summary": {row["fingerprint"]: row["vectorIndex"] for row in summary_entries},
            "image": {row["fingerprint"]: row["vectorIndex"] for row in image_entries},
        },
    }
    _json_write(manifest_path, manifest)
    return {"manga": len(mangas), "summaryEmbedded": summary_embedded, "summaryReused": summary_reused,
            "imagesEmbedded": image_embedded, "imagesReused": image_reused, "output": str(output)}


def _load_index(index: Path, encoder: SearchEncoders):
    import numpy as np

    cache = index.expanduser().resolve() / "cache"
    manifest_path = cache / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No index found under {cache}; run the index command first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    text_name, text_revision, _ = TEXT_MODEL_OPTIONS[encoder.text_model]
    if (manifest.get("profile") != PROFILE or manifest.get("models") != {"summary": text_name, "image": IMAGE_MODEL}
            or manifest.get("textRevision") != text_revision or manifest.get("imageRevision") != IMAGE_REVISION
            or manifest.get("preprocessing") != PREPROCESSING or manifest.get("chunking") != CHUNKING):
        raise ValueError("Index model profile differs from this code; rebuild the index")
    summary_records = json.loads((cache / "summary_records.json").read_text(encoding="utf-8"))
    image_records = json.loads((cache / "image_records.json").read_text(encoding="utf-8"))
    summary_vectors = np.load(cache / "summary_vectors.npy", allow_pickle=False)
    image_vectors = np.load(cache / "image_vectors.npy", allow_pickle=False)
    for records, vectors, dimension in ((summary_records, summary_vectors, encoder.text_dimension),
                                        (image_records, image_vectors, DIMENSIONS["image"])):
        if vectors.ndim != 2 or vectors.shape[1] != dimension or any(
                not isinstance(row.get("vectorIndex"), int) or not 0 <= row["vectorIndex"] < len(vectors)
                for row in records):
            raise ValueError("Index files are inconsistent; rebuild the index")
    return {
        "manifest": manifest,
        "summary_records": summary_records,
        "image_records": image_records,
        "summary_vectors": summary_vectors,
        "image_vectors": image_vectors,
    }


def _groups(records: list[dict], vectors, query_vector, modality: str) -> dict:
    vector_scores = vectors @ query_vector
    groups = {}
    for record in records:
        hit = {**record, "score": float(vector_scores[record["vectorIndex"]])}
        groups.setdefault(record["mangaId"], []).append(hit)
    for hits in groups.values():
        hits.sort(key=lambda hit: (-hit["score"], hit.get("page", hit.get("chunkIndex", 0))))
        del hits[1 if modality == "summary" else 3:]
    return groups


def _rank_map(groups: dict, modality: str) -> dict:
    return {manga_id: rank for rank, (manga_id, _) in enumerate(rank_results(
        groups if modality == "summary" else {}, groups if modality == "image" else {}, modality, len(groups)), 1)}


def run_query(query: str, index: dict, encoder: SearchEncoders, modes: list[str], top_k: int) -> dict:
    import numpy as np

    needed = {modality for mode in modes for modality in
              ({"summary", "image"} if mode == "combined" else {"summary" if mode == "text" else mode})}
    query_vectors = {modality: np.asarray(encoder.encode(modality, [query], query=True)[0], dtype=np.float32)
                     for modality in needed}
    summary_groups = _groups(index["summary_records"], index["summary_vectors"], query_vectors["summary"], "summary") if "summary" in needed else {}
    image_groups = _groups(index["image_records"], index["image_vectors"], query_vectors["image"], "image") if "image" in needed else {}
    text_ranks = _rank_map(summary_groups, "summary")
    image_ranks = _rank_map(image_groups, "image")
    output = {}
    for mode in modes:
        rank_mode = "summary" if mode == "text" else mode
        ranked = rank_results(summary_groups, image_groups, rank_mode, top_k)
        rows = []
        for rank, (manga_id, score) in enumerate(ranked, 1):
            summary = summary_groups.get(manga_id, [])
            images = image_groups.get(manga_id, [])
            exemplar = (summary or images)[0]
            rows.append({"id": manga_id, "title": exemplar["title"], "rank": rank,
                "score": score if mode != "combined" else None, "rankScore": score,
                "textRank": text_ranks.get(manga_id), "imageRank": image_ranks.get(manga_id),
                "summaryScore": summary[0]["score"] if summary else None,
                "imageScore": images[0]["score"] if images else None,
                "excerpt": summary[0]["text"] if summary else None,
                "pages": [{"number": row["page"], "path": row["path"], "score": row["score"]} for row in images]})
        output[mode] = rows
    return output


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


def _print_results(query: str, results: dict, context_chars: int = 1000) -> None:
    print(f'QUERY\n{query}')
    labels = {"image": "IMAGE ONLY", "text": "TEXT ONLY", "combined": "COMBINED"}
    for mode, rows in results.items():
        print(f"\n{labels[mode]}\n" + "─" * 44)
        for row in rows:
            if mode == "combined":
                text_score = f'{row["summaryScore"]:.4f}' if row["summaryScore"] is not None else "—"
                image_score = f'{row["imageScore"]:.4f}' if row["imageScore"] is not None else "—"
                scores = f"text cosine {text_score} · image cosine {image_score}"
            else:
                scores = f'cosine {row["score"]:.4f}'
            print(f'{row["rank"]}. {row["title"]} [{row["id"]}]  {scores}')
            if row["pages"]:
                pages = ", ".join(f'{page["number"]} (cosine {page["score"]:.4f})' for page in row["pages"])
                print(f"   pages: {pages}")
            if row["excerpt"]:
                excerpt = " ".join(row["excerpt"].split())
                print(f'   Summary context: "{excerpt[:context_chars]}{"…" if len(excerpt) > context_chars else ""}"')
            if mode == "combined":
                print(f'   text #{row["textRank"] or "—"} · image #{row["imageRank"] or "—"}')


def _thumbnail(path: str) -> str:
    from PIL import Image, ImageOps

    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((260, 360), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=65, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def write_html_report(query: str, results: dict, destination: Path) -> None:
    labels = {"image": "Image only", "text": "Text only", "combined": "Combined"}
    sections = []
    for mode, rows in results.items():
        cards = []
        for row in rows:
            evidence = []
            for page in row["pages"]:
                src = _thumbnail(page["path"]) if Path(page["path"]).is_file() else ""
                evidence.append(f'<figure><img src="{src}" alt="Page {page["number"]}"><figcaption>Page {page["number"]} · cosine {page["score"]:.4f}</figcaption></figure>')
            excerpt = f'<blockquote>{html.escape(row["excerpt"])}</blockquote>' if row["excerpt"] else ""
            modality_ranks = (f'<p class="ranks">Text #{row["textRank"] or "—"} · Image #{row["imageRank"] or "—"}</p>'
                              if mode == "combined" else "")
            text_score = f'{row["summaryScore"]:.4f}' if row["summaryScore"] is not None else "—"
            image_score = f'{row["imageScore"]:.4f}' if row["imageScore"] is not None else "—"
            scores = (f'Text cosine {text_score} · Image cosine {image_score}'
                      if mode == "combined" else f'Cosine {row["score"]:.4f}')
            cards.append(f'<article><div class="heading"><span>#{row["rank"]}</span><h3>{html.escape(row["title"])}</h3><b>{scores}</b></div>{modality_ranks}{excerpt}<div class="pages">{"".join(evidence)}</div></article>')
        sections.append(f'<section><h2>{labels[mode]}</h2>{"".join(cards) or "<p>No results.</p>"}</section>')
    document = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Semantic search · {html.escape(query)}</title><style>
body{{margin:0;background:#f5f3ef;color:#24211e;font:15px/1.5 system-ui,sans-serif}}main{{max-width:1200px;margin:auto;padding:32px 20px}}header{{padding:24px;background:#fff;border-radius:14px}}h1{{font-size:14px;text-transform:uppercase;letter-spacing:.12em;color:#786f65}}header p{{font-size:22px;margin:6px 0 0}}section{{margin-top:28px}}section h2{{border-bottom:1px solid #d5d0c8;padding-bottom:9px}}article{{background:#fff;padding:18px;margin:12px 0;border-radius:12px;box-shadow:0 2px 10px #211a100c}}.heading{{display:flex;gap:12px;align-items:baseline}}.heading span,.heading b{{color:#7153a4}}.heading h3{{margin:0;flex:1}}.pages{{display:flex;gap:12px;overflow-x:auto}}figure{{margin:0;min-width:140px}}img{{display:block;max-height:360px;max-width:260px;object-fit:contain;background:#eee}}figcaption,.ranks{{color:#756c63;font-size:13px}}blockquote{{margin:12px 0;padding:10px 14px;background:#f7f6f4;border-left:3px solid #b4a1d5;white-space:pre-wrap}}
</style></head><body><main><header><h1>Query</h1><p>{html.escape(query)}</p></header>{"".join(sections)}</main></body></html>'''
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")


def _metrics(runs: list[dict]) -> dict:
    relevant_pages = [run["pageRecallAt5"] for run in runs if run["pageRecallAt5"] is not None]
    latencies = [run["latencyMs"] for run in runs]
    return {
        "queries": len(runs),
        "hitAt1": sum(run["hitAt1"] for run in runs) / len(runs),
        "hitAt5": sum(run["hitAt5"] for run in runs) / len(runs),
        "mrr": statistics.mean(run["reciprocalRank"] for run in runs),
        "pageRecallAt5": statistics.mean(relevant_pages) if relevant_pages else None,
        "meanLatencyMs": statistics.mean(latencies),
        "p95LatencyMs": sorted(latencies)[math.ceil(len(latencies) * .95) - 1],
    }


def benchmark(index: dict, encoder: SearchEncoders, labels_path: Path, top_k: int) -> dict:
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    if not isinstance(labels, dict):
        raise ValueError("Labels file must contain a JSON object")
    queries = labels.get("queries")
    if not isinstance(labels, dict) or not isinstance(queries, list) or not queries:
        raise ValueError("Labels file must contain a nonempty queries array")
    for row in queries:
        relevant = row.get("relevantManga") or row.get("relevantGroupIds") if isinstance(row, dict) else None
        if (not isinstance(row, dict) or not isinstance(row.get("query"), str) or not row["query"].strip()
                or not isinstance(relevant, list) or not relevant):
            raise ValueError("Each query needs query and relevantManga fields")
        pages = row.get("relevantPages", row.get("relevantPageIds", []))
        if not isinstance(pages, list):
            raise ValueError("relevantPages must be a list of mangaId:page values")
    encoder._load("summary")
    encoder._load("image")
    report = {}
    for mode in ("image", "text", "combined"):
        runs = []
        for row in queries:
            started = time.perf_counter()
            found = run_query(row["query"], index, encoder, [mode], top_k)[mode]
            elapsed = (time.perf_counter() - started) * 1000
            relevant = set(row.get("relevantManga") or row.get("relevantGroupIds", []))
            ranks = [item["rank"] for item in found if item["id"] in relevant or item["title"] in relevant]
            page_recall = None
            relevant_pages = set(row.get("relevantPages", row.get("relevantPageIds", [])))
            if relevant_pages and mode != "text":
                actual = {f'{item["id"]}:{page["number"]}' for item in found[:5] for page in item["pages"]}
                page_recall = len(actual & relevant_pages) / len(relevant_pages)
            runs.append({"query": row["query"], "hitAt1": bool(ranks and min(ranks) == 1),
                "hitAt5": bool(ranks and min(ranks) <= 5), "reciprocalRank": 1 / min(ranks) if ranks else 0,
                "pageRecallAt5": page_recall, "latencyMs": round(elapsed, 2), "results": found})
        report[mode] = {"metrics": _metrics(runs), "runs": runs}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    index_parser = commands.add_parser("index", help="Build or incrementally update the local index")
    index_parser.add_argument("--input", nargs="+", type=Path, required=True, help="Manga directories or ZIP archives")
    index_parser.add_argument("--output", type=Path, default=Path("devscripts/data/semantic_search"))
    index_parser.add_argument("--mode", choices=("summary", "image", "all"), default="all",
                              help="Index summaries, images, or both (default: all)")
    index_parser.add_argument("--device", help="Torch device; defaults to CUDA, then MPS, then CPU")
    index_parser.add_argument("--text-model", choices=tuple(TEXT_MODEL_OPTIONS), default="bge",
                              help="Text embedding model (default: bge)")
    for name in ("search", "compare", "benchmark", "retrieve"):
        command = commands.add_parser(name)
        command.add_argument("--index", type=Path, default=Path("devscripts/data/semantic_search"))
        command.add_argument("--device", help="Torch device; defaults to CUDA, then MPS, then CPU")
        command.add_argument("--text-model", choices=tuple(TEXT_MODEL_OPTIONS), default="bge",
                             help="Text embedding model used to build this index")
    search_parser = commands.choices["search"]
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--mode", choices=("image", "text", "combined"), default="combined")
    search_parser.add_argument("--top-k", type=int, default=5)
    search_parser.add_argument("--report", type=Path)
    search_parser.add_argument("--context-chars", type=int, default=1000,
                               help="Maximum summary context characters to print (default: 1000)")
    compare_parser = commands.choices["compare"]
    compare_parser.add_argument("--query", required=True)
    compare_parser.add_argument("--top-k", type=int, default=5)
    compare_parser.add_argument("--report", type=Path)
    compare_parser.add_argument("--context-chars", type=int, default=1000,
                                help="Maximum summary context characters to print (default: 1000)")
    benchmark_parser = commands.choices["benchmark"]
    benchmark_parser.add_argument("--labels", type=Path, required=True,
                                  help="JSON containing queries with relevantManga and optional relevantPages lists")
    benchmark_parser.add_argument("--top-k", type=int, default=20)
    benchmark_parser.add_argument("--output", type=Path)
    retrieve_parser = commands.choices["retrieve"]
    retrieve_parser.add_argument("--query", required=True)
    retrieve_parser.add_argument("--top-k", type=int, default=5)
    retrieve_parser.add_argument("--output", type=Path, help="Write retrieval JSON to this file")

    args = parser.parse_args()
    try:
        encoder = SearchEncoders(device=args.device, text_model=args.text_model)
        if args.command == "index":
            result = build_index(args.input, args.output, encoder, args.mode)
            print(f'Indexed {result["manga"]} manga at {result["output"]}')
            print(f'Summary chunks: embedded {result["summaryEmbedded"]}, reused {result["summaryReused"]}')
            print(f'Images: embedded {result["imagesEmbedded"]}, reused {result["imagesReused"]}')
            return
        index = _load_index(args.index, encoder)
        if args.top_k < 1:
            parser.error("--top-k must be at least 1")
        if args.command == "retrieve":
            result = {"query": args.query, "model": TEXT_MODEL_OPTIONS[encoder.text_model][0],
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
            report = benchmark(index, encoder, args.labels, args.top_k)
            encoded = json.dumps(report, indent=2, ensure_ascii=False)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(encoded, encoding="utf-8")
            for mode, result in report.items():
                metrics = result["metrics"]
                print(f'{mode:8} Hit@1 {metrics["hitAt1"]:.3f}  Hit@5 {metrics["hitAt5"]:.3f}  '
                      f'MRR {metrics["mrr"]:.3f}  Page R@5 {metrics["pageRecallAt5"]}  '
                      f'latency {metrics["meanLatencyMs"]:.1f} ms')
            return
        modes = ["image", "text", "combined"] if args.command == "compare" else [args.mode]
        results = run_query(args.query, index, encoder, modes, args.top_k)
        _print_results(args.query, results, args.context_chars)
        if args.report:
            write_html_report(args.query, results, args.report)
            print(f"\nHTML report: {args.report}")
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
