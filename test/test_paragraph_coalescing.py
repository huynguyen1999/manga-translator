import cv2
import numpy as np

from manga_translator.rendering.paragraph_coalescing import coalesce_free_text_regions
from manga_translator.utils import TextBlock


def _region(bounds, text, region_id, direction="h"):
    x1, y1, x2, y2 = bounds
    return TextBlock(
        lines=[[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]],
        texts=[text],
        font_size=14,
        direction=direction,
        region_id=region_id,
        source_region_ids=[region_id],
    )


def _panel_page():
    image = np.full((300, 300, 3), 255, dtype=np.uint8)
    cv2.line(image, (150, 0), (150, 299), (0, 0, 0), 3)
    cv2.line(image, (0, 150), (299, 150), (0, 0, 0), 3)
    return image


def test_coalesces_aligned_adjacent_free_text_and_preserves_sources():
    image = _panel_page()
    first = _region((20, 20, 72, 34), "first line", "source-a")
    second = _region((20, 36, 78, 50), "second line", "source-b")
    first.text_raw = "raw first"
    second.text_raw = "raw second"

    result = coalesce_free_text_regions([first, second], image)

    assert len(result) == 1
    assert result[0].text == "first line\nsecond line"
    assert result[0].text_raw == "raw first\nraw second"
    assert result[0].texts == ["first line", "second line"]
    assert np.array_equal(result[0].lines, np.concatenate([first.lines, second.lines]))
    assert result[0].source_region_ids == ["source-a", "source-b"]
    assert [source["id"] for source in result[0].source_regions] == ["source-a", "source-b"]


def test_intervening_text_and_bubble_regions_are_barriers():
    image = _panel_page()
    first = _region((20, 20, 72, 34), "first", "source-a")
    text_barrier = _region((20, 36, 78, 50), "barrier", "source-barrier")
    text_barrier.font_size = 40
    last = _region((20, 52, 80, 66), "last", "source-c")

    assert coalesce_free_text_regions([first, text_barrier, last], image) == [
        first, text_barrier, last,
    ]

    text_barrier.font_size = 14
    text_barrier.bubble_id = "bubble-0"
    text_barrier._bubble_mask = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.fillPoly(text_barrier._bubble_mask, [text_barrier.lines[0]], 1)
    assert coalesce_free_text_regions([first, text_barrier, last], image) == [
        first, text_barrier, last,
    ]

    orientation_barrier = _region((20, 36, 78, 50), "vertical", "source-v", direction="v")
    assert coalesce_free_text_regions([first, orientation_barrier, last], image) == [
        first, orientation_barrier, last,
    ]


def test_page_fallback_and_different_panels_never_count_as_panel_evidence():
    first = _region((20, 20, 72, 34), "first", "source-a")
    second = _region((20, 36, 78, 50), "second", "source-b")

    assert coalesce_free_text_regions([first, second], np.full((300, 300, 3), 255, np.uint8)) == [
        first, second,
    ]

    image = _panel_page()
    first = _region((20, 20, 72, 34), "first", "source-a")
    second = _region((170, 36, 228, 50), "second", "source-b")
    assert coalesce_free_text_regions([first, second], image) == [first, second]


def test_vertical_columns_follow_japanese_right_to_left_order():
    image = _panel_page()
    right = _region((70, 20, 84, 75), "right", "source-right", direction="v")
    left = _region((52, 20, 66, 75), "left", "source-left", direction="v")

    merged = coalesce_free_text_regions([left, right], image)

    assert len(merged) == 1
    assert merged[0].text == "right\nleft"
    assert merged[0].source_region_ids == ["source-right", "source-left"]


def test_coalesces_nested_duplicate_free_text_regions_into_one():
    image = np.full((300, 300, 3), 255, dtype=np.uint8)
    outer = _region((0, 0, 272, 283), "うああっ♡あハァっ！チンコぉッ奥くるぅっ", "d0f20e38", direction="v")
    outer.font_size = 272
    inner_a = _region((22, 29, 165, 209), "あハァっチンコぉ奥くるら", "976202b1", direction="v")
    inner_a.font_size = 143
    inner_b = _region((43, 52, 179, 187), "あハァ奥くる", "6e7a1a32", direction="v")
    inner_b.font_size = 56
    inner_c = _region((55, 214, 142, 258), "っッ", "2477b831", direction="h")
    inner_c.font_size = 42

    merged = coalesce_free_text_regions([outer, inner_a, inner_b, inner_c], image)

    assert len(merged) == 1
    assert merged[0].text == "うああっ♡あハァっ！チンコぉッ奥くるぅっ"
    assert merged[0].source_region_ids == ["d0f20e38", "976202b1", "6e7a1a32", "2477b831"]
    assert merged[0].font_size == outer.font_size
    assert np.array_equal(merged[0].lines, outer.lines)


