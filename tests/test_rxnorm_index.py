from __future__ import annotations

import pandas as pd

from src.config import load_config
from src.linking.rxnorm_index import (
    build_rxnorm_aliases,
    build_rxnorm_index,
    filter_rxnorm,
    parse_strength,
    read_rxnorm_rrf,
)


def test_read_rxnorm_rrf_handles_trailing_pipe() -> None:
    config = load_config("configs/default.yaml")
    df = read_rxnorm_rrf(config.path("rxnorm_rff"), config.raw["rxnorm"], nrows=1000)

    assert df.shape[1] == 18
    assert list(df.columns)[0] == "RXCUI"
    assert df.iloc[0]["RXCUI"] == "38"
    assert df.iloc[0]["TTY"] == "BN"
    assert df.iloc[0]["STR"] == "Parlodel"


def test_filter_rxnorm_keeps_expected_schema_values() -> None:
    config = load_config("configs/default.yaml")
    df = read_rxnorm_rrf(config.path("rxnorm_rff"), config.raw["rxnorm"], nrows=5000)
    filtered = filter_rxnorm(df, config.raw["rxnorm"])

    assert not filtered.empty
    assert set(filtered["LAT"]) <= {"ENG"}
    assert set(filtered["SAB"]) <= {"RXNORM"}
    assert not set(filtered["SUPPRESS"]) & {"Y", "O"}


def test_parse_strength_examples() -> None:
    assert parse_strength("metoprolol 25 MG Oral Tablet") == (25.0, "MG")
    assert parse_strength("Chlorpheniramine 0.4 MG/ML") == (0.4, "MG/ML")
    assert parse_strength("example 750 MG/150ML solution") == (750.0, "MG/150ML")


def test_build_rxnorm_index_and_aliases_from_synthetic_rows() -> None:
    rows = [
        ["1", "ENG", "", "", "", "", "", "a", "", "", "", "RXNORM", "IN", "1", "metoprolol", "", "N", ""],
        ["2", "ENG", "", "", "", "", "", "b", "", "", "", "RXNORM", "SCD", "2", "metoprolol 25 MG Oral Tablet", "", "N", ""],
        ["3", "ENG", "", "", "", "", "", "c", "", "", "", "RXNORM", "BN", "3", "Tylenol", "", "N", ""],
        ["4", "ENG", "", "", "", "", "", "d", "", "", "", "RXNORM", "IN", "4", "acetaminophen", "", "N", ""],
    ]
    from src.linking.rxnorm_index import RXNORM_COLUMNS

    df = pd.DataFrame(rows, columns=RXNORM_COLUMNS)
    index = build_rxnorm_index(df)
    aliases = build_rxnorm_aliases(index)

    assert "metoprolol" in set(index["str_norm"])
    assert 25.0 in set(index["strength_value"].dropna())
    assert "metoprolol" in set(aliases["alias_norm"])
    assert "metoprolol 25 mg oral tablet" in set(aliases["alias_norm"])
