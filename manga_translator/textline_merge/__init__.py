import itertools
import numpy as np
from typing import List, Set
from collections import Counter
import networkx as nx
from shapely.geometry import Polygon
from ..utils import TextBlock, Quadrilateral
from .geometry import analyze_textline_pair

def split_text_region(
        bboxes: List[Quadrilateral],
        connected_region_indices: Set[int],
        width,
        height,
        gamma=0.5,
        sigma=2,
        pair_geometries=None,
    ) -> List[Set[int]]:
    if pair_geometries is None:
        return _split_text_region_legacy(bboxes, connected_region_indices, width, height, gamma, sigma)

    connected_region_indices = list(connected_region_indices)
    if len(connected_region_indices) == 1:
        return [set(connected_region_indices)]

    graph = nx.Graph()
    graph.add_nodes_from(connected_region_indices)
    for first, second in itertools.combinations(connected_region_indices, 2):
        pair = _get_pair_geometry(bboxes, first, second, pair_geometries)
        if pair.can_merge:
            graph.add_edge(first, second, weight=pair.score)

    if len(connected_region_indices) == 2 or graph.number_of_edges() == 0:
        return [set(connected_region_indices)]

    tree = nx.minimum_spanning_tree(graph, weight="weight")
    if not nx.is_connected(tree):
        return [set(component) for component in nx.connected_components(tree)]

    tree_edges = sorted(tree.edges(data=True), key=lambda edge: edge[2]["weight"], reverse=True)
    possible_edges = [
        edge for edge in tree_edges
        if _get_pair_geometry(bboxes, edge[0], edge[1], pair_geometries).merge_class == "possible"
    ]
    if not possible_edges:
        return [set(connected_region_indices)]

    split_edge = possible_edges[0]
    edge_score = float(split_edge[2]["weight"])
    typical_score = float(np.median([edge[2]["weight"] for edge in tree_edges]))
    if edge_score < 1.0 or edge_score - typical_score < 0.65:
        return [set(connected_region_indices)]

    # Strong overlap edges are never cut; split only a costly fallback edge.
    tree.remove_edge(split_edge[0], split_edge[1])
    components = list(nx.connected_components(tree))
    if len(components) == 1:
        return [set(connected_region_indices)]

    result = []
    for component in components:
        result.extend(split_text_region(bboxes, component, width, height, pair_geometries=pair_geometries))
    return result


def _split_text_region_legacy(bboxes, connected_region_indices, width, height, gamma, sigma):
    """Keep model_manga_ocr's independent grouping behavior unchanged."""
    indices = list(connected_region_indices)
    if len(indices) == 1:
        return [set(indices)]
    if len(indices) == 2:
        first, second = (bboxes[index] for index in indices)
        if first.distance(second) < (1 + gamma) * max(first.font_size, second.font_size) \
                and abs(first.angle - second.angle) < 0.2 * np.pi:
            return [set(indices)]
        return [{index} for index in indices]

    graph = nx.Graph()
    graph.add_nodes_from(indices)
    for first, second in itertools.combinations(indices, 2):
        graph.add_edge(first, second, weight=bboxes[first].distance(bboxes[second]))
    edges = sorted(
        nx.minimum_spanning_edges(graph, algorithm="kruskal", data=True),
        key=lambda edge: edge[2]["weight"],
        reverse=True,
    )
    distances = [edge[2]["weight"] for edge in edges]
    fontsize = np.mean([bboxes[index].font_size for index in indices])
    std = np.std(distances)
    mean = np.mean(distances)
    std_threshold = max(0.3 * fontsize + 5, 5)
    first, second = (bboxes[index] for index in edges[0][:2])
    aligned = min(abs(first.centroid[0] - second.centroid[0]), abs(first.centroid[1] - second.centroid[1])) < 5
    if ((distances[0] <= mean + std * sigma or distances[0] <= fontsize * (1 + gamma))
            and (std < std_threshold or Polygon(first.pts).distance(Polygon(second.pts)) == 0 and aligned)):
        return [set(indices)]

    forest = nx.Graph()
    forest.add_nodes_from(indices)
    forest.add_edges_from((edge[0], edge[1]) for edge in edges[1:])
    result = []
    for component in nx.connected_components(forest):
        result.extend(_split_text_region_legacy(bboxes, component, width, height, gamma, sigma))
    return result


def _pair_key(first: int, second: int) -> tuple[int, int]:
    return (first, second) if first < second else (second, first)


def _get_pair_geometry(bboxes, first, second, pair_geometries):
    key = _pair_key(first, second)
    if pair_geometries is not None and key in pair_geometries:
        return pair_geometries[key]
    return analyze_textline_pair(bboxes[first], bboxes[second])

# def get_mini_boxes(contour):
#     bounding_box = cv2.minAreaRect(contour)
#     points = sorted(list(cv2.boxPoints(bounding_box)), key=lambda x: x[0])

#     index_1, index_2, index_3, index_4 = 0, 1, 2, 3
#     if points[1][1] > points[0][1]:
#         index_1 = 0
#         index_4 = 1
#     else:
#         index_1 = 1
#         index_4 = 0
#     if points[3][1] > points[2][1]:
#         index_2 = 2
#         index_3 = 3
#     else:
#         index_2 = 3
#         index_3 = 2

#     box = [points[index_1], points[index_2], points[index_3], points[index_4]]
#     box = np.array(box)
#     startidx = box.sum(axis=1).argmin()
#     box = np.roll(box, 4 - startidx, 0)
#     box = np.array(box)
#     return box

