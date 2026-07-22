from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.io_utils import write_json
from src.ner.experiment_registry import directory_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Require byte-identical NER-2 prediction directories.")
    parser.add_argument("--run1", required=True)
    parser.add_argument("--run2", required=True)
    parser.add_argument("--output", default="outputs/reports/v2_ner2_selected/determinism.json")
    parser.add_argument("--expected-count", type=int, default=None)
    args = parser.parse_args()
    first = directory_manifest(args.run1, "*.json")
    second = directory_manifest(args.run2, "*.json")
    names = sorted(set(first) | set(second))
    mismatches = [name for name in names if first.get(name) != second.get(name)]
    report = {
        "byte_identical": not mismatches,
        "files_run1": len(first), "files_run2": len(second),
        "expected_count": args.expected_count, "mismatched_files": mismatches,
        "run1": first, "run2": second,
    }
    if args.expected_count is not None and (len(first) != args.expected_count or len(second) != args.expected_count):
        report["byte_identical"] = False
        report["count_error"] = True
    write_json(args.output, report)
    print(json.dumps({"byte_identical": report["byte_identical"], "mismatch_count": len(mismatches)}, ensure_ascii=False))
    return 0 if report["byte_identical"] else 2


if __name__ == "__main__":
    raise SystemExit(main())