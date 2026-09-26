import os
import re
import cv2
import numpy as np
import freetype
import functools
import logging
from threading import local
from pathlib import Path
from typing import Tuple, Optional, List
from ..utils import BASE_PATH, is_punctuation
from .hyphenation import HYPHENATOR_LANGUAGES, select_hyphenator
from .font_coverage import missing_glyphs

CJK_H2V = {
    "‥": "︰",
    "—": "︱",
    "―": "|",
    "–": "︲",
    "_": "︳",
    "_": "︴",
    "(": "︵",
    ")": "︶",
    "（": "︵",
    "）": "︶",
    "{": "︷",
    "}": "︸",
    "〔": "︹",
    "〕": "︺",
    "【": "︻",
    "】": "︼",
    "《": "︽",
    "》": "︾",
    "〈": "︿",
    "〉": "﹀",
    "⟨": "︿",   
    "⟩": "﹀",   
    "⟪": "︿",   
    "⟫": "﹀",       
    "「": "﹁",
    "」": "﹂",
    "『": "﹃",
    "』": "﹄",
    "﹑": "﹅",
    "﹆": "﹆",
    "[": "﹇",
    "]": "﹈",
    "⦅": "︵",   
    "⦆": "︶",   
    "❨": "︵",          
    "❩": "︶",   
    "❪": "︷",   
    "❫": "︸",   
    "❬": "﹇",   
    "❭": "﹈",   
    "❮": "︿",   
    "❯": "﹀",    
    "﹉": "﹉",
    "﹊": "﹊",
    "﹋": "﹋",
    "﹌": "﹌",
    "﹍": "﹍",
    "﹎": "﹎",
    "﹏": "﹏",
    "…": "⋮",
    "⋯": "︙", 
    "⋰": "⋮",    
    "⋱": "⋮",           
    """: "﹁",   
    """: "﹂",   
    "'": "﹁",   
    "'": "﹂",   
    "″": "﹂",   
    "‴": "﹂",   
    "‶": "﹁",   
    "‷": "﹁",   
    "~": "︴",   
    "〜": "︴",   
    "～": "︴",   
    "~": "≀",
    "〰": "︴",
    "!": "︕",    
    "?": "︖",    
    "؟": "︖",    
    "¿": "︖",    
    "¡": "︕",    
    ".": "︒",    
    "。": "︒",   
    ";": "︔",    
    "；": "︔",   
    ":": "︓",    
    "：": "︓",  
    ",": "︐",    
    "，": "︐",   
    # "､": "︐",    
    "‚": "︐",    
    "„": "︐",    
    #"、": "︑",    
    "-": "︲",    
    "−": "︲",
    "・": "·",          
}

CJK_V2H = {
    **dict(zip(CJK_H2V.items(), CJK_H2V.keys())),
}

logger = logging.getLogger(__name__)  
logger.addHandler(logging.NullHandler())  

def CJK_Compatibility_Forms_translate(cdpt: str, direction: int):
    """direction: 0 - horizontal, 1 - vertical"""
    if cdpt == 'ー' and direction == 1:
        return 'ー', 90
    if cdpt in CJK_V2H:
        if direction == 0:
            # translate
            return CJK_V2H[cdpt], 0
        else:
            return cdpt, 0
    elif cdpt in CJK_H2V:
        if direction == 1:
            # translate
            return CJK_H2V[cdpt], 0
        else:
            return cdpt, 0
    return cdpt, 0

def compact_special_symbols(text: str) -> str:  
    text = text.replace('...', '…')  
    text = text.replace('..', '…')      
    # Remove half-width and full-width spaces after each punctuation mark
    pattern = r'([^\w\s])[ \u3000]+'  
    text = re.sub(pattern, r'\1', text) 
    return text
    
def rotate_image(image, angle):
    if angle == 0:
        return image, (0, 0)
    image_exp = np.zeros((round(image.shape[0] * 1.5), round(image.shape[1] * 1.5), image.shape[2]), dtype = np.uint8)
    diff_i = (image_exp.shape[0] - image.shape[0]) // 2
    diff_j = (image_exp.shape[1] - image.shape[1]) // 2
    image_exp[diff_i:diff_i+image.shape[0], diff_j:diff_j+image.shape[1]] = image
    # from https://stackoverflow.com/questions/9041681/opencv-python-rotate-image-by-x-degrees-around-specific-point
    image_center = tuple(np.array(image_exp.shape[1::-1]) / 2)
    rot_mat = cv2.getRotationMatrix2D(image_center, angle, 1.0)
    result = cv2.warpAffine(image_exp, rot_mat, image_exp.shape[1::-1], flags=cv2.INTER_LINEAR)
    if angle == 90:
        return result, (0, 0)
    return result, (diff_i, diff_j)