def merge_bboxes_text_region(bboxes: List[Quadrilateral], width, height, pair_diagnostics=None):
    # step 0: merge quadrilaterals that belong to the same textline
    # u = 0
    # removed_counter = 0
    # while u < len(bboxes) - 1 - removed_counter:
    #     v = u
    #     while v < len(bboxes) - removed_counter:
    #         if quadrilateral_can_merge_region(bboxes[u], bboxes[v], aspect_ratio_tol=1.1, font_size_ratio_tol=1,
    #                                         char_gap_tolerance=1, char_gap_tolerance2=3, discard_connection_gap=0) \
    #            and abs(bboxes[u].centroid[0] - bboxes[v].centroid[0]) < 5 or abs(bboxes[u].centroid[1] - bboxes[v].centroid[1]) < 5:
    #                 bboxes[u] = merge_quadrilaterals(bboxes[u], bboxes[v])
    #                 removed_counter += 1
    #                 bboxes.pop(v)
    #         else:
    #             v += 1
    #     u += 1

    # step 1: divide into multiple text region candidates
    G = nx.Graph()
    for i, box in enumerate(bboxes):
        G.add_node(i, box=box)

    pair_geometries = {}
    for ((u, ubox), (v, vbox)) in itertools.combinations(enumerate(bboxes), 2):
        pair = analyze_textline_pair(ubox, vbox)
        pair_geometries[(u, v)] = pair
        if pair_diagnostics is not None:
            pair_diagnostics.append(pair.to_dict(u, v))
        if pair.can_merge:
            G.add_edge(u, v, weight=pair.score, merge_class=pair.merge_class)

    # step 2: postprocess - further split each region
    region_indices: List[Set[int]] = []
    for node_set in nx.algorithms.components.connected_components(G):
        region_indices.extend(
            split_text_region(bboxes, node_set, width, height, pair_geometries=pair_geometries)
        )

    # step 3: return regions
    for node_set in region_indices:
    # for node_set in nx.algorithms.components.connected_components(G):
        nodes = list(node_set)
        txtlns: List[Quadrilateral] = np.array(bboxes)[nodes]

        # calculate average fg and bg color
        fg_r = round(np.mean([box.fg_r for box in txtlns]))
        fg_g = round(np.mean([box.fg_g for box in txtlns]))
        fg_b = round(np.mean([box.fg_b for box in txtlns]))
        bg_r = round(np.mean([box.bg_r for box in txtlns]))
        bg_g = round(np.mean([box.bg_g for box in txtlns]))
        bg_b = round(np.mean([box.bg_b for box in txtlns]))

        # majority vote for direction
        dirs = [box.direction for box in txtlns]
        majority_dir_top_2 = Counter(dirs).most_common(2)
        if len(majority_dir_top_2) == 1 :
            majority_dir = majority_dir_top_2[0][0]
        elif majority_dir_top_2[0][1] == majority_dir_top_2[1][1] : # if top 2 have the same counts
            max_aspect_ratio = -100
            for box in txtlns :
                if box.aspect_ratio > max_aspect_ratio :
                    max_aspect_ratio = box.aspect_ratio
                    majority_dir = box.direction
                if 1.0 / box.aspect_ratio > max_aspect_ratio :
                    max_aspect_ratio = 1.0 / box.aspect_ratio
                    majority_dir = box.direction
        else :
            majority_dir = majority_dir_top_2[0][0]

        # sort textlines
        if majority_dir == 'h':
            nodes = sorted(nodes, key=lambda x: bboxes[x].centroid[1])
        elif majority_dir == 'v':
            nodes = sorted(nodes, key=lambda x: -bboxes[x].centroid[0])
        txtlns = np.array(bboxes)[nodes]

        # yield overall bbox and sorted indices
        yield txtlns, (fg_r, fg_g, fg_b), (bg_r, bg_g, bg_b)

async def dispatch(
    textlines: List[Quadrilateral],
    width: int,
    height: int,
    verbose: bool = False,
    pair_diagnostics=None,
) -> List[TextBlock]:
    # print(width, height)
    # import re
    # for l in textlines:
    #     s = str(l.pts)
    #     s = re.sub(r'([\d\]]) ', r'\1, ', s.replace('\n ', ', ')).replace(']]', ']],')
    #     print(s)

    text_regions: List[TextBlock] = []
    for (txtlns, fg_color, bg_color) in merge_bboxes_text_region(
        textlines, width, height, pair_diagnostics=pair_diagnostics
    ):
        total_logprobs = 0
        for txtln in txtlns:
            total_logprobs += np.log(txtln.prob) * txtln.area
        total_logprobs /= sum(txtln.area for txtln in txtlns)

        font_size = int(min([txtln.font_size for txtln in txtlns]))
        angle = np.rad2deg(np.mean([txtln.angle for txtln in txtlns])) - 90
        if abs(angle) < 3:
            angle = 0
        lines = [txtln.pts for txtln in txtlns]
        texts = [txtln.text for txtln in txtlns]
        region = TextBlock(lines, texts, font_size=font_size, angle=angle, prob=np.exp(total_logprobs),
                           fg_color=fg_color, bg_color=bg_color)
        region.source_line_styles = [getattr(txtln, "source_style", None) for txtln in txtlns]
        text_regions.append(region)
    return text_regions
