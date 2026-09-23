from abc import abstractmethod
from typing import List, Tuple
from collections import Counter
import numpy as np
import cv2

from ..utils import InfererModule, ModelWrapper, Quadrilateral


class CommonDetector(InfererModule):

    async def _detect_batch(self, images, detect_size, text_threshold, box_threshold, unclip_ratio, verbose=False):
        return [
            await self._detect(image, detect_size, text_threshold, box_threshold, unclip_ratio, verbose)
            for image in images
        ]

    async def detect_batch(
        self,
        images: list[np.ndarray],
        detect_size: int,
        text_threshold: float,
        box_threshold: float,
        unclip_ratio: float,
        invert: bool,
        gamma_correct: bool,
        rotate: bool,
        auto_rotate: bool = False,
        verbose: bool = False,
    ):
        if not images:
            return []
        processed = []
        metadata = []
        for image in images:
            img_h, img_w = image.shape[:2]
            orig_image = image.copy()
            if rotate:
                image = self._add_rotation(image)
            add_border = min(img_w, img_h) < 400
            if add_border:
                image = self._add_border(image, 400)
            if invert:
                image = self._add_inversion(image)
            if gamma_correct:
                image = self._add_gamma_correction(image)
            processed.append(image)
            metadata.append((orig_image, img_w, img_h, add_border))

        results = await self._detect_batch(
            processed, detect_size, text_threshold, box_threshold, unclip_ratio, verbose
        )
        if len(results) != len(images):
            raise RuntimeError(f"Detector returned {len(results)} pages for {len(images)} inputs")
        output = []
        for image, (textlines, raw_mask, mask), (orig_image, img_w, img_h, add_border) in zip(
            processed, results, metadata
        ):
            textlines = [line for line in textlines if line.area > 1]
            if add_border:
                textlines, raw_mask, mask = self._remove_border(image, img_w, img_h, textlines, raw_mask, mask)
            if auto_rotate:
                orientation = (
                    Counter("h" if line.aspect_ratio > 1 else "v" for line in textlines).most_common(1)[0][0]
                    if textlines else "h"
                )
                if orientation == "h":
                    output.append(await self.detect(
                        orig_image, detect_size, text_threshold, box_threshold, unclip_ratio,
                        invert, gamma_correct, not rotate, auto_rotate=False, verbose=verbose,
                    ))
                    continue
            if rotate:
                textlines, raw_mask, mask = self._remove_rotation(textlines, raw_mask, mask, img_w, img_h)
            output.append((textlines, raw_mask, mask))
        return output

    async def detect(self, image: np.ndarray, detect_size: int, text_threshold: float, box_threshold: float, unclip_ratio: float,
                     invert: bool, gamma_correct: bool, rotate: bool, auto_rotate: bool = False, verbose: bool = False):
        '''
        Returns textblock list and text mask.
        '''

        # Apply filters
        img_h, img_w = image.shape[:2]
        orig_image = image.copy()
        minimum_image_size = 400
        # Automatically add border if image too small (instead of simply resizing due to them more likely containing large fonts)
        add_border = min(img_w, img_h) < minimum_image_size
        if rotate:
            self.logger.debug('Adding rotation')
            image = self._add_rotation(image)
        if add_border:
            self.logger.debug('Adding border')
            image = self._add_border(image, minimum_image_size)
        if invert:
            self.logger.debug('Adding inversion')
            image = self._add_inversion(image)
        if gamma_correct:
            self.logger.debug('Adding gamma correction')
            image = self._add_gamma_correction(image)
        # if True:
        #     self.logger.debug('Adding histogram equalization')
        #     image = self._add_histogram_equalization(image)

        # cv2.imwrite('histogram.png', image)
        # cv2.waitKey(0)

        # Run detection
        textlines, raw_mask, mask = await self._detect(image, detect_size, text_threshold, box_threshold, unclip_ratio, verbose)
        textlines = list(filter(lambda x: x.area > 1, textlines))

        # Remove filters
        if add_border:
            textlines, raw_mask, mask = self._remove_border(image, img_w, img_h, textlines, raw_mask, mask)
        if auto_rotate:
            # Rotate if horizontal aspect ratios are prevalent to potentially improve detection
            if len(textlines) > 0:
                orientations = ['h' if txtln.aspect_ratio > 1 else 'v' for txtln in textlines]
                majority_orientation = Counter(orientations).most_common(1)[0][0]
            else:
                majority_orientation = 'h'
            if majority_orientation == 'h':
                self.logger.info('Rerunning detection with 90° rotation')
                return await self.detect(orig_image, detect_size, text_threshold, box_threshold, unclip_ratio, invert, gamma_correct,
                                         rotate=(not rotate), auto_rotate=False, verbose=verbose)
        if rotate:
            textlines, raw_mask, mask = self._remove_rotation(textlines, raw_mask, mask, img_w, img_h)

        return textlines, raw_mask, mask


    @abstractmethod
    async def _detect(self, image: np.ndarray, detect_size: int, text_threshold: float, box_threshold: float,
                      unclip_ratio: float, verbose: bool = False) -> Tuple[List[Quadrilateral], np.ndarray, np.ndarray]:
        pass

    def _add_border(self, image: np.ndarray, target_side_length: int):
        old_h, old_w = image.shape[:2]
        new_w = new_h = max(old_w, old_h, target_side_length)
        new_image = np.zeros([new_h, new_w, 3]).astype(np.uint8)
        # new_image[:] = np.array([255, 255, 255], np.uint8)
        x, y = 0, 0
        # x, y = (new_h - old_h) // 2, (new_w - old_w) // 2
        new_image[y:y+old_h, x:x+old_w] = image
        return new_image

    def _remove_border(self, image: np.ndarray, old_w: int, old_h: int, textlines: List[Quadrilateral], raw_mask, mask):
        new_h, new_w = image.shape[:2]
        raw_mask = cv2.resize(raw_mask, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        raw_mask = raw_mask[:old_h, :old_w]
        if mask is not None:
            mask = cv2.resize(mask, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            mask = mask[:old_h, :old_w]

        # Filter out regions within the border and clamp the points of the remaining regions
        new_textlines = []
        for txtln in textlines:
            if txtln.xyxy[0] >= old_w and txtln.xyxy[1] >= old_h:
                continue
            points = txtln.pts
            points[:,0] = np.clip(points[:,0], 0, old_w)
            points[:,1] = np.clip(points[:,1], 0, old_h)
            new_txtln = Quadrilateral(points, txtln.text, txtln.prob)
            new_textlines.append(new_txtln)
        return new_textlines, raw_mask, mask

    def _add_rotation(self, image: np.ndarray):
        return np.rot90(image, k=-1)

    def _remove_rotation(self, textlines, raw_mask, mask, img_w, img_h):
        raw_mask = np.ascontiguousarray(np.rot90(raw_mask))
        if mask is not None:
            mask = np.ascontiguousarray(np.rot90(mask).astype(np.uint8))

        for i, txtln in enumerate(textlines):
            rotated_pts = txtln.pts[:,[1,0]]
            rotated_pts[:,1] = -rotated_pts[:,1] + img_h
            textlines[i] = Quadrilateral(rotated_pts, txtln.text, txtln.prob)
        return textlines, raw_mask, mask

    def _add_inversion(self, image: np.ndarray):
        return cv2.bitwise_not(image)

    def _add_gamma_correction(self, image: np.ndarray):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        mid = 0.5
        mean = np.mean(gray)
        gamma = np.log(mid * 255) / np.log(mean)
        img_gamma = np.power(image, gamma).clip(0,255).astype(np.uint8)
        return img_gamma

    def _add_histogram_equalization(self, image: np.ndarray):
        img_yuv = cv2.cvtColor(image, cv2.COLOR_BGR2YUV)

        # equalize the histogram of the Y channel
        img_yuv[:,:,0] = cv2.equalizeHist(img_yuv[:,:,0])

        # convert the YUV image back to RGB format
        img_output = cv2.cvtColor(img_yuv, cv2.COLOR_YUV2BGR)
        return img_output


class OfflineDetector(CommonDetector, ModelWrapper):
    _MODEL_SUB_DIR = 'detection'

    async def _detect(self, *args, **kwargs):
        return await self.infer(*args, **kwargs)

    @abstractmethod
    async def _infer(self, image: np.ndarray, detect_size: int, text_threshold: float, box_threshold: float,
                       unclip_ratio: float, verbose: bool = False):
        pass


def dbnet_detect_batch(
    images: list[np.ndarray],
    forward,
    device: str,
    detect_size: int,
    text_threshold: float,
    box_threshold: float,
    unclip_ratio: float,
    *,
    interpolation: int,
    blur_before_resize: bool,
) -> list[tuple[list[Quadrilateral], np.ndarray, None]]:
    """Run compatible DBNet pages in one model call and map each result back."""
    if not images:
        return []
    from .default_utils import craft_utils, dbnet_utils, imgproc

    prepared = []
    for image in images:
        if blur_before_resize:
            image = cv2.bilateralFilter(image, 17, 80, 80)
        resized, ratio, _, pad_w, pad_h = imgproc.resize_aspect_ratio(
            image, detect_size, interpolation, mag_ratio=1
        )
        if not blur_before_resize:
            resized = cv2.bilateralFilter(resized, 9, 80, 80)
        prepared.append((resized, ratio, pad_w, pad_h))

    max_height = max(image.shape[0] for image, _, _, _ in prepared)
    max_width = max(image.shape[1] for image, _, _, _ in prepared)
    batch = np.zeros((len(prepared), max_height, max_width, 3), dtype=np.uint8)
    for index, (image, _, _, _) in enumerate(prepared):
        batch[index, :image.shape[0], :image.shape[1]] = image
    db, masks = forward(batch, device)

    output = []
    representer = dbnet_utils.SegDetectorRepresenter(
        text_threshold, box_threshold, unclip_ratio=unclip_ratio
    )
    for index, (image, ratio, pad_w, pad_h) in enumerate(prepared):
        height, width = image.shape[:2]
        out_height = round(height * db.shape[-2] / max_height)
        out_width = round(width * db.shape[-1] / max_width)
        db_page = db[index:index + 1, :, :out_height, :out_width]
        boxes, scores = representer({"shape": [(height, width)]}, db_page)
        boxes, scores = boxes[0], scores[0]
        if boxes.size:
            valid = boxes.reshape(boxes.shape[0], -1).sum(axis=1) > 0
            boxes, scores = boxes[valid], scores[valid]
            boxes = craft_utils.adjustResultCoordinates(
                boxes.astype(np.float64), 1 / ratio, 1 / ratio, ratio_net=1
            ).astype(np.int64)
        textlines = [
            Quadrilateral(points.astype(int), "", score)
            for points, score in zip(boxes, scores)
        ]
        mask_height = round(height * masks.shape[-2] / max_height)
        mask_width = round(width * masks.shape[-1] / max_width)
        mask = masks[index, 0, :mask_height, :mask_width]
        raw_mask = cv2.resize(
            mask, (mask_width * 2, mask_height * 2), interpolation=cv2.INTER_LINEAR
        )
        if pad_h:
            raw_mask = raw_mask[:-pad_h, :]
        elif pad_w:
            raw_mask = raw_mask[:, :-pad_w]
        raw_mask = np.clip(raw_mask * 255, 0, 255).astype(np.uint8)
        output.append((textlines, raw_mask, None))
    return output
