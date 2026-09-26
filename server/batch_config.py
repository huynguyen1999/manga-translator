"""Conversion from persisted batch settings to translation Config."""

import json

from manga_translator import Config
from manga_translator.config import Ocr
from manga_translator.rendering import resolve_font_name_or_path


def config_for(batch: dict, item: dict) -> Config:
        raw = item.get("config")
        if not isinstance(raw, dict):
            settings = item.get("settings", batch.get("settings", {}))
            ocr_prob_val = settings.get("customOcrProb") if settings.get("customOcrProb") not in (None, "") else settings.get("ocrMinConfidence")
            ocr_prob = None
            if ocr_prob_val not in (None, ""):
                try:
                    ocr_prob = float(ocr_prob_val)
                except (ValueError, TypeError):
                    ocr_prob = None
            letter_case = str(settings.get("letterCase") or settings.get("letter_case") or "").strip().lower()
            is_upper = bool(settings.get("uppercase", False)) or letter_case in ("upper", "uppercase", "all_caps", "caps")
            is_lower = (bool(settings.get("lowercase", False)) or letter_case in ("lower", "lowercase")) and not is_upper
            raw = {
                "detector": {
                    "detector": "none" if settings.get("colorizeOnly") else settings.get("textDetector", "default"),
                    "detection_size": settings.get("detectionResolution", "2048"),
                    "box_threshold": settings.get("customBoxThreshold", 0.5),
                    "unclip_ratio": settings.get("customUnclipRatio", 2.3),
                },
                "ocr": {
                    "ocr": settings.get("ocr", Ocr.ocr48px_ctc.value),
                    "prob": ocr_prob,
                    "min_text_length": int(settings.get("minTextLength", 1)),
                    "use_mocr_merge": bool(settings.get("useMocrMerge", False)),
                },
                "render": {
                    "direction": str(settings.get("renderTextDirection", "auto")).rsplit(".", 1)[-1],
                    "uppercase": is_upper,
                    "lowercase": is_lower,
                    "renderer": settings.get("renderer", "default"),
                    "alignment": str(settings.get("renderAlignment", "auto")).rsplit(".", 1)[-1],
                    "gimp_font": settings.get("renderFont", "wildwords"),
                    "font_path": resolve_font_name_or_path(settings.get("renderFont", "wildwords")),
                    "font_size": settings.get("customFontSize") if settings.get("customFontSize") not in (None, "") else None,
                    "font_size_offset": int(settings.get("fontSizeOffset", 0) or 0),
                    "font_size_minimum": int(settings.get("fontSizeMinimum", 0) if settings.get("fontSizeMinimum") not in (None, "") else 0),
                    "line_spacing": float(settings.get("lineSpacing")) if settings.get("lineSpacing") not in (None, "") else None,
                    "no_hyphenation": bool(settings.get("noHyphenation", False)),
                },
                "translator": {
                    "translator": "none" if settings.get("colorizeOnly") else settings.get("translator", "deepseek"),
                    "target_lang": settings.get("targetLanguage", "ENG"),
                    "translation_quality": settings.get("translationQuality", "fast"),
                    "translation_batch_size": settings.get("translationBatchSize", 20),
                    "story_page_ranges": settings.get("storyPageRanges") or None,
                    "story_plan": settings.get("storyPlan"),
                    "no_text_lang_skip": bool(settings.get("noTextLangSkip", True)),
                    "keep_failed_pages_for_editing": bool(settings.get("keepFailedPagesForEditing", True)),
                },
                "inpainter": {
                    "inpainter": "original" if settings.get("colorizeOnly") else settings.get("inpainter", "default"),
                    "inpainting_size": settings.get("inpaintingSize", "2048"),
                },
                "colorizer": {
                    "colorizer": "none" if item.get("excludeColor") else settings.get("colorizer", "none"),
                    "colorization_size": int(settings.get("colorizationSize", 576)),
                    "denoise_sigma": int(settings.get("denoiseSigma", 25)),
                    "color_threshold": float(settings.get("colorThreshold", 31)),
                    "restore_size": True,
                },
                "upscale": {
                    "upscaler": settings.get("upscaler", "esrgan"),
                    "upscale_ratio": settings.get("upscaleRatio"),
                    "revert_upscaling": bool(settings.get("upscaleRatio")) and bool(settings.get("revertUpscaling", True)),
                },
                "mask_dilation_offset": settings.get("maskDilationOffset", 20),
                "bubble_detection": {
                    "enabled": bool(settings.get("bubbleDetection", True)),
                    "model": settings.get("bubbleModel", "yolov8m"),
                    "confidence": float(settings.get("bubbleConfidence", 0.25)),
                    "mask_threshold": float(settings.get("bubbleMaskThreshold", 0.5)),
                    "padding": int(settings.get("bubblePadding", 9)),
                    "group_regions": bool(settings.get("bubbleGroupRegions", False)),
                },
            }
        config = Config.parse_raw(json.dumps(raw))
        config.original_name = item.get("name")
        config.manga_title = item.get("mangaTitle") or batch.get("title") or batch.get("mangaTitle") or "Ungrouped"
        config.manga_group_id = item.get("mangaGroupId") or batch.get("mangaGroupId")
        config.request_id = item.get("requestId")
        config.page_order = item.get("pageOrder")
        config.source_path = item.get("sourcePath")
        config._web_frontend_optimized = True
        return config
