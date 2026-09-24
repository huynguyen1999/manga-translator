"""Pinned, local search encoders and deterministic retrieval helpers."""
from __future__ import annotations

import hashlib
import math
import os
import uuid
from pathlib import Path

TEXT_MODEL = "BAAI/bge-small-en-v1.5"
TEXT_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
IMAGE_MODEL = "google/siglip-base-patch16-256"
IMAGE_REVISION = "b078df89e446d623010d890864d4207fe6399f61"
PROFILE = "bge-small-siglip-base-v1"
COLLECTION = "manga_search_v1"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
VECTOR_NAMES = {"summary": "summary_dense", "image": "page_image"}
DIMENSIONS = {"summary": 384, "image": 768}


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


def rank_results(summary, images, mode, limit=20):
    """Inputs are grouped by manga already. Similarities never cross vector spaces."""
    scores = {}
    for modality, groups in (("summary", summary), ("image", images)):
        if mode != "combined" and mode != modality:
            continue
        ordered = sorted(groups.items(), key=lambda item: (-item[1][0]["score"], item[0]))
        for rank, (group_id, hits) in enumerate(ordered, 1):
            scores[group_id] = scores.get(group_id, 0) + (
                1 / (60 + rank) if mode == "combined" else hits[0]["score"]
            )
    return [(group_id, score) for group_id, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def prepare_image(path: Path):
    from PIL import Image, ImageOps

    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        return ImageOps.pad(image, (256, 256), method=Image.Resampling.LANCZOS, color="white")


class SearchEncoders:
    """Production calls run on the shared model executor; local tools use it directly."""

    def __init__(self, device=None):
        self.models = {}
        self.processors = {}
        self.device = device

    def _load(self, modality):
        import torch
        from transformers import AutoModel, AutoProcessor, AutoTokenizer

        if self.device is None:
            if torch.cuda.is_available():
                self.device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                self.device = "mps"
            else:
                self.device = "cpu"
        if modality not in self.models:
            name, revision = (TEXT_MODEL, TEXT_REVISION) if modality == "summary" else (IMAGE_MODEL, IMAGE_REVISION)
            loader = AutoTokenizer if modality == "summary" else AutoProcessor
            cache = os.getenv("MANGA_SEARCH_MODEL_CACHE", str(Path(__file__).resolve().parents[1] / "models" / "search"))
            self.processors[modality] = loader.from_pretrained(name, revision=revision, cache_dir=cache)
            self.models[modality] = AutoModel.from_pretrained(name, revision=revision, cache_dir=cache).eval().to(self.device)
        return self.models[modality], self.processors[modality]

    def encode(self, modality, values, query=False):
        import torch

        model, processor = self._load(modality)
        if modality == "summary":
            texts = [QUERY_PREFIX + value if query else value for value in values]
            for value in texts:
                validate_query(value, processor, 512)
            inputs = processor(texts, padding=True, truncation=False, return_tensors="pt").to(self.device)
        elif query:
            for value in values:
                validate_query(value, processor.tokenizer, 64)
            inputs = processor(text=values, padding="max_length", max_length=64, truncation=False, return_tensors="pt").to(self.device)
        else:
            inputs = processor(images=[prepare_image(Path(value)) for value in values], return_tensors="pt").to(self.device)
        with torch.inference_mode():
            if modality == "summary":
                features = model(**inputs).last_hidden_state[:, 0]
            elif query:
                features = model.get_text_features(**inputs)
            else:
                features = model.get_image_features(**inputs)
            if not isinstance(features, torch.Tensor):
                features = features.pooler_output
            vectors = features.float().cpu().tolist()
        return [validate_vector(vector, DIMENSIONS[modality]) for vector in vectors]

    def chunks(self, text):
        _, tokenizer = self._load("summary")
        return chunk_summary(text, tokenizer)

    def unload(self):
        self.models.clear()
        self.processors.clear()
        from manga_translator.utils.device_memory import empty_device_cache
        empty_device_cache(self.device)
