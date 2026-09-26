"""Horizontal glyph rasterization."""

from typing import Tuple

import cv2
import freetype
import numpy as np

from .text_render import CJK_Compatibility_Forms_translate, get_char_border, get_char_glyph


def put_char_horizontal(font_size: int, cdpt: str, pen_l: Tuple[int, int], canvas_text: np.ndarray, canvas_border: np.ndarray, border_size: int, stroke_width: int = None):
    """
    Render a single character (with optional stroke) onto horizontally oriented canvas.
    将单个字符（包括可能的描边）渲染到水平方向的画布上。

    Args:
        font_size: Font size in pixels. 字体大小（像素）
        cdpt: Character to render. 要渲染的字符
        pen_l: Current pen position (x, y), where x is horizontal origin and y is baseline. 
               画笔的当前位置 (x, y)，其中 x 是水平原点，y 是基线
        canvas_text: Grayscale canvas for character rendering (numpy array).
                    用于渲染字符本身的灰度画布 (numpy array)
        canvas_border: Grayscale canvas for stroke rendering (numpy array).
                      用于渲染字符描边的灰度画布 (numpy array)
        border_size: Target stroke size (used to calculate stroker radius, enabled when >0).
                    描边的目标大小（用于计算描边器半径，>0 时启用描边）

    Returns:
        The character's horizontal advance distance (int). 该字符的水平步进距离 (int)
    """
    pen = list(pen_l)  # Use mutable copy 使用可变副本

    # Get character and rotation angle (0° means horizontal)
    # 获取字符和旋转角度（方向0代表水平）
    cdpt, rot_degree = CJK_Compatibility_Forms_translate(cdpt, 0)
    
    # Get glyph information 获取字形信息
    slot = get_char_glyph(cdpt, font_size, 0)
    bitmap = slot.bitmap  # Original character bitmap 原始字符位图对象

    # --- Calculate horizontal advance (char_offset_x) ---
    # 优先使用 horiAdvance 获取水平布局的步进
    # Priority: Use horiAdvance for horizontal layout advance
    if hasattr(slot, 'metrics') and hasattr(slot.metrics, 'horiAdvance') and slot.metrics.horiAdvance:
        char_offset_x = slot.metrics.horiAdvance >> 6
    
    # Fallback: Use advance.x (usually same as horiAdvance)
    # 备选：使用 advance.x (通常与 horiAdvance 相同)
    elif hasattr(slot, 'advance') and slot.advance.x:
        char_offset_x = slot.advance.x >> 6
    
    # Further fallback: Estimate based on bitmap width if metrics missing (rare case)
    # 更进一步的备选：如果缺少度量信息，基于位图宽度和左跨距估算
    elif bitmap.width > 0 and hasattr(slot, 'bitmap_left'):
         char_offset_x = slot.bitmap_left + bitmap.width  # Rough estimation 非常粗略的估算
    
    # Final fallback: Guess based on font size
    # 最后备选：基于字体大小猜测
    else:
         char_offset_x = font_size // 2  # If no information available 如果完全没有信息

    # --- Check bitmap validity ---
    # 处理空格、无效字符等情况，在访问 buffer 前检查
    # Handle spaces/invalid chars before accessing buffer
    if bitmap.rows * bitmap.width == 0 or len(bitmap.buffer) != bitmap.rows * bitmap.width:
        return char_offset_x  # Return advance for empty/invalid bitmap 对于无效或空位图直接返回步进

    # --- For valid bitmap, proceed with rendering ---
    # 将位图缓冲区转换为 numpy 数组
    # Convert bitmap buffer to numpy array
    bitmap_char = np.array(bitmap.buffer, dtype=np.uint8).reshape((bitmap.rows, bitmap.width))

    # --- Calculate character placement ---
    # pen[0] is horizontal origin (cursor x)
    # pen[1] is vertical baseline (cursor y)
    # bitmap_left is horizontal distance from origin to left edge
    # bitmap_top is vertical distance from baseline to top edge (positive upwards)
    # pen[0] 是水平原点 (光标的 x 位置)
    # pen[1] 是垂直基线 (光标的 y 位置)
    # bitmap_left 是从原点到字形位图左边缘的水平距离
    # bitmap_top 是从基线到字形位图上边缘的垂直距离 (向上为正)
    char_place_x = pen[0] + slot.bitmap_left
    char_place_y = pen[1] - slot.bitmap_top

    # --- Paste character to canvas_text ---
    # Ensure paste area is within canvas and indices non-negative
    # 确保粘贴范围在画布内且索引非负
    paste_y_start = max(0, char_place_y)
    paste_x_start = max(0, char_place_x)
    paste_y_end = min(canvas_text.shape[0], char_place_y + bitmap.rows)
    paste_x_end = min(canvas_text.shape[1], char_place_x + bitmap.width)

    # Calculate source bitmap slicing area
    # 计算源位图需要切片的区域
    bitmap_slice_y_start = paste_y_start - char_place_y
    bitmap_slice_x_start = paste_x_start - char_place_x
    bitmap_slice_y_end = bitmap_slice_y_start + (paste_y_end - paste_y_start)
    bitmap_slice_x_end = bitmap_slice_x_start + (paste_x_end - paste_x_start)

    # Extract slice from source bitmap
    # 从源位图中提取切片
    bitmap_char_slice = bitmap_char[bitmap_slice_y_start:bitmap_slice_y_end, 
                                   bitmap_slice_x_start:bitmap_slice_x_end]

    # Paste if slice is valid and shapes match
    # 检查切片是否有效且形状匹配，然后粘贴
    if (bitmap_char_slice.size > 0 and 
        bitmap_char_slice.shape == (paste_y_end - paste_y_start, 
                                   paste_x_end - paste_x_start)):
        canvas_text[paste_y_start:paste_y_end, 
                    paste_x_start:paste_x_end] = bitmap_char_slice

    # --- Handle stroke rendering (if border_size > 0) ---
    # 处理描边渲染 (如果 border_size > 0)
    if border_size > 0:
        # Get glyph outline for stroke 获取用于描边的字形轮廓
        glyph_border = get_char_border(cdpt, font_size, 0)  # Same horizontal orientation 同样水平方向
        
        # Configure stroker 配置描边器
        stroker = freetype.Stroker()
        stroke_radius = 64 * (max(int(0.07 * font_size), 1) if stroke_width is None else max(int(stroke_width), 1))  # In 1/64 pixel units 单位: 1/64 像素
        stroker.set(stroke_radius, 
                   freetype.FT_STROKER_LINEJOIN_ROUND,  # Round joins 圆角连接
                   freetype.FT_STROKER_LINECAP_ROUND,   # Round line caps 圆头线帽
                   0)
        
        # Apply stroke 应用描边
        glyph_border.stroke(stroker, destroy=True)
        
        # Render stroked glyph to bitmap 将描边后的字形渲染到位图
        blyph = glyph_border.to_bitmap(freetype.FT_RENDER_MODE_NORMAL, 
                                      freetype.Vector(0, 0), True)
        bitmap_b = blyph.bitmap  # Stroked bitmap 描边后的位图

        # --- Process stroke bitmap ---
        border_bitmap_rows = bitmap_b.rows
        border_bitmap_width = bitmap_b.width

        # Only proceed if stroke bitmap is valid
        # 仅在描边位图有效时继续
        if (border_bitmap_rows * border_bitmap_width > 0 and 
            len(bitmap_b.buffer) == border_bitmap_rows * border_bitmap_width):
            
            # Convert stroke bitmap to numpy array
            # 将描边位图缓冲区转为 numpy 数组
            bitmap_border = np.array(bitmap_b.buffer, dtype=np.uint8
                                   ).reshape((border_bitmap_rows, border_bitmap_width))

            # --- Calculate stroke placement (center alignment logic) ---
            # 原始字符位图的尺寸
            char_bitmap_rows = bitmap.rows
            char_bitmap_width = bitmap.width

            # Original character center offsets
            # 原始字符位图中心相对于其左上角的偏移
            char_center_offset_x = char_bitmap_width / 2.0
            char_center_offset_y = char_bitmap_rows / 2.0

            # Stroke bitmap center offsets
            # 描边位图中心相对于其自身左上角的偏移
            border_center_offset_x = border_bitmap_width / 2.0
            border_center_offset_y = border_bitmap_rows / 2.0

            # Calculate absolute center coordinates on canvas
            # 计算原始字符中心在画布上的绝对坐标
            char_center_on_canvas_x = char_place_x + char_center_offset_x
            char_center_on_canvas_y = char_place_y + char_center_offset_y

            # Calculate stroke placement position (pen_border_x/y)
            # So its center aligns with character center
            # 计算描边位图的左上角放置位置 (pen_border_x, pen_border_y)
            # 使得其中心与字符中心对齐
            pen_border_x_float = char_center_on_canvas_x - border_center_offset_x
            pen_border_y_float = char_center_on_canvas_y - border_center_offset_y

            # Convert to integer coordinates
            # 转换为整数坐标进行放置
            pen_border_x = int(round(pen_border_x_float))
            pen_border_y = int(round(pen_border_y_float))

            # --- Paste stroke to canvas_border ---
            # Ensure paste area is within canvas
            # 确保粘贴范围在画布内且索引非负
            paste_border_y_start = max(0, pen_border_y)
            paste_border_x_start = max(0, pen_border_x)
            paste_border_y_end = min(canvas_border.shape[0], pen_border_y + border_bitmap_rows)
            paste_border_x_end = min(canvas_border.shape[1], pen_border_x + border_bitmap_width)

            # Calculate source stroke bitmap slicing area
            # 计算源描边位图需要切片的区域
            border_slice_y_start = paste_border_y_start - pen_border_y
            border_slice_x_start = paste_border_x_start - pen_border_x
            border_slice_y_end = border_slice_y_start + (paste_border_y_end - paste_border_y_start)
            border_slice_x_end = border_slice_x_start + (paste_border_x_end - paste_border_x_start)

            # Extract slice from stroke bitmap
            # 从源描边位图中提取切片
            bitmap_border_slice = bitmap_border[
                border_slice_y_start:border_slice_y_end,
                border_slice_x_start:border_slice_x_end
            ]

            # Check slice validity before pasting
            # 检查切片是否有效且形状匹配
            if (bitmap_border_slice.size > 0 and 
                bitmap_border_slice.shape == (paste_border_y_end - paste_border_y_start,
                                            paste_border_x_end - paste_border_x_start)):
                
                # Get target canvas area
                # 获取目标画布上的对应区域
                target_slice = canvas_border[
                    paste_border_y_start:paste_border_y_end,
                    paste_border_x_start:paste_border_x_end
                ]
                
                # Ensure shape consistency
                # 确保形状一致（切片逻辑正确的话应该是一致的）
                if target_slice.shape == bitmap_border_slice.shape:
                    # Blend stroke using cv2.add or np.maximum
                    # 使用 cv2.add 可以平滑合并重叠的描边部分
                    canvas_border[paste_border_y_start:paste_border_y_end,
                                paste_border_x_start:paste_border_x_end] = cv2.add(
                        target_slice, bitmap_border_slice)
                else:
                    print(f"[Error] Shape mismatch during border paste: "
                         f"target={target_slice.shape}, source={bitmap_border_slice.shape}")

    return char_offset_x  # Return horizontal advance 返回水平步进距离

