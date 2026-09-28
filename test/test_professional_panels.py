import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from manga_translator.professional_panels import (
    assign_regions_to_panels,
    build_page_panel_structure,
    format_page_transcript_for_analysis,
    format_synopsis_transcript,
    group_transcript_panel_texts,
    read_page_panels,
    read_page_regions,
)


class TestProfessionalPanels(unittest.TestCase):
    def test_assign_regions_to_panels_by_explicit_index(self):
        panels = [{"xyxy": [0, 0, 100, 100]}, {"xyxy": [100, 100, 200, 200]}]
        regions = [
            {"id": "r1", "panel_index": 1, "text": "hello"},
            {"id": "r2", "panel_index": 0, "text": "world"},
            {"id": "r3", "panel_index": 99, "text": "invalid index"},
        ]
        grouped, unassigned = assign_regions_to_panels(regions, panels)
        self.assertEqual([r["id"] for r in grouped[0]], ["r2"])
        self.assertEqual([r["id"] for r in grouped[1]], ["r1"])
        self.assertEqual([r["id"] for r in unassigned], ["r3"])

    def test_assign_regions_to_panels_by_geometric_containment(self):
        panels = [
            {"xyxy": [0, 0, 100, 100]},
            {"xyxy": [0, 100, 100, 200]},
        ]
        regions = [
            {"id": "r1", "xywh": [10, 10, 20, 20]},   # Center (20, 20) -> Panel 0
            {"id": "r2", "xyxy": [10, 110, 30, 150]}, # Center (20, 130) -> Panel 1
            {"id": "r3", "center": (500, 500)},        # Outside -> Unassigned
            {"id": "r4"},                              # No coordinates -> Unassigned
        ]
        grouped, unassigned = assign_regions_to_panels(regions, panels)
        self.assertEqual([r["id"] for r in grouped[0]], ["r1"])
        self.assertEqual([r["id"] for r in grouped[1]], ["r2"])
        self.assertEqual([r["id"] for r in unassigned], ["r3", "r4"])

    def test_build_page_panel_structure_without_panels(self):
        regions = [{"id": "r1", "source": "こんにちは"}]
        res = build_page_panel_structure(1, regions, panels=None)
        self.assertEqual(res["page"], 1)
        self.assertNotIn("panels", res)
        self.assertEqual(len(res["regions"]), 1)
        self.assertEqual(res["regions"][0]["id"], "r1")
        self.assertEqual(res["regions"][0]["japanese"], "こんにちは")

    def test_build_page_panel_structure_with_panels_and_draft(self):
        panels = [{"xyxy": [0, 0, 100, 100], "order": 1}]
        regions = [
            {"id": "r1", "panel_index": 0, "source": "テスト", "draft": "Test"},
            {"id": "r2", "source": "外", "draft": "Outside"},
        ]
        res = build_page_panel_structure(2, regions, panels=panels, include_draft=True)
        self.assertEqual(res["page"], 2)
        self.assertEqual(len(res["panels"]), 1)
        self.assertEqual(res["panels"][0]["panel_id"], "p2_01")
        self.assertEqual(res["panels"][0]["regions"][0]["draft"], "Test")
        self.assertEqual(len(res["unassigned_regions"]), 1)
        self.assertEqual(res["unassigned_regions"][0]["draft"], "Outside")

    def test_format_page_transcript_for_analysis_with_panels(self):
        page_data = {
            "page": 3,
            "panels": [
                {
                    "panel_id": "p3_01",
                    "panel_order": 1,
                    "regions": [{"id": "r1", "japanese": "おはよう"}],
                }
            ],
            "unassigned_regions": [{"id": "r2", "japanese": "ナレーション"}],
        }
        transcript = format_page_transcript_for_analysis(page_data)
        self.assertIn("[PAGE 3]", transcript)
        self.assertIn("[PANEL p3_01 | PANEL ORDER 1]", transcript)
        self.assertIn("[r1 | estimated order 1]\nおはよう", transcript)
        self.assertIn("[UNASSIGNED REGIONS]", transcript)
        self.assertIn("[r2 | estimated order 1]\nナレーション", transcript)

    def test_format_page_transcript_for_analysis_without_panels(self):
        page_data = {
            "page": 4,
            "regions": [
                {"id": "r1", "japanese": "おはよう"},
                {"id": "r2", "japanese": "さようなら"},
            ],
        }
        transcript = format_page_transcript_for_analysis(page_data)
        self.assertEqual(
            transcript,
            "[PAGE 4]\n[r1 | estimated order 1]\nおはよう\n[r2 | estimated order 2]\nさようなら",
        )
        self.assertNotIn("[PANEL", transcript)
        self.assertNotIn("[UNASSIGNED", transcript)

    def test_group_transcript_panel_texts(self):
        panels = [{"xyxy": [0, 0, 100, 100], "order": 1}]
        regions = [
            {"panel_index": 0, "original_text": "セリフ1"},
            {"original_text": "セリフ2"},
        ]
        groups = group_transcript_panel_texts(regions, panels)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["panel_id"], 1)
        self.assertEqual(groups[0]["texts"], ["セリフ1"])
        self.assertEqual(groups[1]["panel_id"], "unassigned")
        self.assertEqual(groups[1]["texts"], ["セリフ2"])

        self.assertIsNone(group_transcript_panel_texts([], panels))
        self.assertIsNone(group_transcript_panel_texts(regions, None))

    def test_format_synopsis_transcript(self):
        snapshot = {
            "entries": [
                {
                    "name": "001.jpg",
                    "panel_groups": [
                        {"panel_id": 1, "texts": ["Line 1", "Line 2"]},
                        {"panel_id": "unassigned", "texts": ["Narration"]},
                    ],
                },
                {
                    "name": "002.jpg",
                    "texts": ["Fallback plain text"],
                },
            ]
        }
        pages = format_synopsis_transcript(snapshot)
        self.assertEqual(len(pages), 2)
        self.assertIn("[Page 001.jpg]", pages[0])
        self.assertIn("[PANEL 1]\nLine 1\nLine 2", pages[0])
        self.assertIn("[UNASSIGNED]\nNarration", pages[0])
        self.assertIn("[Page 002.jpg]\nFallback plain text", pages[1])

    def test_read_page_regions_and_panels_from_disk(self):
        with TemporaryDirectory() as tmp_dir:
            page_dir = Path(tmp_dir)
            (page_dir / "text_regions.json").write_text('[{"text": "disk text"}]', encoding="utf-8")
            (page_dir / "panel_detections.json").write_text('[{"xyxy": [0, 0, 10, 10]}]', encoding="utf-8")

            page = {"path": str(page_dir)}
            regions = read_page_regions(page)
            panels = read_page_panels(page)
            self.assertEqual(regions, [{"text": "disk text"}])
            self.assertEqual(panels, [{"xyxy": [0, 0, 10, 10]}])

            # In-memory overrides
            page_mem = {"textRegions": [{"text": "mem text"}], "panels": [{"xyxy": [1, 1, 2, 2]}]}
            self.assertEqual(read_page_regions(page_mem), [{"text": "mem text"}])
            self.assertEqual(read_page_panels(page_mem), [{"xyxy": [1, 1, 2, 2]}])


if __name__ == "__main__":
    unittest.main()
