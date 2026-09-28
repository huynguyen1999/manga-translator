from types import SimpleNamespace

import numpy as np

from manga_translator.rendering.layout import solve_core
from manga_translator.rendering.layout.geometry import BubbleGeometry
from manga_translator.rendering.layout.models import BandSlot, PlacedLine
from manga_translator.rendering.layout.profiling import SolverProfileStats
from manga_translator.rendering.layout.readable_text import centered_candidate_key


def _lines(marker, x, y, count=1):
    return [
        PlacedLine(
            marker if index == 0 else f"{marker}-{index}",
            y + index * 8,
            x + index,
            6,
            4,
            BandSlot(x - 1 + index, x + 7 + index, y + index * 8 - 1, y + index * 8 + 5),
        )
        for index in range(count)
    ]


def _install_search(monkeypatch, placements, center_errors, penalties, gaps=()):
    geom = BubbleGeometry(np.pad(np.ones((50, 60), np.uint8), ((20, 10), (10, 30))))
    validations = []
    validator_results = {}
    profile = SolverProfileStats()

    monkeypatch.setattr(solve_core, "get_solver_profile", lambda: profile)
    monkeypatch.setattr(solve_core, "_precompute_widths", lambda *_: ([1], 1))
    monkeypatch.setattr(solve_core, "_cached_row_slot_table", lambda *_a, **_k: [object()])
    monkeypatch.setattr(
        solve_core,
        "_cached_placement_target",
        lambda *_a, **_k: SimpleNamespace(center_x=20),
    )
    monkeypatch.setattr(
        solve_core,
        "_cached_zone_shape_profile",
        lambda *_a, **_k: SimpleNamespace(
            width=60, height=50, aspect_ratio=1.2, vertical_capacity=4,
        ),
    )
    monkeypatch.setattr(solve_core, "_y_origin_sequence", lambda *_: [5])
    monkeypatch.setattr(solve_core, "_try_placement_rows", lambda *_a, **_k: placements)
    monkeypatch.setattr(solve_core, "_compact_vertical_rhythm", lambda lines, *_a, **_k: lines)
    monkeypatch.setattr(
        solve_core,
        "_classify_adjacent_gaps",
        lambda lines, *_a, **_k: (
            [{"valid": False, "reason": "unexplained_gap", "gap_h": 2}]
            if lines[0].text in gaps else []
        ),
    )
    monkeypatch.setattr(solve_core, "_center_layout_block", lambda lines, *_: lines)
    monkeypatch.setattr(solve_core, "_optimize_x", lambda lines, **_k: lines)
    monkeypatch.setattr(solve_core, "_bbox_validate", lambda *_: (True, 1.0))
    monkeypatch.setattr(
        solve_core,
        "_composite_penalty",
        lambda lines, *_a, **_k: (
            penalties[lines[0].text],
            {"center_error_px": center_errors[lines[0].text]},
        ),
    )

    def validate(lines, *_args):
        marker = lines[0].text
        validations.append(marker)
        return validator_results.get(marker, (True, 0.5))

    monkeypatch.setattr(solve_core, "_validate_glyph_pixels", validate)
    return geom, validations, validator_results, profile


def test_glyph_validation_stops_at_top_k_in_final_candidate_order(monkeypatch):
    placements = [
        _lines("A", 1, 3),
        _lines("B", 2, 5),
        _lines("C", 3, 1, count=2),
        _lines("D", 5, 2, count=2),
    ]
    geom, validations, results, profile = _install_search(
        monkeypatch,
        placements,
        {"A": 1, "B": 7, "C": 2, "D": 3},
        {"A": 2, "B": 0, "C": 1, "D": 3},
    )
    results.update({"C": (False, 0.0), "A": (True, 0.5), "D": (True, 0.5)})

    candidates = solve_core.solve_layout(geom, ["x"], 10, 10, top_k=2)

    assert validations == ["C", "A", "D"]
    assert [candidate.lines[0].text for candidate in candidates] == ["A", "D"]
    assert [centered_candidate_key(candidate) for candidate in candidates] == sorted(
        centered_candidate_key(candidate) for candidate in candidates
    )
    assert profile.glyph_validations == 3
    assert (placements[0][0].x, placements[0][0].y) == (11, 23)
    assert placements[0][0].slot == BandSlot(10, 18, 22, 28)
    assert (placements[2][0].x, placements[2][0].y) == (3, 1)
    assert (placements[3][0].x, placements[3][0].y) == (15, 22)


def test_top_k_matches_exhaustive_validation_reference(monkeypatch):
    def run(top_k):
        placements = [
            _lines("A", 1, 3),
            _lines("B", 2, 5),
            _lines("C", 3, 1, count=2),
            _lines("D", 5, 2, count=2),
        ]
        geom, validations, results, profile = _install_search(
            monkeypatch,
            placements,
            {"A": 1, "B": 7, "C": 2, "D": 3},
            {"A": 2, "B": 0, "C": 1, "D": 3},
        )
        results.update({"C": (False, 0.0), "A": (True, 0.5), "D": (True, 0.5)})
        candidates = solve_core.solve_layout(geom, ["x"], 10, 10, top_k=top_k)
        return candidates, validations, profile

    exhaustive, exhaustive_validations, _ = run(8)
    top_two, top_two_validations, profile = run(2)
    signature = lambda candidate: (
        candidate.font_size,
        tuple((line.text, line.x, line.y, line.width, line.height, line.slot) for line in candidate.lines),
        candidate.penalty,
        candidate.valid,
        candidate.status,
        candidate.qa,
    )

    assert exhaustive_validations == ["C", "A", "D", "B"]
    assert [candidate.lines[0].text for candidate in exhaustive] == ["A", "D", "B"]
    assert top_two_validations == ["C", "A", "D"]
    assert [signature(candidate) for candidate in top_two] == [
        signature(candidate) for candidate in exhaustive[:2]
    ]
    assert profile.glyph_validations == 3


def test_gap_fallback_skips_glyph_overflow_and_offsets_once(monkeypatch):
    placements = [
        _lines("A", 1, 3),
        _lines("B", 2, 5),
        _lines("C", 3, 1, count=2),
        _lines("D", 5, 2, count=2),
    ]
    geom, validations, results, profile = _install_search(
        monkeypatch,
        placements,
        {"A": 1, "B": 2, "C": 3, "D": 8},
        {"A": 0, "B": 1, "C": 2, "D": 0},
        gaps={"B", "C", "D"},
    )
    results.update({"A": (False, 0.0), "B": (False, 0.0), "C": (True, 0.5)})

    candidate = solve_core.solve_layout(geom, ["x"], 10, 10)

    assert validations == ["A", "B", "C"]
    assert profile.glyph_validations == 3
    assert candidate.lines[0].text == "C"
    assert candidate.status == "unexplained_gap"
    assert candidate.valid is True
    assert (placements[2][0].x, placements[2][0].y) == (13, 21)
    assert placements[2][0].slot == BandSlot(12, 20, 20, 26)
    assert (placements[0][0].x, placements[0][0].y) == (1, 3)
