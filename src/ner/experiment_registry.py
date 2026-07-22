"""Validation and artifact helpers for NER-2 experiments (not a runner)."""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    run_id: str
    family: str
    parent_run_id: str | None
    hypothesis: str
    primary_change: str
    overrides: dict[str, Any]


def load_experiment_matrix(path: str | Path) -> dict[str, Any]:
    matrix = _load_yaml(path)
    validate_registry(matrix)
    return matrix


def parse_experiment_specs(matrix: Mapping[str, Any], family: str | None = None) -> list[ExperimentSpec]:
    families = matrix.get("families", {})
    if not isinstance(families, Mapping):
        raise ValueError("experiment matrix 'families' must be a mapping")
    output: list[ExperimentSpec] = []
    for family_name, rows in families.items():
        if family is not None and family_name != family:
            continue
        if not isinstance(rows, list):
            raise ValueError(f"experiment family {family_name!r} must be a list")
        for row in rows:
            if not isinstance(row, Mapping) or not row.get("run_id"):
                raise ValueError(f"invalid experiment row in family {family_name!r}")
            overrides = row.get("overrides", {})
            if not isinstance(overrides, Mapping):
                raise ValueError(f"overrides for {row['run_id']} must be a mapping")
            output.append(ExperimentSpec(
                run_id=str(row["run_id"]), family=str(family_name),
                parent_run_id=str(row["parent_run_id"]) if row.get("parent_run_id") else None,
                hypothesis=str(row.get("hypothesis", "")),
                primary_change=str(row.get("primary_change", family_name)),
                overrides=_copy_mapping(overrides),
            ))
    return output


def validate_registry(matrix: Mapping[str, Any]) -> list[ExperimentSpec]:
    """Validate unique IDs, parents/cycles, and one override axis per family."""
    specs = parse_experiment_specs(matrix)
    if not specs:
        raise ValueError("experiment registry is empty")
    by_id: dict[str, ExperimentSpec] = {}
    for spec in specs:
        if spec.run_id in by_id:
            raise ValueError(f"duplicate run_id: {spec.run_id}")
        by_id[spec.run_id] = spec
        if not spec.hypothesis.strip() or not spec.primary_change.strip():
            raise ValueError(f"experiment {spec.run_id} needs hypothesis and primary_change")
        if not spec.overrides:
            raise ValueError(f"experiment {spec.run_id} has no real overrides")

    defaults = {"labels": "extractors.gliner.label_map", "windows": "extractors.gliner.windowing",
                "thresholds": "extractors.gliner.threshold", "passes": "extractors.gliner.passes"}
    configured_axes = matrix.get("axis_paths", {})
    if configured_axes and not isinstance(configured_axes, Mapping):
        raise ValueError("axis_paths must be a mapping")
    axes = dict(configured_axes or {})
    for spec in specs:
        expected = str(axes.get(spec.family, defaults.get(spec.family, spec.family)))
        leaves = _leaf_paths(spec.overrides)
        if not leaves or any(path != expected and not path.startswith(expected + ".") for path in leaves):
            raise ValueError(f"experiment {spec.run_id} violates one-axis policy for {spec.family}: {sorted(leaves)}")
        if spec.parent_run_id is not None:
            parent = by_id.get(spec.parent_run_id)
            if parent is None:
                raise ValueError(f"unknown parent_run_id {spec.parent_run_id!r} for {spec.run_id}")
            if parent.family != spec.family:
                raise ValueError(f"parent {parent.run_id!r} is in a different experiment family")

    state: dict[str, int] = {}
    trail: list[str] = []
    def visit(run_id: str) -> None:
        if state.get(run_id) == 1:
            start = trail.index(run_id)
            raise ValueError("experiment parent cycle: " + " -> ".join((*trail[start:], run_id)))
        if state.get(run_id) == 2:
            return
        state[run_id] = 1
        trail.append(run_id)
        parent = by_id[run_id].parent_run_id
        if parent is not None:
            visit(parent)
        trail.pop()
        state[run_id] = 2
    for run_id in by_id:
        visit(run_id)
    return specs


def get_experiment(matrix: Mapping[str, Any], run_id: str) -> ExperimentSpec:
    for spec in validate_registry(matrix):
        if spec.run_id == run_id:
            return spec
    raise KeyError(f"unknown experiment run_id: {run_id}")


