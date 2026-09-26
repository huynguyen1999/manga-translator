"""Vertical text measurement and rasterization."""

import cv2
import freetype
import numpy as np
from typing import Optional, Tuple

from .text_render import (
    CJK_Compatibility_Forms_translate,
    add_color,
    compact_special_symbols,
    get_char_border,
    get_char_glyph,
    logger,
)
from ..utils import is_punctuation


def calc_vertical(font_size: int, text: str, max_height: int):
    line_text_list = []
    # line_width_list = []
    line_height_list = []

    line_str = ""
    line_height = 0
    line_width_left = 0
    line_width_right = 0
    for i, cdpt in enumerate(text):
        if line_height == 0 and cdpt == ' ':
            continue
        cdpt, rot_degree = CJK_Compatibility_Forms_translate(cdpt, 1)
        ckpt = get_char_glyph(cdpt, font_size, 1)
        bitmap = ckpt.bitmap
        # spaces, etc
        if bitmap.rows * bitmap.width == 0 or len(bitmap.buffer) != bitmap.rows * bitmap.width:
            char_offset_y = ckpt.metrics.vertBearingY >> 6
        else:
            char_offset_y = ckpt.metrics.vertAdvance >> 6
        char_width = bitmap.width
        char_bearing_x = ckpt.metrics.vertBearingX >> 6
        if line_height + char_offset_y > max_height:
            line_text_list.append(line_str)
            line_height_list.append(line_height)
            # line_width_list.append(line_width_left + line_width_right)
            line_str = ""
            line_height = 0
            line_width_left = 0
            line_width_right = 0
        line_height += char_offset_y
        line_str += cdpt
        line_width_left = max(line_width_left, abs(char_bearing_x))
        line_width_right = max(line_width_right, char_width - abs(char_bearing_x))
    # last char
    line_text_list.append(line_str)
    line_height_list.append(line_height)
    # line_width_list.append(line_width_left + line_width_right)

    # box_calc_x = sum(line_width_list) + (len(line_width_list) - 1) * spacing_x
    # box_calc_y = max(line_height_list)
    return line_text_list, line_height_list