def test_reported_nested_cluster_keeps_dominant_ocr_only():
    outer = _region(
        (0, 0, 295, 305),
        "うああっ♡あハァっ！チンコぉッ奥くるぅっ！",
        "8c592ca2b5bd46a2a3787bcf8579e7c1",
        direction="v",
    )
    outer.font_size = 295
    inner = _region(
        (9, 16, 179, 223),
        "あハァっチンコぉ奥くるぅ",
        "ff3d867de8e44fc5801a155c6c037f28",
        direction="v",
    )
    inner.font_size = 170
    inner_fragment = _region(
        (36, 44, 187, 195),
        "あハァ奥ぐる",
        "019ad5291a604bb3add642103c5418f7",
    )
    inner_fragment.font_size = 71
    false_fragment = _region(
        (45, 200, 186, 269),
        "ファッ！？",
        "1694320318214a7fbe231df09c5dcb34",
    )
    false_fragment.font_size = 69

    merged = coalesce_free_text_regions([outer, inner_fragment, inner, false_fragment], None)

    assert len(merged) == 1
    assert merged[0].region_id == outer.region_id
    assert merged[0].text == outer.text
    assert merged[0].texts == [outer.text]
    assert merged[0].source_region_ids == [
        "8c592ca2b5bd46a2a3787bcf8579e7c1",
        "019ad5291a604bb3add642103c5418f7",
        "ff3d867de8e44fc5801a155c6c037f28",
        "1694320318214a7fbe231df09c5dcb34",
    ]
    assert merged[0].font_size == outer.font_size
    assert [source["id"] for source in merged[0].source_regions] == [outer.region_id]


def test_reported_nested_cluster_uses_dominant_owner_when_it_is_last():
    small_regions = [
        _region((783, 429, 841, 486), "ん", "0af66da2a775444f90ca793c802d9d2f"),
        _region((751, 426, 814, 490), "ぐ", "c05628e6747047428425522a7d46e8fb", direction="v"),
        _region((719, 427, 787, 497), "いっ", "80f5dc7468b14675a641d265f1fc79cf", direction="v"),
        _region((719, 447, 847, 533), "い断るんない男ない", "4d444322a4284893933433b3734db81b"),
    ]
    for region, font_size in zip(small_regions, (40, 43, 45, 82)):
        region.font_size = font_size
    outer = _region(
        (684, 442, 882, 674),
        "そんなん断る男とかいないだろ．．．",
        "d61131279c0740789af1ba4cea8a9455",
        direction="v",
    )
    outer.font_size = 195

    merged = coalesce_free_text_regions([*small_regions, outer], None)

    assert [region.region_id for region in merged] == [
        "0af66da2a775444f90ca793c802d9d2f",
        "c05628e6747047428425522a7d46e8fb",
        "80f5dc7468b14675a641d265f1fc79cf",
        "d61131279c0740789af1ba4cea8a9455",
    ]
    assert merged[-1].text == outer.text
    assert merged[-1].source_region_ids == [
        "d61131279c0740789af1ba4cea8a9455",
        "4d444322a4284893933433b3734db81b",
    ]


def test_transitive_overlap_without_one_dominant_owner_stays_separate():
    first = _region((0, 0, 100, 100), "longest primary text", "first")
    middle = _region((25, 0, 125, 100), "middle", "middle")
    last = _region((50, 0, 150, 100), "last", "last")

    assert coalesce_free_text_regions([first, middle, last], None) == [first, middle, last]


def test_saved_page_overlap_keeps_only_largest_original_region():
    outer = _region((0, 0, 272, 282), "full OCR", "4cbea48839c34896991ccdb17281e406")
    fragment = _region((41, 50, 180, 188), "partial", "65ce0b06dfec47208d0ebdcea253ec56")
    lower = _region((50, 205, 181, 264), "small", "6cf0ce631c8a42979f4c1bc6f48abc62")
    tilted = _region((10, 24, 170, 217), "longer partial OCR", "0eae381f5d52405799ffd6f7f0772790")
    tilted.lines = np.array([[[10, 37], [154, 24], [170, 204], [26, 217]]])

    result = coalesce_free_text_regions([fragment, lower, tilted, outer], None)

    assert len(result) == 1
    winner = result[0]
    assert winner.region_id == outer.region_id
    assert winner.text == outer.text
    assert np.array_equal(winner.lines, outer.lines)
    assert winner.source_region_ids == [outer.region_id, fragment.region_id, lower.region_id, tilted.region_id]
    assert [source["id"] for source in winner.source_regions] == [outer.region_id]


def test_direct_polygon_coverage_threshold_and_bubble_exclusion():
    large = _region((0, 0, 100, 100), "large", "large")
    at_threshold = _region((10, 0, 110, 100), "exactly 90% covered", "threshold")
    below_threshold = _region((11, 0, 111, 100), "89% covered", "below")
    bubble = _region((10, 0, 110, 100), "bubble", "bubble")
    bubble.bubble_id = "bubble-0"

    result = coalesce_free_text_regions([large, at_threshold, below_threshold, bubble], None)

    assert [region.region_id for region in result] == ["large", "below", "bubble"]
    assert result[0].source_region_ids == ["large", "threshold"]


def test_overlap_chain_does_not_suppress_without_direct_winner_coverage():
    first = _region((0, 0, 100, 100), "first", "first")
    second = _region((10, 0, 110, 100), "second", "second")
    third = _region((20, 0, 120, 100), "third", "third")

    result = coalesce_free_text_regions([first, second, third], None)

    assert [region.region_id for region in result] == ["first", "third"]
    assert result[0].source_region_ids == ["first", "second"]
