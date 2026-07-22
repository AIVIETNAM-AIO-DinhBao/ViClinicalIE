from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_yaml
from src.data_types import VALID_ENTITY_TYPES
from src.io_utils import read_json, write_json
from src.ner.experiment_registry import file_hash


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize NER-2 runs and apply the frozen selection policy.")
    parser.add_argument("--output-root", default="outputs/experiments/ner2")
    parser.add_argument("--policy", default="configs/ner2/selection_policy.yaml")
    parser.add_argument("--family", default=None)
    parser.add_argument("--summary-dir", default="outputs/reports/ner2_experiment_summary")
    parser.add_argument("--shortlist-size", type=int, default=None)
    parser.add_argument("--freeze-run", default=None, help="Calibration run ID to freeze after review.")
    parser.add_argument("--selected-config", default="configs/ner2/selected_zero_shot.yaml")
    parser.add_argument("--selected-report-dir", default="outputs/reports/v2_ner2_selected")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root, summary_dir = Path(args.output_root), Path(args.summary_dir)
    policy_path = Path(args.policy)
    policy = load_yaml(policy_path)
    rows = collect_runs(root, family=args.family)
    decisions = apply_policy(rows, policy)
    for row in rows:
        decision = decisions[row["run_id"]]
        write_json(Path(row["run_dir"]) / "decision.json", decision)
        row.update({"decision": decision["decision"], "decision_reasons": decision["reasons"]})
    ranked = sorted(rows, key=_rank_key)
    summary_dir.mkdir(parents=True, exist_ok=True)
    write_json(summary_dir / "summary.json", {
        "policy": str(policy_path), "policy_hash": file_hash(policy_path), "runs": ranked,
    })
    _write_csv(summary_dir / "summary.csv", ranked)
    size = args.shortlist_size or int(policy.get("calibration", {}).get("shortlist_size", 3))
    shortlist = [row["run_id"] for row in ranked if row["decision"] in {"keep", "investigate"}][:size]
    write_json(summary_dir / "shortlist.json", {"run_ids": shortlist, "policy_hash": file_hash(policy_path)})
    if args.freeze_run:
        freeze_selected(args.freeze_run, rows, policy_path, Path(args.selected_config), Path(args.selected_report_dir))
    print(json.dumps({"runs": len(rows), "shortlist": shortlist}, ensure_ascii=False))
    return 0


