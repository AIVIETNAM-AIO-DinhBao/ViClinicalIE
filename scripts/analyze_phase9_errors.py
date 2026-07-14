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
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize Phase 12/9 evaluator error JSONL files for metric-guided tuning.")
    parser.add_argument("--report-dir", required=True, help="Evaluator report directory.")
    parser.add_argument("--top-k", type=int, default=20, help="Number of top items to print per section.")
    args = parser.parse_args()

    report_dir = _resolve_project_path(args.report_dir)
    if not report_dir.is_dir():
        raise FileNotFoundError(report_dir)

    fp_rows = _read_jsonl(report_dir / "false_positives.jsonl")
    fn_rows = _read_jsonl(report_dir / "false_negatives.jsonl")
    type_rows = _read_jsonl(report_dir / "type_mismatches.jsonl")
    span_rows = _read_jsonl(report_dir / "span_mismatches.jsonl")
    assertion_rows = _read_jsonl(report_dir / "assertion_mismatches.jsonl")
    candidate_rows = _read_jsonl(report_dir / "candidate_mismatches.jsonl")

    print("Phase 9 error analysis")
    print(f"report_dir: {report_dir}")
    print(f"false_positive_count: {len(fp_rows)}")
    print(f"false_negative_count: {len(fn_rows)}")
    print(f"type_mismatch_count: {len(type_rows)}")
    print(f"span_mismatch_count: {len(span_rows)}")
    print(f"assertion_mismatch_count: {len(assertion_rows)}")
    print(f"candidate_mismatch_count: {len(candidate_rows)}")

    _print_counter("top_false_positive_text", _counter(fp_rows, "pred", "text"), args.top_k)
    _print_counter("top_false_positive_type", _counter(fp_rows, "pred", "type"), args.top_k)
    _print_counter("top_false_negative_type", _counter(fn_rows, "gold", "type"), args.top_k)
    _print_counter("top_type_mismatch_pair", Counter(str(row.get("subcategory", "")) for row in type_rows), args.top_k)
    _print_counter("top_assertion_pred_sets", Counter(str(row.get("pred", {}).get("assertions", [])) for row in assertion_rows), args.top_k)
    _print_counter("top_candidate_subcategory", Counter(str(row.get("subcategory", "")) for row in candidate_rows), args.top_k)
    _print_span_lengths(span_rows, args.top_k)
    return 0


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _counter(rows: list[dict[str, Any]], container: str, key: str) -> Counter[str]:
    return Counter(str(row.get(container, {}).get(key, "")) for row in rows)


def _print_counter(title: str, counter: Counter[str], top_k: int) -> None:
    print(f"\n{title}:")
    for value, count in counter.most_common(top_k):
        if value:
            print(f"  {count:4d}  {value}")


def _print_span_lengths(rows: list[dict[str, Any]], top_k: int) -> None:
    print("\ntop_span_mismatch_pred_longer_than_gold:")
    scored: list[tuple[int, str, str, str]] = []
    for row in rows:
        pred = row.get("pred", {})
        gold = row.get("gold", {})
        pred_text = str(pred.get("text", ""))
        gold_text = str(gold.get("text", ""))
        scored.append((len(pred_text) - len(gold_text), str(row.get("file_id", "")), pred_text, gold_text))
    for delta, file_id, pred_text, gold_text in sorted(scored, reverse=True)[:top_k]:
        print(f"  +{delta:3d} file={file_id} pred={pred_text!r} gold={gold_text!r}")


def _resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())