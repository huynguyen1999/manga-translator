"""Data contracts shared by layout, rendering, and diagnostics."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


class PlacementMode(str, Enum):
    """The single dispatch decision made by the page layout engine."""

    BUBBLE = "BUBBLE"
    FREE_TEXT = "FREE_TEXT"


@dataclass(frozen=True)
class ScanInterval:
    """A horizontal safe run on a scanline, represented as [left, right)."""

    left: int
    right: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def center(self) -> float:
        return (self.left + self.right) / 2.0


@dataclass
class BandSlot:
    """A horizontal slot that is safe across a vertical band."""

    left: int
    right: int
    y_start: int
    y_end: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def center(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def height(self) -> int:
        return self.y_end - self.y_start


@dataclass
class LobeGraph:
    """Connected speech-bubble geometry and its detected lobes."""

    cleaned_mask: np.ndarray
    distance: np.ndarray
    labels: np.ndarray
    lobe_masks: List[np.ndarray]
    centers: List[Tuple[int, int]]
    capacities: List[float]
    adjacency: List[Tuple[int, int]]
    necks: List[Dict[str, Any]]


@dataclass
class OriginalLayoutProfile:
    """The source typesetting footprint used as a placement prior."""

    font_size: float
    line_count: int
    lines: List[Dict[str, Any]]
    centroid: Tuple[float, float]
    bbox: Tuple[int, int, int, int]
    block_width: float
    block_height: float
    occupancy: float
    normalized_centroid: Tuple[float, float] = (0.5, 0.5)


@dataclass
class BubbleLayoutGroup:
    """One shared bubble and the source regions assigned to it."""

    bubble_mask: np.ndarray
    interior: np.ndarray
    regions: List[Any]
    lobe_graph: Optional[LobeGraph] = None
    zones: List[np.ndarray] = field(default_factory=list)


@dataclass
class PageObstacleMap:
    """Immutable page obstacles exposed to placement solvers."""

    bubble_mask: np.ndarray
    protected_bubble_mask: np.ndarray
    text_mask: np.ndarray
    panel_mask: np.ndarray


@dataclass
class PlacementTarget:
    """Capacity-weighted center and bounds for a safe placement mask."""

    mask: np.ndarray
    center_x: float
    center_y: float
    bbox: Tuple[int, int, int, int]
    preferred_center_x: float
    preferred_center_y: float
    is_single_region: bool = True


@dataclass
class ZoneShapeProfile:
    """Compact shape summary used to choose wrapping candidates."""

    width: float
    height: float
    aspect_ratio: float
    usable_area: float
    vertical_capacity: int
    min_capacity: int
    center_x: float
    center_y: float
    width_by_y: List[float]
    bbox: Tuple[int, int, int, int]


@dataclass
class FreeTextDamageTarget:
    """The source-plus-erased footprint anchoring a free-text paragraph."""

    mask: np.ndarray
    centroid_x: float
    centroid_y: float
    bbox: Tuple[int, int, int, int]
    area: int
    width: int
    height: int
    source_centroid: Tuple[float, float]
    source_bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)
    inpaint_bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)
    inpaint_centroid: Tuple[float, float] = (0.0, 0.0)


@dataclass
class FreeTextZone:
    """Disjoint ownership and obstacle masks for one free-text region."""

    source_bbox: Tuple[int, int, int, int]
    ownership_mask: np.ndarray
    obstacle_mask: np.ndarray
    coverage_target_mask: np.ndarray
    coverable_damage_mask: np.ndarray
    coverage_weight_map: np.ndarray
    core_damage_mask: np.ndarray
    total_coverable_weight: float = 1.0
    total_core_coverable: int = 0
    damage_target: Optional[FreeTextDamageTarget] = None


@dataclass
class PlacedLine:
    """A frozen line that a renderer can paint without reflowing."""

    text: str
    y: int
    x: int
    width: int
    height: int
    slot: Optional[BandSlot] = None


@dataclass
class LayoutCandidate:
    """One solver candidate before it is committed to a region."""

    font_size: int
    y_origin: int
    line_spacing: float
    lines: List[PlacedLine]
    penalty: float
    glyph_clearance_p5: float
    status: str = "ok"
    valid: bool = True
    qa: Dict[str, float] = field(default_factory=dict)


@dataclass
class RegionLayout:
    """Committed placement data for one translated region."""

    region_id: str
    source_region_ids: List[str] = field(default_factory=list)
    placement_mode: Optional[PlacementMode] = None
    font: Optional[str] = None
    font_size: int = 0
    lines: List[PlacedLine] = field(default_factory=list)
    target_geometry: Any = None
    source_geometry: Any = None
    solver_path: Optional[str] = None
    solver_status: Optional[str] = None
    qa_metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LayoutDiagnostics:
    """Structured validation output; warnings do not fail rendering."""

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass
class PageLayoutResult:
    """The page-level handoff between layout and rendering."""

    regions: Dict[str, RegionLayout] = field(default_factory=dict)
    diagnostics: LayoutDiagnostics = field(default_factory=LayoutDiagnostics)
    timings: Dict[str, float] = field(default_factory=dict)

    def for_region(self, region_id: str) -> Optional[RegionLayout]:
        return self.regions.get(str(region_id))
