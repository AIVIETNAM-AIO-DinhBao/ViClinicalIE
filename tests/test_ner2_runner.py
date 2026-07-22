from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_ner2_experiments import _assert_conditional_pass, _equivalence_policy
from scripts.summarize_ner2_experiments import apply_policy, freeze_selected
from src.ner.experiment_registry import ExperimentSpec


def test_threshold_family_requires_passing_equivalence_report(tmp_path: Path) -> None:
    spec = ExperimentSpec("t", "thresholds", None, "h", "threshold", {"extractors": {"gliner": {"threshold": {"default": .3}}}})
    with pytest.raises(FileNotFoundError):
        _equivalence_policy(tmp_path / "missing.json", [spec])
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"equivalent": False}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="forbidden"):
        _equivalence_policy(report, [spec])
    report.write_text(json.dumps({"equivalent": True}), encoding="utf-8")
    assert _equivalence_policy(report, [spec])["mode"] == "raw_floor"


def test_p3_requires_keep_decision_from_p1_or_p2(tmp_path: Path) -> None:
    spec = ExperimentSpec("pass_full_problem_structured", "passes", "pass_full", "h", "passes", {"extractors": {"gliner": {"passes": []}}})
    with pytest.raises(PermissionError, match="P3"):
        _assert_conditional_pass(spec, tmp_path)
    decision = tmp_path / "pass_full_problem" / "decision.json"
    decision.parent.mkdir(parents=True)
    decision.write_text(json.dumps({"decision": "keep"}), encoding="utf-8")
    _assert_conditional_pass(spec, tmp_path)


def test_policy_rejects_density_and_marks_small_gain_for_investigation() -> None:
    policy = {
        "version": "x", "minimum_useful_effect": {"overall_exact_f1_absolute": .01},
        "regression_budget": {"per_type_exact_f1_absolute": .03, "density_ratio_max": 1.5},
    }
    rows = [
        {"run_id": "a", "parent_run_id": None, "exact_f1": .4, "density_ratio": 1.0, "validation_errors": 0, "by_type_exact": {}},
        {"run_id": "b", "parent_run_id": "a", "exact_f1": .405, "density_ratio": 1.0, "validation_errors": 0, "by_type_exact": {}},
        {"run_id": "c", "parent_run_id": "a", "exact_f1": .5, "density_ratio": 2.0, "validation_errors": 0, "by_type_exact": {}},
    ]
    decisions = apply_policy(rows, policy)
    assert decisions["a"]["decision"] == "keep"
    assert decisions["b"]["decision"] == "investigate"
    assert decisions["c"]["decision"] == "reject"


def test_freeze_rejects_non_calibration_run(tmp_path: Path) -> None:
    row = {"run_id": "x", "split": "development", "run_dir": str(tmp_path / "x")}
    with pytest.raises(PermissionError, match="calibration"):
        freeze_selected("x", [row], tmp_path / "policy.yaml", tmp_path / "selected.yaml", tmp_path / "report")