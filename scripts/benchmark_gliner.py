from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
import tracemalloc
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config, load_yaml
from src.evaluation import GoldenEvaluator, write_evaluation_report
from src.evaluation.official_like_scorer import score_directories
from src.formatting.json_formatter import write_prediction_json
from src.io_utils import read_json, write_json
from src.pipeline import ClinicalIEPipeline
from src.validation import validate_prediction_directory, write_directory_validation_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark GLiNER extraction through the repository ClinicalIEPipeline.")
    # Original NER-1 arguments/defaults remain unchanged.
    parser.add_argument("--config", default="configs/gliner_zero_shot.yaml")
    parser.add_argument("--split-config", default="configs/splits_v2.yaml")
    parser.add_argument("--split", choices=("development", "calibration", "lockbox", "all"), default="development")
    parser.add_argument("--input-dir", default="data/golden/input")
    parser.add_argument("--gold-dir", default="data/golden/gold")
    parser.add_argument("--output-dir", default="outputs/predictions/v2_ner1_gliner_reproduction")
    parser.add_argument("--report-dir", default="outputs/reports/v2_ner1_gliner_reproduction")
    parser.add_argument("--max-files", type=int, default=None)
    parser.add_argument("--allow-lockbox", action="store_true", help="Explicitly authorize a split containing lockbox samples.")
    parser.add_argument("--end-to-end", action="store_true", help="Also emit assertions/linking/postprocessing artifacts.")
    parser.add_argument("--end-to-end-output-dir", default=None, help="Default: <output-dir>/end_to_end")
    parser.add_argument("--end-to-end-report-dir", default=None, help="Default: <report-dir>/end_to_end")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    _assert_lockbox_allowed(args.split, args.allow_lockbox)
    config = load_config(args.config, project_root=PROJECT_ROOT)
    ids = _split_ids(load_yaml(args.split_config), args.split)
    if args.max_files is not None:
        if args.max_files < 0:
            raise ValueError("--max-files must be non-negative")
        ids = ids[: args.max_files]

    input_dir, gold_dir = Path(args.input_dir), Path(args.gold_dir)
    output_dir, report_dir = Path(args.output_dir), Path(args.report_dir)
    selected_input, selected_gold = _materialize_selected_corpus(ids, input_dir, gold_dir, report_dir)
    extraction = _run_mode(
        config=config, ids=ids, input_dir=input_dir, selected_input=selected_input,
        selected_gold=selected_gold, output_dir=output_dir, report_dir=report_dir,
        ner_only=True, official_like=False,
    )

    end_to_end: dict[str, Any] | None = None
    e2e_output = Path(args.end_to_end_output_dir) if args.end_to_end_output_dir else output_dir / "end_to_end"
    e2e_report = Path(args.end_to_end_report_dir) if args.end_to_end_report_dir else report_dir / "end_to_end"
    if args.end_to_end:
        end_to_end = _run_mode(
            config=config, ids=ids, input_dir=input_dir, selected_input=selected_input,
            selected_gold=selected_gold, output_dir=e2e_output, report_dir=e2e_report,
            ner_only=False, official_like=True,
        )

    resolved = config.to_serializable()
    resolved["benchmark"] = {
        "artifact_schema_version": "ner2_benchmark_v1",
        "split": args.split, "sample_ids": ids, "allow_lockbox": bool(args.allow_lockbox),
        "extraction_only": True, "end_to_end": bool(args.end_to_end),
        "split_config": str(Path(args.split_config).resolve()),
        "input_dir": str(input_dir.resolve()), "gold_dir": str(gold_dir.resolve()),
        "extraction_output_dir": str(output_dir.resolve()), "extraction_report_dir": str(report_dir.resolve()),
        "end_to_end_output_dir": str(e2e_output.resolve()) if args.end_to_end else None,
        "end_to_end_report_dir": str(e2e_report.resolve()) if args.end_to_end else None,
    }
    write_json(report_dir / "resolved_config.json", resolved)
    if args.end_to_end:
        write_json(e2e_report / "resolved_config.json", resolved)
    print(json.dumps({
        "files": len(ids), "exact_f1": extraction["exact_f1"],
        "entities_by_type": extraction["entities_by_type"],
        "end_to_end_official_like_final_score": end_to_end.get("official_like_final_score") if end_to_end else None,
    }, ensure_ascii=False))
    return 0