def materialize_experiment_config(base_config_path: str | Path, spec: ExperimentSpec, output_path: str | Path) -> Path:
    base = Path(base_config_path).resolve()
    if not base.is_file():
        raise FileNotFoundError(base)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump({"extends": str(base), **_copy_mapping(spec.overrides)}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def directory_manifest(directory: str | Path, pattern: str = "*") -> dict[str, str]:
    root = Path(directory)
    return {path.relative_to(root).as_posix(): file_hash(path) for path in sorted(root.glob(pattern)) if path.is_file()}


def artifact_entry(path: str | Path) -> dict[str, Any]:
    item = Path(path)
    if not item.is_file():
        raise FileNotFoundError(item)
    return {"path": str(item), "sha256": file_hash(item), "bytes": item.stat().st_size}


def build_artifact_manifest(artifacts: Mapping[str, str | Path], **metadata: Any) -> dict[str, Any]:
    entries = {name: artifact_entry(path) for name, path in sorted(artifacts.items())}
    manifest = {**metadata, "artifacts": entries}
    manifest["artifact_set_hash"] = canonical_hash({name: row["sha256"] for name, row in entries.items()})
    return manifest


def write_artifact_manifest(path: str | Path, artifacts: Mapping[str, str | Path], **metadata: Any) -> dict[str, Any]:
    manifest = build_artifact_manifest(artifacts, **metadata)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify_artifact_manifest(manifest: Mapping[str, Any]) -> None:
    """Raise when an artifact is missing, modified, or the set hash is invalid."""
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError("artifact manifest needs an artifacts mapping")
    observed: dict[str, str] = {}
    for name, raw_entry in artifacts.items():
        if not isinstance(raw_entry, Mapping) or not raw_entry.get("path") or not raw_entry.get("sha256"):
            raise ValueError(f"invalid artifact entry: {name}")
        actual = file_hash(str(raw_entry["path"]))
        expected = str(raw_entry["sha256"])
        if actual != expected:
            raise ValueError(f"artifact hash mismatch: {name}")
        observed[str(name)] = actual
    expected_set_hash = canonical_hash({name: observed[name] for name in sorted(observed)})
    if manifest.get("artifact_set_hash") != expected_set_hash:
        raise ValueError("artifact set hash mismatch")


def validate_split_manifest(splits: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    normalized: dict[str, tuple[str, ...]] = {}
    owner: dict[str, str] = {}
    for name in ("development", "calibration", "lockbox"):
        raw = splits.get(name)
        values = raw.get("ids") if isinstance(raw, Mapping) else raw
        if not isinstance(values, (list, tuple)) or not values:
            raise ValueError(f"split {name!r} must contain a non-empty ids list")
        ids = tuple(str(value) for value in values)
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate sample ID within {name} split")
        for sample_id in ids:
            if sample_id in owner:
                raise ValueError(f"sample {sample_id} occurs in both {owner[sample_id]} and {name}")
            owner[sample_id] = name
        normalized[name] = ids
    return normalized


def assert_split_allowed(split: str, *, purpose: str, policy: Mapping[str, Any], shortlisted: bool = False, milestone: str | None = None) -> None:
    split, purpose = str(split), str(purpose)
    if split not in {"development", "calibration", "lockbox"}:
        raise ValueError(f"unknown split: {split}")
    split_policy = policy.get(split, {})
    if not isinstance(split_policy, Mapping):
        raise ValueError(f"selection policy {split} section must be a mapping")
    if split == "lockbox" and purpose in {"selection", "tuning", "threshold_selection", "calibration"}:
        raise PermissionError("lockbox must not be used for experiment selection or tuning")
    allowed_purposes = tuple(str(item) for item in split_policy.get("allowed_purposes", ()))
    if allowed_purposes and purpose not in allowed_purposes:
        raise PermissionError(f"purpose {purpose!r} is not allowed on {split}")
    if split == "calibration" and bool(split_policy.get("require_explicit_shortlist", True)) and not shortlisted:
        raise PermissionError("calibration access is restricted to shortlisted runs")
    if split != "lockbox":
        return
    lockbox = split_policy
    allowed = tuple(str(item) for item in lockbox.get("open_milestones", ()))
    if not milestone or milestone not in allowed:
        raise PermissionError(f"lockbox is sealed outside milestones: {list(allowed)}")


def build_run_manifest(spec: ExperimentSpec, *, config_path: str | Path, split: str, metrics: Mapping[str, Any], density: Mapping[str, Any], runtime: Mapping[str, Any], model_hash: str | None = None, dataset_hash: str | None = None, scorer_hash: str | None = None, git_commit: str | None = None) -> dict[str, Any]:
    config = Path(config_path)
    return {
        "run_id": spec.run_id, "family": spec.family, "parent_run_id": spec.parent_run_id,
        "hypothesis": spec.hypothesis, "primary_change": spec.primary_change,
        "git_commit": git_commit or _git_commit(), "config_path": str(config),
        "config_hash": file_hash(config), "overrides_hash": canonical_hash(spec.overrides),
        "model_hash": model_hash, "dataset_hash": dataset_hash, "scorer_hash": scorer_hash,
        "split": split, "metrics": dict(metrics), "density": dict(density), "runtime": dict(runtime),
    }


def write_ledger_entry(path: str | Path, record: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n")


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError("YAML root must be a mapping")
    return value


def _copy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(value), ensure_ascii=False))


def _leaf_paths(value: Mapping[str, Any], prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(child, Mapping) and child:
            paths.update(_leaf_paths(child, path))
        else:
            paths.add(path)
    return paths


sha256_file = file_hash
hash_config = canonical_hash
validate_experiment_registry = validate_registry