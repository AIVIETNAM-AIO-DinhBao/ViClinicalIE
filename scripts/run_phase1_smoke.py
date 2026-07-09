from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.config import load_config
from src.linking.icd10_index import build_icd10_aliases, build_icd10_index, read_icd10_csv
from src.linking.rxnorm_index import build_rxnorm_aliases, build_rxnorm_index, parse_strength, read_rxnorm_rrf
from src.linking.sparse_retriever import SparseAliasRetriever


def main() -> int:
    parser = argparse.ArgumentParser(description="Run lightweight Phase 1 smoke checks without pytest.")
    parser.add_argument("--config", default="configs/default.yaml", help="Path to YAML config.")
    parser.add_argument(
        "--skip-retrieval",
        action="store_true",
        help="Skip loading full TF-IDF matrices; only validate parsers/builders on samples.",
    )
    args = parser.parse_args()

    config = load_config(args.config, project_root=PROJECT_ROOT)
    _check_icd_sample(config)
    _check_rxnorm_sample(config)
    if not args.skip_retrieval:
        _check_sparse_retrieval(config)
    print("Phase 1 smoke checks passed.")
    return 0


def _check_icd_sample(config) -> None:
    df = read_icd10_csv(config.path("icd10_csv"), config.raw["icd10"], nrows=50)
    assert df.shape[1] == 29
    assert df.iloc[0]["MÃ BỆNH"] == "A00"
    index = build_icd10_index(df, config.raw["icd10"])
    aliases = build_icd10_aliases(
        df,
        index,
        config.raw["icd10"],
        manual_alias_path=config.path("diagnosis_aliases_csv"),
    )
    assert "A00" in set(index["code"])
    assert "bệnh tả" in set(aliases["alias_norm"])
    assert "GERD" in set(aliases["alias"])


def _check_rxnorm_sample(config) -> None:
    df = read_rxnorm_rrf(config.path("rxnorm_rff"), config.raw["rxnorm"], nrows=1000)
    assert df.shape[1] == 18
    assert df.iloc[0]["STR"] == "Parlodel"
    assert parse_strength("metoprolol 25 MG Oral Tablet") == (25.0, "MG")
    assert parse_strength("Chlorpheniramine 0.4 MG/ML") == (0.4, "MG/ML")

    synthetic = pd.DataFrame(
        [
            ["1", "ENG", "", "", "", "", "", "a", "", "", "", "RXNORM", "IN", "1", "metoprolol", "", "N", ""],
            ["2", "ENG", "", "", "", "", "", "b", "", "", "", "RXNORM", "SCD", "2", "metoprolol 25 MG Oral Tablet", "", "N", ""],
        ],
        columns=[
            "RXCUI",
            "LAT",
            "TS",
            "LUI",
            "STT",
            "SUI",
            "ISPREF",
            "RXAUI",
            "SAUI",
            "SCUI",
            "SDUI",
            "SAB",
            "TTY",
            "CODE",
            "STR",
            "SRL",
            "SUPPRESS",
            "CVF",
        ],
    )
    rx_index = build_rxnorm_index(synthetic)
    rx_aliases = build_rxnorm_aliases(rx_index)
    assert "metoprolol 25 mg oral tablet" in set(rx_aliases["alias_norm"])


def _check_sparse_retrieval(config) -> None:
    processed = config.path("processed_dir")
    required = [
        processed / "icd10_aliases.parquet",
        processed / "rxnorm_aliases.parquet",
        processed / "vector_indices" / "icd_tfidf.pkl",
        processed / "vector_indices" / "icd_tfidf_matrix.npz",
        processed / "vector_indices" / "rx_tfidf.pkl",
        processed / "vector_indices" / "rx_tfidf_matrix.npz",
    ]
    missing = [path for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing built artifacts: {missing}")

    icd = SparseAliasRetriever.from_processed(processed, kind="icd")
    rx = SparseAliasRetriever.from_processed(processed, kind="rx")
    assert icd.query("COPD", top_k=1)[0].code == "J44.9"
    metoprolol = rx.query("metoprolol 25 mg", top_k=3)
    assert metoprolol
    assert any("25" in result.metadata.get("alias", "") for result in metoprolol)


if __name__ == "__main__":
    raise SystemExit(main())
