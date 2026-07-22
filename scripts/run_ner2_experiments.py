from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_yaml
from src.io_utils import read_json, write_json
from src.ner.experiment_registry import (
    ExperimentSpec,
    assert_split_allowed,
    build_run_manifest,
    canonical_hash,
    directory_manifest,
    file_hash,
    get_experiment,
    load_experiment_matrix,
    materialize_experiment_config,
    parse_experiment_specs,
    validate_split_manifest,
    write_ledger_entry,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run controlled NER-2 experiments without lockbox selection.")
    parser.add_argument("--matrix", default="configs/ner2/experiment_matrix.yaml")
    parser.add_argument("--policy", default="configs/ner2/selection_policy.yaml")
    parser.add_argument("--split-config", default="configs/splits_v2.yaml")
    parser.add_argument("--split", choices=("development", "calibration", "lockbox"), default="development")
    parser.add_argument("--purpose", default="selection")
    parser.add_argument("--family", choices=("labels", "windows", "thresholds", "passes"), default=None)
    parser.add_argument("--run-id", action="append", default=[])
    parser.add_argument("--shortlist", default=None, help="JSON file containing shortlisted run IDs; required for calibration.")
    parser.add_argument("--parent-config", default=None, help="Frozen winner config used as the base for window/threshold/pass families.")
    parser.add_argument("--equivalence-report", default="outputs/reports/ner2_proposal_equivalence.json")
    parser.add_argument("--output-root", default="outputs/experiments/ner2")
    parser.add_argument("--max-files", type=int, default=None)
    parser.add_argument("--end-to-end", action="store_true", default=True)
    parser.add_argument("--no-end-to-end", action="store_false", dest="end_to_end")
    parser.add_argument("--milestone", default=None)
    parser.add_argument("--confirm-lockbox-after-freeze", action="store_true")
    parser.add_argument("--frozen-config", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    matrix_path, policy_path, split_path = map(Path, (args.matrix, args.policy, args.split_config))
    matrix = load_experiment_matrix(matrix_path)
    policy = load_yaml(policy_path)
    splits = load_yaml(split_path)
    normalized_splits = validate_split_manifest(splits)
    shortlist = _load_shortlist(args.shortlist)
    assert_split_allowed(args.split, purpose=args.purpose, policy=policy, shortlisted=bool(shortlist), milestone=args.milestone)
    _assert_lockbox_freeze(args)

    requested = set(args.run_id)
    specs = parse_experiment_specs(matrix, args.family)
    if requested:
        unknown = requested - {spec.run_id for spec in specs}
        if unknown:
            raise KeyError(f"unknown or excluded run IDs: {sorted(unknown)}")
        specs = [spec for spec in specs if spec.run_id in requested]
    if args.split == "calibration":
        specs = [spec for spec in specs if spec.run_id in shortlist]
        if not specs:
            raise PermissionError("no requested run is present in the calibration shortlist")
    if not specs:
        raise ValueError("no experiments selected")

    base_config = _base_config(matrix_path, matrix, args.parent_config)
    output_root = Path(args.output_root)
    config_dir = PROJECT_ROOT / "configs"
    equivalence = _equivalence_policy(Path(args.equivalence_report), specs)
    ledger = output_root / "ledger.jsonl"
    model_hash = _model_artifact_hash()
    dataset_hash = canonical_hash({sample_id: {
        "input": file_hash(PROJECT_ROOT / "data/golden/input" / f"{sample_id}.txt"),
        "gold": file_hash(PROJECT_ROOT / "data/golden/gold" / f"{sample_id}.json"),
    } for sample_id in normalized_splits[args.split]})

    for spec in specs:
        _assert_conditional_pass(spec, output_root)
        effective = _with_infrastructure_override(spec, equivalence)
        config_path = materialize_experiment_config(base_config, effective, config_dir / f".ner2_run_{spec.run_id}.yaml")
        run_dir = output_root / spec.run_id
        extraction_predictions = run_dir / "predictions" / "extraction_only"
        extraction_report = run_dir / "evaluation" / "extraction_only"
        e2e_predictions = run_dir / "predictions" / "end_to_end"
        e2e_report = run_dir / "evaluation" / "end_to_end"
        command = [
            sys.executable, str(PROJECT_ROOT / "scripts/benchmark_gliner.py"),
            "--config", str(config_path), "--split-config", str(split_path), "--split", args.split,
            "--output-dir", str(extraction_predictions), "--report-dir", str(extraction_report),
        ]
        if args.max_files is not None:
            command.extend(["--max-files", str(args.max_files)])
        if args.end_to_end:
            command.extend(["--end-to-end", "--end-to-end-output-dir", str(e2e_predictions), "--end-to-end-report-dir", str(e2e_report)])
        if args.split == "lockbox":
            command.append("--allow-lockbox")
        completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
        if completed.returncode:
            raise RuntimeError(f"benchmark failed for {spec.run_id} with exit code {completed.returncode}")
        _promote_run_artifacts(run_dir, extraction_report, e2e_report, args.end_to_end)
        metrics = read_json(extraction_report / "evaluation_summary.json")
        density = read_json(extraction_report / "density.json")
        runtime = read_json(extraction_report / "runtime.json")
        manifest = build_run_manifest(
            spec, config_path=config_path, split=args.split, metrics=metrics, density=density, runtime=runtime,
            model_hash=model_hash, dataset_hash=dataset_hash,
            scorer_hash=file_hash(PROJECT_ROOT / "src/evaluation/official_like_scorer.py"),
        )
        manifest.update({
            "selection_policy_hash": file_hash(policy_path), "split_config_hash": file_hash(split_path),
            "sample_ids": list(normalized_splits[args.split])[: args.max_files],
            "proposal_equivalence": equivalence, "base_config": str(base_config),
            "prediction_hashes": directory_manifest(extraction_predictions, "*.json"),
        })
        write_json(run_dir / "run_manifest.json", manifest)
        _write_prediction_diff(run_dir, spec, output_root)
        write_ledger_entry(ledger, manifest)
    return 0


def _base_config(matrix_path: Path, matrix: dict[str, Any], override: str | None) -> Path:
    if override:
        return Path(override).resolve()
    value = Path(str(matrix["base_config"]))
    return (matrix_path.parent / value).resolve()


def _load_shortlist(path: str | None) -> set[str]:
    if not path:
        return set()
    payload = read_json(path)
    values = payload.get("run_ids", []) if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        raise ValueError("shortlist must be a list or contain run_ids")
    return {str(value) for value in values}


def _equivalence_policy(path: Path, specs: list[ExperimentSpec]) -> dict[str, Any]:
    needs_floor = any(spec.family == "thresholds" for spec in specs)
    if not needs_floor:
        return {"required": False, "mode": "inference_threshold"}
    if not path.is_file():
        raise FileNotFoundError(f"threshold sweeps require proposal equivalence report: {path}")
    report = read_json(path)
    if not isinstance(report, dict) or not report.get("equivalent"):
        raise RuntimeError("raw-floor proposal cache is forbidden because equivalence did not pass")
    return {"required": True, "mode": "raw_floor", "report": str(path), "report_hash": file_hash(path)}


def _with_infrastructure_override(spec: ExperimentSpec, equivalence: dict[str, Any]) -> ExperimentSpec:
    overrides = json.loads(json.dumps(spec.overrides, ensure_ascii=False))
    gliner = overrides.setdefault("extractors", {}).setdefault("gliner", {})
    gliner["lazy_load"] = True
    gliner["proposal_cache_mode"] = equivalence["mode"]
    if equivalence["mode"] == "raw_floor":
        gliner["proposal_threshold"] = 0.15
    # Infrastructure fields are identical across the experiment family and are
    # intentionally added only after registry one-axis validation.
    return ExperimentSpec(spec.run_id, spec.family, spec.parent_run_id, spec.hypothesis, spec.primary_change, overrides)


def _assert_lockbox_freeze(args: argparse.Namespace) -> None:
    if args.split != "lockbox":
        return
    if not args.confirm_lockbox_after_freeze or not args.frozen_config:
        raise PermissionError("lockbox requires --confirm-lockbox-after-freeze and --frozen-config")
    if not Path(args.frozen_config).is_file():
        raise FileNotFoundError(args.frozen_config)


def _assert_conditional_pass(spec: ExperimentSpec, output_root: Path) -> None:
    if spec.run_id != "pass_full_problem_structured":
        return
    decisions = []
    for run_id in ("pass_full_problem", "pass_full_structured"):
        path = output_root / run_id / "decision.json"
        if path.is_file():
            decisions.append(read_json(path).get("decision"))
    if "keep" not in decisions:
        raise PermissionError("P3 requires a keep decision for P1 or P2")


def _promote_run_artifacts(run_dir: Path, extraction_report: Path, e2e_report: Path, end_to_end: bool) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    debug_source = extraction_report / "debug_entities"
    debug_target = run_dir / "debug_entities"
    if debug_target.exists():
        shutil.rmtree(debug_target)
    if debug_source.is_dir():
        shutil.copytree(debug_source, debug_target)
    for name in ("density.json", "runtime.json", "resolved_config.json"):
        shutil.copy2(extraction_report / name, run_dir / name)
    if end_to_end and (e2e_report / "official_like.json").is_file():
        shutil.copy2(e2e_report / "official_like.json", run_dir / "official_like_score.json")


def _write_prediction_diff(run_dir: Path, spec: ExperimentSpec, output_root: Path) -> None:
    current = directory_manifest(run_dir / "predictions/extraction_only", "*.json")
    parent = directory_manifest(output_root / spec.parent_run_id / "predictions/extraction_only", "*.json") if spec.parent_run_id and (output_root / spec.parent_run_id).is_dir() else {}
    names = sorted(set(current) | set(parent))
    write_json(run_dir / "prediction_diff.json", {
        "parent_run_id": spec.parent_run_id, "files_compared": len(names),
        "changed_files": [name for name in names if current.get(name) != parent.get(name)],
        "added_files": [name for name in names if name not in parent],
        "removed_files": [name for name in names if name not in current],
    })


def _model_artifact_hash() -> str | None:
    manifests = [
        PROJECT_ROOT / "outputs/reports/v2_ner1_gliner_reproduction/model_manifest.json",
        PROJECT_ROOT / "outputs/reports/v2_ner1_gliner_reproduction/tokenizer_manifest.json",
    ]
    return canonical_hash([read_json(path) for path in manifests]) if all(path.is_file() for path in manifests) else None


if __name__ == "__main__":
    raise SystemExit(main())