def _run_mode(
    *, config: Any, ids: Sequence[str], input_dir: Path, selected_input: Path,
    selected_gold: Path, output_dir: Path, report_dir: Path, ner_only: bool,
    official_like: bool,
) -> dict[str, Any]:
    _reset_json_directory(output_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    if not official_like:
        # A previous end-to-end run must never make an extraction report look
        # as though the attribute-aware scorer was run on extraction records.
        (report_dir / "official_like.json").unlink(missing_ok=True)
    debug_dir = report_dir / "debug_entities"
    _reset_json_directory(debug_dir)

    tracemalloc.start()
    run_start = time.perf_counter()
    startup_start = time.perf_counter()
    pipeline = ClinicalIEPipeline(config, enable_sparse_retrieval=not ner_only, ner_only=ner_only)
    startup_seconds = time.perf_counter() - startup_start
    counts: Counter[str] = Counter()
    per_file: list[dict[str, Any]] = []
    for file_id in ids:
        path = input_dir / f"{file_id}.txt"
        file_start = time.perf_counter()
        result = pipeline.process_file(path)
        elapsed = time.perf_counter() - file_start
        write_prediction_json(result.records, output_dir / f"{file_id}.json", config.raw.get("output_format", {}))
        write_json(debug_dir / f"{file_id}.json", [{
            "text": entity.text, "position": entity.position, "type": str(entity.type),
            "confidence": entity.confidence, "provenance": entity.provenance,
        } for entity in result.entities])
        counts.update(result.entities_by_type)
        per_file.append({"file_id": file_id, "seconds": elapsed, "entities": len(result.entities), "counters": result.counters})
    total_seconds = time.perf_counter() - run_start
    _, peak_python_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    validation = validate_prediction_directory(
        selected_input, output_dir, expected_count=len(ids),
        config=config.raw.get("prediction_validation", {}),
    )
    write_directory_validation_report(validation, report_dir)
    evaluator = GoldenEvaluator(config.raw.get("evaluation", {}), validation_config=config.raw.get("prediction_validation", {}))
    report = evaluator.evaluate_directories(
        input_dir=selected_input, gold_dir=selected_gold, pred_dir=output_dir, expected_count=len(ids),
    )
    write_evaluation_report(report, report_dir)

    predictions = {file_id: read_json(output_dir / f"{file_id}.json") for file_id in ids}
    golds = {file_id: read_json(selected_gold / f"{file_id}.json") for file_id in ids}
    write_json(report_dir / "density.json", build_density_report(predictions, golds, ids=ids))
    write_json(report_dir / "runtime.json", build_runtime_report(
        per_file, startup_seconds=startup_seconds, total_seconds=total_seconds,
        peak_python_memory_bytes=peak_python_memory, entities_by_type=dict(sorted(counts.items())),
    ))

    official_score: float | None = None
    # Attribute/linking scores are meaningful only for full end-to-end records.
    if official_like:
        score = score_directories(output_dir, selected_gold, ids=ids)
        payload = score.to_dict()
        payload["scope"] = "end_to_end_only"
        write_json(report_dir / "official_like.json", payload)
        official_score = score.final_score
    return {
        "exact_f1": report.overall_exact.f1, "entities_by_type": dict(sorted(counts.items())),
        "official_like_final_score": official_score, "validation_ok": validation.ok,
    }


def build_density_report(
    predictions: Mapping[str, Sequence[Mapping[str, Any]]],
    golds: Mapping[str, Sequence[Mapping[str, Any]]], *, ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    selected = list(ids) if ids is not None else sorted(set(predictions) | set(golds), key=_natural_key)
    pred_counts = [len(predictions.get(file_id, ())) for file_id in selected]
    gold_counts = [len(golds.get(file_id, ())) for file_id in selected]
    all_types = sorted({
        str(record.get("type", "")) for corpus in (predictions, golds)
        for records in corpus.values() for record in records
    })
    per_type: dict[str, Any] = {}
    for entity_type in all_types:
        type_pred = [sum(str(record.get("type", "")) == entity_type for record in predictions.get(file_id, ())) for file_id in selected]
        type_gold = [sum(str(record.get("type", "")) == entity_type for record in golds.get(file_id, ())) for file_id in selected]
        per_type[entity_type] = {
            "predicted": _distribution(type_pred), "gold": _distribution(type_gold),
            "pred_gold_ratio": _safe_ratio(sum(type_pred), sum(type_gold)),
        }
    threshold = _percentile(pred_counts, 95)
    median = float(statistics.median(pred_counts)) if pred_counts else 0.0
    per_file = [{
        "file_id": file_id, "predicted": pred_count, "gold": gold_count,
        "pred_gold_ratio": _safe_ratio(pred_count, gold_count),
    } for file_id, pred_count, gold_count in zip(selected, pred_counts, gold_counts)]
    outliers = [item for item in per_file if item["predicted"] >= threshold and item["predicted"] > median]
    outliers.sort(key=lambda item: (-item["predicted"], _natural_key(item["file_id"])))
    return {
        "files": len(selected), "predicted": _distribution(pred_counts), "gold": _distribution(gold_counts),
        "pred_gold_ratio": _safe_ratio(sum(pred_counts), sum(gold_counts)), "per_type": per_type,
        "outlier_rule": "predicted entities >= corpus p95 and > corpus median",
        "outliers": outliers, "per_file": per_file,
    }


def build_runtime_report(
    per_file: Sequence[Mapping[str, Any]], *, startup_seconds: float, total_seconds: float,
    peak_python_memory_bytes: int, entities_by_type: Mapping[str, int],
) -> dict[str, Any]:
    cold = float(per_file[0]["seconds"]) if per_file else None
    warm_values = [float(item["seconds"]) for item in per_file[1:]]
    return {
        # Preserve NER-1 keys, adding explicit cold/warm and memory groups.
        "total_seconds": total_seconds, "peak_python_memory_bytes": peak_python_memory_bytes,
        "per_file": list(per_file), "entities_by_type": dict(sorted(entities_by_type.items())),
        "cold": {"pipeline_initialization_seconds": startup_seconds, "first_note_seconds": cold},
        "warm": {"note_count": len(warm_values), **_distribution(warm_values)},
        "memory": {"peak_python_bytes": peak_python_memory_bytes, **_process_memory()},
    }


def _distribution(values: Sequence[float | int]) -> dict[str, float | int]:
    numeric = [float(value) for value in values]
    return {
        "count": len(numeric), "total": sum(numeric),
        "mean": statistics.fmean(numeric) if numeric else 0.0,
        "median": float(statistics.median(numeric)) if numeric else 0.0,
        "p95": _percentile(numeric, 95),
    }


def _percentile(values: Sequence[float | int], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else (0.0 if numerator == 0 else None)


def _process_memory() -> dict[str, int | None]:
    current_rss: int | None = None
    peak_rss: int | None = None
    try:
        import psutil  # type: ignore
        current_rss = int(psutil.Process(os.getpid()).memory_info().rss)
        peak_rss = current_rss
    except (ImportError, OSError):
        try:
            import resource
            raw_peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            peak_rss = raw_peak if sys.platform == "darwin" else raw_peak * 1024
        except (ImportError, OSError):
            pass
    gpu_allocated: int | None = None
    gpu_reserved: int | None = None
    try:
        # Do not import a framework merely to measure it; the model backend will
        # already have loaded torch when CUDA is in use.
        torch = sys.modules.get("torch")
        if torch is not None and torch.cuda.is_available():
            gpu_allocated = int(torch.cuda.max_memory_allocated())
            gpu_reserved = int(torch.cuda.max_memory_reserved())
    except (AttributeError, RuntimeError):
        pass
    return {
        "process_rss_bytes": current_rss,
        "peak_process_rss_bytes": peak_rss,
        "peak_gpu_allocated_bytes": gpu_allocated,
        "peak_gpu_reserved_bytes": gpu_reserved,
    }


def _materialize_selected_corpus(ids: Sequence[str], input_dir: Path, gold_dir: Path, report_dir: Path) -> tuple[Path, Path]:
    selected_input, selected_gold = report_dir / "selected_input", report_dir / "selected_gold"
    selected_input.mkdir(parents=True, exist_ok=True)
    selected_gold.mkdir(parents=True, exist_ok=True)
    for stale in [*selected_input.glob("*"), *selected_gold.glob("*")]:
        if stale.is_file():
            stale.unlink()
    for file_id in ids:
        source_input, source_gold = input_dir / f"{file_id}.txt", gold_dir / f"{file_id}.json"
        (selected_input / source_input.name).write_text(source_input.read_text(encoding="utf-8"), encoding="utf-8")
        (selected_gold / source_gold.name).write_text(source_gold.read_text(encoding="utf-8"), encoding="utf-8")
    return selected_input, selected_gold


def _reset_json_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for stale in path.glob("*.json"):
        stale.unlink()


def _assert_lockbox_allowed(split: str, allow_lockbox: bool) -> None:
    if split in {"lockbox", "all"} and not allow_lockbox:
        raise PermissionError(
            f"split {split!r} includes sealed lockbox samples; pass --allow-lockbox only for an authorized milestone evaluation"
        )


def _split_ids(config: Mapping[str, Any], split: str) -> list[str]:
    if split == "all":
        values = [*config["development"]["ids"], *config["calibration"]["ids"], *config["lockbox"]["ids"]]
    else:
        values = config[split]["ids"]
    return [str(value) for value in values]


def _natural_key(value: str) -> tuple[int, int | str]:
    try:
        return (0, int(value))
    except ValueError:
        return (1, value)


if __name__ == "__main__":
    raise SystemExit(main())