def add_color(bw_char_map, color, stroke_char_map, stroke_color):
    if bw_char_map.size == 0:
        fg = np.zeros((bw_char_map.shape[0], bw_char_map.shape[1], 4), dtype = np.uint8)
        return fg
    
    # print(bw_char_map.shape, stroke_char_map.shape)
    # import matplotlib.pyplot as plt
    # x1, y1, w1, h1 = cv2.boundingRect(bw_char_map)
    # x2, y2, w2, h2 = cv2.boundingRect(stroke_char_map)
    # fig, ax = plt.subplots(1, 2)
    # ax[0].imshow(bw_char_map)
    # ax[1].imshow(stroke_char_map)
    # # draw bounding boxes
    # rect1 = plt.Rectangle((x1, y1), w1, h1, fill=False, color='red')
    # rect2 = plt.Rectangle((x2, y2), w2, h2, fill=False, color='blue')
    # ax[0].add_patch(rect1)
    # ax[0].add_patch(rect2)
    # rect1 = plt.Rectangle((x1, y1), w1, h1, fill=False, color='red')
    # rect2 = plt.Rectangle((x2, y2), w2, h2, fill=False, color='blue')
    # ax[1].add_patch(rect1)
    # ax[1].add_patch(rect2)
    # plt.show()

    # since bg rect is always larger than fg rect, we can just use the bg rect
    if stroke_color is None :
        x, y, w, h = cv2.boundingRect(bw_char_map)
    else :
        x, y, w, h = cv2.boundingRect(stroke_char_map)

    fg = np.zeros((h, w, 4), dtype = np.uint8)
    fg[:,:,0] = color[0]
    fg[:,:,1] = color[1]
    fg[:,:,2] = color[2]
    fg[:,:,3] = bw_char_map[y:y+h, x:x+w]

    if stroke_color is None :
        stroke_color = color
    bg = np.zeros((stroke_char_map.shape[0], stroke_char_map.shape[1], 4), dtype = np.uint8)
    bg[:,:,0] = stroke_color[0]
    bg[:,:,1] = stroke_color[1]
    bg[:,:,2] = stroke_color[2]
    bg[:,:,3] = stroke_char_map

    fg_alpha = fg[:, :, 3] / 255.0
    bg_alpha = 1.0 - fg_alpha
    bg[y:y+h, x:x+w, :] = (fg_alpha[:, :, np.newaxis] * fg[:, :, :] + bg_alpha[:, :, np.newaxis] * bg[y:y+h, x:x+w, :])

    #alpha_char_map = cv2.add(bw_char_map, stroke_char_map)
    #alpha_char_map[alpha_char_map > 0] = 255
    return bg#, alpha_char_map

FALLBACK_FONTS = [
    os.path.join(BASE_PATH, 'fonts/Arial-Unicode-Regular.ttf'),
    os.path.join(BASE_PATH, 'fonts/msyh.ttc'),
    os.path.join(BASE_PATH, 'fonts/msgothic.ttc'),
]
# FreeType faces mutate their size and glyph slot, so a face cannot be shared by CPU workers.
_FONT_STATE = local()


def _font_state():
    if not hasattr(_FONT_STATE, "font_cache"):
        _FONT_STATE.font_cache = {}
        _FONT_STATE.selection = []
        _FONT_STATE.selection_key = ()
        _FONT_STATE.current_font_path = ""
    return _FONT_STATE


def __getattr__(name):
    if name in {
        "calc_horizontal", "get_char_offset_x", "get_string_width",
        "put_char_horizontal", "put_text_horizontal",
        "_score_layout_candidate", "_evaluate_bubble_layout_candidates",
        "_binary_search_font_size", "_render_horizontal_lines",
        "_crop_and_position_box",
    }:
        from . import text_render_horizontal

        return getattr(text_render_horizontal, name)
    if name in {"calc_vertical", "put_char_vertical", "put_text_vertical"}:
        from . import text_render_vertical

        return getattr(text_render_vertical, name)
    if name in {"calc_vertical", "put_char_vertical", "put_text_vertical"}:
        from . import text_render_vertical

        return getattr(text_render_vertical, name)
    state = _font_state()
    if name == "FONT_SELECTION":
        return state.selection
    if name == "FONT_SELECTION_KEY":
        return state.selection_key
    if name == "CURRENT_FONT_PATH":
        return state.current_font_path
    if name == "font_cache":
        return state.font_cache
    raise AttributeError(name)


def _normalize_font_path(path: str) -> str:
    return os.path.abspath(path.replace('\\', '/'))


def get_cached_font(path: str) -> freetype.Face:
    path = _normalize_font_path(path)
    font_cache = _font_state().font_cache
    if path not in font_cache:
        # To circumvent a bug with non ascii paths in windows use memory fonts
        # https://github.com/rougier/freetype-py/issues/157#issuecomment-1683713726
        font_cache[path] = freetype.Face(Path(path).open('rb'))
    return font_cache[path]

