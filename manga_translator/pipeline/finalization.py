"""Final image sizing and result artifact finalization."""

import asyncio
import os
import traceback

import cv2
import numpy as np

from manga_translator.rendering.bubble_layout import encode_rendered_box, encode_safe_shape


async def revert_upscale(owner, config, ctx, *, logger, save_jpeg, save_result_documents):
    should_downscale = bool(
        config.upscale.upscale_ratio
        and config.upscale.revert_upscaling
        and getattr(ctx, 'upscaled_ran', True)
        and ctx.result
        and ctx.result.size != ctx.input.size
    )
    if should_downscale:
        await owner._report_progress('downscaling')
        ctx.result = ctx.result.resize(ctx.input.size)

    # 保存verbose或pipeline run的final.jpg到调试文件夹
    final_saved = False
    if ctx.result and (owner.verbose or owner._pipeline_run is not None):
        try:
            final_img = np.array(ctx.result)
            final_path = owner._result_path('final.jpg')
            await asyncio.to_thread(save_jpeg, final_img, final_path)
            final_saved = True
        except Exception as e:
            logger.error(f"Error saving final.jpg debug image: {e}")
            logger.debug(f"Exception details: {traceback.format_exc()}")

    # Web流式模式优化：保存final.jpg并使用占位符
    if ctx.result and (
        (not owner.result_sub_folder and hasattr(owner, '_is_streaming_mode') and owner._is_streaming_mode)
        or owner._pipeline_run is not None
    ):
        # 保存final.jpg文件 (skip if already saved above)
        if not final_saved:
            try:
                final_path = owner._result_path('final.jpg')
                if hasattr(ctx.result, 'save'):
                    await asyncio.to_thread(save_jpeg, ctx.result, final_path)
                else:
                    await asyncio.to_thread(save_jpeg, np.array(ctx.result), final_path)
            except Exception as e:
                logger.error(f"Error saving final.jpg: {e}")

        # 保存inpainted.jpg与text_regions.json供交互式编辑
        try:
            scale_x = 1.0
            scale_y = 1.0
            if ctx.img_inpainted is not None:
                inpainted_img = ctx.img_inpainted
                target_w, target_h = ctx.result.size
                cur_h, cur_w = inpainted_img.shape[:2]
                if (cur_w, cur_h) != (target_w, target_h) and cur_w > 0 and cur_h > 0:
                    interp = cv2.INTER_AREA if (target_w < cur_w and target_h < cur_h) else cv2.INTER_LINEAR
                    inpainted_img = cv2.resize(inpainted_img, (target_w, target_h), interpolation=interp)
                    scale_x = target_w / cur_w
                    scale_y = target_h / cur_h
                await asyncio.to_thread(save_jpeg, inpainted_img, owner._result_path('inpainted.jpg'))
                try:
                    os.unlink(owner._result_path('inpainted.png'))
                except FileNotFoundError:
                    pass

            text_regions_data = []
            if ctx.text_regions:
                for i, blk in enumerate(ctx.text_regions):
                    try:
                        xywh = blk.xywh.tolist() if hasattr(blk.xywh, 'tolist') else list(blk.xywh)
                        fg = list(blk.fg_colors) if hasattr(blk.fg_colors, '__iter__') else [0, 0, 0]
                        bg = list(blk.bg_colors) if hasattr(blk.bg_colors, '__iter__') else [255, 255, 255]
                        fg = [int(c) for c in fg[:3]]
                        bg = [int(c) for c in bg[:3]]
                        lines = blk.lines.tolist() if hasattr(blk.lines, 'tolist') else []
                        lines = [
                            [[int(round(point[0] * scale_x)), int(round(point[1] * scale_y))] for point in line]
                            for line in lines
                        ]

                        bx = int(round(xywh[0] * scale_x))
                        by = int(round(xywh[1] * scale_y))
                        bw = max(20, int(round(xywh[2] * scale_x)))
                        bh = max(20, int(round(xywh[3] * scale_y)))
                        trans_text = blk.translation if hasattr(blk, 'translation') else ""
                        dir_val = getattr(blk, 'direction', 'h')
                        layout_bounds = getattr(blk, 'layout_bounds', None)
                        layout_segments = getattr(blk, 'layout_segments', None)

                        # If rendering horizontal text (e.g. English) from a vertical OCR textline,
                        # expand narrow boxes to the true speech bubble contour or proportions.
                        if dir_val != 'v' and layout_bounds and len(layout_bounds) == 4:
                            bx = int(round(layout_bounds[0] * scale_x))
                            by = int(round(layout_bounds[1] * scale_y))
                            bw = max(20, int(round((layout_bounds[2] - layout_bounds[0]) * scale_x)))
                            bh = max(20, int(round((layout_bounds[3] - layout_bounds[1]) * scale_y)))
                        elif dir_val != 'v' and bw < 140 and bh >= 50:
                            try:
                                from ..rendering.ballon_extractor import safe_ballon_bounds
                                if ctx.img_rgb is not None:
                                    enlarge_ratio = min(max(xywh[2] / max(1, xywh[3]), xywh[3] / max(1, xywh[2])) * 1.5, 3)
                                    detected_bounds = safe_ballon_bounds(ctx.img_rgb, xywh, enlarge_ratio=enlarge_ratio)
                                    if detected_bounds:
                                        layout_bounds = detected_bounds
                                        bx = int(round(detected_bounds[0] * scale_x))
                                        by = int(round(detected_bounds[1] * scale_y))
                                        bw = max(bw, int(round((detected_bounds[2] - detected_bounds[0]) * scale_x)))
                                        bh = max(bh, int(round((detected_bounds[3] - detected_bounds[1]) * scale_y)))
                            except Exception:
                                pass

                            if bw < 140 and bh >= 50:
                                desired_w = max(140, int(round(bh * 0.85)))
                                diff_w = desired_w - bw
                                bx = max(0, bx - diff_w // 2)
                                bw = desired_w

                        # Font size calculation:
                        # Instead of copying raw Japanese Kanji height, estimate a proportionate
                        # font size based on the translated string length and bubble area.
                        font_sz = blk.font_size if hasattr(blk, 'font_size') and blk.font_size > 0 else 24
                        scaled_font_size = max(10, int(round(font_sz * scale_y)))

                        if trans_text and dir_val != 'v' and not layout_segments:
                            words = [w for w in trans_text.split() if w]
                            if words:
                                longest_w = max(len(w) for w in words)
                                safe_w = max(20, int(bw * 0.82))
                                safe_h = max(20, int(bh * 0.84))
                                max_w_size = int(safe_w / (longest_w * 0.56))
                                est_size = min(scaled_font_size, max_w_size, 42)
                                while est_size > 11:
                                    char_w = est_size * 0.54
                                    line_h = est_size * 1.15
                                    c_per_line = max(1, int(safe_w / char_w))
                                    lines_count = 1
                                    cur_line_chars = 0
                                    for w in words:
                                        if cur_line_chars == 0:
                                            cur_line_chars = len(w)
                                        elif cur_line_chars + 1 + len(w) <= c_per_line:
                                            cur_line_chars += 1 + len(w)
                                        else:
                                            lines_count += 1
                                            cur_line_chars = len(w)
                                    if lines_count * line_h <= safe_h:
                                        break
                                    est_size -= 1
                                scaled_font_size = max(11, est_size)

                        text_regions_data.append({
                            "id": getattr(blk, 'group_id', f"bubble_{getattr(blk, '_bubble_source_order', i)}"),
                            "group_members": getattr(blk, 'group_members', []),
                            "review_required": bool(getattr(blk, 'review_required', False)),
                            "review_reason": getattr(blk, 'review_reason', None),
                            "x": bx,
                            "y": by,
                            "width": bw,
                            "height": bh,
                            "layout_bounds": {
                                "x": bx,
                                "y": by,
                                "width": bw,
                                "height": bh,
                            } if layout_bounds else None,
                            "layout_segments": [
                                {
                                    "x": int(round(segment["x"] * scale_x)),
                                    "y": int(round(segment["y"] * scale_y)),
                                    "width": max(1, int(round(segment["width"] * scale_x))),
                                    "height": max(1, int(round(segment["height"] * scale_y))),
                                    "text": segment["text"],
                                    "font_size": int(segment.get("font_size", blk.font_size)),
                                    "rendered_png": encode_rendered_box(
                                        next((box["box"] for box in (getattr(blk, "_bubble_segments", None) or [])
                                              if box["bounds"][0] == segment["x"] and box["bounds"][1] == segment["y"]), None),
                                        scale_x, scale_y,
                                    ),
                                    "positioned_lines": [
                                        {
                                            "text": line["text"],
                                            "x": int(round(line["x"] * scale_x)),
                                            "y": int(round(line["y"] * scale_y)),
                                        }
                                        for line in segment.get("lines", [])
                                    ],
                                }
                                for segment in (layout_segments or [])
                            ],
                            "bubble_safe_shape": encode_safe_shape(
                                getattr(blk, '_bubble_interior', None), scale_x, scale_y
                            ),
                            "lines": lines,
                            "original_text": blk.text if hasattr(blk, 'text') else "",
                            "translation": trans_text,
                            "font_size": scaled_font_size,
                            "font_family": getattr(blk, 'font_family', "") or "Comic Neue",
                            "fg_color": fg,
                            "bg_color": bg,
                            "stroke_width": float(getattr(blk, 'stroke_width', 2.0)),
                            "angle": float(getattr(blk, 'angle', 0)),
                            "direction": dir_val,
                            "alignment": getattr(blk, 'alignment', 'center'),
                            "line_spacing": float(config.render.line_spacing or 0) if layout_segments else float(getattr(blk, 'line_spacing', 1.0)),
                            "letter_spacing": float(getattr(blk, 'letter_spacing', 1.0)),
                            "bold": bool(getattr(blk, 'bold', False)),
                            "italic": bool(getattr(blk, 'italic', False)),
                            "target_lang": getattr(blk, 'target_lang', None) or str(getattr(config.translator, 'target_lang', 'ENG')),
                        })
                    except Exception as block_err:
                        logger.warning(f"Error serializing text region {i}: {block_err}")

        except Exception as e:
            logger.error(f"Error saving editor artifacts: {e}")
            raise

        # 保存meta.json记录元数据
        try:
            meta = owner._build_result_metadata(config, ctx)
        except Exception as e:
            logger.error(f"Error saving meta.json: {e}")
            raise

        folder_name = owner._current_image_context['subfolder']
        documents = {
            **(getattr(ctx, 'result_documents', None) or {}),
            'meta.json': meta,
            'text_regions.json': text_regions_data,
        }
        if owner._pipeline_run is not None:
            owner._pipeline_run.documents.update(documents)
            await owner._pipeline_run.checkpoint()
        else:
            await save_result_documents(folder_name, documents, owner.result_root)

        # 通知前端文件已就绪
        if hasattr(owner, '_progress_hooks') and owner._current_image_context:
            await owner._report_progress(f'final_ready:{folder_name}')

        await owner._report_progress('finished', True)
        if owner._pipeline_run is not None:
            owner._pipeline_run.release_runtime(preserve_output=True)
            owner._pipeline_run = None

        # 创建占位符结果并立即返回
        from PIL import Image
        placeholder = Image.new('RGB', (1, 1), color='white')
        ctx.result = placeholder
        ctx.use_placeholder = True
        return ctx

    await owner._report_progress('finished', True)
    if owner._pipeline_run is not None:
        owner._pipeline_run.release_runtime(preserve_output=True)
        owner._pipeline_run = None
    return ctx
