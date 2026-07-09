from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from src.linking.terminology_normalizer import (
    ensure_text,
    normalize_for_lookup,
    normalize_no_diacritics_for_lookup,
    normalize_whitespace,
)


RXNORM_COLUMNS = [
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
]


RXNORM_DEFAULT_CONFIG: dict[str, Any] = {
    "sep": "|",
    "encoding": "utf-8",
    "keep_lat": ["ENG"],
    "keep_sab": ["RXNORM"],
    "suppress_values_to_drop": ["Y", "O"],
    "tty_keep": ["IN", "PIN", "MIN", "BN", "SCD", "SBD", "GPCK", "BPCK", "SCDC", "SCDF", "SBDF", "SBDC"],
}


STRENGTH_RE = re.compile(
    r"(?P<value>\d+(?:[\.,]\d+)?)\s*(?P<unit>MG(?:\s*/\s*(?:\d+(?:[\.,]\d+)?)?\s*ML)?|MCG|G|GM|ML|UNT|UNIT[S]?|%)",
    flags=re.IGNORECASE,
)

DOSE_FORM_HINTS = [
    "Oral Tablet",
    "Oral Capsule",
    "Oral Solution",
    "Oral Suspension",
    "Injectable Solution",
    "Injection",
    "Inhalation Solution",
    "Nasal Spray",
    "Topical Cream",
    "Topical Gel",
    "Ophthalmic Solution",
    "Extended Release Oral Tablet",
]


def read_rxnorm_rrf(
    path: str | Path,
    config: Mapping[str, Any] | None = None,
    *,
    nrows: int | None = None,
) -> pd.DataFrame:
    cfg = {**RXNORM_DEFAULT_CONFIG, **dict(config or {})}
    df = pd.read_csv(
        path,
        sep=str(cfg.get("sep", "|")),
        header=None,
        dtype=str,
        keep_default_na=False,
        encoding=str(cfg.get("encoding", "utf-8")),
        nrows=nrows,
    )
    if df.shape[1] == len(RXNORM_COLUMNS) + 1 and (df.iloc[:, -1] == "").all():
        df = df.iloc[:, :-1]
    if df.shape[1] != len(RXNORM_COLUMNS):
        raise ValueError(f"Expected {len(RXNORM_COLUMNS)} RxNorm columns, got {df.shape[1]}")
    df.columns = RXNORM_COLUMNS
    return df


def filter_rxnorm(df: pd.DataFrame, config: Mapping[str, Any] | None = None) -> pd.DataFrame:
    cfg = {**RXNORM_DEFAULT_CONFIG, **dict(config or {})}
    filtered = df.copy()
    keep_lat = set(cfg.get("keep_lat") or [])
    keep_sab = set(cfg.get("keep_sab") or [])
    tty_keep = set(cfg.get("tty_keep") or [])
    suppress_drop = set(cfg.get("suppress_values_to_drop") or [])
    if keep_lat:
        filtered = filtered[filtered["LAT"].isin(keep_lat)]
    if keep_sab:
        filtered = filtered[filtered["SAB"].isin(keep_sab)]
    if tty_keep:
        filtered = filtered[filtered["TTY"].isin(tty_keep)]
    if suppress_drop:
        filtered = filtered[~filtered["SUPPRESS"].isin(suppress_drop)]
    filtered = filtered[filtered["STR"].str.strip() != ""]
    return filtered.reset_index(drop=True)


def parse_strength(text: object) -> tuple[float | None, str | None]:
    match = STRENGTH_RE.search(ensure_text(text))
    if not match:
        return None, None
    raw_value = match.group("value").replace(",", ".")
    try:
        value = float(raw_value)
    except ValueError:
        value = None
    unit = re.sub(r"\s+", "", match.group("unit").upper())
    return value, unit


def guess_ingredient(text: object) -> str:
    value = normalize_whitespace(text)
    match = STRENGTH_RE.search(value)
    if match:
        value = value[: match.start()]
    value = re.sub(r"\[[^\]]+\]", " ", value)
    value = re.sub(r"\s*/\s*", " / ", value)
    return normalize_whitespace(value.strip(" /-"))


def guess_dose_form(text: object) -> str:
    value = ensure_text(text).lower()
    for hint in DOSE_FORM_HINTS:
        if hint.lower() in value:
            return hint
    return ""


def build_rxnorm_index(df: pd.DataFrame, config: Mapping[str, Any] | None = None) -> pd.DataFrame:
    filtered = filter_rxnorm(df, config)
    rows: list[dict[str, Any]] = []
    for _, row in filtered.iterrows():
        tty = ensure_text(row.get("TTY", ""))
        drug_str = normalize_whitespace(row.get("STR", ""))
        strength_value, strength_unit = parse_strength(drug_str)
        rows.append(
            {
                "rxcui": ensure_text(row.get("RXCUI", "")),
                "tty": tty,
                "str": drug_str,
                "str_norm": normalize_for_lookup(drug_str),
                "ingredient_guess": guess_ingredient(drug_str),
                "strength_value": strength_value,
                "strength_unit": strength_unit or "",
                "dose_form_guess": guess_dose_form(drug_str),
                "is_brand": tty in {"BN", "SBD", "SBDF", "SBDC"},
                "is_clinical_drug": tty in {"SCD", "SBD", "GPCK", "BPCK"},
                "is_ingredient": tty in {"IN", "PIN", "MIN"},
                "sab": ensure_text(row.get("SAB", "")),
                "suppress": ensure_text(row.get("SUPPRESS", "")),
                "metadata_json": json.dumps(
                    {
                        "code": ensure_text(row.get("CODE", "")),
                        "rxaui": ensure_text(row.get("RXAUI", "")),
                        "ispref": ensure_text(row.get("ISPREF", "")),
                    },
                    ensure_ascii=False,
                ),
            }
        )
    index = pd.DataFrame(rows)
    if not index.empty:
        index = index.drop_duplicates(subset=["rxcui", "tty", "str_norm"]).reset_index(drop=True)
    return index


