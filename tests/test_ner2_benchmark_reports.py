from __future__ import annotations

import pytest

from scripts.benchmark_gliner import (
    _assert_lockbox_allowed,
    _percentile,
    build_density_report,
    build_parser,
    build_runtime_report,
)


def test_ner1_cli_defaults_are_preserved_and_new_modes_are_opt_in() -> None:
    args = build_parser().parse_args([])
    assert args.config == "configs/gliner_zero_shot.yaml"
    assert args.split == "development"
    assert args.output_dir == "outputs/predictions/v2_ner1_gliner_reproduction"
    assert args.report_dir == "outputs/reports/v2_ner1_gliner_reproduction"
    assert args.allow_lockbox is False
    assert args.end_to_end is False


def test_lockbox_and_all_require_explicit_guard() -> None:
    _assert_lockbox_allowed("development", False)
    _assert_lockbox_allowed("lockbox", True)
    with pytest.raises(PermissionError, match="--allow-lockbox"):
        _assert_lockbox_allowed("lockbox", False)
    with pytest.raises(PermissionError, match="--allow-lockbox"):
        _assert_lockbox_allowed("all", False)


def test_density_has_distribution_per_type_ratio_and_outliers() -> None:
    predictions = {
        "1": [{"type": "A"}],
        "2": [{"type": "A"}, {"type": "B"}],
        "3": [{"type": "B"}] * 10,
    }
    golds = {
        "1": [{"type": "A"}],
        "2": [{"type": "B"}],
        "3": [{"type": "B"}, {"type": "B"}],
    }
    report = build_density_report(predictions, golds, ids=["1", "2", "3"])
    assert report["predicted"]["mean"] == pytest.approx(13 / 3)
    assert report["predicted"]["median"] == 2.0
    assert report["predicted"]["p95"] == pytest.approx(9.2)
    assert report["pred_gold_ratio"] == pytest.approx(13 / 4)
    assert report["per_type"]["A"]["pred_gold_ratio"] == 2.0
    assert report["per_type"]["B"]["predicted"]["p95"] == pytest.approx(9.1)
    assert [item["file_id"] for item in report["outliers"]] == ["3"]


def test_density_zero_gold_ratio_is_json_safe() -> None:
    report = build_density_report({"1": [{"type": "A"}]}, {"1": []})
    assert report["pred_gold_ratio"] is None
    assert report["per_type"]["A"]["pred_gold_ratio"] is None
    assert _percentile([], 95) == 0.0


def test_runtime_keeps_ner1_fields_and_adds_cold_warm_memory(monkeypatch) -> None:
    monkeypatch.setattr("scripts.benchmark_gliner._process_memory", lambda: {
        "process_rss_bytes": 100, "peak_process_rss_bytes": 120,
    })
    per_file = [
        {"file_id": "1", "seconds": 4.0, "entities": 2, "counters": {}},
        {"file_id": "2", "seconds": 2.0, "entities": 1, "counters": {}},
        {"file_id": "3", "seconds": 1.0, "entities": 0, "counters": {}},
    ]
    report = build_runtime_report(
        per_file, startup_seconds=3.0, total_seconds=10.0,
        peak_python_memory_bytes=99, entities_by_type={"A": 3},
    )
    assert report["total_seconds"] == 10.0
    assert report["peak_python_memory_bytes"] == 99
    assert report["per_file"] == per_file
    assert report["entities_by_type"] == {"A": 3}
    assert report["cold"] == {"pipeline_initialization_seconds": 3.0, "first_note_seconds": 4.0}
    assert report["warm"]["note_count"] == 2
    assert report["warm"]["mean"] == 1.5
    assert report["memory"]["peak_process_rss_bytes"] == 120