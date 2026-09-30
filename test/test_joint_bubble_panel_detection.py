from unittest.mock import MagicMock
import numpy as np
import pytest
import torch

from manga_translator.detection.panel import (
    PanelDetection,
    serialize_panel_detections,
    deserialize_panel_detections,
    sort_panel_detections_reading_order,
)
from manga_translator.detection.bubble import BubbleDetection, BubbleDetectionState, BubbleDetector
from manga_translator.geometry.panels import infer_panel_constraints
from manga_translator.geometry.panel_matching import match_panel_to_source
from manga_translator.utils.sort import sort_regions
from manga_translator.utils.textblock import TextBlock


class TestPanelDetectionDataclass:
    def test_serialization_round_trip(self):
        panels = [
            PanelDetection(
                xyxy=[500, 0, 1000, 700],
                polygons=[[[500, 0], [1000, 0], [1000, 700], [500, 700]]],
                confidence=0.95,
                order_index=1,
            ),
            PanelDetection(
                xyxy=[0, 0, 500, 700],
                polygons=[],
                confidence=0.91,
                order_index=2,
            ),
        ]
        data = serialize_panel_detections(panels, image_shape=(1500, 1000))
        assert len(data) == 2
        assert data[0]["order"] == 1
        assert data[0]["xyxy"] == [500, 0, 1000, 700]
        assert data[0]["confidence"] == 0.95

        restored = deserialize_panel_detections(data)
        assert len(restored) == 2
        assert restored[0].order_index == 1
        assert restored[0].xyxy == [500, 0, 1000, 700]
        assert restored[0].polygons == [[[500, 0], [1000, 0], [1000, 700], [500, 700]]]
        assert restored[0].confidence == 0.95

        assert restored[1].order_index == 2
        assert restored[1].polygons == []

    def test_deserialize_empty_and_corrupt(self):
        assert deserialize_panel_detections(None) == []
        assert deserialize_panel_detections({}) == []
        assert deserialize_panel_detections([]) == []
        assert deserialize_panel_detections("invalid") == []

    def test_sort_panel_detections_reading_order_rtl(self):
        # 4 panels in a 2x2 grid:
        # Top-Right (Panel A: y=0..500, x=500..1000) -> 1st
        # Top-Left (Panel B: y=0..500, x=0..500) -> 2nd
        # Bottom-Right (Panel C: y=500..1000, x=500..1000) -> 3rd
        # Bottom-Left (Panel D: y=500..1000, x=0..500) -> 4th
        panel_bl = PanelDetection(xyxy=[0, 500, 500, 1000], polygons=[], confidence=0.9)
        panel_tr = PanelDetection(xyxy=[500, 0, 1000, 500], polygons=[], confidence=0.9)
        panel_tl = PanelDetection(xyxy=[0, 0, 500, 500], polygons=[], confidence=0.9)
        panel_br = PanelDetection(xyxy=[500, 500, 1000, 1000], polygons=[], confidence=0.9)

        unsorted = [panel_bl, panel_tr, panel_tl, panel_br]
        sorted_panels = sort_panel_detections_reading_order(unsorted, rtl=True)

        assert [p.xyxy for p in sorted_panels] == [
            [500, 0, 1000, 500],
            [0, 0, 500, 500],
            [500, 500, 1000, 1000],
            [0, 500, 500, 1000],
        ]
        assert [p.order_index for p in sorted_panels] == [1, 2, 3, 4]


class TestSortRegionsWithPanels:
    def test_sort_regions_prioritizes_panel_order(self):
        # Panel 1: Top-Right ([500, 0, 1000, 500])
        # Panel 2: Top-Left ([0, 0, 500, 500])
        panel1 = PanelDetection(xyxy=[500, 0, 1000, 500], polygons=[], confidence=0.9, order_index=1)
        panel2 = PanelDetection(xyxy=[0, 0, 500, 500], polygons=[], confidence=0.9, order_index=2)

        # Block 1 is inside Panel 2 (Top-Left)
        # Block 2 is inside Panel 1 (Top-Right)
        # In RTL manga, Panel 1 is read before Panel 2, so Block 2 should be sorted before Block 1!
        block_left = TextBlock(
            lines=[[[100, 100], [300, 100], [300, 200], [100, 200]]],
            texts=["Left panel text"],
        )
        block_right = TextBlock(
            lines=[[[600, 100], [800, 100], [800, 200], [600, 200]]],
            texts=["Right panel text"],
        )

        sorted_blocks = sort_regions(
            [block_left, block_right],
            right_to_left=True,
            panel_detections=[panel1, panel2],
        )

        assert len(sorted_blocks) == 2
        assert sorted_blocks[0].text == "Right panel text"
        assert sorted_blocks[1].text == "Left panel text"

    def test_sort_regions_assigns_to_smallest_enclosing_panel(self):
        # Macro container panel (order 1)
        macro_panel = PanelDetection(xyxy=[0, 0, 1000, 1000], polygons=[], confidence=0.95, order_index=1)
        # Nested smaller inset panel (order 2)
        inset_panel = PanelDetection(xyxy=[100, 100, 400, 400], polygons=[], confidence=0.90, order_index=2)

        # Block inside inset panel (center 200, 200)
        block_inset = TextBlock(
            lines=[[[150, 150], [250, 150], [250, 250], [150, 250]]],
            texts=["Inset text"],
        )
        # Block in macro panel outside inset (center 700, 700)
        block_macro = TextBlock(
            lines=[[[650, 650], [750, 650], [750, 750], [650, 750]]],
            texts=["Macro text"],
        )

        sort_regions([block_inset, block_macro], right_to_left=True, panel_detections=[macro_panel, inset_panel])

        # block_inset should be assigned to inset_panel (index 1), not macro_panel (index 0)
        assert block_inset.panel_index == 1
        assert block_macro.panel_index == 0


