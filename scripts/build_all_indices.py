from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.io_utils import write_json
from src.linking.icd10_index import build_and_write_icd10_resources
from src.linking.rxnorm_index import build_and_write_rxnorm_resources
from src.linking.sparse_retriever import build_and_write_sparse_indices
from src.logging_utils import create_run_report_dir, write_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build all Phase 1 terminology and sparse retrieval indices.")
    parser.add_argument("--config", default="configs/default.yaml", help="Path to YAML config.")
    parser.add_argument(
        "--skip-sparse",
        action="store_true",
        help="Build parquet terminology resources only; skip TF-IDF artifacts.",
    )
    args = parser.parse_args()

    config = load_config(args.config, project_root=PROJECT_ROOT)
    summary: dict[str, Any] = {"status": "running"}

    icd_cfg = config.raw.get("icd10", {})
    rx_cfg = config.raw.get("rxnorm", {})
    sparse_cfg = config.raw.get("sparse", {})

    icd_manual_key = icd_cfg.get("manual_alias_path_key", "diagnosis_aliases_csv")
    rx_manual_key = rx_cfg.get("manual_alias_path_key", "drug_aliases_csv")

    summary.update(
        build_and_write_icd10_resources(
            config.path("icd10_csv"),
            config.path("processed_dir"),
            icd_cfg,
            manual_alias_path=config.path(icd_manual_key) if icd_manual_key in config.paths else None,
        )
    )
    summary.update(
        build_and_write_rxnorm_resources(
            config.path("rxnorm_rff"),
            config.path("processed_dir"),
            rx_cfg,
            manual_alias_path=config.path(rx_manual_key) if rx_manual_key in config.paths else None,
        )
    )
    if not args.skip_sparse:
        summary.update(
            build_and_write_sparse_indices(
                config.path("processed_dir"),
                config.path("vector_index_dir") if "vector_index_dir" in config.paths else None,
                sparse_cfg,
            )
        )
    summary["status"] = "passed"

    report_dir = create_run_report_dir(
        config.path("report_dir"),
        config,
        run_name="build_indices",
        log_files=config.raw.get("logging", {}).get("log_files"),
    )
    summary["report_dir"] = str(report_dir)
    write_summary(report_dir, summary)
    write_json(report_dir / "build_indices_summary.json", summary)

    print("Phase 1 terminology index build passed.")
    print(f"icd10_index_rows: {summary['icd10_index_rows']}")
    print(f"icd10_aliases: {summary['icd10_aliases']}")
    print(f"rxnorm_rows_filtered: {summary['rxnorm_rows_filtered']}")
    print(f"rxnorm_aliases: {summary['rxnorm_aliases']}")
    if not args.skip_sparse:
        print(f"icd_tfidf_shape: {summary['icd_tfidf_shape']}")
        print(f"rx_tfidf_shape: {summary['rx_tfidf_shape']}")
    print(f"report_dir: {report_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
