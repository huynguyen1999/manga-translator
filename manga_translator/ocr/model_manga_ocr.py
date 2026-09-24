import itertools
import math
from typing import Callable, List, Set, Optional, Tuple, Union
from collections import defaultdict, Counter
import os
import shutil
import cv2
from PIL import Image
import numpy as np
import einops
import networkx as nx
from shapely.geometry import Polygon

import torch

try:
    from manga_ocr import MangaOcr
except ImportError:
    MangaOcr = None

from .common import OfflineOCR
from .model_48px import OCR
from ..config import OcrConfig
from ..textline_merge import split_text_region
from ..utils import TextBlock, Quadrilateral, quadrilateral_can_merge_region, chunks
from ..utils.generic import AvgMeter

async def merge_bboxes(bboxes: List[Quadrilateral], width: int, height: int) -> Tuple[List[Quadrilateral], int]:
    # step 1: divide into multiple text region candidates
    G = nx.Graph()
    for i, box in enumerate(bboxes):
        G.add_node(i, box=box)
    for ((u, ubox), (v, vbox)) in itertools.combinations(enumerate(bboxes), 2):
        # if quadrilateral_can_merge_region_coarse(ubox, vbox):
        if quadrilateral_can_merge_region(ubox, vbox, aspect_ratio_tol=1.3, font_size_ratio_tol=2,
                                          char_gap_tolerance=1, char_gap_tolerance2=3):
            G.add_edge(u, v)

    # step 2: postprocess - further split each region
    region_indices: List[Set[int]] = []
    for node_set in nx.algorithms.components.connected_components(G):
         region_indices.extend(split_text_region(bboxes, node_set, width, height))

    # step 3: return regions
    merge_box = []
    merge_idx = []
    for node_set in region_indices:
    # for node_set in nx.algorithms.components.connected_components(G):
        nodes = list(node_set)
        txtlns: List[Quadrilateral] = np.array(bboxes)[nodes]

        # majority vote for direction
        dirs = [box.direction for box in txtlns]
        majority_dir_top_2 = Counter(dirs).most_common(2)
        if len(majority_dir_top_2) == 1 :
            majority_dir = majority_dir_top_2[0][0]
        elif majority_dir_top_2[0][1] == majority_dir_top_2[1][1] : # if top 2 have the same counts
            max_aspect_ratio = -100
            for box in txtlns :
                if box.aspect_ratio > max_aspect_ratio :
                    max_aspect_ratio = box.aspect_ratio
                    majority_dir = box.direction
                if 1.0 / box.aspect_ratio > max_aspect_ratio :
                    max_aspect_ratio = 1.0 / box.aspect_ratio
                    majority_dir = box.direction
        else :
            majority_dir = majority_dir_top_2[0][0]

        # sort textlines
        if majority_dir == 'h':
            nodes = sorted(nodes, key=lambda x: bboxes[x].centroid[1])
        elif majority_dir == 'v':
            nodes = sorted(nodes, key=lambda x: -bboxes[x].centroid[0])
        txtlns = np.array(bboxes)[nodes]
        # yield overall bbox and sorted indices
        merge_box.append(txtlns)
        merge_idx.append(nodes)
    
    return_box = []
    for bbox in merge_box:
        if len(bbox) == 1:
            return_box.append(bbox[0])
        else:
            prob = [q.prob for q in bbox]
            prob = sum(prob)/len(prob)
            base_box = bbox[0]
            for box in bbox[1:]:
                min_rect = np.array(Polygon([*base_box.pts, *box.pts]).minimum_rotated_rectangle.exterior.coords[:4])
                base_box = Quadrilateral(min_rect, '', prob)
            return_box.append(base_box)
    return return_box, merge_idx

def infer_mocr_batch(mocr: MangaOcr, images: List[Image.Image], batch_size: int = 8) -> List[str]:
    results = []
    for i in range(0, len(images), batch_size):
        chunk = images[i:i + batch_size]
        converted = [img.convert("L").convert("RGB") for img in chunk]
        pixel_values = mocr.processor(converted, return_tensors="pt").pixel_values
        device = getattr(mocr.model, 'device', 'cpu')
        generated = mocr.model.generate(pixel_values.to(device), max_length=300).cpu()
        decoded = mocr.tokenizer.batch_decode(generated, skip_special_tokens=True)
        from manga_ocr.ocr import post_process
        results.extend([post_process(t) for t in decoded])
    return results

