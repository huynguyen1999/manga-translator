"""Textline merging and retention-policy pipeline stage."""

import time
import uuid

import numpy as np

from .config import Config
from .translators.common import ISO_639_1_TO_VALID_LANGUAGES
from .utils import (
    Context,
    NumericClassification,
    classify_numeric_ocr_region,
    contains_linguistic_ocr_text,
    rect_distance,
    sort_regions,
)


async def merge_textlines(
    owner,
    config: Config,
    ctx: Context,
    *,
    logger,
    langid,
    langcodes,
    dispatch_textline_merge,
    save_result_documents,
):
    current_time = time.time()
    owner._model_usage_timestamps[("textline_merge", "textline_merge")] = current_time

    # Filter out languages to skip before merging textlines
    if config.translator.skip_lang is not None:  
        skip_langs = [lang.strip().upper() for lang in config.translator.skip_lang.split(',')]  
        filtered_textlines = []  
        for txtln in ctx.textlines:  
            if not contains_linguistic_ocr_text(txtln.text):
                filtered_textlines.append(txtln)
                continue
            try:  
                detected_lang, confidence = langid.classify(txtln.text)
                source_language = ISO_639_1_TO_VALID_LANGUAGES.get(detected_lang, 'UNKNOWN')
                if source_language != 'UNKNOWN':
                    source_language = source_language.upper()
            except Exception:  
                source_language = 'UNKNOWN'  

            if source_language in skip_langs:  
                logger.info(f'Filtered out: {txtln.text}')  
                logger.info(f'Reason: Detected language {source_language} is in skip_langs')  
                continue  # Skip this region  
            filtered_textlines.append(txtln)  
        ctx.textlines = filtered_textlines  

    verbose = getattr(owner, "verbose", False)
    pair_diagnostics = [] if verbose else None
    text_regions = await dispatch_textline_merge(
        ctx.textlines,
        ctx.img_rgb.shape[1],
        ctx.img_rgb.shape[0],
        verbose=verbose,
        pair_diagnostics=pair_diagnostics,
    )
    if pair_diagnostics is not None:
        debug_document = {"pairs": pair_diagnostics}
        if getattr(ctx, "result_documents", None) is None:
            ctx.result_documents = {}
        ctx.result_documents["textline_merge_debug.json"] = debug_document
        pipeline_run = getattr(owner, "_pipeline_run", None)
        if pipeline_run is not None:
            pipeline_run.write_json("textline_merge_debug.json", debug_document)
        elif getattr(owner, "_current_image_context", None):
            await save_result_documents(
                owner._current_image_context["subfolder"],
                {"textline_merge_debug.json": debug_document},
                owner.result_root,
            )
    for region in text_regions:
        if not hasattr(region, "text_raw"):
            region.text_raw = region.text      # <- Save the initial OCR results to expand the render detection box. Also, prevent affecting the forbidden translation function.  
        if not getattr(region, "region_id", None):
            region.region_id = uuid.uuid4().hex

    new_text_regions = []
    for region in text_regions:
        # Remove leading spaces after pre-translation dictionary replacement                
        original_text = region.text  
        stripped_text = original_text.strip()  

        # Record removed leading characters  
        removed_start_chars = original_text[:len(original_text) - len(stripped_text)]  
        if removed_start_chars:  
            logger.info(f'Removed leading characters: "{removed_start_chars}" from "{original_text}"')  

        # Modified filtering condition: handle incomplete parentheses  
        bracket_pairs = {  
            '(': ')', '（': '）', '[': ']', '【': '】', '{': '}', '〔': '〕', '〈': '〉', '「': '」',  
            '"': '"', '＂': '＂', "'": "'", "“": "”", '《': '》', '『': '』', '"': '"', '〝': '〞', '﹁': '﹂', '﹃': '﹄',  
            '⸂': '⸃', '⸄': '⸅', '⸉': '⸊', '⸌': '⸍', '⸜': '⸝', '⸠': '⸡', '‹': '›', '«': '»', '＜': '＞', '<': '>'  
        }   
        left_symbols = set(bracket_pairs.keys())  
        right_symbols = set(bracket_pairs.values())  

        has_brackets = any(s in stripped_text for s in left_symbols) or any(s in stripped_text for s in right_symbols)  

        if has_brackets:  
            result_chars = []  
            stack = []  
            to_skip = []    

            # 第一次遍历：标记匹配的括号  
            # First traversal: mark matching brackets
            for i, char in enumerate(stripped_text):  
                if char in left_symbols:  
                    stack.append((i, char))  
                elif char in right_symbols:  
                    if stack:  
                        # 有对应的左括号，出栈  
                        # There is a corresponding left bracket, pop the stack
                        stack.pop()  
                    else:  
                        # 没有对应的左括号，标记为删除  
                        # No corresponding left parenthesis, marked for deletion
                        to_skip.append(i)  

            # 标记未匹配的左括号为删除
            # Mark unmatched left brackets as delete  
            for pos, _ in stack:  
                to_skip.append(pos)  

            has_removed_symbols = len(to_skip) > 0  

            # 第二次遍历：处理匹配但不对应的括号
            # Second pass: Process matching but mismatched brackets
            stack = []  
            for i, char in enumerate(stripped_text):  
                if i in to_skip:  
                    # 跳过孤立的括号
                    # Skip isolated parentheses
                    continue  

                if char in left_symbols:  
                    stack.append(char)  
                    result_chars.append(char)  
                elif char in right_symbols:  
                    if stack:  
                        left_bracket = stack.pop()  
                        expected_right = bracket_pairs.get(left_bracket)  

                        if char != expected_right:  
                            # 替换不匹配的右括号为对应左括号的正确右括号
                            # Replace mismatched right brackets with the correct right brackets corresponding to the left brackets
                            result_chars.append(expected_right)  
                            logger.info(f'Fixed mismatched bracket: replaced "{char}" with "{expected_right}"')  
                        else:  
                            result_chars.append(char)  
                else:  
                    result_chars.append(char)  

            new_stripped_text = ''.join(result_chars)  

            if has_removed_symbols:  
                logger.info(f'Removed unpaired bracket from "{stripped_text}"')  

            if new_stripped_text != stripped_text and not has_removed_symbols:  
                logger.info(f'Fixed brackets: "{stripped_text}" → "{new_stripped_text}"')  

            stripped_text = new_stripped_text  

        region.text = stripped_text.strip()     

    # Precompute bounds and linguistic properties for all candidate regions to enable spatial context heuristics
    def _get_region_bounds(reg):
        pts = []
        for line in getattr(reg, "lines", []):
            for pt in line:
                pts.append(pt)
        if pts:
            arr = np.asarray(pts)
            return float(arr[:, 0].min()), float(arr[:, 1].min()), float(arr[:, 0].max()), float(arr[:, 1].max())
        return 0.0, 0.0, 0.0, 0.0

    def _is_region_in_bubbles(reg, bubble_detections):
        if not bubble_detections:
            return False
        if getattr(reg, "_bubble_mask", None) is not None:
            return True
        rx1, ry1, rx2, ry2 = _get_region_bounds(reg)
        cx, cy = int((rx1 + rx2) / 2), int((ry1 + ry2) / 2)
        for bd in bubble_detections:
            mask = getattr(bd, "mask", None)
            if mask is not None and 0 <= cy < mask.shape[0] and 0 <= cx < mask.shape[1]:
                if mask[cy, cx] > 0:
                    return True
        return False

    region_bounds_list = [_get_region_bounds(r) for r in text_regions]
    has_linguistic_list = [contains_linguistic_ocr_text(r.text) for r in text_regions]
    bubble_dets = getattr(ctx, "bubble_detections", None) or []

    new_text_regions = []
    for idx, region in enumerate(text_regions):
        if not region.text:
            continue

        # Compute spatial proximity to neighboring linguistic text
        r_bounds = region_bounds_list[idx]
        r_fs = getattr(region, "font_size", 14) or 14
        max_dist = max(3.0 * r_fs, 80.0)
        is_near_text = False
        for other_idx, other_region in enumerate(text_regions):
            if other_idx != idx and has_linguistic_list[other_idx]:
                o_bounds = region_bounds_list[other_idx]
                d = rect_distance(
                    r_bounds[0], r_bounds[1], r_bounds[2], r_bounds[3],
                    o_bounds[0], o_bounds[1], o_bounds[2], o_bounds[3]
                )
                if d <= max_dist:
                    is_near_text = True
                    break

        is_in_bubble = _is_region_in_bubbles(region, bubble_dets)
        ocr_prob = float(getattr(region, "prob", 1.0) or 1.0)
        min_conf = float(getattr(config.ocr, "prob", 0.40) or 0.40)

        classification = classify_numeric_ocr_region(
            region.text,
            prob=ocr_prob,
            is_in_bubble=is_in_bubble,
            is_near_text=is_near_text,
            min_confidence=min_conf,
        )

        is_kept_numeric = classification in (
            NumericClassification.MEANINGFUL_NUMERIC,
            NumericClassification.POSSIBLE_NUMERIC,
        )
        has_linguistic_text = classification == NumericClassification.LINGUISTIC

        same_as_target_language = (
            has_linguistic_text
            and not config.translator.no_text_lang_skip
            and langcodes is not None
            and langcodes.tag_distance(region.source_lang, config.translator.target_lang) == 0
        )

        if is_kept_numeric:
            region.retention = "kept"
            region.retention_reason = "numeric_content"
            region.translation_policy = "preserve"
            region.translation = region.text
            if classification == NumericClassification.POSSIBLE_NUMERIC:
                region.review_required = True
                region.review_reasons = getattr(region, "review_reasons", []) or []
                if "possible_numeric_content" not in region.review_reasons:
                    region.review_reasons.append("possible_numeric_content")
        elif has_linguistic_text:
            region.retention = "kept"
            region.retention_reason = "linguistic_content"
            region.translation_policy = "translate"

        should_filter = False
        filter_reason = None
        if not is_kept_numeric and not has_linguistic_text:
            should_filter = True
            filter_reason = "Text contains no letters or meaningful numbers."
        elif has_linguistic_text and len(region.text) < config.ocr.min_text_length:
            should_filter = True
            filter_reason = "Text length is less than the minimum required length."
        elif has_linguistic_text and same_as_target_language:
            should_filter = True
            filter_reason = "Text language matches the target language and no_text_lang_skip is False."

        if should_filter:
            if region.text.strip():
                logger.info(f'Filtered out: {region.text}')
                logger.info(f'Reason: {filter_reason}')
        else:
            if config.render.font_color_fg or config.render.font_color_bg:
                if config.render.font_color_bg:
                    region.adjust_bg_color = False
            new_text_regions.append(region)
    text_regions = new_text_regions

    text_regions = sort_regions(
        text_regions,
        right_to_left=config.render.rtl,
        img=ctx.img_rgb,
        force_simple_sort=config.force_simple_sort
    )   

    return text_regions
