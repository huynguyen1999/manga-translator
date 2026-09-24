from contextlib import nullcontext
import os
import shutil
import threading
import numpy as np
import torch
import cv2
try:
    import einops
except ImportError:
    einops = None
from functools import partial
from typing import List, Tuple

from .default_utils.DBNet_resnet34 import TextDetection as TextDetectionDefault
from .default_utils import imgproc, dbnet_utils, craft_utils
from .common import OfflineDetector, dbnet_detect_batch
from ..utils import TextBlock, Quadrilateral, det_rearrange_forward

_GLOBAL_MODEL = None
MODEL = threading.local()

def det_batch_forward_default(batch: np.ndarray, device: str, model=None):
    if isinstance(batch, list):
        batch = np.array(batch)
    if not batch.flags['C_CONTIGUOUS']:
        batch = np.ascontiguousarray(batch)
    tensor = torch.from_numpy(batch).to(device)
    if einops is not None:
        tensor = einops.rearrange(tensor.float().div_(127.5).sub_(1.0), 'n h w c -> n c h w')
    else:
        tensor = tensor.float().div_(127.5).sub_(1.0).permute(0, 3, 1, 2)

    target_model = model
    if target_model is None:
        if callable(MODEL):
            target_model = MODEL
        else:
            target_model = getattr(MODEL, 'value', None) or _GLOBAL_MODEL

    if target_model is None:
        raise RuntimeError("Text detection model is not loaded.")

    amp = torch.autocast(device_type='mps', dtype=torch.float16) if device == 'mps' else nullcontext()
    with torch.inference_mode(), amp:
        db, mask = target_model(tensor)
        db = db.cpu().float().sigmoid().numpy()
        mask = mask.cpu().float().numpy()
    return db, mask

class DefaultDetector(OfflineDetector):
    _MODEL_MAPPING = {
        'model': {
            'url': 'https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/detect-20241225.ckpt',
            'hash': '67ce1c4ed4793860f038c71189ba9630a7756f7683b1ee5afb69ca0687dc502e',
            'file': '.',
        }
    }

    def __init__(self, *args, **kwargs):
        os.makedirs(self.model_dir, exist_ok=True)
        if os.path.exists('detect-20241225.ckpt'):
            shutil.move('detect-20241225.ckpt', self._get_file_path('detect-20241225.ckpt'))
        super().__init__(*args, **kwargs)

    async def _load(self, device: str):
        self.model = TextDetectionDefault()
        sd = torch.load(self._get_file_path('detect-20241225.ckpt'), map_location='cpu')
        self.model.load_state_dict(sd['model'] if 'model' in sd else sd)
        self.model.eval()
        self.device = device
        if device.startswith('cuda') or device == 'mps' or device == 'xpu':
            self.model = self.model.to(self.device)
        global _GLOBAL_MODEL
        _GLOBAL_MODEL = self.model
        MODEL.value = self.model

    async def _unload(self):
        del self.model

    async def _detect_batch(self, images, detect_size, text_threshold, box_threshold, unclip_ratio, verbose=False):
        output = [None] * len(images)
        eligible = []
        for index, image in enumerate(images):
            height, width = image.shape[:2]
            long_side, short_side = max(height, width), min(height, width)
            if long_side / detect_size > 2.5 and long_side / short_side > 3:
                output[index] = await self._detect(
                    image, detect_size, text_threshold, box_threshold, unclip_ratio, verbose
                )
            else:
                eligible.append((index, image))
        if eligible:
            forward = partial(det_batch_forward_default, model=self.model)
            results = dbnet_detect_batch(
                [image for _, image in eligible], forward, self.device, detect_size,
                text_threshold, box_threshold, unclip_ratio,
                interpolation=cv2.INTER_LINEAR,
                blur_before_resize=False,
            )
            for (index, _), result in zip(eligible, results):
                output[index] = result
        return output

    async def _infer(self, image: np.ndarray, detect_size: int, text_threshold: float, box_threshold: float,
                     unclip_ratio: float, verbose: bool = False):

        forward_fn = partial(det_batch_forward_default, model=self.model)
        db, mask = det_rearrange_forward(image, forward_fn, detect_size, 4, device=self.device, verbose=verbose)

        if db is None:
            # rearrangement is not required, fallback to default forward
            img_resized, target_ratio, _, pad_w, pad_h = imgproc.resize_aspect_ratio(image, detect_size, cv2.INTER_LINEAR, mag_ratio = 1)
            img_resized = cv2.bilateralFilter(img_resized, 9, 80, 80)
            img_resized_h, img_resized_w = img_resized.shape[:2]
            ratio_h = ratio_w = 1 / target_ratio
            db, mask = det_batch_forward_default([img_resized], self.device, model=self.model)
        else:
            img_resized_h, img_resized_w = image.shape[:2]
            ratio_w = ratio_h = 1
            pad_h = pad_w = 0
        self.logger.info(f'Detection resolution: {img_resized_w}x{img_resized_h}')

        mask = mask[0, 0, :, :]
        det = dbnet_utils.SegDetectorRepresenter(text_threshold, box_threshold, unclip_ratio=unclip_ratio)
        # boxes, scores = det({'shape': [(img_resized.shape[0], img_resized.shape[1])]}, db)
        boxes, scores = det({'shape':[(img_resized_h, img_resized_w)]}, db)
        boxes, scores = boxes[0], scores[0]
        if boxes.size == 0:
            polys = []
        else:
            idx = boxes.reshape(boxes.shape[0], -1).sum(axis=1) > 0
            polys, _ = boxes[idx], scores[idx]
            polys = polys.astype(np.float64)
            polys = craft_utils.adjustResultCoordinates(polys, ratio_w, ratio_h, ratio_net=1)
            polys = polys.astype(np.int64)

        textlines = [Quadrilateral(pts.astype(int), '', score) for pts, score in zip(polys, scores)]
        textlines = list(filter(lambda q: q.area > 16, textlines))
        mask_resized = cv2.resize(mask, (mask.shape[1] * 2, mask.shape[0] * 2), interpolation=cv2.INTER_LINEAR)
        if pad_h > 0:
            mask_resized = mask_resized[:-pad_h, :]
        elif pad_w > 0:
            mask_resized = mask_resized[:, :-pad_w]
        raw_mask = np.clip(mask_resized * 255, 0, 255).astype(np.uint8)

        # if verbose:
        #     img_bbox_raw = np.copy(image)
        #     for txtln in textlines:
        #         cv2.polylines(img_bbox_raw, [txtln.pts], True, color=(255, 0, 0), thickness=2)
        #     cv2.imwrite(f'result/bboxes_unfiltered.png', cv2.cvtColor(img_bbox_raw, cv2.COLOR_RGB2BGR))

        return textlines, raw_mask, None