class ModelMangaOCR(OfflineOCR):
    _MODEL_MAPPING = {
        'model': {
            'url': 'https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/ocr_ar_48px.ckpt',
            'hash': '29daa46d080818bb4ab239a518a88338cbccff8f901bef8c9db191a7cb97671d',
        },
        'dict': {
            'url': 'https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/alphabet-all-v7.txt',
            'hash': 'f5722368146aa0fbcc9f4726866e4efc3203318ebb66c811d8cbbe915576538a',
        },
    }

    MOCR_REPO_ID = 'kha-white/manga-ocr-base'
    MOCR_REQUIRED_FILES = (
        'config.json',
        'preprocessor_config.json',
        'pytorch_model.bin',
        'special_tokens_map.json',
        'tokenizer_config.json',
        'vocab.txt',
    )

    def __init__(self, *args, **kwargs):
        os.makedirs(self.model_dir, exist_ok=True)
        if os.path.exists('ocr_ar_48px.ckpt'):
            shutil.move('ocr_ar_48px.ckpt', self._get_file_path('ocr_ar_48px.ckpt'))
        if os.path.exists('alphabet-all-v7.txt'):
            shutil.move('alphabet-all-v7.txt', self._get_file_path('alphabet-all-v7.txt'))
        super().__init__(*args, **kwargs)

    def _check_mocr_downloaded(self) -> bool:
        return all(os.path.isfile(self._get_file_path(f)) for f in self.MOCR_REQUIRED_FILES)

    def _check_downloaded(self) -> bool:
        return super()._check_downloaded() and self._check_mocr_downloaded()

    async def _download(self):
        await super()._download()
        if not self._check_mocr_downloaded():
            print(f' -- Downloading Hugging Face model {self.MOCR_REPO_ID} to {self.model_dir}')
            try:
                from huggingface_hub import snapshot_download
                snapshot_download(
                    repo_id=self.MOCR_REPO_ID,
                    local_dir=self.model_dir,
                )
            except Exception as e:
                print(f'Failed to download {self.MOCR_REPO_ID} to {self.model_dir}: {e}')
                raise

    async def _load(self, device: str):
        with open(self._get_file_path('alphabet-all-v7.txt'), 'r', encoding = 'utf-8') as fp:
            dictionary = [s[:-1] for s in fp.readlines()]

        self.model = OCR(dictionary, 768)
        mocr_path = self.model_dir if os.path.exists(self._get_file_path('config.json')) else self.MOCR_REPO_ID
        self.mocr = MangaOcr(mocr_path)
        mocr_model = getattr(self.mocr, 'model', None)
        if mocr_model is not None and hasattr(mocr_model, 'to'):
            mocr_model.to(device)
        mocr_device = getattr(mocr_model, 'device', None)
        if mocr_device is None:
            try:
                mocr_device = next(mocr_model.parameters()).device
            except (AttributeError, StopIteration):
                mocr_device = 'unknown'
        self.logger.info(
            f'MangaOCR auxiliary model: requested_device={device} '
            f'runtime_device={mocr_device} backend=manga-ocr'
        )
        sd = torch.load(self._get_file_path('ocr_ar_48px.ckpt'))
        self.model.load_state_dict(sd)
        self.model.eval()
        self.device = device
        if (device.startswith('cuda') or device == 'mps' or device == 'xpu'):
            self.use_gpu = True
        else:
            self.use_gpu = False
        if self.use_gpu:
            self.model = self.model.to(device)


    async def _unload(self):
        del self.model
        del self.mocr
    
    async def _infer(self, image: np.ndarray, textlines: List[Quadrilateral], config: OcrConfig, verbose: bool = False, ignore_bubble: int = 0) -> List[TextBlock]:
        return (await self._infer_batch([(image, textlines, config)], verbose))[0]

    async def _prepare_mocr_regions(self, image, textlines, config, quadrilaterals):
        if config.use_mocr_merge:
            merged_textlines, merged_idx = await merge_bboxes(textlines, image.shape[1], image.shape[0])
            merged_quadrilaterals = list(self._generate_text_direction(merged_textlines))
        else:
            merged_idx = [[i] for i in range(len(quadrilaterals))]
            merged_quadrilaterals = quadrilaterals

        return merged_idx, merged_quadrilaterals

    async def _infer_batch(
        self,
        pages: List[Tuple[np.ndarray, List[Quadrilateral], OcrConfig]],
        verbose: bool = False,
    ) -> List[List[TextBlock]]:
        if not pages:
            return []

        text_height = 48
        prepared = []
        for image, textlines, config in pages:
            quadrilaterals = list(self._generate_text_direction(textlines))
            perm = list(range(len(quadrilaterals)))
            is_quadrilaterals = bool(quadrilaterals) and isinstance(quadrilaterals[0][0], Quadrilateral)
            if is_quadrilaterals:
                perm.sort(key=lambda index: (
                    quadrilaterals[index][0].aabb.w / max(1, quadrilaterals[index][0].aabb.h)
                    if quadrilaterals[index][1] == 'h'
                    else quadrilaterals[index][0].aabb.h / max(1, quadrilaterals[index][0].aabb.w)
                ))
            merged_idx, merged_quadrilaterals = await self._prepare_mocr_regions(
                image, textlines, config, quadrilaterals
            )
            prepared.append({
                'image': image,
                'textlines': textlines,
                'config': config,
                'quadrilaterals': quadrilaterals,
                'perm': perm,
                'is_quadrilaterals': is_quadrilaterals,
                'merged_idx': merged_idx,
                'merged_quadrilaterals': merged_quadrilaterals,
                'texts': {},
                'out_regions': {},
            })

        mocr_image_refs = [
            (page_index, rank)
            for rank in range(max((len(page['merged_quadrilaterals']) for page in prepared), default=0))
            for page_index, page in enumerate(prepared)
            if rank < len(page['merged_quadrilaterals'])
        ]
        if mocr_image_refs:
            self.logger.info(
                f'MangaOCR batch: pages={len(pages)} crops={len(mocr_image_refs)} device={self.device}'
            )
            for crop_refs in chunks(mocr_image_refs, max(32, len(pages))):
                mocr_images = []
                for page_index, crop_index in crop_refs:
                    page = prepared[page_index]
                    region, direction = page['merged_quadrilaterals'][crop_index]
                    crop_height = region.aabb.w if direction == 'h' else region.aabb.h
                    mocr_images.append(Image.fromarray(
                        region.get_transformed_region(page['image'], 'h', crop_height)
                    ))
                mocr_texts = infer_mocr_batch(self.mocr, mocr_images, batch_size=max(8, len(pages)))
                for (page_index, crop_index), text in zip(crop_refs, mocr_texts):
                    prepared[page_index]['texts'][crop_index] = text
                del mocr_images, mocr_texts

        region_order = [
            (page_index, page['perm'][rank])
            for rank in range(max((len(page['perm']) for page in prepared), default=0))
            for page_index, page in enumerate(prepared)
            if rank < len(page['perm'])
        ]
        debug_index = 0
        for crop_refs in chunks(region_order, max(32, len(pages))):
            crops = [
                prepared[page_index]['quadrilaterals'][region_index][0].get_transformed_region(
                    prepared[page_index]['image'],
                    prepared[page_index]['quadrilaterals'][region_index][1],
                    text_height,
                )
                for page_index, region_index in crop_refs
            ]
            widths = [crop.shape[1] for crop in crops]
            max_width = 4 * (max(widths) + 7) // 4
            region = np.zeros((len(crop_refs), text_height, max_width, 3), dtype=np.uint8)
            for row, (page_index, region_index) in enumerate(crop_refs):
                page = prepared[page_index]
                crop = crops[row]
                region[row, :, :crop.shape[1], :] = crop
                if verbose:
                    os.makedirs('result/ocrs/', exist_ok=True)
                    if page['quadrilaterals'][region_index][1] == 'v':
                        cv2.imwrite(f'result/ocrs/{debug_index}.png', cv2.rotate(cv2.cvtColor(region[row], cv2.COLOR_RGB2BGR), cv2.ROTATE_90_CLOCKWISE))
                    else:
                        cv2.imwrite(f'result/ocrs/{debug_index}.png', cv2.cvtColor(region[row], cv2.COLOR_RGB2BGR))
                debug_index += 1
            image_tensor = (torch.from_numpy(region).float() - 127.5) / 127.5
            image_tensor = einops.rearrange(image_tensor, 'N H W C -> N C H W')
            if self.use_gpu:
                image_tensor = image_tensor.to(self.device)
            with torch.inference_mode():
                results = self.model.infer_beam_batch(image_tensor, widths, beams_k=1, max_seq_length=32)
            for row, (pred_chars_index, prob, fg_pred, bg_pred, fg_ind_pred, bg_ind_pred) in enumerate(results):
                has_fg = (fg_ind_pred[:, 1] > fg_ind_pred[:, 0])
                has_bg = (bg_ind_pred[:, 1] > bg_ind_pred[:, 0])
                fr = AvgMeter()
                fg = AvgMeter()
                fb = AvgMeter()
                br = AvgMeter()
                bg = AvgMeter()
                bb = AvgMeter()
                for chid, c_fg, c_bg, h_fg, h_bg in zip(pred_chars_index, fg_pred, bg_pred, has_fg, has_bg) :
                    ch = self.model.dictionary[chid]
                    if ch == '<S>':
                        continue
                    if ch == '</S>':
                        break
                    if h_fg.item() :
                        fr(int(c_fg[0] * 255))
                        fg(int(c_fg[1] * 255))
                        fb(int(c_fg[2] * 255))
                    if h_bg.item() :
                        br(int(c_bg[0] * 255))
                        bg(int(c_bg[1] * 255))
                        bb(int(c_bg[2] * 255))
                    else :
                        br(int(c_fg[0] * 255))
                        bg(int(c_fg[1] * 255))
                        bb(int(c_fg[2] * 255))
                fr = min(max(int(fr()), 0), 255)
                fg = min(max(int(fg()), 0), 255)
                fb = min(max(int(fb()), 0), 255)
                br = min(max(int(br()), 0), 255)
                bg = min(max(int(bg()), 0), 255)
                bb = min(max(int(bb()), 0), 255)
                page_index, region_index = crop_refs[row]
                cur_region = prepared[page_index]['quadrilaterals'][region_index][0]
                if isinstance(cur_region, Quadrilateral):
                    cur_region.prob = prob
                    cur_region.fg_r = fr
                    cur_region.fg_g = fg
                    cur_region.fg_b = fb
                    cur_region.bg_r = br
                    cur_region.bg_g = bg
                    cur_region.bg_b = bb
                else:
                    cur_region.update_font_colors(np.array([fr, fg, fb]), np.array([br, bg, bb]))

                prepared[page_index]['out_regions'][region_index] = cur_region

            del crops, region, image_tensor, results

        return [self._finish_page(page) for page in prepared]

    def _finish_page(self, page) -> List[TextBlock]:
        config = page['config']
        threshold = 0.2 if config.prob is None else config.prob
        merged_idx = page['merged_idx']
        merged_quadrilaterals = page['merged_quadrilaterals']
        texts = page['texts']
        out_regions = page['out_regions']
        output_regions = []
        for i, nodes in enumerate(merged_idx):
            total_logprobs = 0
            total_area = 0
            fg_r = []
            fg_g = []
            fg_b = []
            bg_r = []
            bg_g = []
            bg_b = []
            
            for idx in nodes:
                if idx not in out_regions:
                    continue
                    
                region_out = out_regions[idx]
                total_logprobs += np.log(region_out.prob) * region_out.area
                total_area += region_out.area
                fg_r.append(region_out.fg_r)
                fg_g.append(region_out.fg_g)
                fg_b.append(region_out.fg_b)
                bg_r.append(region_out.bg_r)
                bg_g.append(region_out.bg_g)
                bg_b.append(region_out.bg_b)
                
            if total_area > 0:
                total_logprobs /= total_area
                prob = np.exp(total_logprobs)
            else:
                prob = 0.0
            if prob < threshold:
                continue
            fr = round(np.mean(fg_r)) if fg_r else 0
            fg = round(np.mean(fg_g)) if fg_g else 0
            fb = round(np.mean(fg_b)) if fg_b else 0
            br = round(np.mean(bg_r)) if bg_r else 0
            bg = round(np.mean(bg_g)) if bg_g else 0
            bb = round(np.mean(bg_b)) if bg_b else 0

            txt = texts.get(i, '')
            self.logger.info(f'prob: {prob} {txt} fg: ({fr}, {fg}, {fb}) bg: ({br}, {bg}, {bb})')
            cur_region = merged_quadrilaterals[i][0]
            if isinstance(cur_region, Quadrilateral):
                cur_region.text = txt
                cur_region.prob = prob
                cur_region.fg_r = fr
                cur_region.fg_g = fg
                cur_region.fg_b = fb
                cur_region.bg_r = br
                cur_region.bg_g = bg
                cur_region.bg_b = bb
            else: # TextBlock
                cur_region.text.append(txt)
                cur_region.update_font_colors(np.array([fr, fg, fb]), np.array([br, bg, bb]))
            output_regions.append(cur_region)

        if page['is_quadrilaterals']:
            return output_regions
        return page['textlines']
