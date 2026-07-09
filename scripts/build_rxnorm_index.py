from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.io_utils import write_json
from src.linking.rxnorm_index import build_and_write_rxnorm_resources
from src.logging_utils import create_run_report_dir, write_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build RxNorm canonical and alias parquet resources.")
    parser.add_argument("--config", default="configs/default.yaml", help="Path to YAML config.")
    args = parser.parse_args()

    config = load_config(args.config, project_root=PROJECT_ROOT)
    rx_cfg = config.raw.get("rxnorm", {})
    manual_alias_key = rx_cfg.get("manual_alias_path_key", "drug_aliases_csv")
    manual_alias_path = config.path(manual_alias_key) if manual_alias_key in config.paths else None

    summary = build_and_write_rxnorm_resources(
        config.path("rxnorm_rff"),
        config.path("processed_dir"),
        rx_cfg,
        manual_alias_path=manual_alias_path,
    )
    summary["status"] = "passed"

    report_dir = create_run_report_dir(
        config.path("report_dir"),
        config,
        run_name="build_rxnorm_index",
        log_files=config.raw.get("logging", {}).get("log_files"),
    )
    write_summary(report_dir, summary)
    write_json(report_dir / "rxnorm_summary.json", summary)

    print("RxNorm index build passed.")
    print(f"raw_rows: {summary['rxnorm_rows_raw']}")
    print(f"filtered_rows: {summary['rxnorm_rows_filtered']}")
    print(f"aliases: {summary['rxnorm_aliases']}")
    print(f"report_dir: {report_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
