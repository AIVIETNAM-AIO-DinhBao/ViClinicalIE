from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from src.linking.terminology_normalizer import (
    dotless_code,
    ensure_text,
    guess_alias_lang,
    normalize_code,
    normalize_for_lookup,
    normalize_no_diacritics_for_lookup,
    normalize_whitespace,
)


ICD10_REQUIRED_COLUMNS = {
    "MÃ BỆNH",
    "MÃ BỆNH KHÔNG DẤU",
    "DISEASE NAME WHO 2019 (ENGLISH)",
    "TÊN BỆNH",
}


ICD10_DEFAULT_CONFIG: dict[str, Any] = {
    "sep": ";",
    "encoding": "utf-8-sig",
    "skiprows": [1],
    "code_col": "MÃ BỆNH",
    "code_no_dot_col": "MÃ BỆNH KHÔNG DẤU",
    "vi_name_col": "TÊN BỆNH",
    "en_name_col": "DISEASE NAME WHO 2019 (ENGLISH)",
    "vi_guidance_col": "HƯỚNG DẪN MÃ HÓA BỔ SUNG CỦA WHO 2019",
    "en_guidance_col": "ADDITIONAL CODING GUIDANCE WHO 2019 (ENGLISH)",
}


def read_icd10_csv(
    path: str | Path,
    config: Mapping[str, Any] | None = None,
    *,
    nrows: int | None = None,
) -> pd.DataFrame:
    cfg = {**ICD10_DEFAULT_CONFIG, **dict(config or {})}
    df = pd.read_csv(
        path,
        sep=str(cfg.get("sep", ";")),
        encoding=str(cfg.get("encoding", "utf-8-sig")),
        dtype=str,
        keep_default_na=False,
        skiprows=cfg.get("skiprows", [1]),
        nrows=nrows,
    )
    missing = ICD10_REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"ICD-10 CSV missing required columns: {sorted(missing)}")
    return df


def build_icd10_index(df: pd.DataFrame, config: Mapping[str, Any] | None = None) -> pd.DataFrame:
    cfg = {**ICD10_DEFAULT_CONFIG, **dict(config or {})}
    code_col = str(cfg["code_col"])
    code_no_dot_col = str(cfg["code_no_dot_col"])
    vi_name_col = str(cfg["vi_name_col"])
    en_name_col = str(cfg["en_name_col"])

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        code = normalize_code(row.get(code_col, ""))
        if not code:
            continue
        code_no_dot = normalize_code(row.get(code_no_dot_col, "")) or dotless_code(code)
        flag_metadata = {
            "not_primary": ensure_text(row.get("MÃ KHÔNG ĐƯỢC DÙNG LÀ BỆNH CHÍNH", "")),
            "not_recommended_primary": ensure_text(row.get("MÃ KHÔNG KHUYẾN KHÍCH DÙNG LÀ BỆNH CHÍNH", "")),
            "has_more_specific_code": ensure_text(row.get("MÃ KHÔNG ĐƯỢC SỬ DỤNG VÌ CÓ MÃ 4 HOẶC 5 KÝ TỰ CỤ THỂ HƠN", "")),
            "mortality_only": ensure_text(row.get("CHỈ SỬ DỤNG MÃ HÓA NGUYÊN NHÂN TỬ VONG", "")),
            "female_only": ensure_text(row.get("CÁC MÃ BỆNH CHỈ CÓ HOẶC CHỦ YẾU CÓ Ở NỮ GIỚI", "")),
            "male_only": ensure_text(row.get("CÁC MÃ BỆNH CHỈ CÓ HOẶC CHỦ YẾU CÓ Ở NAM GIỚI", "")),
        }
        rows.append(
            {
                "code": code,
                "code_no_dot": code_no_dot,
                "canonical_name_vi": normalize_whitespace(row.get(vi_name_col, "")),
                "canonical_name_en": normalize_whitespace(row.get(en_name_col, "")),
                "chapter_code_or_range": normalize_whitespace(row.get("PHẠM VI MÃ NHÓM BỆNH", "")),
                "chapter_name_vi": normalize_whitespace(row.get("TÊN CHƯƠNG", "")),
                "chapter_name_en": normalize_whitespace(row.get("CHAPTER NAME", "")),
                "block_code": normalize_whitespace(row.get("MÃ KHỐI", "")),
                "block_name_vi": normalize_whitespace(row.get("TÊN KHỐI", "")),
                "block_name_en": normalize_whitespace(row.get("BLOCK NAME", "")),
                "three_char_code": normalize_code(row.get("MÃ NHÓM BỆNH 3 KÝ TỰ", "")),
                "three_char_name_vi": normalize_whitespace(row.get("TÊN NHÓM BỆNH 3 KÝ TỰ", "")),
                "three_char_name_en": normalize_whitespace(row.get("3-CHARACTER SUB-CATEGORY NAME", "")),
                "is_valid_primary_allowed": not any(flag_metadata[key] for key in ("not_primary", "has_more_specific_code", "mortality_only")),
                "metadata_json": json.dumps(flag_metadata, ensure_ascii=False),
            }
        )

    index = pd.DataFrame(rows)
    if not index.empty:
        index = index.drop_duplicates(subset=["code"]).sort_values("code").reset_index(drop=True)
    return index


