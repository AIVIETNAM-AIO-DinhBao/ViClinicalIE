from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data_types import VALID_ENTITY_TYPES
from src.io_utils import read_json, write_json, write_text


ERROR_FILES = {
    "false_positives": "false_positives.jsonl",
    "false_negatives": "false_negatives.jsonl",
    "boundary_errors": "span_mismatches.jsonl",
    "type_confusions": "type_mismatches.jsonl",
}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Build the selected NER-2 residual-error handoff.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-dir", default="outputs/reports/v2_ner2_selected/error_review")
    parser.add_argument("--data-requests", default="data/golden/DATA_REQUESTS_V1.md")
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    evaluation_dir = _evaluation_dir(run_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    debug_index = _load_debug_index(run_dir / "debug_entities")
    summary: dict[str, Any] = {"run_dir": str(run_dir), "by_type": {}}
    for entity_type in sorted(VALID_ENTITY_TYPES):
        type_summary: dict[str, Any] = {}
        for category, file_name in ERROR_FILES.items():
            rows = _read_jsonl(evaluation_dir / file_name)
            selected = [_attach_provenance(row, debug_index) for row in rows if _row_mentions_type(row, entity_type)]
            path = output_dir / entity_type / file_name
            _write_jsonl(path, selected)
            type_summary[category] = len(selected)
        summary["by_type"][entity_type] = type_summary

    density_path = run_dir / "density.json"
    density = read_json(density_path) if density_path.is_file() else {}
    summary["density_outliers"] = density.get("notes_over_guard", density.get("outliers", [])) if isinstance(density, dict) else []
    summary["pass_contribution"] = _pass_contribution(debug_index)
    write_json(output_dir / "error_review_summary.json", summary)
    write_text(args.data_requests, _data_requests_markdown(summary))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _evaluation_dir(run_dir: Path) -> Path:
    candidates = [run_dir / "evaluation" / "extraction_only", run_dir / "evaluation", run_dir]
    for path in candidates:
        if (path / "false_positives.jsonl").is_file():
            return path
    raise FileNotFoundError(f"No extraction evaluation artifacts under {run_dir}")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _row_mentions_type(row: dict[str, Any], entity_type: str) -> bool:
    return any(isinstance(value, dict) and value.get("type") == entity_type for value in row.values())


def _load_debug_index(directory: Path) -> dict[tuple[str, int, int, str], dict[str, Any]]:
    index: dict[tuple[str, int, int, str], dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        rows = read_json(path)
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            position = row.get("position", [])
            if isinstance(position, list) and len(position) == 2:
                index[(path.stem, int(position[0]), int(position[1]), str(row.get("type", "")))] = row.get("provenance", {})
    return index


def _attach_provenance(row: dict[str, Any], index: dict[tuple[str, int, int, str], dict[str, Any]]) -> dict[str, Any]:
    output = dict(row)
    file_id = str(row.get("file_id", ""))
    pred = row.get("pred")
    if isinstance(pred, dict):
        position = pred.get("position", [])
        if isinstance(position, list) and len(position) == 2:
            output["prediction_provenance"] = index.get((file_id, int(position[0]), int(position[1]), str(pred.get("type", ""))), {})
    return output


def _pass_contribution(index: dict[tuple[str, int, int, str], dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for provenance in index.values():
        features = provenance.get("chosen_candidate_features", {}) if isinstance(provenance, dict) else {}
        passes = features.get("supporting_passes", []) if isinstance(features, dict) else []
        for pass_name in passes if isinstance(passes, list) else []:
            counts[str(pass_name)] += 1
    return dict(sorted(counts.items()))


def _data_requests_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# NER-2 Residual Data Requests V1",
        "",
        "Generated only from the frozen selected NER-2 extraction error review. Preserve the shared V2 annotation contract and raw offsets.",
        "",
        "## Shared gate",
        "",
        "- Use `data/golden/ANNOTATION_GUIDELINE_V2.md` and `data/golden/ner_data_schema.json`.",
        "- Keep clean/noisy variants grouped by original sample and concept family.",
        "- Validate pilots with `python scripts/validate_ner_dataset.py --input <file.jsonl>`.",
        "- Do not use lockbox examples or predictions for data generation.",
        "",
    ]
    for entity_type, counts in summary.get("by_type", {}).items():
        lines.extend([
            f"## {entity_type}",
            "",
            f"Residual counts: FP={counts.get('false_positives', 0)}, FN={counts.get('false_negatives', 0)}, boundary={counts.get('boundary_errors', 0)}, type={counts.get('type_confusions', 0)}.",
            "",
            "Create reviewed minimal contrasts targeting the corresponding residual files under the selected error-review artifact. Include positive, hard-negative, boundary, type-confusion, mixed-language, and noisy-format variants.",
            "",
        ])
    lines.extend(["## Owners", "", "- Problem Data owner: `TRIỆU_CHỨNG`, `CHẨN_ĐOÁN`.", "- Structured Data owner: `THUỐC`, `TÊN_XÉT_NGHIỆM`, `KẾT_QUẢ_XÉT_NGHIỆM`.", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())