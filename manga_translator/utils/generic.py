import os
from typing import Callable, Tuple, Optional, List
import numpy as np
import cv2
import functools
from PIL import Image
import tqdm
import requests
import sys
import hashlib
import re
try:
    import einops
except ImportError:
    einops = None
import json
from shapely import affinity
from shapely.geometry import Polygon, MultiPoint
from .generic2 import *

MODULE_PATH = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
BASE_PATH = os.path.dirname(MODULE_PATH)

# Adapted from argparse.Namespace
class Context(dict):
    def __init__(self, **kwargs):
        for name in kwargs:
            setattr(self, name, kwargs[name])
    
    def __getattr__(self, item):
        return self.get(item)
    
    def __delattr__(self, key) -> None:
        return self.__delitem__(key)

    def __setattr__(self, key, value):
        return self.__setitem__(key, value)

    def __getstate__(self):
        return self.copy()

    def __setstate__(self, state):
        self.update(state)

    def __eq__(self, other):
        if not isinstance(other, Context):
            return NotImplemented
        return dict(self) == dict(other)

    def __contains__(self, key):
        return key in self.keys()
    
    def __repr__(self):
        type_name = type(self).__name__
        arg_strings = []
        star_args = {}
        for arg in self._get_args():
            arg_strings.append(repr(arg))
        for name, value in self._get_kwargs():
            if name.isidentifier():
                arg_strings.append('%s=%r' % (name, value))
            else:
                star_args[name] = value
        if star_args:
            arg_strings.append('**%s' % repr(star_args))
        return '%s(%s)' % (type_name, ', '.join(arg_strings))

    def _get_kwargs(self):
        return list(self.items())

    def _get_args(self):
        return []

    def cleanup_intermediate(self, keep_input: bool = False):
        """Release large image arrays and intermediate pixel buffers to reclaim memory."""
        intermediate_keys = [
            "img_rgb", "img_alpha", "upscaled", "img_colorized",
            "mask_raw", "mask", "inpaint_mask", "text_mask", "bubble_mask",
            "detector_rescue_mask", "bubble_residual_mask", "protected_edge_mask",
            "mask_bundle", "mask_profile", "img_inpainted", "img_rendered", "gimp_mask",
        ]
        if not keep_input:
            intermediate_keys.append("input")
        for key in intermediate_keys:
            if key in self:
                self[key] = None
        self.cleanup_layout_workspace()

    def cleanup_detection_workspace(self):
        """Drop decoded detector and OCR work after its checkpoint has been saved."""
        for key in ("mask_raw", "textlines", "bubble_detections"):
            if key in self:
                self[key] = None
        for region in self.get("text_regions", []) or []:
            if isinstance(region, dict):
                region.pop("_bubble_mask", None)
            elif hasattr(region, "_bubble_mask"):
                region._bubble_mask = None

    def cleanup_mask_workspace(self):
        """Drop mask-generation buffers while retaining page and translation metadata."""
        for key in (
            "mask_raw", "mask", "inpaint_mask", "text_mask", "bubble_mask",
            "detector_rescue_mask", "bubble_residual_mask", "protected_edge_mask",
            "mask_bundle", "mask_profile", "page_geometry",
        ):
            if key in self:
                self[key] = None

    def cleanup_mask_diagnostics(self):
        """Drop mask diagnostics after their artifacts are saved, keeping the final mask."""
        for key in (
            "text_mask", "bubble_mask", "detector_rescue_mask",
            "bubble_residual_mask", "protected_edge_mask", "mask_bundle",
        ):
            if key in self:
                self[key] = None

    def cleanup_layout_workspace(self):
        """Drop page-sized layout scratch while keeping frozen region placements."""
        for key in ("_layout_obstacles", "_free_text_zones", "page_geometry"):
            if key in self:
                self[key] = None
        for region in self.get("text_regions", []) or []:
            for key in (
                "_free_text_source_mask", "_free_text_inpaint_mask",
                "_free_text_ownership_mask", "_free_text_zone",
            ):
                if isinstance(region, dict):
                    region.pop(key, None)
                elif hasattr(region, key):
                    setattr(region, key, None)

    def cleanup_runtime(self, preserve_output: bool = False):
        """Release runtime page buffers, optionally keeping API output images."""
        output = {
            key: self.get(key)
            for key in ("result", "img_inpainted")
        } if preserve_output else {}
        self.cleanup_intermediate(keep_input=False)
        self.cleanup_detection_workspace()
        if not preserve_output and "result" in self:
            self["result"] = None
        else:
            self.update(output)

    def cleanup_all_images(self):
        """Release all image buffers including input and result."""
        self.cleanup_runtime()