def put_char_vertical(font_size: int, cdpt: str, pen_l: Tuple[int, int], canvas_text: np.ndarray, canvas_border: np.ndarray, border_size: int, stroke_width: int = None):
    """  
    在画布上垂直放置一个字符，并可选地添加描边效果。  
    Vertically place a character on the canvas with optional border effect.  
    
    Args:  
        font_size: 字体大小 / Font size  
        cdpt: 要渲染的字符 / Character to render  
        pen_l: 笔的位置（起始绘制位置） / Pen position (starting drawing position)  
        canvas_text: 用于绘制文本的NumPy数组 / NumPy array for drawing text  
        canvas_border: 用于绘制描边的NumPy数组 / NumPy array for drawing border  
        border_size: 描边大小 / Border size  
        
    Returns:  
        int: 垂直步进值 / Vertical advance value  
    """  
    # 复制笔位置，避免修改原始值  
    # Copy pen position to avoid modifying the original value  
    pen = pen_l.copy()  

    # 检查是否是标点符号  
    # Check if the character is a punctuation  
    is_pun = is_punctuation(cdpt)  
    
    # 处理CJK兼容形式转换，并获取旋转角度  
    # Process CJK compatibility forms translation and get rotation degree  
    cdpt, rot_degree = CJK_Compatibility_Forms_translate(cdpt, 1)  
    
    # 获取字符字形  
    # Get character glyph  
    slot = get_char_glyph(cdpt, font_size, 1)  
    bitmap = slot.bitmap  # 这是原始字符的 bitmap 对象 / This is the bitmap object of the original character  

    # --- 获取原始字符位图信息 / Get original character bitmap information ---  
    char_bitmap_rows = bitmap.rows  
    char_bitmap_width = bitmap.width  
    
    # 检查位图是否有效（如空格等字符可能没有有效位图）  
    # Check if the bitmap is valid (characters like spaces may not have valid bitmaps)  
    if char_bitmap_rows * char_bitmap_width == 0 or len(bitmap.buffer) != char_bitmap_rows * char_bitmap_width:  
        # 对于无效位图（如空格），计算垂直步进 char_offset_y  
        # For invalid bitmaps (like spaces), calculate vertical advance char_offset_y  

        # 优先使用 vertAdvance (这是最适合垂直布局的)  
        # Prefer to use vertAdvance (this is most suitable for vertical layout)  
        if hasattr(slot, 'metrics') and hasattr(slot.metrics, 'vertAdvance') and slot.metrics.vertAdvance:  
             char_offset_y = slot.metrics.vertAdvance >> 6  
        # 其次尝试 advance.y (理论上 vertAdvance 更可靠)  
        # Then try advance.y (theoretically vertAdvance is more reliable)  
        elif hasattr(slot, 'advance') and slot.advance.y:  
             char_offset_y = slot.advance.y >> 6  
        # 再次尝试 vertBearingY (作为最后的度量回退，虽然不是步进值)  
        # Then try vertBearingY (as a last metric fallback, although not an advance value)  
        elif hasattr(slot, 'metrics') and hasattr(slot.metrics, 'vertBearingY'):  
             char_offset_y = slot.metrics.vertBearingY >> 6  
        # 最后的手段：使用 font_size 作为估算值  
        # Last resort: use font_size as an estimated value  
        else:  
             char_offset_y = font_size  

        # 对于空白字符等，只返回垂直步进距离  
        # For whitespace characters, just return the vertical advance  
        return char_offset_y  

    # --- 对于有效位图，正常处理 / For valid bitmaps, process normally ---  
    # 这里的 char_offset_y 应该是最终的垂直步进  
    # Here char_offset_y should be the final vertical advance  
    char_offset_y = slot.metrics.vertAdvance >> 6  

    # 将位图缓冲区转换为NumPy数组  
    # Convert bitmap buffer to NumPy array  
    bitmap_char = np.array(bitmap.buffer, dtype=np.uint8).reshape((char_bitmap_rows, char_bitmap_width))  

    # --- 计算原始字符在画布上的放置位置 (左上角) ---  
    # --- Calculate the placement position of the original character on canvas (top-left corner) ---  
    # 注意：这里的 pen[0] 和 pen[1] 是放置 bitmap_char 的左上角参考点  
    # Note: pen[0] and pen[1] are the top-left reference points for placing bitmap_char  
    char_place_x = pen[0] + (slot.metrics.vertBearingX >> 6)  
    char_place_y = pen[1] + (slot.metrics.vertBearingY >> 6)   

    # 在 canvas_text 上放置原始字符  
    # Place the original character on canvas_text  
    # 确保索引不为负  
    # Ensure indices are not negative  
    paste_y_start = max(0, char_place_y)  
    paste_x_start = max(0, char_place_x)  
    paste_y_end = min(canvas_text.shape[0], char_place_y + char_bitmap_rows)  
    paste_x_end = min(canvas_text.shape[1], char_place_x + char_bitmap_width)  

    # 检查切片是否有效（宽度和高度都大于0）  
    # 字符完全在画布上方或下方时， paste_y_start 等于 paste_y_end ，切片 paste_y_start:paste_y_end 会产生高度为0的区域
    # 简单处理，有概率漏字
    # Check if the slice is valid (width and height both greater than 0)
    # When a character is completely above or below the canvas, paste_y_start equals paste_y_end, and the slice paste_y_start:paste_y_end will produce a region with height 0.
    # Simple handling, there's a probability of missing characters 
    if paste_y_start >= paste_y_end or paste_x_start >= paste_x_end:  
        logger.warning(f"Char '{cdpt}' is completely outside the canvas or on the boundary, skipped. Position: x={char_place_x}, y={char_place_y}, Canvas size: {canvas_text.shape}")      
    else: 
    # 确保切片源和目标尺寸匹配  
    # Ensure slice source and target dimensions match  
        bitmap_char_slice = bitmap_char[paste_y_start-char_place_y : paste_y_end-char_place_y,   
                                        paste_x_start-char_place_x : paste_x_end-char_place_x]  
        if bitmap_char_slice.size > 0:       
            canvas_text[paste_y_start:paste_y_end, paste_x_start:paste_x_end] = bitmap_char_slice        
            
    # --- 处理描边 / Process border ---  
    if border_size > 0:  
        # 获取字符描边  
        # Get character border  
        glyph_border = get_char_border(cdpt, font_size, 1)  
        stroker = freetype.Stroker()  
        
        # 设置描边半径和样式  
        # Set stroke radius and style  
        stroke_radius = 64 * (max(int(0.07 * font_size), 1) if stroke_width is None else max(int(stroke_width), 1))  # 基于字体大小的比例值 / Proportional value based on font size
        stroker.set(stroke_radius, freetype.FT_STROKER_LINEJOIN_ROUND, freetype.FT_STROKER_LINECAP_ROUND, 0)  
        
        # 应用描边效果  
        # Apply stroke effect  
        glyph_border.stroke(stroker, destroy=True)  
        
        # 渲染描边字形到位图  
        # Render stroked glyph to bitmap  
        blyph = glyph_border.to_bitmap(freetype.FT_RENDER_MODE_NORMAL, freetype.Vector(0, 0), True)  
        bitmap_b = blyph.bitmap  # 这是描边后的 bitmap 对象 / This is the bitmap object after stroking  

        # --- 获取描边位图信息 / Get border bitmap information ---  
        border_bitmap_rows = bitmap_b.rows  
        border_bitmap_width = bitmap_b.width  

        if border_bitmap_rows * border_bitmap_width > 0 and len(bitmap_b.buffer) == border_bitmap_rows * border_bitmap_width:  
            # 将描边位图缓冲区转换为NumPy数组  
            # Convert border bitmap buffer to NumPy array  
            bitmap_border = np.array(bitmap_b.buffer, dtype=np.uint8).reshape((border_bitmap_rows, border_bitmap_width))  

            # --- 计算描边位图放置位置，使其中心与原始字符位图中心对齐 ---  
            # --- Calculate border bitmap placement position to align its center with the original character bitmap center ---  
            
            # 原始字符位图中心偏移 (相对于其左上角)  
            # Original character bitmap center offset (relative to its top-left corner)  
            char_center_offset_x = char_bitmap_width / 2.0  
            char_center_offset_y = char_bitmap_rows / 2.0  
            
            # 描边位图中心偏移 (相对于其左上角)  
            # Border bitmap center offset (relative to its top-left corner)  
            border_center_offset_x = border_bitmap_width / 2.0  
            border_center_offset_y = border_bitmap_rows / 2.0  

            # 原始字符中心在画布上的坐标  
            # Coordinates of the original character center on canvas  
            char_center_on_canvas_x = char_place_x + char_center_offset_x  
            char_center_on_canvas_y = char_place_y + char_center_offset_y  

            # 计算描边位图的左上角放置位置 (pen_border)，使得其中心与字符中心重合  
            # Calculate the top-left placement position of border bitmap (pen_border) so that its center coincides with the character center  
            pen_border_x_float = char_center_on_canvas_x - border_center_offset_x  
            pen_border_y_float = char_center_on_canvas_y - border_center_offset_y  

            # 转换为整数坐标  
            # Convert to integer coordinates  
            pen_border_x = int(round(pen_border_x_float))  
            pen_border_y = int(round(pen_border_y_float))  

            # 最终的 pen_border，确保不小于 0  
            # Final pen_border, ensure not less than 0  
            pen_border = (max(0, pen_border_x), max(0, pen_border_y))  

            # --- 在 canvas_border 上放置描边位图 / Place border bitmap on canvas_border ---  
            # 确保索引不为负，且在画布范围内  
            # Ensure indices are not negative and within canvas range  
            paste_border_y_start = pen_border[1]  
            paste_border_x_start = pen_border[0]  
            paste_border_y_end = min(canvas_border.shape[0], pen_border[1] + border_bitmap_rows)  
            paste_border_x_end = min(canvas_border.shape[1], pen_border[0] + border_bitmap_width)  

            # 检查切片是否有效（宽度和高度都大于0）
            if paste_border_y_start >= paste_border_y_end or paste_border_x_start >= paste_border_x_end:  
                logger.warning(f"The border of char '{cdpt}' is completely outside the canvas or on the boundary, skipped. Position: x={pen_border[0]}, y={pen_border[1]}, Canvas size: {canvas_border.shape}")  
            else:        
                # 确保切片源和目标尺寸匹配  
                # Ensure slice source and target dimensions match  
                bitmap_border_slice = bitmap_border[0 : paste_border_y_end-paste_border_y_start,   
                                                    0 : paste_border_x_end-paste_border_x_start]  
                if bitmap_border_slice.size > 0:
                    # 使用 cv2.add 叠加描边  
                    # Use cv2.add to overlay border  
                    target_slice = canvas_border[paste_border_y_start:paste_border_y_end,   
                                                 paste_border_x_start:paste_border_x_end]  
                    # 确保形状匹配后再添加  
                    # Ensure shapes match before adding  
                    if target_slice.shape == bitmap_border_slice.shape:  
                        canvas_border[paste_border_y_start:paste_border_y_end,   
                                      paste_border_x_start:paste_border_x_end] = cv2.add(target_slice, bitmap_border_slice)  
                    else:  
                        # 处理形状不匹配的情况  
                        # Handle shape mismatch if necessary  
                        logger.warning(f"Shape mismatch: target={target_slice.shape}, source={bitmap_border_slice.shape}")  

    # 返回垂直步进值  
    # Return vertical advance value  
    return char_offset_y  

