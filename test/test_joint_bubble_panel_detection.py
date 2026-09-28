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
from manga_translator.detection.bubble import BubbleDetection, BubbleDetector
from manga_translator.geometry.panels import infer_panel_constraints
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
