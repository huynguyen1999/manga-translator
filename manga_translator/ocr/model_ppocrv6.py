"""PP-OCRv6 Small Manga CPU recognizer with unpadded exact-width parallel inference."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from typing import List, Sequence, Tuple
import urllib.request

import cv2
import numpy as np

try:
    import onnxruntime as ort
except ImportError:
    ort = None

from ..config import Ocr, OcrConfig
from ..pipeline.cpu import CPU_PRIORITY_BACKGROUND, CPU_PRIORITY_NORMAL, run_cpu_stage
from ..utils import Quadrilateral, TextBlock
from ..utils.bubble import is_ignore
from .common import OfflineOCR


PPOCRV6_REPO_ID = "Kellenok/PP-OCRv6_manga"
PPOCRV6_BASE_URL = f"https://huggingface.co/{PPOCRV6_REPO_ID}/resolve/main"
REC_REMOTE_FILENAME = "rec/manga_rec_v0.2.onnx"
REC_LOCAL_FILENAME = "manga_rec_v0.2.onnx"
DICT_FILENAME = "ppocrv6_dict.txt"
PPOCRV6_REQUIRED_FILES = (REC_LOCAL_FILENAME, DICT_FILENAME)


def is_cpu_ocr(ocr_key: object) -> bool:
    value = getattr(ocr_key, "value", ocr_key)
    return str(value or "").lower() == Ocr.ppocrv6.value


def estimate_crop_colors(
    crop_rgb: np.ndarray,
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Estimate (fg_rgb, bg_rgb) on a 48px RGB text crop using fast Otsu binarization."""
    if crop_rgb is None or crop_rgb.size == 0 or crop_rgb.ndim != 3:
        return (0, 0, 0), (255, 255, 255)

    gray = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY)
    thresh_val, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark = gray <= thresh_val
    light = ~dark
    if not np.any(dark) or not np.any(light):
        val = int(np.clip(round(float(np.mean(crop_rgb))), 0, 255))
        fg = (0, 0, 0) if val >= 128 else (255, 255, 255)
        return fg, (val, val, val)

    h, w = gray.shape
    border = np.zeros((h, w), dtype=bool)
    band = min(2, max(1, min(h, w) // 6))
    border[:band, :] = True
    border[-band:, :] = True
    border[:, :band] = True
    border[:, -band:] = True

    dark_is_fg = float(np.mean(dark[border])) < 0.5
    fg_mask, bg_mask = (dark, light) if dark_is_fg else (light, dark)
    fg_rgb = tuple(
        int(np.clip(round(float(v)), 0, 255))
        for v in np.median(crop_rgb[fg_mask], axis=0)
    )
    bg_rgb = tuple(
        int(np.clip(round(float(v)), 0, 255))
        for v in np.median(crop_rgb[bg_mask], axis=0)
    )
    return fg_rgb, bg_rgb


def decode_ctc_greedy(probs: np.ndarray, vocab: Sequence[str]) -> tuple[str, float]:
    """Collapse repeated CTC indices and remove blanks (index 0)."""
    if probs.size == 0:
        return "", 0.0
    indices = np.argmax(probs, axis=-1)
    vocab_len = len(vocab)
    chars: list[str] = []
    confs: list[float] = []
    prev_idx = -1
    for t, idx in enumerate(indices):
        idx_int = int(idx)
        if idx_int != 0 and idx_int != prev_idx and idx_int < vocab_len:
            chars.append(vocab[idx_int])
            confs.append(float(probs[t, idx_int]))
        prev_idx = idx_int
    text = "".join(chars).strip()
    score = float(np.mean(confs)) if confs else 0.0
    return text, score


def prepare_crop_tensor(crop_rgb: np.ndarray) -> tuple[np.ndarray, int] | None:
    """Resize 48px-tall RGB crop to its natural width in BGR CHW [-1, 1] float32."""
    if crop_rgb is None or crop_rgb.size == 0:
        return None
    ch, cw = crop_rgb.shape[:2]
    if ch < 2 or cw < 2:
        return None
    if ch > cw * 1.15:
        crop_rgb = cv2.rotate(crop_rgb, cv2.ROTATE_90_COUNTERCLOCKWISE)
        ch, cw = crop_rgb.shape[:2]

    natural_w = int(round(48.0 * cw / max(1, ch)))
    target_w = max(16, min(2400 if natural_w > 2000 else 640, natural_w))
    crop_bgr = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2BGR)
    if ch != 48 or cw != target_w:
        resized = cv2.resize(crop_bgr, (target_w, 48), interpolation=cv2.INTER_LINEAR)
    else:
        resized = crop_bgr
    norm = (resized.astype(np.float32) * (1.0 / 127.5)) - 1.0
    tensor = np.ascontiguousarray(norm.transpose((2, 0, 1)), dtype=np.float32)
    return tensor, target_w


class ModelPPOCRv6(OfflineOCR):
    _MODEL_MAPPING = {}
    PPOCRV6_REPO_ID = PPOCRV6_REPO_ID
    PPOCRV6_REQUIRED_FILES = PPOCRV6_REQUIRED_FILES

    def __init__(self, *args, **kwargs):
        self.session = None
        self.vocab: list[str] = []
        self.input_name = "x"
        self.output_name = None
        self.device = "cpu"
        self.use_gpu = False
        self._crop_executor: ThreadPoolExecutor | None = None
        super().__init__(*args, **kwargs)

    @property
    def shared_rec_dir(self) -> str:
        return os.path.join(self._MODEL_DIR, "recognition", "ppocrv6")

    def _resolve_file_path(self, filename: str) -> str:
        primary = self._get_file_path(filename)
        if os.path.isfile(primary):
            return primary
        shared = os.path.join(self.shared_rec_dir, filename)
        if os.path.isfile(shared):
            return shared
        return primary

    def _check_downloaded(self) -> bool:
        return all(
            os.path.isfile(self._resolve_file_path(fname))
            for fname in self.PPOCRV6_REQUIRED_FILES
        )

    async def _download(self):
        os.makedirs(self.model_dir, exist_ok=True)
        downloads = (
            (REC_REMOTE_FILENAME, REC_LOCAL_FILENAME),
            (DICT_FILENAME, DICT_FILENAME),
        )
        for remote_name, local_name in downloads:
            resolved = self._resolve_file_path(local_name)
            if os.path.isfile(resolved):
                continue
            target_path = Path(self._get_file_path(local_name))
            target_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                from huggingface_hub import hf_hub_download

                downloaded = Path(
                    hf_hub_download(
                        repo_id=self.PPOCRV6_REPO_ID,
                        filename=remote_name,
                        local_dir=str(target_path.parent),
                    )
                )
                if downloaded != target_path and downloaded.is_file():
                    downloaded.replace(target_path)
                    rec_subdir = target_path.parent / "rec"
                    if rec_subdir.is_dir() and not any(rec_subdir.iterdir()):
                        rec_subdir.rmdir()
                continue
            except Exception as exc:
                self.logger.warning(
                    f"huggingface_hub download failed for {remote_name} ({exc}); falling back to direct URL"
                )
            url = f"{PPOCRV6_BASE_URL}/{remote_name}"
            urllib.request.urlretrieve(url, str(target_path))

    async def load(self, device: str = "cpu", *args, **kwargs):
        return await super().load("cpu", *args, **kwargs)

    async def _load(self, device: str):
        if ort is None:
            raise RuntimeError("onnxruntime is required for PP-OCRv6 OCR")
        model_path = self._resolve_file_path(REC_LOCAL_FILENAME)
        dict_path = self._resolve_file_path(DICT_FILENAME)
        with open(dict_path, "r", encoding="utf-8") as fp:
            self.vocab = ["blank"] + [line.strip("\r\n") for line in fp] + [" "]

        cpu_cores = max(1, os.cpu_count() or 4)
        intra_threads = max(1, min(4, cpu_cores // 2))
        worker_threads = max(1, min(4, cpu_cores // max(1, intra_threads)))

        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        sess_options.enable_cpu_mem_arena = True
        sess_options.inter_op_num_threads = 1
        sess_options.intra_op_num_threads = intra_threads

        self.session = ort.InferenceSession(
            model_path,
            sess_options=sess_options,
            providers=["CPUExecutionProvider"],
        )
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name
        self.device = "cpu"
        self.use_gpu = False
        self._set_runtime(device="cpu", backend="onnxruntime-cpu")
        self._crop_executor = ThreadPoolExecutor(
            max_workers=worker_threads,
            thread_name_prefix="ppocrv6-cpu",
        )
        self.logger.info(
            f"Loaded PP-OCRv6 Small Manga on CPU (intra_threads={intra_threads}, "
            f"crop_workers={worker_threads}, vocab={len(self.vocab)})"
        )

    async def _unload(self):
        if self._crop_executor is not None:
            self._crop_executor.shutdown(wait=True)
            self._crop_executor = None
        self.session = None
        self.vocab = []

    def _run_exact_width_group(
        self, items: list[tuple[int, np.ndarray]]
    ) -> list[tuple[int, str, float]]:
        if len(items) == 1:
            idx, tensor = items[0]
            probs = self.session.run(
                [self.output_name], {self.input_name: tensor[np.newaxis, ...]}
            )[0][0]
            text, score = decode_ctc_greedy(probs, self.vocab)
            return [(idx, text, score)]

        batch_tensor = np.stack([tensor for _, tensor in items], axis=0)
        batch_probs = self.session.run(
            [self.output_name], {self.input_name: batch_tensor}
        )[0]
        decoded: list[tuple[int, str, float]] = []
        for (idx, _), probs in zip(items, batch_probs):
            text, score = decode_ctc_greedy(probs, self.vocab)
            decoded.append((idx, text, score))
        return decoded

    def _recognize_pages_sync(
        self,
        pages: Sequence[Tuple[np.ndarray, List[Quadrilateral], OcrConfig]],
        verbose: bool = False,
    ) -> List[List[Quadrilateral]]:
        if not pages:
            return []

        text_height = 48
        outputs: list[list] = []
        is_quad_pages: list[bool] = []
        width_groups: dict[int, list[tuple[int, np.ndarray]]] = {}
        crop_records: list[tuple[int, object, str, np.ndarray, bool]] = []
        debug_idx = 0

        for page_index, (image, textlines, config) in enumerate(pages):
            lines = list(self._generate_text_direction(textlines))
            is_quads = bool(lines) and isinstance(lines[0][0], Quadrilateral)
            is_quad_pages.append(is_quads)
            outputs.append([] if is_quads else textlines)
            ignore_bubble = getattr(config, "ignore_bubble", 0) or 0

            for region, direction in lines:
                crop = region.get_transformed_region(image, direction, text_height)
                if 1 <= ignore_bubble <= 50 and is_ignore(crop, ignore_bubble):
                    debug_idx += 1
                    continue
                if verbose:
                    ocr_result_dir = os.environ.get("MANGA_OCR_RESULT_DIR", "result/ocrs/")
                    os.makedirs(ocr_result_dir, exist_ok=True)
                    img_data = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
                    if direction == "v":
                        img_data = cv2.rotate(img_data, cv2.ROTATE_90_CLOCKWISE)
                    cv2.imwrite(os.path.join(ocr_result_dir, f"{debug_idx}.png"), img_data)
                debug_idx += 1

                prepared = prepare_crop_tensor(crop)
                if prepared is None:
                    continue
                tensor, target_w = prepared
                record_index = len(crop_records)
                crop_records.append((page_index, region, direction, crop, is_quads))
                width_groups.setdefault(target_w, []).append((record_index, tensor))

        if not crop_records:
            return outputs

        groups = list(width_groups.values())
        recognized: dict[int, tuple[str, float]] = {}
        if len(groups) == 1 or self._crop_executor is None:
            for group in groups:
                for record_index, text, score in self._run_exact_width_group(group):
                    recognized[record_index] = (text, score)
        else:
            for group_results in self._crop_executor.map(self._run_exact_width_group, groups):
                for record_index, text, score in group_results:
                    recognized[record_index] = (text, score)

        for record_index, (page_index, region, _, crop, is_quads) in enumerate(crop_records):
            text, prob = recognized.get(record_index, ("", 0.0))
            if not text:
                continue
            config = pages[page_index][2]
            threshold = 0.2 if config.prob is None else config.prob
            if prob < threshold:
                continue
            (fr, fg, fb), (br, bg, bb) = estimate_crop_colors(crop)
            self.logger.info(
                f"prob: {prob:.3f} {text} fg: ({fr}, {fg}, {fb}) bg: ({br}, {bg}, {bb})"
            )
            if is_quads:
                region.text = text
                region.prob = prob
                region.fg_r, region.fg_g, region.fg_b = fr, fg, fb
                region.bg_r, region.bg_g, region.bg_b = br, bg, bb
                outputs[page_index].append(region)
            else:
                region.text.append(text)
                region.update_font_colors(
                    np.array([fr, fg, fb]), np.array([br, bg, bb])
                )

        return outputs

    async def _infer(
        self,
        image: np.ndarray,
        textlines: List[Quadrilateral],
        config: OcrConfig,
        verbose: bool = False,
    ) -> List[TextBlock]:
        return self._recognize_pages_sync([(image, textlines, config)], verbose)[0]

    async def _infer_batch(
        self,
        pages: List[Tuple[np.ndarray, List[Quadrilateral], OcrConfig]],
        verbose: bool = False,
    ) -> List[List[TextBlock]]:
        return self._recognize_pages_sync(pages, verbose)


async def call_ocr_with_policy(owner, fn, ocr_key, *args):
    """Run CPU OCR on the bounded CPU stage lane; keep GPU OCR on _mps_call."""
    if is_cpu_ocr(ocr_key):
        priority = (
            CPU_PRIORITY_BACKGROUND
            if getattr(owner, "_pipeline_run", None) is not None
            else CPU_PRIORITY_NORMAL
        )
        return await run_cpu_stage(fn, ocr_key, *args, priority=priority)
    return await owner._mps_call(fn, ocr_key, *args)


def ocr_operation(operation):
    """Bypass GPU SharedModelExecutor slots for CPU OCR while sharing cache."""
    from functools import wraps
    from ..utils.model_cache import (
        get_model_executor,
        model_operation,
        reset_model_cache,
        set_model_cache,
    )

    wrapped_gpu = model_operation(operation)

    @wraps(operation)
    async def run(*args, **kwargs):
        ocr_key = args[0] if args else kwargs.get("ocr_key")
        if is_cpu_ocr(ocr_key):
            executor = get_model_executor()
            if executor is None:
                return await operation(*args, **kwargs)
            token = set_model_cache(executor._cache)
            try:
                return await operation(*args, **kwargs)
            finally:
                reset_model_cache(token)
        return await wrapped_gpu(*args, **kwargs)

    return run

