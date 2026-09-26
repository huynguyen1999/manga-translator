#!/usr/bin/env python3
"""Test image embedding models with index caching and text/image queries."""
from __future__ import annotations

import argparse
import cmd
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Ensure repository root is on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import torch
from PIL import Image, ImageOps
from transformers import AutoModel, AutoProcessor

SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
DEFAULT_INDEX_PATH = ROOT / "devscripts" / "data" / "image_embeddings_index.json"

DEFAULT_MODELS = {
    "siglip": "google/siglip-base-patch16-256",
    "siglip2": "google/siglip2-base-patch16-256",
    "clip": "openai/clip-vit-base-patch32",
}


def prepare_image(path: Path, image_size: int = 256) -> Image.Image:
    """Preprocess image with EXIF orientation correction and white padding."""
    with Image.open(path) as original:
        img = ImageOps.exif_transpose(original).convert("RGB")
        return ImageOps.pad(img, (image_size, image_size), method=Image.Resampling.LANCZOS, color="white")


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Cosine similarity between two unit-normalized vectors (dot product)."""
    return sum(a * b for a, b in zip(v1, v2))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ImageEmbeddingTester:
    def __init__(self, model_name_or_path: str, device: str | None = None, image_size: int = 256):
        self.model_name = DEFAULT_MODELS.get(model_name_or_path, model_name_or_path)
        self.image_size = image_size
        self.device = device
        self._processor = None
        self._model = None

        self.gallery: Dict[str, Dict[str, Any]] = {}  # {path_str: {"sha256": ..., "embedding": [...]}}

    def _ensure_model_loaded(self):
        if self._model is not None:
            return

        if self.device is None:
            if torch.cuda.is_available():
                self.device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                self.device = "mps"
            else:
                self.device = "cpu"

        cache_dir = os.getenv("MANGA_SEARCH_MODEL_CACHE", str(ROOT / "models" / "search"))
        print(f"[*] Loading model '{self.model_name}' on device '{self.device}'...")
        self._processor = AutoProcessor.from_pretrained(self.model_name, cache_dir=cache_dir)
        self._model = AutoModel.from_pretrained(self.model_name, cache_dir=cache_dir).eval().to(self.device)
        print("[+] Model loaded successfully.")

    def _normalize(self, features: torch.Tensor) -> List[List[float]]:
        features = features / features.norm(p=2, dim=-1, keepdim=True)
        return features.float().cpu().tolist()

    def encode_images(self, paths: List[Path], batch_size: int = 8) -> List[List[float]]:
        self._ensure_model_loaded()
        all_embeddings: List[List[float]] = []
        for i in range(0, len(paths), batch_size):
            batch_paths = paths[i : i + batch_size]
            images = [prepare_image(p, self.image_size) for p in batch_paths]
            inputs = self._processor(images=images, return_tensors="pt").to(self.device)
            with torch.inference_mode():
                features = self._model.get_image_features(**inputs)
                if not isinstance(features, torch.Tensor):
                    features = features.pooler_output
                embeddings = self._normalize(features)
                all_embeddings.extend(embeddings)
        return all_embeddings

    def encode_text(self, text: str) -> List[float]:
        self._ensure_model_loaded()
        inputs = self._processor(text=[text], padding="max_length", max_length=64, truncation=True, return_tensors="pt").to(self.device)
        with torch.inference_mode():
            features = self._model.get_text_features(**inputs)
            if not isinstance(features, torch.Tensor):
                features = features.pooler_output
            embedding = self._normalize(features)[0]
        return embedding

    def save_index(self, index_path: Path):
        index_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "model_name": self.model_name,
            "image_size": self.image_size,
            "items": self.gallery,
        }
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[+] Saved {len(self.gallery)} embeddings to: {index_path}")

    def load_index(self, index_path: Path, strict_model: bool = True) -> bool:
        if not index_path.is_file():
            return False

        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        saved_model = data.get("model_name")
        if strict_model and saved_model and saved_model != self.model_name:
            print(f"[!] Warning: Index was created with model '{saved_model}', but current model is '{self.model_name}'.")
            print("[!] Embeddings may not match across different models.")

        self.gallery = data.get("items", {})
        print(f"[+] Loaded {len(self.gallery)} indexed images from: {index_path}")
        return True

    def index_images(self, image_paths: List[Path], batch_size: int = 8):
        valid_paths = [p for p in image_paths if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTS]
        if not valid_paths:
            print("[!] No valid image files provided.")
            return

        to_encode: List[Path] = []
        hashes: Dict[Path, str] = {}

        for p in valid_paths:
            path_str = str(p.resolve())
            file_hash = sha256_file(p)
            hashes[p] = file_hash
            cached = self.gallery.get(path_str)
            if not cached or cached.get("sha256") != file_hash:
                to_encode.append(p)

        if to_encode:
            print(f"[*] Encoding {len(to_encode)} new/modified images (out of {len(valid_paths)} total)...")
            embeddings = self.encode_images(to_encode, batch_size=batch_size)
            for p, emb in zip(to_encode, embeddings):
                path_str = str(p.resolve())
                self.gallery[path_str] = {
                    "sha256": hashes[p],
                    "embedding": emb,
                }
            print(f"[+] Successfully indexed {len(to_encode)} images.")
        else:
            print(f"[+] All {len(valid_paths)} images are already up to date in the index.")

    def query(self, query_val: str | Path, is_image: bool = False, top_k: int = 10) -> List[Tuple[Path, float]]:
        if not self.gallery:
            print("[!] Gallery is empty. Index images first using --index or load an index file.")
            return []

        if is_image:
            img_path = Path(query_val).resolve()
            if not img_path.exists():
                raise FileNotFoundError(f"Query image not found: {img_path}")
            query_emb = self.encode_images([img_path])[0]
        else:
            query_emb = self.encode_text(str(query_val))

        scores: List[Tuple[Path, float]] = []
        for path_str, item in self.gallery.items():
            emb = item["embedding"]
            score = cosine_similarity(query_emb, emb)
            scores.append((Path(path_str), score))

        scores.sort(key=lambda item: item[1], reverse=True)
        return scores[:top_k]


def collect_images_from_inputs(inputs: List[str]) -> List[Path]:
    paths: List[Path] = []
    for inp in inputs:
        p = Path(inp).resolve()
        if p.is_dir():
            for item in p.rglob("*"):
                if item.is_file() and item.suffix.lower() in SUPPORTED_IMAGE_EXTS:
                    paths.append(item)
        elif p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTS:
            paths.append(p)
        else:
            print(f"[!] Warning: Path '{inp}' does not exist or is not a supported image file.")
    return sorted(list(set(paths)))


class InteractiveTester(cmd.Cmd):
    intro = "\n=== Image Embedding Tester CLI ===\nCommands: index <paths>, text <query>, image <path>, list, save, help, exit\n"
    prompt = "embed-test> "

    def __init__(self, tester: ImageEmbeddingTester, index_file: Path):
        super().__init__()
        self.tester = tester
        self.index_file = index_file

    def do_index(self, arg: str):
        """Index image files or folders. Usage: index <path1> <path2> ..."""
        args = arg.split()
        if not args:
            print("Usage: index <path1> [path2 ...]")
            return
        images = collect_images_from_inputs(args)
        if images:
            self.tester.index_images(images)
            self.tester.save_index(self.index_file)

    def do_list(self, _arg: str):
        """List currently indexed images."""
        if not self.tester.gallery:
            print("Gallery index is empty.")
            return
        print(f"Gallery contains {len(self.tester.gallery)} indexed images:")
        for idx, p in enumerate(self.tester.gallery.keys()):
            print(f"  [{idx + 1}] {p}")

    def do_text(self, arg: str):
        """Query gallery with text. Usage: text <query string>"""
        if not arg.strip():
            print("Usage: text <search text>")
            return
        results = self.tester.query(arg.strip(), is_image=False)
        self._print_results(results, query_desc=f"Text: '{arg.strip()}'")

    def do_image(self, arg: str):
        """Query gallery with an image. Usage: image <path to image>"""
        path_str = arg.strip().strip("'\"")
        if not path_str:
            print("Usage: image <path_to_image>")
            return
        img_path = Path(path_str).resolve()
        if not img_path.is_file():
            print(f"Error: File not found or not a valid image '{path_str}'")
            return
        results = self.tester.query(img_path, is_image=True)
        self._print_results(results, query_desc=f"Image: '{img_path.name}'")

    def do_save(self, arg: str):
        """Save current index to file. Usage: save [custom_index_path]"""
        path = Path(arg.strip()).resolve() if arg.strip() else self.index_file
        self.tester.save_index(path)

    def _print_results(self, results: List[Tuple[Path, float]], query_desc: str):
        print(f"\n--- Top matches for [{query_desc}] ---")
        if not results:
            print("No results.")
            return
        for rank, (path, score) in enumerate(results, 1):
            bar_len = int(max(0.0, score) * 20)
            bar = "#" * bar_len + "-" * (20 - bar_len)
            print(f"  {rank:2d}. [{score:.4f}] [{bar}] {path}")
        print()

    def do_exit(self, _arg: str):
        """Exit the tester."""
        print("Goodbye!")
        return True

    def do_quit(self, _arg: str):
        """Exit the tester."""
        return self.do_exit(_arg)

    def do_EOF(self, _arg: str):
        """Exit on EOF (Ctrl+D)."""
        print()
        return self.do_exit(_arg)


def main():
    parser = argparse.ArgumentParser(description="Test multimodal image embedding models with persistent index & 2-shot commands.")
    parser.add_argument("--index-file", default=str(DEFAULT_INDEX_PATH), help=f"Path to saved index JSON file (default: {DEFAULT_INDEX_PATH}).")
    parser.add_argument("--index", "-i", nargs="+", help="Shot 1: Images or folders to compute embeddings and save to index.")
    parser.add_argument("--model", "-m", default="siglip", help="Model name, HuggingFace ID, or preset (siglip, siglip2, clip). Default: siglip")
    parser.add_argument("--device", "-d", choices=["cuda", "mps", "cpu"], default=None, help="Inference device")
    parser.add_argument("--query-text", "-t", help="Shot 2: Query indexed embeddings using text.")
    parser.add_argument("--query-image", "-q", help="Shot 2: Query indexed embeddings using another image.")
    parser.add_argument("--top-k", "-k", type=int, default=10, help="Number of top matches to display (default: 10).")
    parser.add_argument("--batch-size", "-b", type=int, default=8, help="Batch size for image encoding (default: 8).")
    parser.add_argument("--interactive", action="store_true", help="Launch interactive CLI shell.")
    
    args = parser.parse_args()
    index_file = Path(args.index_file).resolve()

    tester = ImageEmbeddingTester(args.model, device=args.device)

    # Automatically load existing index if present
    if index_file.is_file():
        tester.load_index(index_file)

    # Step / Shot 1: Index images and save
    if args.index:
        images = collect_images_from_inputs(args.index)
        if images:
            tester.index_images(images, batch_size=args.batch_size)
            tester.save_index(index_file)

    # Step / Shot 2: Query with text
    if args.query_text:
        results = tester.query(args.query_text, is_image=False, top_k=args.top_k)
        print(f"\n--- Top matches for Text: '{args.query_text}' ---")
        for rank, (p, score) in enumerate(results, 1):
            bar_len = int(max(0.0, score) * 20)
            bar = "#" * bar_len + "-" * (20 - bar_len)
            print(f"  {rank:2d}. [{score:.4f}] [{bar}] {p}")
        return

    # Step / Shot 2: Query with image
    if args.query_image:
        img_path = Path(args.query_image).resolve()
        results = tester.query(img_path, is_image=True, top_k=args.top_k)
        print(f"\n--- Top matches for Image: '{img_path.name}' ---")
        for rank, (p, score) in enumerate(results, 1):
            bar_len = int(max(0.0, score) * 20)
            bar = "#" * bar_len + "-" * (20 - bar_len)
            print(f"  {rank:2d}. [{score:.4f}] [{bar}] {p}")
        return

    # If neither query was given and not explicitly indexing only, or --interactive requested
    if args.interactive or not (args.index or args.query_text or args.query_image):
        InteractiveTester(tester, index_file).cmdloop()


if __name__ == "__main__":
    main()