def collect_runs(root: Path, *, family: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for manifest_path in sorted(root.glob("*/run_manifest.json")):
        manifest = read_json(manifest_path)
        if family and manifest.get("family") != family:
            continue
        run_dir = manifest_path.parent
        metrics = manifest.get("metrics", {})
        density = manifest.get("density", {})
        official_path = run_dir / "official_like_score.json"
        official = read_json(official_path) if official_path.is_file() else {}
        exact = metrics.get("exact", {})
        relaxed = metrics.get("relaxed", {})
        rows.append({
            "run_id": str(manifest["run_id"]), "parent_run_id": manifest.get("parent_run_id"),
            "family": manifest.get("family"), "split": manifest.get("split"), "run_dir": str(run_dir),
            "exact_f1": float(exact.get("f1", 0.0)), "exact_precision": float(exact.get("precision", 0.0)),
            "exact_recall": float(exact.get("recall", 0.0)), "relaxed_f1": float(relaxed.get("f1", 0.0)),
            "end_to_end_score": float(official.get("final_score", 0.0)) if official else None,
            "density_ratio": density.get("pred_gold_ratio"), "validation_errors": _validation_errors(run_dir),
            "by_type_exact": metrics.get("by_type_exact", {}),
        })
    return rows


def apply_policy(rows: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_id = {row["run_id"]: row for row in rows}
    minimum = float(policy.get("minimum_useful_effect", {}).get("overall_exact_f1_absolute", 0.0))
    per_type_budget = float(policy.get("regression_budget", {}).get("per_type_exact_f1_absolute", 1.0))
    density_max = float(policy.get("regression_budget", {}).get("density_ratio_max", float("inf")))
    decisions: dict[str, dict[str, Any]] = {}
    for row in rows:
        reasons: list[str] = []
        if row["validation_errors"]:
            reasons.append("technical_validation_failed")
        ratio = row.get("density_ratio")
        if ratio is None or float(ratio) > density_max:
            reasons.append("density_budget_exceeded")
        parent = by_id.get(str(row.get("parent_run_id")))
        if parent:
            gain = row["exact_f1"] - parent["exact_f1"]
            if gain < minimum:
                reasons.append(f"minimum_useful_effect_not_met:{gain:.6f}")
            for entity_type, parent_metrics in parent.get("by_type_exact", {}).items():
                current = row.get("by_type_exact", {}).get(entity_type, {})
                regression = float(parent_metrics.get("f1", 0.0)) - float(current.get("f1", 0.0))
                if regression > per_type_budget:
                    reasons.append(f"per_type_regression:{entity_type}:{regression:.6f}")
        if any(reason.startswith(("technical_", "density_", "per_type_")) for reason in reasons):
            decision = "reject"
        elif parent and reasons:
            decision = "investigate"
        else:
            decision = "keep"
        decisions[row["run_id"]] = {"decision": decision, "reasons": reasons, "policy_version": policy.get("version")}
    return decisions


def freeze_selected(run_id: str, rows: list[dict[str, Any]], policy_path: Path, selected_config: Path, report_dir: Path) -> None:
    row = next((item for item in rows if item["run_id"] == run_id), None)
    if row is None:
        raise KeyError(f"unknown completed run: {run_id}")
    if row["split"] != "calibration":
        raise PermissionError("only a calibration run can be frozen")
    decision_path = Path(row["run_dir"]) / "decision.json"
    decision = read_json(decision_path)
    if decision.get("decision") != "keep":
        raise PermissionError("only a keep decision can be frozen")
    resolved = read_json(Path(row["run_dir"]) / "resolved_config.json")
    gliner = resolved.get("extractors", {}).get("gliner")
    if not isinstance(gliner, dict):
        raise ValueError("selected resolved config has no GLiNER section")
    gliner = json.loads(json.dumps(gliner, ensure_ascii=False))
    gliner["lazy_load"] = True
    gliner["proposal_cache_mode"] = "raw_floor"
    gliner["proposal_threshold"] = 0.15
    threshold = gliner.get("threshold", {"default": 0.35})
    default_threshold = float(threshold.get("default", 0.35)) if isinstance(threshold, dict) else float(threshold)
    existing = threshold if isinstance(threshold, dict) else {}
    gliner["threshold"] = {
        "default": default_threshold,
        **{entity_type: float(existing.get(entity_type, default_threshold)) for entity_type in sorted(VALID_ENTITY_TYPES)},
    }
    payload = {
        "extends": "../gliner_zero_shot.yaml",
        "project": {"phase": "v2_ner2_selected_zero_shot_frozen"},
        "paths_config": "../paths.yaml",
        "entity_rules_config": "../entity_rules.yaml",
        "section_detection": {"patterns_config": "../section_patterns.yaml"},
        "assertion_detection": {"rules_config": "../assertion_rules.yaml"},
        "extractors": {"gliner": gliner},
        "ner2_freeze": {
            "run_id": run_id, "selection_policy_hash": file_hash(policy_path),
            "run_manifest_hash": file_hash(Path(row["run_dir"]) / "run_manifest.json"),
            "lockbox_seen": False,
        },
    }
    selected_config.parent.mkdir(parents=True, exist_ok=True)
    selected_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    if report_dir.exists():
        shutil.rmtree(report_dir)
    shutil.copytree(Path(row["run_dir"]), report_dir)
    write_json(report_dir / "freeze_manifest.json", {
        "run_id": run_id, "selected_config": str(selected_config), "selected_config_hash": file_hash(selected_config),
        "selection_policy_hash": file_hash(policy_path), "lockbox_seen": False,
    })


def _validation_errors(run_dir: Path) -> int:
    path = run_dir / "evaluation/extraction_only/validation_summary.json"
    if not path.is_file():
        return 1
    return int(read_json(path).get("error_count", 1))


def _rank_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (-row["exact_f1"], -(row["end_to_end_score"] or 0.0), row["density_ratio"] if row["density_ratio"] is not None else float("inf"), row["run_id"])


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = ["run_id", "parent_run_id", "family", "split", "exact_f1", "exact_precision", "exact_recall", "relaxed_f1", "end_to_end_score", "density_ratio", "validation_errors", "decision"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in columns} for row in rows)


if __name__ == "__main__":
    raise SystemExit(main())