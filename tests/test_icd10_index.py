from __future__ import annotations

import pandas as pd

from src.config import load_config
from src.linking.icd10_index import build_icd10_aliases, build_icd10_index, read_icd10_csv
from src.linking.terminology_normalizer import normalize_for_lookup, normalize_no_diacritics_for_lookup


def test_read_icd10_csv_handles_semicolon_and_skips_numeric_row() -> None:
    config = load_config("configs/default.yaml")
    df = read_icd10_csv(config.path("icd10_csv"), config.raw["icd10"], nrows=5)

    assert df.shape[1] == 29
    assert df.iloc[0]["MÃ BỆNH"] == "A00"
    assert df.iloc[0]["TÊN BỆNH"] == "Bệnh tả"
    assert "1" not in set(df["MÃ BỆNH"].head(1))


def test_build_icd10_index_and_aliases_from_sample() -> None:
    config = load_config("configs/default.yaml")
    df = read_icd10_csv(config.path("icd10_csv"), config.raw["icd10"], nrows=50)
    index = build_icd10_index(df, config.raw["icd10"])
    aliases = build_icd10_aliases(
        df,
        index,
        config.raw["icd10"],
        manual_alias_path=config.path("diagnosis_aliases_csv"),
    )

    assert {"code", "canonical_name_vi", "canonical_name_en", "metadata_json"} <= set(index.columns)
    assert {"code", "alias", "alias_norm", "alias_no_diacritics", "alias_source"} <= set(aliases.columns)
    assert "A00" in set(index["code"])
    assert "bệnh tả" in set(aliases["alias_norm"])
    assert "cholera" in set(aliases["alias_norm"])
    assert "GERD" in set(aliases["alias"])


def test_normalization_supports_vietnamese_accent_insensitive_lookup() -> None:
    assert normalize_for_lookup(" Bệnh   Tả ") == "bệnh tả"
    assert normalize_no_diacritics_for_lookup("Bệnh trào ngược dạ dày") == "benh trao nguoc da day"