class TestInferPanelConstraintsWithPanels:
    def test_infer_panel_constraints_uses_panel_detections(self):
        panels = [
            PanelDetection(xyxy=[500, 0, 1000, 500], polygons=[], confidence=0.95, order_index=1),
            PanelDetection(xyxy=[0, 0, 500, 500], polygons=[], confidence=0.90, order_index=2),
        ]
        # Text block inside Panel 1
        block = TextBlock(
            lines=[[[550, 50], [750, 50], [750, 150], [550, 150]]],
            texts=["Sample text"],
        )
        img = np.zeros((1000, 1000, 3), dtype=np.uint8)

        constraints = infer_panel_constraints(
            img,
            [block],
            panel_detections=panels,
        )

        assert constraints is not None
        assert id(block) in constraints
        c = constraints[id(block)]
        assert c.source == "ml"
        assert c.bounds == (500, 0, 1000, 500)
        assert c.confidence == pytest.approx(0.95)

    def test_panel_match_requires_confidence_and_substantial_source_coverage(self):
        source = np.zeros((100, 100), dtype=np.uint8)
        source[20:80, 20:80] = 1
        low_confidence = PanelDetection([0, 0, 100, 100], [], confidence=0.4)
        partial = PanelDetection([20, 20, 80, 40], [], confidence=0.95)

        assert match_panel_to_source(source, [low_confidence]) is None
        assert match_panel_to_source(source, [partial]) is None


class TestBubbleDetectorJointParsing:
    def test_read_result_separates_panels_and_bubbles(self):
        detector = object.__new__(BubbleDetector)
        detector.is_multiclass = True
        detector.class_map = {0: "panel", 1: "balloon", 2: "text"}

        mock_result = MagicMock()
        mask_panel = torch.ones((200, 200), dtype=torch.float32)
        mask_bubble = torch.ones((200, 200), dtype=torch.float32)
        mock_result.masks.data = [mask_panel, mask_bubble]
        mock_result.masks.__len__.return_value = 2

        box_panel = MagicMock()
        box_panel.cls = torch.tensor([0])
        box_panel.xyxy = torch.tensor([[10, 10, 190, 190]])

        box_bubble = MagicMock()
        box_bubble.cls = torch.tensor([1])
        box_bubble.xyxy = torch.tensor([[20, 20, 80, 80]])

        mock_boxes = MagicMock()
        mock_boxes.__iter__.side_effect = lambda: iter([box_panel, box_bubble])
        mock_boxes.conf = torch.tensor([0.95, 0.88])
        mock_result.boxes = mock_boxes

        mock_result.masks.xy = [
            np.array([[10, 10], [190, 10], [190, 190], [10, 190]]),
            np.array([[20, 20], [80, 20], [80, 80], [20, 80]]),
        ]

        bubbles, panels = detector._read_result(mock_result, (200, 200), confidence_threshold=0.5, mask_threshold=0.5)

        assert len(bubbles) == 1
        assert isinstance(bubbles[0], BubbleDetection)
        assert bubbles[0].confidence == pytest.approx(0.88)

        assert len(panels) == 1
        assert isinstance(panels[0], PanelDetection)
        assert panels[0].confidence == pytest.approx(0.95)
        assert panels[0].order_index == 1
        assert panels[0].xyxy == [10, 10, 190, 190]

    @pytest.mark.parametrize("class_map,single_class,expected", [
        ({0: "balloon", 1: "panel"}, False, 0),
        ({0: "balloon"}, True, 1),
    ])
    def test_unknown_detector_class_is_only_a_bubble_for_known_single_class_model(
        self, class_map, single_class, expected,
    ):
        detector = object.__new__(BubbleDetector)
        detector.class_map = class_map
        detector.single_class_bubble_model = single_class
        result = MagicMock()
        result.masks.data = [torch.ones((40, 40), dtype=torch.float32)]
        result.masks.__len__.return_value = 1
        box = MagicMock()
        box.cls = torch.tensor([9])
        box.xyxy = torch.tensor([[2, 2, 38, 38]])
        result.boxes.__iter__.side_effect = lambda: iter([box])
        result.boxes.conf = torch.tensor([0.95])
        result.masks.xy = [np.array([[2, 2], [38, 2], [38, 38], [2, 38]])]

        bubbles, panels = detector._read_result(result, (40, 40), 0.5, 0.5)

        assert len(bubbles) == expected
        assert panels == []