def build_rxnorm_aliases(
    index_df: pd.DataFrame,
    *,
    manual_alias_path: str | Path | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, row in index_df.iterrows():
        rows.append(_make_rx_alias_row(row, alias=row["str"], alias_source="rxnorm_str"))
        ingredient = normalize_whitespace(row.get("ingredient_guess", ""))
        if ingredient and normalize_for_lookup(ingredient) != row.get("str_norm", ""):
            rows.append(_make_rx_alias_row(row, alias=ingredient, alias_source="ingredient_guess"))
    rows.extend(_manual_rx_alias_rows(manual_alias_path, index_df))
    aliases = pd.DataFrame(rows)
    if not aliases.empty:
        aliases = aliases[aliases["alias_norm"] != ""]
        aliases = aliases.drop_duplicates(subset=["rxcui", "alias_norm", "alias_source"]).reset_index(drop=True)
    return aliases


def build_and_write_rxnorm_resources(
    rxnorm_rrf: str | Path,
    processed_dir: str | Path,
    config: Mapping[str, Any] | None = None,
    *,
    manual_alias_path: str | Path | None = None,
) -> dict[str, Any]:
    processed = Path(processed_dir)
    processed.mkdir(parents=True, exist_ok=True)
    df = read_rxnorm_rrf(rxnorm_rrf, config)
    index_df = build_rxnorm_index(df, config)
    aliases_df = build_rxnorm_aliases(index_df, manual_alias_path=manual_alias_path)

    index_path = processed / "rxnorm_index.parquet"
    aliases_path = processed / "rxnorm_aliases.parquet"
    index_df.to_parquet(index_path, index=False)
    aliases_df.to_parquet(aliases_path, index=False)
    return {
        "rxnorm_rows_raw": int(len(df)),
        "rxnorm_rows_filtered": int(len(index_df)),
        "rxnorm_aliases": int(len(aliases_df)),
        "rxnorm_index_path": str(index_path),
        "rxnorm_aliases_path": str(aliases_path),
    }


def _make_rx_alias_row(row: Mapping[str, Any], *, alias: str, alias_source: str, notes: str = "") -> dict[str, Any]:
    return {
        "rxcui": ensure_text(row.get("rxcui", "")),
        "tty": ensure_text(row.get("tty", "")),
        "alias": normalize_whitespace(alias),
        "alias_norm": normalize_for_lookup(alias),
        "alias_no_diacritics": normalize_no_diacritics_for_lookup(alias),
        "alias_source": alias_source,
        "ingredient_guess": normalize_whitespace(row.get("ingredient_guess", "")),
        "strength_value": row.get("strength_value"),
        "strength_unit": ensure_text(row.get("strength_unit", "")),
        "dose_form_guess": ensure_text(row.get("dose_form_guess", "")),
        "is_brand": bool(row.get("is_brand", False)),
        "is_clinical_drug": bool(row.get("is_clinical_drug", False)),
        "metadata_json": json.dumps({"notes": notes, "source_str": ensure_text(row.get("str", ""))}, ensure_ascii=False),
    }


def _manual_rx_alias_rows(manual_alias_path: str | Path | None, index_df: pd.DataFrame) -> list[dict[str, Any]]:
    if not manual_alias_path:
        return []
    path = Path(manual_alias_path)
    if not path.is_file():
        return []
    manual = pd.read_csv(path, dtype=str, keep_default_na=False)
    rows: list[dict[str, Any]] = []
    for _, row in manual.iterrows():
        alias = normalize_whitespace(row.get("alias", ""))
        generic_hint = normalize_for_lookup(row.get("generic_hint", ""))
        rxcui_hint = ensure_text(row.get("rxcui_hint", "")).strip()
        if not alias:
            continue
        candidates = index_df
        if rxcui_hint:
            candidates = candidates[candidates["rxcui"] == rxcui_hint]
        elif generic_hint:
            ingredient_exact = candidates[
                (candidates["is_ingredient"])
                & (candidates["str_norm"].isin({generic_hint, generic_hint.replace(" ", "/")}))
            ]
            if ingredient_exact.empty:
                ingredient_exact = candidates[candidates["str_norm"] == generic_hint]
            candidates = ingredient_exact
        if candidates.empty:
            continue
        preferred = candidates.sort_values(by=["is_ingredient", "is_clinical_drug"], ascending=[False, False]).head(3)
        for _, candidate in preferred.iterrows():
            rows.append(_make_rx_alias_row(candidate, alias=alias, alias_source="manual_brand", notes=ensure_text(row.get("notes", ""))))
    return rows