def set_font(font_path: str):
    state = _font_state()
    if font_path:
        selection = [font_path] + FALLBACK_FONTS
    else:
        selection = FALLBACK_FONTS
    selection_key = tuple(_normalize_font_path(path) for path in selection)
    state.selection = [get_cached_font(p) for p in selection]
    state.current_font_path = selection_key[0] if selection_key else ''
    state.selection_key = selection_key

class namespace:
    pass

class Glyph:
    def __init__(self, glyph):
        self.bitmap = namespace()
        self.bitmap.buffer = glyph.bitmap.buffer
        self.bitmap.rows = glyph.bitmap.rows
        self.bitmap.width = glyph.bitmap.width
        self.advance = namespace()
        self.advance.x = glyph.advance.x
        self.advance.y = glyph.advance.y
        self.bitmap_left = glyph.bitmap_left
        self.bitmap_top = glyph.bitmap_top
        self.metrics = namespace()
        self.metrics.vertBearingX = glyph.metrics.vertBearingX
        self.metrics.vertBearingY = glyph.metrics.vertBearingY
        self.metrics.horiBearingX = glyph.metrics.horiBearingX
        self.metrics.horiBearingY = glyph.metrics.horiBearingY
        self.metrics.horiAdvance = glyph.metrics.horiAdvance
        self.metrics.vertAdvance = glyph.metrics.vertAdvance

@functools.lru_cache(maxsize = 1024, typed = True)
def _get_char_glyph_cached(cdpt: str, font_size: int, direction: int, font_face_id: Tuple[str, ...]) -> Glyph:
    font_selection = _font_state().selection
    for i, face in enumerate(font_selection):
        if face.get_char_index(cdpt) == 0 and i != len(font_selection) - 1:
            continue
        if direction == 0:
            face.set_pixel_sizes(0, font_size)
        elif direction == 1:
            face.set_pixel_sizes(font_size, 0)
        face.load_char(cdpt)
        return Glyph(face.glyph)


def get_char_glyph(cdpt: str, font_size: int, direction: int) -> Glyph:
    """Return a glyph keyed by the active font selection as well as size/direction."""
    return _get_char_glyph_cached(cdpt, font_size, direction, _font_state().selection_key)


# Preserve the small cache API used by callers and diagnostics.
get_char_glyph.cache_clear = _get_char_glyph_cached.cache_clear
get_char_glyph.cache_info = _get_char_glyph_cached.cache_info
get_char_glyph.cache_parameters = _get_char_glyph_cached.cache_parameters
get_char_glyph.__wrapped__ = _get_char_glyph_cached.__wrapped__

#@functools.lru_cache(maxsize = 1024, typed = True)
def get_char_border(cdpt: str, font_size: int, direction: int):
    font_selection = _font_state().selection
    for i, face in enumerate(font_selection):
        if face.get_char_index(cdpt) == 0 and i != len(font_selection) - 1:
            continue
        if direction == 0:
            face.set_pixel_sizes(0, font_size)
        elif direction == 1:
            face.set_pixel_sizes(font_size, 0)
        face.load_char(cdpt, freetype.FT_LOAD_DEFAULT | freetype.FT_LOAD_NO_BITMAP)
        slot_border = face.glyph
        return slot_border.get_glyph()

# def get_char_kerning(cdpt, prev, font_size: int, direction: int):
#     global FONT_SELECTION
#     for i, face in enumerate(FONT_SELECTION):
#         if face.get_char_index(cdpt) == 0 and i != len(FONT_SELECTION) - 1:
#             continue
#         if direction == 0:
#             face.set_pixel_sizes(0, font_size)
#         elif direction == 1:
#             face.set_pixel_sizes(font_size, 0)
#         face.load_char(cdpt, freetype.FT_LOAD_DEFAULT | freetype.FT_LOAD_NO_BITMAP)
#         #print("VV", prev, cdpt, face.get_char_index(prev), face.get_char_index(cdpt))
#         print("VR", face.has_kerning)
#         return face.get_kerning(face.get_char_index(prev), face.get_char_index(cdpt))


# def put_text(img: np.ndarray, text: str, line_count: int, x: int, y: int, w: int, h: int, fg: Tuple[int, int, int], bg: Optional[Tuple[int, int, int]]):
#     pass

def test():
    #canvas = put_text_vertical(64, 1.0, '因为不同‼ [这"真的是普]通的》肉！那个“姑娘”的恶作剧！是吗？咲夜⁉。', 700, (0, 0, 0), (255, 128, 128))
    canvas = put_text_horizontal(64, 1.0, '因为不同‼ [这"真的是普]通的》肉！那个“姑娘”的恶作剧！是吗？咲夜⁉', 400, (0, 0, 0), (255, 128, 128))
    cv2.imwrite('text_render_combined.png', canvas)

if __name__ == '__main__':
    test()
