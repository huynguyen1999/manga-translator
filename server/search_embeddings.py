"""Pinned, local search encoders and deterministic retrieval helpers."""
from __future__ import annotations

import hashlib
import math
import os
import uuid
from pathlib import Path

TEXT_MODEL = "BAAI/bge-base-en-v1.5"
TEXT_REVISION = "a5beb1e3e68b9ab74eb54cfd186867f64f240e1a"
RERANK_MODEL = "mixedbread-ai/mxbai-rerank-xsmall-v1"
RERANK_REVISION = "b5c6e9da73abc3711f593f705371cdbe9e0fe422"
PROFILE = "bge-base-siglip-base-v1"
COLLECTION = "manga_search_v1"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
VECTOR_NAMES = {"summary": "summary_dense"}
DIMENSIONS = {"summary": 768}
CANDIDATE_LIMIT = 50


def fingerprint(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def point_id(source_key: str, version: str, chunk: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{PROFILE}:{source_key}:{version}:{chunk}"))


def chunk_summary(text, tokenizer, max_tokens=512, overlap=64):
    """Return exact source excerpts, including offsets; never decode tokens to prose."""
    offsets = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]
    capacity = max_tokens - tokenizer.num_special_tokens_to_add(pair=False)
    if not 0 <= overlap < capacity:
        raise ValueError("Invalid summary chunk overlap")
    chunks = []
    start = 0
    while start < len(offsets):
        end = min(start + capacity, len(offsets))
        left, right = offsets[start][0], offsets[end - 1][1]
        # Retokenizing a substring may change word-boundary tokens.
        while len(tokenizer(text[left:right])["input_ids"]) > max_tokens and end > start + 1:
            end -= 1
            right = offsets[end - 1][1]
        chunks.append({"text": text[left:right], "start": left, "end": right})
        if end == len(offsets):
            break
        start = max(start + 1, end - overlap)
    return chunks


def validate_query(query, tokenizer, limit, prefix=""):
    count = len(tokenizer(prefix + query, add_special_tokens=True, truncation=False)["input_ids"])
    if count > limit:
        raise ValueError(f"Query uses {count} tokens; this mode allows {limit}. Shorten your description.")
    return count


def validate_vector(vector, dimension):
    if len(vector) != dimension or not all(math.isfinite(x) for x in vector):
        raise ValueError("Encoder returned an invalid vector")
    norm = math.sqrt(sum(x * x for x in vector))
    if norm < 1e-10:
        raise ValueError("Encoder returned a zero vector")
    return [x / norm for x in vector]


def rank_results(summary, limit=CANDIDATE_LIMIT):
    """Order grouped summary matches deterministically by highest chunk cosine score."""
    ordered = sorted(summary.items(), key=lambda item: (-item[1][0]["score"], item[0]))
    return [(group_id, float(hits[0]["score"])) for group_id, hits in ordered[:limit]]


def rerank_candidates(candidates, scores, limit=20, min_score=None):
    """Attach reranker scores to Stage 1 candidates, filter by min_score, and sort deterministically."""
    if len(candidates) != len(scores):
        raise ValueError("Candidate and rerank score counts must match")
    scored = []
    for idx, (item, rerank) in enumerate(zip(candidates, scores), 1):
        initial_rank = item.get("rank", idx)
        score = float(rerank["score"])
        logit = float(rerank["logit"])
        if not (math.isfinite(score) and math.isfinite(logit)):
            raise ValueError("Reranker returned an invalid score")
        if min_score is not None and score < min_score:
            continue
        scored.append({**item, "initialRank": initial_rank, "rerankScore": score, "rerankLogit": logit})
    scored.sort(key=lambda row: (
        -row["rerankScore"],
        -row["rerankLogit"],
        -(row.get("summarySimilarity") if row.get("summarySimilarity") is not None else row.get("score", 0.0)),
        row.get("groupId") or row.get("id") or "",
    ))
    return [{**row, "rank": rank, "rankDelta": row["initialRank"] - rank}
            for rank, row in enumerate(scored[:limit], 1)]


class SearchEncoders:
    """Production calls run on the shared model executor; local tools use it directly."""

    def __init__(self, device=None):
        self.models = {}
        self.processors = {}
        self.device = device

    def _load(self, modality):
        import torch
        from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

        if self.device is None:
            if torch.cuda.is_available():
                self.device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                self.device = "mps"
            else:
                self.device = "cpu"
        if modality not in self.models:
            cache = os.getenv("MANGA_SEARCH_MODEL_CACHE", str(Path(__file__).resolve().parents[1] / "models" / "search"))
            if modality == "summary":
                self.processors[modality] = AutoTokenizer.from_pretrained(TEXT_MODEL, revision=TEXT_REVISION, cache_dir=cache)
                self.models[modality] = AutoModel.from_pretrained(TEXT_MODEL, revision=TEXT_REVISION, cache_dir=cache).eval().to(self.device)
            elif modality == "rerank":
                self.processors[modality] = AutoTokenizer.from_pretrained(RERANK_MODEL, revision=RERANK_REVISION, cache_dir=cache)
                self.models[modality] = AutoModelForSequenceClassification.from_pretrained(
                    RERANK_MODEL, revision=RERANK_REVISION, cache_dir=cache
                ).eval().to(self.device)
            else:
                raise ValueError(f"Unsupported search modality: {modality}")
        return self.models[modality], self.processors[modality]

    def encode(self, modality, values, query=False):
        import torch

        if modality != "summary":
            raise ValueError(f"Unsupported encoding modality: {modality}")
        model, processor = self._load("summary")
        texts = [QUERY_PREFIX + value if query else value for value in values]
        for value in texts:
            validate_query(value, processor, 512)
        inputs = processor(texts, padding=True, truncation=False, return_tensors="pt").to(self.device)
        with torch.inference_mode():
            features = model(**inputs).last_hidden_state[:, 0]
            if not isinstance(features, torch.Tensor):
                features = features.pooler_output
            vectors = features.float().cpu().tolist()
        return [validate_vector(vector, DIMENSIONS["summary"]) for vector in vectors]

    def rerank(self, query: str, passages: list[str], batch_size: int = 8) -> list[dict]:
        import torch

        if not passages:
            return []
        model, tokenizer = self._load("rerank")
        validate_query(query, tokenizer, 512)
        results = []
        for offset in range(0, len(passages), batch_size):
            batch = passages[offset:offset + batch_size]
            pairs = [[query, passage or ""] for passage in batch]
            inputs = tokenizer(pairs, padding=True, truncation=True, max_length=512, return_tensors="pt").to(self.device)
            with torch.inference_mode():
                logits = model(**inputs).logits.squeeze(-1).float().cpu()
                if logits.ndim == 0:
                    logits = logits.unsqueeze(0)
                probs = torch.sigmoid(logits)
            for prob, logit in zip(probs.tolist(), logits.tolist()):
                results.append({"score": float(prob), "logit": float(logit)})
        return results

    def chunks(self, text):
        _, tokenizer = self._load("summary")
        return chunk_summary(text, tokenizer)

    def unload(self):
        self.models.clear()
        self.processors.clear()
        from manga_translator.utils.device_memory import empty_device_cache
        empty_device_cache(self.device)
