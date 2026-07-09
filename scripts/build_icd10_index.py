from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.io_utils import write_json
from src.linking.icd10_index import build_and_write_icd10_resources
from src.logging_utils import create_run_report_dir, write_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ICD-10 canonical and alias parquet resources.")
    parser.add_argument("--config", default="configs/default.yaml", help="Path to YAML config.")
    args = parser.parse_args()

    config = load_config(args.config, project_root=PROJECT_ROOT)
    icd_cfg = config.raw.get("icd10", {})
    manual_alias_key = icd_cfg.get("manual_alias_path_key", "diagnosis_aliases_csv")
    manual_alias_path = config.path(manual_alias_key) if manual_alias_key in config.paths else None

    summary = build_and_write_icd10_resources(
        config.path("icd10_csv"),
        config.path("processed_dir"),
        icd_cfg,
        manual_alias_path=manual_alias_path,
    )
    summary["status"] = "passed"

    report_dir = create_run_report_dir(
        config.path("report_dir"),
        config,
        run_name="build_icd10_index",
        log_files=config.raw.get("logging", {}).get("log_files"),
    )
    write_summary(report_dir, summary)
    write_json(report_dir / "icd10_summary.json", summary)

    print("ICD-10 index build passed.")
    print(f"rows: {summary['icd10_index_rows']}")
    print(f"aliases: {summary['icd10_aliases']}")
    print(f"report_dir: {report_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