def put_text_vertical(font_size: int, text: str, h: int, alignment: str, fg: Tuple[int, int, int], bg: Optional[Tuple[int, int, int]], line_spacing: int, stroke_width: int = None):
    text = compact_special_symbols(text)
    if not text or font_size <= 0 or h < font_size:
        return
    bg_size = (max(int(stroke_width), 1) if stroke_width is not None else int(max(font_size * 0.07, 1))) if bg is not None else 0
    spacing_x = int(font_size * (line_spacing or 0.2))

    # make large canvas
    num_char_y = h // font_size
    num_char_x = len(text) // num_char_y + 1
    canvas_x = font_size * num_char_x + spacing_x * (num_char_x - 1) + (font_size + bg_size) * 2
    canvas_y = font_size * num_char_y + (font_size + bg_size) * 2
    line_text_list, line_height_list = calc_vertical(font_size, text, h)
    # print(line_text_list, line_height_list)

    canvas_text = np.zeros((canvas_y, canvas_x), dtype=np.uint8)
    canvas_border = canvas_text.copy()

    # pen (x, y)
    pen_orig = [canvas_text.shape[1] - (font_size + bg_size), font_size + bg_size]

    # write stuff
    for line_text, line_height in zip(line_text_list, line_height_list):
        pen_line = pen_orig.copy()
        if alignment == 'center':
            pen_line[1] += (max(line_height_list) - line_height) // 2
        elif alignment == 'right':
            pen_line[1] += max(line_height_list) - line_height

        for c in line_text:
            offset_y = put_char_vertical(font_size, c, pen_line, canvas_text, canvas_border, border_size=bg_size, stroke_width=stroke_width)
            pen_line[1] += offset_y
        pen_orig[0] -= spacing_x + font_size

    # colorize
    canvas_border = np.clip(canvas_border, 0, 255)
    line_box = add_color(canvas_text, fg, canvas_border, bg)
    # rect
    x, y, w, h = cv2.boundingRect(canvas_border)
    return line_box[y:y+h, x:x+w]