# TODO: Add TranslationContext for type linting

def atoi(text: str) -> int | str:
    return int(text) if text.isdigit() else text

def natural_sort(l: List[str]):
    return sorted(l, key=lambda text: [atoi(c) for c in re.split(r'(\d+)', text)])

def repeating_sequence(s: str):
    """Extracts repeating sequence from string. Example: 'abcabca' -> 'abc'."""
    for i in range(1, len(s) // 2 + 1):
        seq = s[:i]
        if seq * (len(s)//len(seq)) + seq[:len(s)%len(seq)] == s:
            return seq
    return s

def count_valuable_text(text: str) -> int:
    return sum([1 for ch in text if is_valuable_char(ch)])

def replace_prefix(s: str, old: str, new: str):
    if s.startswith(old):
        s = new + s[len(old):]
    return s

def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def get_digest(file_path: str) -> str:
    h = hashlib.sha256()
    BUF_SIZE = 65536

    with open(file_path, 'rb') as file:
        while True:
            # Reading is buffered, so we can read smaller chunks.
            chunk = file.read(BUF_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def get_image_md5(image) -> str:
    """计算PIL Image对象的MD5哈希值，确保相同图片内容产生相同的哈希值"""
    import io
    from PIL import Image

    try:
        # 将PIL Image转换为字节数据进行MD5计算
        img_byte_arr = io.BytesIO()
        # 统一转换为RGB格式以确保一致性
        if hasattr(image, 'mode') and image.mode != 'RGB':
            image = image.convert('RGB')
        image.save(img_byte_arr, format='PNG')
        img_bytes = img_byte_arr.getvalue()

        # 计算MD5哈希值
        h = hashlib.md5()
        h.update(img_bytes)
        return h.hexdigest()[:8]  # 只取前8位，避免文件夹名过长
    except Exception as e:
        # 如果计算失败，返回基于时间戳的fallback值
        import time
        return f"fallback_{int(time.time() * 1000)}"

def get_filename_from_url(url: str, default: str = '') -> str:
    m = re.search(r'/([^/?]+)[^/]*$', url)
    if m:
        return m.group(1)
    return default

def download_url_with_progressbar(url: str, path: str):
    if os.path.basename(path) in ('.', '') or os.path.isdir(path):
        new_filename = get_filename_from_url(url)
        if not new_filename:
            raise Exception('Could not determine filename')
        path = os.path.join(path, new_filename)

    headers = {}
    downloaded_size = 0
    if os.path.isfile(path):
        downloaded_size = os.path.getsize(path)
        headers['Range'] = 'bytes=%d-' % downloaded_size
        headers['Accept-Encoding'] = 'deflate'

    r = requests.get(url, stream=True, allow_redirects=True, headers=headers, timeout=(10, 60))
    if downloaded_size and r.headers.get('Accept-Ranges') != 'bytes':
        print('Error: Webserver does not support partial downloads. Restarting from the beginning.')
        r = requests.get(url, stream=True, allow_redirects=True, timeout=(10, 60))
        downloaded_size = 0
    total = int(r.headers.get('content-length', 0))
    chunk_size = 1024

    if r.ok:
        with tqdm.tqdm(
            desc=os.path.basename(path),
            initial=downloaded_size,
            total=total+downloaded_size,
            unit='iB',
            unit_scale=True,
            unit_divisor=chunk_size,
        ) as bar:
            with open(path, 'ab' if downloaded_size else 'wb') as f:
                is_tty = sys.stdout.isatty()
                downloaded_chunks = 0
                for data in r.iter_content(chunk_size=chunk_size):
                    size = f.write(data)
                    bar.update(size)

                    # Fallback for non TTYs so output still shown
                    downloaded_chunks += 1
                    if not is_tty and downloaded_chunks % 1000 == 0:
                        print(bar)
    else:
        raise Exception(f'Couldn\'t resolve url: "{url}" (Error: {r.status_code})')

def prompt_yes_no(query: str, default: bool = None) -> bool:
    s = '%s (%s/%s): ' % (query, 'Y' if default == True else 'y', 'N' if default == False else 'n')
    while True:
        inp = input(s).lower()
        if inp in ('yes', 'y'):
            return True
        elif inp in ('no', 'n'):
            return False
        elif default != None:
            return default
        if inp:
            print('Error: Please answer with "y" or "n"')

class AvgMeter():
    def __init__(self):
        self.reset()

    def reset(self):
        self.sum = 0
        self.count = 0

    def __call__(self, val = None):
        if val is not None:
            self.sum += val
            self.count += 1
        if self.count > 0:
            return self.sum / self.count
        else:
            return 0

def load_image(img: Image.Image) -> Tuple[np.ndarray, Optional[Image.Image]]:
    if img.mode == 'RGBA':
        # from https://stackoverflow.com/questions/9166400/convert-rgba-png-to-rgb-with-pil
        img.load()  # needed for split()
        background = Image.new('RGB', img.size, (255, 255, 255))
        alpha_ch = img.split()[3]
        background.paste(img, mask = alpha_ch)  # 3 is the alpha channel
        return np.array(background), alpha_ch
    elif img.mode == 'P':
        img = img.convert('RGBA')
        img.load()  # needed for split()
        background = Image.new('RGB', img.size, (255, 255, 255))
        alpha_ch = img.split()[3]
        background.paste(img, mask = alpha_ch)  # 3 is the alpha channel
        return np.array(background), alpha_ch
    else:
        return np.array(img.convert('RGB')), None

def dump_image(img_pil: Image.Image, img: np.ndarray, alpha_ch: Image.Image = None):
    if alpha_ch is None:
        if img.dtype != np.uint8:
            img = img.astype(np.uint8)
        return Image.fromarray(img)
    if img.shape[2] != 4:
        img = np.concatenate([img.astype(np.uint8), np.array(alpha_ch).astype(np.uint8)[..., None]], axis = 2)
    result = img_pil.convert('RGBA').resize((img.shape[1], img.shape[0]))
    result.paste(Image.fromarray(img), mask = alpha_ch)
    return result

def resize_keep_aspect(img, size):
    ratio = (float(size)/max(img.shape[0], img.shape[1]))
    new_width = round(img.shape[1] * ratio)
    new_height = round(img.shape[0] * ratio)
    return cv2.resize(img, (new_width, new_height), interpolation = cv2.INTER_LINEAR_EXACT)

def image_resize(image, width = None, height = None, inter = cv2.INTER_AREA):
    # initialize the dimensions of the image to be resized and
    # grab the image size
    dim = None
    (h, w) = image.shape[:2]

    # if both the width and height are None, then return the
    # original image
    if width is None and height is None:
        return image

    # check to see if the width is None
    if width is None:
        # calculate the ratio of the height and construct the
        # dimensions
        r = height / float(h)
        dim = (int(w * r), height)

    # otherwise, the height is None
    else:
        # calculate the ratio of the width and construct the
        # dimensions
        r = width / float(w)
        dim = (width, int(h * r))

    # resize the image
    resized = cv2.resize(image, dim, interpolation = inter)

    # return the resized image
    return resized

def resize_polygon(pts, xfact, yfact, origin='center'):
    poly = Polygon(pts)
    poly = affinity.scale(poly, xfact=xfact, yfact=yfact, origin=origin)
    dst_points = np.array(poly.exterior.coords[:4])
    return dst_points

class BBox(object):
    def __init__(self, x: int, y: int, w: int, h: int, text: str, prob: float, fg_r: int = 0, fg_g: int = 0, fg_b: int = 0, bg_r: int = 0, bg_g: int = 0, bg_b: int = 0):
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.text = text
        self.prob = prob
        self.fg_r = fg_r
        self.fg_g = fg_g
        self.fg_b = fg_b
        self.bg_r = bg_r
        self.bg_g = bg_g
        self.bg_b = bg_b

    def width(self):
        return self.w

    def height(self):
        return self.h

    def to_points(self):
        tl, tr, br, bl = np.array([self.x, self.y]), np.array([self.x + self.w, self.y]), np.array([self.x + self.w, self.y+ self.h]), np.array([self.x, self.y + self.h])
        return tl, tr, br, bl

    @property
    def xywh(self):
        return np.array([self.x, self.y, self.w, self.h], dtype=np.int32)
    

def sort_pnts(pts: np.ndarray):
    '''
    Direction must be provided for sorting.
    The longer structure vector (mean of long side vectors) of input points is used to determine the direction.
    It is reliable enough for text lines but not for blocks.
    '''

    if isinstance(pts, List):
        pts = np.array(pts)
    assert isinstance(pts, np.ndarray) and pts.shape == (4, 2)
    pairwise_vec = (pts[:, None] - pts[None]).reshape((16, -1))
    pairwise_vec_norm = np.linalg.norm(pairwise_vec, axis=1)
    long_side_ids = np.argsort(pairwise_vec_norm)[[8, 10]]
    long_side_vecs = pairwise_vec[long_side_ids]
    inner_prod = (long_side_vecs[0] * long_side_vecs[1]).sum()
    if inner_prod < 0:
        long_side_vecs[0] = -long_side_vecs[0]
    struc_vec = np.abs(long_side_vecs.mean(axis=0))
    is_vertical = struc_vec[0] <= struc_vec[1]

    if is_vertical:
        pts = pts[np.argsort(pts[:, 1])]
        pts = pts[[*np.argsort(pts[:2, 0]), *np.argsort(pts[2:, 0])[::-1] + 2]]
        return pts, is_vertical
    else:
        pts = pts[np.argsort(pts[:, 0])]
        pts_sorted = np.zeros_like(pts)
        pts_sorted[[0, 3]] = sorted(pts[[0, 1]], key=lambda x: x[1])
        pts_sorted[[1, 2]] = sorted(pts[[2, 3]], key=lambda x: x[1])
        return pts_sorted, is_vertical



# def merge_quadrilaterals(q1: Quadrilateral, q2: Quadrilateral):
#     min_rect = np.array(Polygon([*q1.pts, *q2.pts]).minimum_rotated_rectangle.exterior.coords[:4])
#     if q1.centroid[0] < q2.centroid[0] or q1.centroid[1] < q1.centroid[1]:
#         text = q1.text + ' ' + q2.text
#         # if q1.centroid[0] < q2.centroid[0]:
#         #     min_rect = np.array([q1.pts[0], q2.pts[1], q2.pts[2], q1.pts[3]])
#     else:
#         text = q2.text + ' ' + q1.text
#     prob = (q1.prob + q2.prob) / 2
#     fg_colors = (q1.fg_colors + q2.fg_colors) // 2
#     bg_colors = (q1.bg_colors + q2.bg_colors) // 2
#     return Quadrilateral(min_rect, text, prob, *fg_colors, *bg_colors)



def distance_point_point(a: np.ndarray, b: np.ndarray) -> float:
    return np.linalg.norm(a - b)

# from https://stackoverflow.com/questions/849211/shortest-distance-between-a-point-and-a-line-segment
def distance_point_lineseg(p: np.ndarray, p1: np.ndarray, p2: np.ndarray):
    x = p[0]
    y = p[1]
    x1 = p1[0]
    y1 = p1[1]
    x2 = p2[0]
    y2 = p2[1]
    A = x - x1
    B = y - y1
    C = x2 - x1
    D = y2 - y1

    dot = A * C + B * D
    len_sq = C * C + D * D
    param = -1
    if len_sq != 0:
        param = dot / len_sq

    if param < 0:
        xx = x1
        yy = y1
    elif param > 1:
        xx = x2
        yy = y2
    else:
        xx = x1 + param * C
        yy = y1 + param * D

    dx = x - xx
    dy = y - yy
    return np.sqrt(dx * dx + dy * dy)


from .generic_quadrilateral import Quadrilateral


def quadrilateral_can_merge_region(a: Quadrilateral, b: Quadrilateral, ratio = 1.9, discard_connection_gap = 2, char_gap_tolerance = 0.6, char_gap_tolerance2 = 1.5, font_size_ratio_tol = 1.5, aspect_ratio_tol = 2) -> bool:
    b1 = a.aabb
    b2 = b.aabb
    char_size = min(a.font_size, b.font_size)
    x1, y1, w1, h1 = b1.x, b1.y, b1.w, b1.h
    x2, y2, w2, h2 = b2.x, b2.y, b2.w, b2.h
    # dist = rect_distance(x1, y1, x1 + w1, y1 + h1, x2, y2, x2 + w2, y2 + h2)
    p1 = Polygon(a.pts)
    p2 = Polygon(b.pts)
    dist = p1.distance(p2)
    if dist > discard_connection_gap * char_size:
        return False
    if max(a.font_size, b.font_size) / char_size > font_size_ratio_tol:
        return False
    if a.aspect_ratio > aspect_ratio_tol and b.aspect_ratio < 1. / aspect_ratio_tol:
        return False
    if b.aspect_ratio > aspect_ratio_tol and a.aspect_ratio < 1. / aspect_ratio_tol:
        return False
    a_aa = a.is_approximate_axis_aligned
    b_aa = b.is_approximate_axis_aligned
    if a_aa and b_aa:
        if dist < char_size * char_gap_tolerance:
            if abs(x1 + w1 // 2 - (x2 + w2 // 2)) < char_gap_tolerance2:
                return True
            if w1 > h1 * ratio and h2 > w2 * ratio:
                return False
            if w2 > h2 * ratio and h1 > w1 * ratio:
                return False
            if w1 > h1 * ratio or w2 > h2 * ratio : # h
                return abs(x1 - x2) < char_size * char_gap_tolerance2 or abs(x1 + w1 - (x2 + w2)) < char_size * char_gap_tolerance2
            elif h1 > w1 * ratio or h2 > w2 * ratio : # v
                return abs(y1 - y2) < char_size * char_gap_tolerance2 or abs(y1 + h1 - (y2 + h2)) < char_size * char_gap_tolerance2
            return False
        else:
            return False
    if True:#not a_aa and not b_aa:
        if abs(a.angle - b.angle) < 15 * np.pi / 180:
            fs_a = a.font_size
            fs_b = b.font_size
            fs = min(fs_a, fs_b)
            if a.poly_distance(b) > fs * char_gap_tolerance2:
                return False
            if abs(fs_a - fs_b) / fs > 0.25:
                return False
            return True
    return False

def quadrilateral_can_merge_region_coarse(a: Quadrilateral, b: Quadrilateral, discard_connection_gap = 2, font_size_ratio_tol = 0.7) -> bool:
    if a.assigned_direction != b.assigned_direction:
        return False
    if abs(a.angle - b.angle) > 15 * np.pi / 180:
        return False
    fs_a = a.font_size
    fs_b = b.font_size
    fs = min(fs_a, fs_b)
    if abs(fs_a - fs_b) / fs > font_size_ratio_tol:
        return False
    fs = max(fs_a, fs_b)
    dist = a.poly_distance(b)
    if dist > discard_connection_gap * fs:
        return False
    return True

def findNextPowerOf2(n):
    i = 0
    while n != 0:
        i += 1
        n = n >> 1
    return 1 << i

class Point:
    def __init__(self, x = 0, y = 0):
        self.x = x
        self.y = y

    def length2(self) -> float:
        return self.x * self.x + self.y * self.y

    def length(self) -> float:
        return np.sqrt(self.length2())

    def __str__(self):
        return f'({self.x}, {self.y})'

    def __add__(self, other):
        x = self.x + other.x
        y = self.y + other.y
        return Point(x, y)

    def __sub__(self, other):
        x = self.x - other.x
        y = self.y - other.y
        return Point(x, y)

    def __mul__(self, other):
        if isinstance(other, Point):
            return self.x * other.x + self.y * other.y
        else:
            return Point(self.x * other, self.y * other)

    def __truediv__(self, other):
        return self.x * other.y - self.y * other.x

    def neg(self):
        return Point(-self.x, -self.y)

    def normalize(self):
        return self * (1. / self.length())

def center_of_points(pts: List[Point]) -> Point:
    ans = Point()
    for p in pts:
        ans.x += p.x
        ans.y += p.y
    ans.x /= len(pts)
    ans.y /= len(pts)
    return ans

def support_impl(pts: List[Point], d: Point) -> Point:
    dist = -1.0e-20
    ans = pts[0]
    for p in pts:
        proj = p * d
        if proj > dist:
            dist = proj
            ans = p
    return ans

def support(a: List[Point], b: List[Point], d: Point) -> Point:
    return support_impl(a, d) - support_impl(b, d.neg())

def cross(a: Point, b: Point, c: Point) -> Point:
    return b * (a * c) - a * (b * c)

def closest_point_to_origin(a: Point, b: Point) -> Point:
    da = a.length()
    db = b.length()
    dist = abs(a / b) / (a - b).length()
    ab = b - a
    ba = a - b
    ao = a.neg()
    bo = b.neg()
    if ab * ao > 0 and ba * bo > 0:
        return cross(ab, ao, ab).normalize() * dist
    return a.neg() if da < db else b.neg()

def dcmp(a) -> bool:
    if abs(a) < 1e-8:
        return False
    return True

def gjk_distance(s1: List[Point], s2: List[Point]) -> float:
    d = center_of_points(s2) - center_of_points(s1)
    a = support(s1, s2, d)
    b = support(s1, s2, d.neg())
    d = closest_point_to_origin(a, b)
    s = [a, b]
    for _ in range(8):
        c = support(s1, s2, d)
        a = s.pop()
        b = s.pop()
        da = d * a
        db = d * b
        dc = d * c
        if not dcmp(dc - da) or not dcmp(dc - db):
            return d.length()
        p1 = closest_point_to_origin(a, c)
        p2 = closest_point_to_origin(b, c)
        if p1.length2() < p2.length2():
            s.append(a)
            d = p1
        else:
            s.append(b)
            d = p2
        s.append(c)
    return 0

def rgb2hex(r,g,b):
    return "#{:02x}{:02x}{:02x}".format(r,g,b)

def hex2rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def get_color_name(rgb: List[int]) -> str:
        try:
            # TODO: Maybe replace with offline alternative
            url = f'https://www.thecolorapi.com/id?format=json&rgb={rgb[0]},{rgb[1]},{rgb[2]}'
            response = requests.get(url, timeout=2.0)
            if response.status_code == 200:
                return json.loads(response.text)['name']['value']
            else:
                return 'Unnamed'
        except Exception:
            return 'Unnamed'

def square_pad_resize(img: np.ndarray, tgt_size: int):
    h, w = img.shape[:2]
    pad_h, pad_w = 0, 0
    
    # make square image
    if w < h:
        pad_w = h - w
        w += pad_w
    elif h < w:
        pad_h = w - h
        h += pad_h

    pad_size = tgt_size - h
    if pad_size > 0:
        pad_h += pad_size
        pad_w += pad_size

    if pad_h > 0 or pad_w > 0:    
        img = cv2.copyMakeBorder(img, 0, pad_h, 0, pad_w, cv2.BORDER_CONSTANT)

    down_scale_ratio = tgt_size / img.shape[0]
    assert down_scale_ratio <= 1
    if down_scale_ratio < 1:
        img = cv2.resize(img, (tgt_size, tgt_size), interpolation=cv2.INTER_LINEAR)

    return img, down_scale_ratio, pad_h, pad_w

def _unrearrange_patches(
    patch_lst: List[np.ndarray],
    transpose: bool,
    ph_step: int,
    patch_size: int,
    pw_num: int,
    w: int,
    h: int,
    rel_step_list: List[float],
    channel: int = 1,
    pad_num: int = 0
) -> np.ndarray:
    _psize = _h = patch_lst[0].shape[-1]
    _step = int(ph_step * _psize / patch_size)
    _pw = int(_psize / pw_num)
    _h = int(_pw / w * h)
    tgtmap = np.zeros((channel, _h, _pw), dtype=np.float32)
    num_patches = len(patch_lst) * pw_num - pad_num
    for ii, p in enumerate(patch_lst):
        if transpose:
            p = einops.rearrange(p, 'c h w -> c w h')
        for jj in range(pw_num):
            pidx = ii * pw_num + jj
            rel_t = rel_step_list[pidx]
            t = int(round(rel_t * _h))
            b = min(t + _psize, _h)
            l = jj * _pw
            r = l + _pw
            tgtmap[..., t: b, :] += p[..., : b - t, l: r]
            if pidx > 0:
                interleave = _psize - _step
                tgtmap[..., t: t+interleave, :] /= 2.

            if pidx >= num_patches - 1:
                break

    if transpose:
        tgtmap = einops.rearrange(tgtmap, 'c h w -> c w h')
    return tgtmap[None, ...]


def _patch2batches(
    patch_lst: List[np.ndarray],
    p_num: int,
    transpose: bool,
    tgt_size: int,
    max_batch_size: int,
    verbose: bool
) -> Tuple[List[List[np.ndarray]], float, int]:
    if transpose:
        patch_lst = einops.rearrange(patch_lst, '(p_num pw_num) ph pw c -> p_num (pw_num pw) ph c', p_num=p_num)
    else:
        patch_lst = einops.rearrange(patch_lst, '(p_num pw_num) ph pw c -> p_num ph (pw_num pw) c', p_num=p_num)
    
    batches = [[]]
    down_scale_ratio = 1.0
    pad_size = 0
    for ii, patch in enumerate(patch_lst):
        if len(batches[-1]) >= max_batch_size:
            batches.append([])
        p, down_scale_ratio, pad_h, pad_w = square_pad_resize(patch, tgt_size=tgt_size)

        assert pad_h == pad_w
        pad_size = pad_h
        batches[-1].append(p)
        if verbose:
            cv2.imwrite(f'result/rearrange_{ii}.png', p[..., ::-1])
    return batches, down_scale_ratio, pad_size


def _run_rearranged_dbnet_batches(
    batches: List[List[np.ndarray]],
    dbnet_batch_forward: Callable[[np.ndarray, str], Tuple[np.ndarray, np.ndarray]],
    device: str,
    tgt_size: int,
    pad_size: int
) -> Tuple[List[np.ndarray], List[np.ndarray]]:
    db_lst, mask_lst = [], []
    for batch in batches:
        batch_arr = np.array(batch)
        db, mask = dbnet_batch_forward(batch_arr, device=device)

        for d, m in zip(db, mask):
            if pad_size > 0:
                paddb = int(db.shape[-1] / tgt_size * pad_size)
                padmsk = int(mask.shape[-1] / tgt_size * pad_size)
                d = d[..., :-paddb, :-paddb]
                m = m[..., :-padmsk, :-padmsk]
            db_lst.append(d)
            mask_lst.append(m)
    return db_lst, mask_lst


def det_rearrange_forward(
    img: np.ndarray, 
    dbnet_batch_forward: Callable[[np.ndarray, str], Tuple[np.ndarray, np.ndarray]], 
    tgt_size: int = 1280, 
    max_batch_size: int = 4, 
    device='cuda', verbose=False):
    '''
    Rearrange image to square batches before feeding into network if following conditions are satisfied: \n
    1. Extreme aspect ratio
    2. Is too tall or wide for detect size (tgt_size)

    Returns:
        DBNet output, mask or None, None if rearrangement is not required
    '''
    h, w = img.shape[:2]
    transpose = False
    if h < w:
        transpose = True
        h, w = img.shape[1], img.shape[0]

    asp_ratio = h / w
    down_scale_ratio = h / tgt_size

    # rearrange condition
    require_rearrange = down_scale_ratio > 2.5 and asp_ratio > 3
    if not require_rearrange:
        return None, None

    if verbose:
        print(f'Input image will be rearranged to square batches before fed into network.\
            \n Rearranged batches will be saved to result/rearrange_%d.png')

    if transpose:
        img = einops.rearrange(img, 'h w c -> w h c')
    
    pw_num = max(int(np.floor(2 * tgt_size / w)), 2)
    patch_size = ph = pw_num * w

    ph_num = int(np.ceil(h / ph))
    ph_step = int((h - ph) / (ph_num - 1)) if ph_num > 1 else 0
    rel_step_list = []
    patch_list = []
    for ii in range(ph_num):
        t = ii * ph_step
        b = t + ph
        rel_step_list.append(t / h)
        patch_list.append(img[t: b])

    p_num = int(np.ceil(ph_num / pw_num))
    pad_num = p_num * pw_num - ph_num
    for ii in range(pad_num):
        patch_list.append(np.zeros_like(patch_list[0]))

    batches, down_scale_ratio, pad_size = _patch2batches(
        patch_list, p_num, transpose, tgt_size, max_batch_size, verbose
    )

    db_lst, mask_lst = _run_rearranged_dbnet_batches(
        batches, dbnet_batch_forward, device, tgt_size, pad_size
    )

    db = _unrearrange_patches(
        db_lst, transpose, ph_step, patch_size, pw_num, w, h, rel_step_list, channel=2, pad_num=pad_num
    )
    mask = _unrearrange_patches(
        mask_lst, transpose, ph_step, patch_size, pw_num, w, h, rel_step_list, channel=1, pad_num=pad_num
    )
    return db, mask


def main():
    s1 = [Point(0, 0), Point(0, 2), Point(2, 2), Point(2, 0)]
    offset = 0
    s2 = [Point(1 + offset, 1 + offset), Point(1 + offset, 3 + offset), Point(3 + offset, 3 + offset + 1.5), Point(3 + offset + 1.5, 3 + offset), Point(3 + offset, 1 + offset)]
    print(gjk_distance(s1, s2))

if __name__ == '__main__':
    main()

