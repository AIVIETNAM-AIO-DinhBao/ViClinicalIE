from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.data_types import FinalEntity
from src.linking.icd10_linker import ICD10Linker, normalize_diagnosis_queries


def _write_minimal_processed(tmp_path: Path) -> None:
    index = pd.DataFrame(
        [
            {"code": "I10", "canonical_name_vi": "Tăng huyết áp vô căn", "canonical_name_en": "Essential hypertension"},
            {"code": "J44.9", "canonical_name_vi": "Bệnh phổi tắc nghẽn mạn tính", "canonical_name_en": "COPD"},
        ]
    )
    aliases = pd.DataFrame(
        [
            {
                "code": "I10",
                "canonical_name_vi": "Tăng huyết áp vô căn",
                "canonical_name_en": "Essential hypertension",
                "alias": "tăng huyết áp",
                "alias_norm": "tăng huyết áp",
                "alias_no_diacritics": "tang huyet ap",
                "alias_source": "manual",
            },
            {
                "code": "J44.9",
                "canonical_name_vi": "Bệnh phổi tắc nghẽn mạn tính",
                "canonical_name_en": "COPD",
                "alias": "bệnh phổi tắc nghẽn mạn tính",
                "alias_norm": "bệnh phổi tắc nghẽn mạn tính",
                "alias_no_diacritics": "benh phoi tac nghen man tinh",
                "alias_source": "manual",
            },
        ]
    )
    index.to_parquet(tmp_path / "icd10_index.parquet", index=False)
    aliases.to_parquet(tmp_path / "icd10_aliases.parquet", index=False)


def test_normalize_diagnosis_queries_expands_common_abbreviation() -> None:
    variants = normalize_diagnosis_queries("COPD", {})
    assert "COPD" in variants
    assert "bệnh phổi tắc nghẽn mạn tính" in variants


def test_icd10_linker_links_only_diagnosis_and_preserves_entity_fields(tmp_path) -> None:
    _write_minimal_processed(tmp_path)
    linker = ICD10Linker(
        tmp_path,
        {
            "selection": {"min_score_top1": 0.55},
            "retrieval": {"top_k_exact": 5, "top_k_tfidf": 0, "top_k_bm25": 0},
        },
    )
    raw_text = "Có tiền sử tăng huyết áp và đau đầu."
    diagnosis = FinalEntity(
        text="tăng huyết áp",
        start=11,
        end=24,
        type="CHẨN_ĐOÁN",
        assertions=["isHistorical"],
        confidence=0.81,
        provenance={"phase": "test"},
    )
    symptom = FinalEntity(text="đau đầu", start=28, end=35, type="TRIỆU_CHỨNG")

    linked = linker.link_entities([diagnosis, symptom], raw_text=raw_text)

    assert linked[0].text == diagnosis.text
    assert linked[0].start == diagnosis.start
    assert linked[0].end == diagnosis.end
    assert linked[0].type == diagnosis.type
    assert linked[0].assertions == ["isHistorical"]
    assert linked[0].confidence == diagnosis.confidence
    assert linked[0].candidates == ["I10"]
    assert "icd10_linking" in linked[0].provenance
    assert raw_text[linked[0].start : linked[0].end] == linked[0].text

    assert linked[1] is symptom
    assert linked[1].candidates == []


def test_icd10_linker_expanded_abbreviation_can_exact_match(tmp_path) -> None:
    _write_minimal_processed(tmp_path)
    linker = ICD10Linker(tmp_path, {"retrieval": {"top_k_exact": 5, "top_k_tfidf": 0, "top_k_bm25": 0}})
    entity = FinalEntity(text="COPD", start=0, end=4, type="CHẨN_ĐOÁN")

    linked = linker.link_entity(entity, raw_text="COPD")

    assert linked.candidates == ["J44.9"]


def test_icd10_linker_returns_no_candidate_for_unknown_text(tmp_path) -> None:
    _write_minimal_processed(tmp_path)
    linker = ICD10Linker(tmp_path, {"retrieval": {"top_k_exact": 5, "top_k_tfidf": 0, "top_k_bm25": 0}})
    entity = FinalEntity(text="abcxyz không phải bệnh", start=0, end=21, type="CHẨN_ĐOÁN")

    linked = linker.link_entity(entity, raw_text="abcxyz không phải bệnh")

    assert linked.candidates == []
