from devscripts.layout_page_benchmark import _compare_snapshots, _summary


def test_compare_snapshots_reports_font_wrap_and_positions():
    baseline = {"regions": [{
        "region_id": "r1", "font_size": 14, "layout_bounds": [1, 2, 30, 40],
        "segments": [{"lines": [{"text": "HELLO", "x": 3, "y": 4, "width": 20, "height": 10}]}],
    }]}
    actual = {"regions": [{
        "region_id": "r1", "font_size": 12, "layout_bounds": [2, 2, 30, 40],
        "segments": [{"lines": [
            {"text": "HEL", "x": 4, "y": 4, "width": 12, "height": 9},
            {"text": "LO", "x": 4, "y": 14, "width": 8, "height": 9},
        ]}],
    }]}

    report = _compare_snapshots(actual, baseline)

    assert report["status"] == "compared"
    assert report["added"] == report["removed"] == []
    changes = report["changed"][0]["changes"]
    assert changes["font_size"] == {"baseline": 14, "current": 12}
    assert "wrapping" in changes
    assert "line_positions" in changes


def test_compare_snapshots_reports_added_and_removed_regions():
    report = _compare_snapshots(
        {"regions": [{"region_id": "new"}]},
        {"regions": [{"region_id": "gone"}]},
    )

    assert report == {
        "status": "compared", "changed": [], "added": ["new"], "removed": ["gone"],
    }


def test_summary_uses_per_page_medians_across_measured_repeats():
    runs = [{
        "folder": "page", "page_id": "id", "mode": "background_cpu_stage", "phase": "measured",
        "caller_wall_ms": wall, "layout_execution_ms": wall - queue, "queue_wait_ms": queue,
        "mask_matches_baseline": True, "comparison": {"changed": [{}], "added": [], "removed": []},
    } for wall, queue in ((10, 4), (30, 8), (20, 6))]

    page = _summary(runs)["by_page"]["page"]["modes"]["background_cpu_stage"]

    assert page["runs"] == 3
    assert page["median_caller_wall_ms"] == 20
    assert page["median_layout_execution_ms"] == 14
    assert page["median_queue_wait_ms"] == 6
    assert page["median_changed_regions"] == 1