class TestBubbleDetectionStageBatch:
    @pytest.mark.asyncio
    async def test_run_bubble_detection_batch_returns_bubbles_and_panels(self):
        from manga_translator.bubble_detection_stage import run_bubble_detection_batch
        from manga_translator.config import Config
        from manga_translator.utils import Context

        cfg = Config()
        ctx1 = Context(img_rgb=np.zeros((100, 100, 3), dtype=np.uint8))
        ctx2 = Context(img_rgb=np.zeros((100, 100, 3), dtype=np.uint8))

        bubble1 = BubbleDetection(mask=np.zeros((100, 100), dtype=bool), confidence=0.9)
        panel1 = PanelDetection(xyxy=[0, 0, 100, 100], polygons=[], confidence=0.95, order_index=1)

        async def fake_dispatch(images, b_cfg, device):
            return [([bubble1], [panel1]), ([], [])]

        owner = MagicMock()
        owner.device = "cpu"
        owner._mps_call.side_effect = lambda fn, *args: fn(*args)

        results = await run_bubble_detection_batch(owner, [cfg, cfg], [ctx1, ctx2], dispatch_batch=fake_dispatch)

        assert len(results) == 2
        assert results[0] == ([bubble1], [panel1])
        assert ctx1.bubble_detections == [bubble1]
        assert ctx1.panel_detections == [panel1]
        assert ctx1.bubble_detection_state is BubbleDetectionState.COMPLETED_WITH_RESULTS
        assert results[1] == ([], [])
        assert ctx2.bubble_detections == []
        assert ctx2.panel_detections == []
        assert ctx2.bubble_detection_state is BubbleDetectionState.COMPLETED_EMPTY

    @pytest.mark.asyncio
    async def test_run_bubble_detection_precomputed_panels(self):
        from manga_translator.bubble_detection_stage import run_bubble_detection
        from manga_translator.config import Config
        from manga_translator.utils import Context

        cfg = Config()
        ctx = Context(img_rgb=np.zeros((100, 100, 3), dtype=np.uint8))
        panel = PanelDetection(xyxy=[0, 0, 100, 100], polygons=[], confidence=0.95, order_index=1)
        owner = MagicMock()
        owner._pipeline_run = None

        await run_bubble_detection(
            owner,
            cfg,
            ctx,
            report_progress=False,
            precomputed_detections=[],
            precomputed_panels=[panel],
            detect_bubbles=MagicMock(),
            dispatch_detection=MagicMock(),
            group_regions_by_bubbles=lambda regions, bubbles, **kw: regions,
            logger=MagicMock(),
        )

        assert ctx.panel_detections == [panel]
        assert ctx.bubble_detections == []
        assert ctx.bubble_detection_state is BubbleDetectionState.COMPLETED_EMPTY

    @pytest.mark.asyncio
    async def test_run_bubble_detection_marks_failure_for_geometry_recovery(self):
        from manga_translator.bubble_detection_stage import run_bubble_detection
        from manga_translator.config import Config
        from manga_translator.utils import Context

        cfg = Config()
        ctx = Context(img_rgb=np.zeros((100, 100, 3), dtype=np.uint8))
        owner = MagicMock()
        owner._pipeline_run = None
        owner._current_image_context = None
        detector = MagicMock(side_effect=RuntimeError("model unavailable"))
        log = MagicMock()

        await run_bubble_detection(
            owner,
            cfg,
            ctx,
            report_progress=False,
            detect_bubbles=detector,
            dispatch_detection=MagicMock(),
            group_regions_by_bubbles=lambda regions, _bubbles, **_kwargs: regions,
            logger=log,
        )

        assert ctx.bubble_detection_state is BubbleDetectionState.FAILED
        assert ctx.bubble_detections == []
