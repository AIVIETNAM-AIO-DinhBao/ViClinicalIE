from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from src.config import load_config
from src.ner.experiment_registry import (
    assert_split_allowed,
    build_artifact_manifest,
    build_run_manifest,
    canonical_hash,
    file_hash,
    get_experiment,
    load_experiment_matrix,
    materialize_experiment_config,
    validate_registry,
    validate_split_manifest,
    verify_artifact_manifest,
    write_artifact_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "configs/ner2/experiment_matrix.yaml"
POLICY = ROOT / "configs/ner2/selection_policy.yaml"


def test_real_matrix_is_valid_unique_and_has_real_single_axis_overrides() -> None:
    matrix = load_experiment_matrix(MATRIX)
    specs = validate_registry(matrix)
    assert len(specs) == 22
    assert len({spec.run_id for spec in specs}) == len(specs)
    assert all(spec.overrides for spec in specs)
    assert get_experiment(matrix, "threshold_035").overrides["extractors"]["gliner"]["threshold"]["default"] == .35


def test_materialized_override_is_consumed_by_normal_config_loader(tmp_path: Path) -> None:
    matrix = load_experiment_matrix(MATRIX)
    spec = get_experiment(matrix, "label_vi")
    base = tmp_path / "base.yaml"
    base.write_text("extractors:\n  gliner:\n    threshold:\n      default: 0.35\n", encoding="utf-8")
    child = materialize_experiment_config(base, spec, tmp_path / "label_vi.yaml")
    config = load_config(child, project_root=tmp_path)
    gliner = config.raw["extractors"]["gliner"]
    assert gliner["label_map"]["triệu chứng"] == "TRIỆU_CHỨNG"
    assert gliner["threshold"]["default"] == .35


def _minimal_matrix() -> dict:
    override = {"extractors": {"gliner": {"threshold": {"default": .2}}}}
    return {"families": {"thresholds": [{"run_id": "a", "hypothesis": "h", "primary_change": "threshold", "overrides": override}]}}


def test_registry_rejects_duplicate_unknown_parent_cycle_and_cross_family_parent() -> None:
    duplicate = _minimal_matrix()
    duplicate["families"]["thresholds"].append(copy.deepcopy(duplicate["families"]["thresholds"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_registry(duplicate)

    unknown = _minimal_matrix()
    unknown["families"]["thresholds"][0]["parent_run_id"] = "missing"
    with pytest.raises(ValueError, match="unknown parent"):
        validate_registry(unknown)

    cycle = _minimal_matrix()
    second = copy.deepcopy(cycle["families"]["thresholds"][0])
    second["run_id"], second["parent_run_id"] = "b", "a"
    cycle["families"]["thresholds"][0]["parent_run_id"] = "b"
    cycle["families"]["thresholds"].append(second)
    with pytest.raises(ValueError, match="cycle"):
        validate_registry(cycle)

    cross = _minimal_matrix()
    cross["families"]["labels"] = [{"run_id": "b", "parent_run_id": "a", "hypothesis": "h", "primary_change": "labels", "overrides": {"extractors": {"gliner": {"label_map": {"x": "THUỐC"}}}}}]
    with pytest.raises(ValueError, match="different experiment family"):
        validate_registry(cross)


def test_registry_rejects_empty_and_multiple_axis_overrides() -> None:
    empty = _minimal_matrix()
    empty["families"]["thresholds"][0]["overrides"] = {}
    with pytest.raises(ValueError, match="no real overrides"):
        validate_registry(empty)
    multiple = _minimal_matrix()
    multiple["families"]["thresholds"][0]["overrides"]["extractors"]["gliner"]["label_map"] = {"x": "THUỐC"}
    with pytest.raises(ValueError, match="one-axis"):
        validate_registry(multiple)


def test_hashes_and_artifact_manifests_are_deterministic(tmp_path: Path) -> None:
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    first.write_bytes(b"alpha")
    second.write_bytes(b"beta")
    assert file_hash(first) == "8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8"
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})
    manifest = build_artifact_manifest({"second": second, "first": first}, run_id="r1")
    assert list(manifest["artifacts"]) == ["first", "second"]
    assert manifest["artifacts"]["first"]["bytes"] == 5
    written = write_artifact_manifest(tmp_path / "manifest.json", {"first": first}, run_id="r1")
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8")) == written
    verify_artifact_manifest(written)
    first.write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_artifact_manifest(written)


def test_run_manifest_includes_reproducibility_hashes(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("x: 1\n", encoding="utf-8")
    spec = get_experiment(load_experiment_matrix(MATRIX), "threshold_020")
    row = build_run_manifest(spec, config_path=config, split="development", metrics={}, density={}, runtime={},
                             model_hash="m", dataset_hash="d", scorer_hash="s", git_commit="abc")
    assert row["config_hash"] == file_hash(config)
    assert row["overrides_hash"] == canonical_hash(spec.overrides)
    assert (row["model_hash"], row["dataset_hash"], row["scorer_hash"]) == ("m", "d", "s")


def test_split_manifest_rejects_overlap_and_duplicates() -> None:
    splits = yaml.safe_load((ROOT / "configs/splits_v2.yaml").read_text(encoding="utf-8"))
    normalized = validate_split_manifest(splits)
    assert normalized["lockbox"] == ("17", "18", "19", "20")
    splits["lockbox"]["ids"][0] = 1
    with pytest.raises(ValueError, match="both development and lockbox"):
        validate_split_manifest(splits)


def test_calibration_shortlist_and_lockbox_guards() -> None:
    policy = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    assert_split_allowed("development", purpose="selection", policy=policy)
    with pytest.raises(PermissionError, match="shortlisted"):
        assert_split_allowed("calibration", purpose="threshold_selection", policy=policy)
    assert_split_allowed("calibration", purpose="threshold_selection", policy=policy, shortlisted=True)
    with pytest.raises(PermissionError, match="must not"):
        assert_split_allowed("lockbox", purpose="selection", policy=policy, milestone="M3")
    with pytest.raises(PermissionError, match="sealed"):
        assert_split_allowed("lockbox", purpose="final_evaluation", policy=policy)
    assert_split_allowed("lockbox", purpose="final_evaluation", policy=policy, milestone="M6")