def build_icd10_aliases(
    df: pd.DataFrame,
    index_df: pd.DataFrame,
    config: Mapping[str, Any] | None = None,
    *,
    manual_alias_path: str | Path | None = None,
) -> pd.DataFrame:
    cfg = {**ICD10_DEFAULT_CONFIG, **dict(config or {})}
    code_col = str(cfg["code_col"])
    vi_name_col = str(cfg["vi_name_col"])
    en_name_col = str(cfg["en_name_col"])
    vi_guidance_col = str(cfg["vi_guidance_col"])
    en_guidance_col = str(cfg["en_guidance_col"])

    canonical_by_code = index_df.set_index("code").to_dict("index") if not index_df.empty else {}
    alias_rows: list[dict[str, Any]] = []

    source_columns = [
        (vi_name_col, "disease_name_vi", False),
        (en_name_col, "disease_name_en", False),
        (vi_guidance_col, "guidance_vi", False),
        (en_guidance_col, "guidance_en", False),
        ("TÊN NHÓM BỆNH 3 KÝ TỰ", "three_char_group_vi", True),
        ("3-CHARACTER SUB-CATEGORY NAME", "three_char_group_en", True),
    ]

    for _, row in df.iterrows():
        code = normalize_code(row.get(code_col, ""))
        if not code:
            continue
        canonical = canonical_by_code.get(code, {})
        for column, source, is_group_alias in source_columns:
            alias = normalize_whitespace(row.get(column, ""))
            if not alias:
                continue
            alias_rows.append(
                _make_icd_alias_row(
                    code=code,
                    canonical_name_vi=ensure_text(canonical.get("canonical_name_vi", row.get(vi_name_col, ""))),
                    canonical_name_en=ensure_text(canonical.get("canonical_name_en", row.get(en_name_col, ""))),
                    alias=alias,
                    alias_source=source,
                    is_group_alias=is_group_alias,
                )
            )

    alias_rows.extend(_manual_icd_alias_rows(manual_alias_path, canonical_by_code))
    aliases = pd.DataFrame(alias_rows)
    if not aliases.empty:
        aliases = aliases[aliases["alias_norm"] != ""]
        aliases = aliases.drop_duplicates(subset=["code", "alias_norm", "alias_source"]).reset_index(drop=True)
    return aliases


def build_and_write_icd10_resources(
    icd10_csv: str | Path,
    processed_dir: str | Path,
    config: Mapping[str, Any] | None = None,
    *,
    manual_alias_path: str | Path | None = None,
) -> dict[str, Any]:
    processed = Path(processed_dir)
    processed.mkdir(parents=True, exist_ok=True)
    df = read_icd10_csv(icd10_csv, config)
    index_df = build_icd10_index(df, config)
    aliases_df = build_icd10_aliases(df, index_df, config, manual_alias_path=manual_alias_path)

    index_path = processed / "icd10_index.parquet"
    aliases_path = processed / "icd10_aliases.parquet"
    index_df.to_parquet(index_path, index=False)
    aliases_df.to_parquet(aliases_path, index=False)
    return {
        "icd10_rows_raw": int(len(df)),
        "icd10_index_rows": int(len(index_df)),
        "icd10_aliases": int(len(aliases_df)),
        "icd10_index_path": str(index_path),
        "icd10_aliases_path": str(aliases_path),
    }


def _make_icd_alias_row(
    *,
    code: str,
    canonical_name_vi: str,
    canonical_name_en: str,
    alias: str,
    alias_source: str,
    is_group_alias: bool,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "code": normalize_code(code),
        "canonical_name_vi": normalize_whitespace(canonical_name_vi),
        "canonical_name_en": normalize_whitespace(canonical_name_en),
        "alias": normalize_whitespace(alias),
        "alias_norm": normalize_for_lookup(alias),
        "alias_no_diacritics": normalize_no_diacritics_for_lookup(alias),
        "alias_lang": guess_alias_lang(alias),
        "alias_source": alias_source,
        "is_group_alias": bool(is_group_alias),
        "metadata_json": json.dumps({"notes": notes}, ensure_ascii=False),
    }


def _manual_icd_alias_rows(
    manual_alias_path: str | Path | None,
    canonical_by_code: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not manual_alias_path:
        return []
    path = Path(manual_alias_path)
    if not path.is_file():
        return []
    manual = pd.read_csv(path, dtype=str, keep_default_na=False)
    rows: list[dict[str, Any]] = []
    for _, row in manual.iterrows():
        alias = normalize_whitespace(row.get("alias", ""))
        code = normalize_code(row.get("code_hint", ""))
        if not alias or not code:
            continue
        canonical = canonical_by_code.get(code, {})
        rows.append(
            _make_icd_alias_row(
                code=code,
                canonical_name_vi=ensure_text(canonical.get("canonical_name_vi", row.get("canonical_hint", ""))),
                canonical_name_en=ensure_text(canonical.get("canonical_name_en", "")),
                alias=alias,
                alias_source="manual",
                is_group_alias=False,
                notes=ensure_text(row.get("notes", "")),
            )
        )
    return rows
