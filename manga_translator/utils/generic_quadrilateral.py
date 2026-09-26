"""Quadrilateral text-line geometry used by OCR and detection."""

from typing import List
import functools

import cv2
import numpy as np
from shapely.geometry import MultiPoint, Polygon

from .generic import BBox, dist, distance_point_lineseg, distance_point_point, sort_pnts


class Quadrilateral(object):
    """
    Helper for storing textlines that contains various helper functions.
    """
    def __init__(self, pts: np.ndarray, text: str, prob: float, fg_r: int = 0, fg_g: int = 0, fg_b: int = 0, bg_r: int = 0, bg_g: int = 0, bg_b: int = 0):    
        self.pts, is_vertical = sort_pnts(pts)
        if is_vertical:
            self.direction = 'v'
        else:
            self.direction = 'h'
        self.text = text
        self.prob = prob
        self.fg_r = fg_r
        self.fg_g = fg_g
        self.fg_b = fg_b
        self.bg_r = bg_r
        self.bg_g = bg_g
        self.bg_b = bg_b
        self.assigned_direction: str = None
        self.textlines: List[Quadrilateral] = []

    @functools.cached_property
    def structure(self) -> List[np.ndarray]:
        p1 = ((self.pts[0] + self.pts[1]) / 2).astype(int)
        p2 = ((self.pts[2] + self.pts[3]) / 2).astype(int)
        p3 = ((self.pts[1] + self.pts[2]) / 2).astype(int)
        p4 = ((self.pts[3] + self.pts[0]) / 2).astype(int)
        return [p1, p2, p3, p4]

    @functools.cached_property
    def valid(self) -> bool:
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v1 = l1b - l1a
        v2 = l2b - l2a
        unit_vector_1 = v1 / np.linalg.norm(v1)
        unit_vector_2 = v2 / np.linalg.norm(v2)
        dot_product = np.dot(unit_vector_1, unit_vector_2)
        angle = np.arccos(dot_product) * 180 / np.pi
        return abs(angle - 90) < 10

    @property
    def fg_colors(self):
        return np.array([self.fg_r, self.fg_g, self.fg_b])

    @property
    def bg_colors(self):
        return np.array([self.bg_r, self.bg_g, self.bg_b])

    @functools.cached_property
    def aspect_ratio(self) -> float:
        """hor/ver"""
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v1 = l1b - l1a
        v2 = l2b - l2a
        return np.linalg.norm(v2) / np.linalg.norm(v1)

    @functools.cached_property
    def font_size(self) -> float:
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v1 = l1b - l1a
        v2 = l2b - l2a
        return min(np.linalg.norm(v2), np.linalg.norm(v1))

    def width(self) -> int:
        return self.aabb.w

    def height(self) -> int:
        return self.aabb.h

    @functools.cached_property
    def xyxy(self):
        return self.aabb.x, self.aabb.y, self.aabb.x + self.aabb.w, self.aabb.y + self.aabb.h

    def clip(self, width, height):
        self.pts[:, 0] = np.clip(np.round(self.pts[:, 0]), 0, width)
        self.pts[:, 1] = np.clip(np.round(self.pts[:, 1]), 0, height)

    # @functools.cached_property
    # def points(self):
    #     ans = [a.astype(np.float32) for a in self.structure]
    #     return [Point(a[0], a[1]) for a in ans]

    @functools.cached_property
    def aabb(self) -> BBox:
        kq = self.pts
        max_coord = np.max(kq, axis = 0)
        min_coord = np.min(kq, axis = 0)
        return BBox(min_coord[0], min_coord[1], max_coord[0] - min_coord[0], max_coord[1] - min_coord[1], self.text, self.prob, self.fg_r, self.fg_g, self.fg_b, self.bg_r, self.bg_g, self.bg_b)

    def get_transformed_region(self, img, direction, textheight) -> np.ndarray:
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v_vec = l1b - l1a
        h_vec = l2b - l2a
        ratio = np.linalg.norm(v_vec) / np.linalg.norm(h_vec)

        src_pts = self.pts.astype(np.int64).copy()
        im_h, im_w = img.shape[:2]

        x1, y1, x2, y2 = src_pts[:, 0].min(), src_pts[:, 1].min(), src_pts[:, 0].max(), src_pts[:, 1].max()
        x1 = np.clip(x1, 0, im_w)
        y1 = np.clip(y1, 0, im_h)
        x2 = np.clip(x2, 0, im_w)
        y2 = np.clip(y2, 0, im_h)
        # cv2.warpPerspective could overflow if image size is too large, better crop it here
        img_croped = img[y1: y2, x1: x2]

        
        src_pts[:, 0] -= x1
        src_pts[:, 1] -= y1

        self.assigned_direction = direction
        if direction == 'h':
            h = max(int(textheight), 2)
            w = max(int(round(textheight / ratio)), 2)
            dst_pts = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]]).astype(np.float32)
            M, _ = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
            region = cv2.warpPerspective(img_croped, M, (w, h))
            return region
        elif direction == 'v':
            w = max(int(textheight), 2)
            h = max(int(round(textheight * ratio)), 2)
            dst_pts = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]]).astype(np.float32)
            M, _ = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
            region = cv2.warpPerspective(img_croped, M, (w, h))
            region = cv2.rotate(region, cv2.ROTATE_90_COUNTERCLOCKWISE)
            return region

    @functools.cached_property
    def is_axis_aligned(self) -> bool:
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v1 = l1b - l1a
        v2 = l2b - l2a
        e1 = np.array([0, 1])
        e2 = np.array([1, 0])
        unit_vector_1 = v1 / np.linalg.norm(v1)
        unit_vector_2 = v2 / np.linalg.norm(v2)
        if abs(np.dot(unit_vector_1, e1)) < 1e-2 or abs(np.dot(unit_vector_1, e2)) < 1e-2:
            return True
        return False

    @functools.cached_property
    def is_approximate_axis_aligned(self) -> bool:
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v1 = l1b - l1a
        v2 = l2b - l2a
        e1 = np.array([0, 1])
        e2 = np.array([1, 0])
        unit_vector_1 = v1 / np.linalg.norm(v1)
        unit_vector_2 = v2 / np.linalg.norm(v2)
        if abs(np.dot(unit_vector_1, e1)) < 0.05 or abs(np.dot(unit_vector_1, e2)) < 0.05 or abs(np.dot(unit_vector_2, e1)) < 0.05 or abs(np.dot(unit_vector_2, e2)) < 0.05:
            return True
        return False

    @functools.cached_property
    def cosangle(self) -> float:
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v1 = l1b - l1a
        e2 = np.array([1, 0])
        unit_vector_1 = v1 / np.linalg.norm(v1)
        return np.dot(unit_vector_1, e2)

    @functools.cached_property
    def angle(self) -> float:
        return np.fmod(np.arccos(self.cosangle) + np.pi, np.pi)

    @functools.cached_property
    def centroid(self) -> np.ndarray:
        return np.average(self.pts, axis = 0)

    def distance_to_point(self, p: np.ndarray) -> float:
        d = 1.0e20
        for i in range(4):
            d = min(d, distance_point_point(p, self.pts[i]))
            d = min(d, distance_point_lineseg(p, self.pts[i], self.pts[(i + 1) % 4]))
        return d

    @functools.cached_property
    def polygon(self) -> Polygon:
        return MultiPoint([tuple(self.pts[0]), tuple(self.pts[1]), tuple(self.pts[2]), tuple(self.pts[3])]).convex_hull

    @functools.cached_property
    def area(self) -> float:
        return self.polygon.area

    def poly_distance(self, other) -> float:
        return self.polygon.distance(other.polygon)

    def distance(self, other, rho = 0.5) -> float:
        return self.distance_impl(other, rho)# + 1000 * abs(self.angle - other.angle)

    def distance_impl(self, other, rho = 0.5) -> float:
        # assert self.assigned_direction == other.assigned_direction
        #return gjk_distance(self.points, other.points)
        # b1 = self.aabb
        # b2 = b2.aabb
        # x1, y1, w1, h1 = b1.x, b1.y, b1.w, b1.h
        # x2, y2, w2, h2 = b2.x, b2.y, b2.w, b2.h
        # return rect_distance(x1, y1, x1 + w1, y1 + h1, x2, y2, x2 + w2, y2 + h2)
        pattern = ''
        if self.assigned_direction == 'h':
            pattern = 'h_left'
        else:
            pattern = 'v_top'
        fs = max(self.font_size, other.font_size)
        if self.assigned_direction == 'h':
            poly1 = MultiPoint([tuple(self.pts[0]), tuple(self.pts[3]), tuple(other.pts[0]), tuple(other.pts[3])]).convex_hull
            poly2 = MultiPoint([tuple(self.pts[2]), tuple(self.pts[1]), tuple(other.pts[2]), tuple(other.pts[1])]).convex_hull
            poly3 = MultiPoint([
                tuple(self.structure[0]),
                tuple(self.structure[1]),
                tuple(other.structure[0]),
                tuple(other.structure[1]),
            ]).convex_hull
            dist1 = poly1.area / fs
            dist2 = poly2.area / fs
            dist3 = poly3.area / fs
            if dist1 < fs * rho:
                pattern = 'h_left'
            if dist2 < fs * rho and dist2 < dist1:
                pattern = 'h_right'
            if dist3 < fs * rho and dist3 < dist1 and dist3 < dist2:
                pattern = 'h_middle'
            if pattern == 'h_left':
                return dist(self.pts[0][0], self.pts[0][1], other.pts[0][0], other.pts[0][1])
            elif pattern == 'h_right':
                return dist(self.pts[1][0], self.pts[1][1], other.pts[1][0], other.pts[1][1])
            else:
                return dist(self.structure[0][0], self.structure[0][1], other.structure[0][0], other.structure[0][1])
        else:
            poly1 = MultiPoint([tuple(self.pts[0]), tuple(self.pts[1]), tuple(other.pts[0]), tuple(other.pts[1])]).convex_hull
            poly2 = MultiPoint([tuple(self.pts[2]), tuple(self.pts[3]), tuple(other.pts[2]), tuple(other.pts[3])]).convex_hull
            dist1 = poly1.area / fs
            dist2 = poly2.area / fs
            if dist1 < fs * rho:
                pattern = 'v_top'
            if dist2 < fs * rho and dist2 < dist1:
                pattern = 'v_bottom'
            if pattern == 'v_top':
                return dist(self.pts[0][0], self.pts[0][1], other.pts[0][0], other.pts[0][1])
            else:
                return dist(self.pts[2][0], self.pts[2][1], other.pts[2][0], other.pts[2][1])

    def copy(self, new_pts: np.ndarray):
        return Quadrilateral(new_pts, self.text, self.prob, *self.fg_colors, *self.bg_colors)
