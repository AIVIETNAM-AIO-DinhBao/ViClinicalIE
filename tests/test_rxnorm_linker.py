from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.data_types import FinalEntity
from src.linking.rxnorm_linker import RxNormLinker


def _write_minimal_processed(tmp_path: Path) -> None:
    index = pd.DataFrame(
        [
            {"rxcui": "ingredient_metoprolol", "tty": "IN", "str": "metoprolol", "strength_value": None, "strength_unit": "", "is_clinical_drug": False},
            {"rxcui": "clinical_25", "tty": "SCD", "str": "metoprolol 25 MG Oral Tablet", "strength_value": 25.0, "strength_unit": "MG", "is_clinical_drug": True},
            {"rxcui": "atenolol_in", "tty": "IN", "str": "atenolol", "strength_value": None, "strength_unit": "", "is_clinical_drug": False},
            {"rxcui": "acetaminophen_in", "tty": "IN", "str": "acetaminophen", "strength_value": None, "strength_unit": "", "is_clinical_drug": False},
        ]
    )
    aliases = pd.DataFrame(
        [
            {
                "rxcui": "ingredient_metoprolol",
                "tty": "IN",
                "alias": "metoprolol",
                "alias_norm": "metoprolol",
                "alias_no_diacritics": "metoprolol",
                "alias_source": "rxnorm_str",
                "ingredient_guess": "metoprolol",
                "strength_value": None,
                "strength_unit": "",
                "dose_form_guess": "",
                "is_clinical_drug": False,
            },
            {
                "rxcui": "clinical_25",
                "tty": "SCD",
                "alias": "metoprolol 25 MG Oral Tablet",
                "alias_norm": "metoprolol 25 mg oral tablet",
                "alias_no_diacritics": "metoprolol 25 mg oral tablet",
                "alias_source": "rxnorm_str",
                "ingredient_guess": "metoprolol",
                "strength_value": 25.0,
                "strength_unit": "MG",
                "dose_form_guess": "Oral Tablet",
                "is_clinical_drug": True,
            },
            {
                "rxcui": "atenolol_in",
                "tty": "IN",
                "alias": "atenolol",
                "alias_norm": "atenolol",
                "alias_no_diacritics": "atenolol",
                "alias_source": "rxnorm_str",
                "ingredient_guess": "atenolol",
                "strength_value": None,
                "strength_unit": "",
                "dose_form_guess": "",
                "is_clinical_drug": False,
            },
            {
                "rxcui": "acetaminophen_in",
                "tty": "IN",
                "alias": "tylenol",
                "alias_norm": "tylenol",
                "alias_no_diacritics": "tylenol",
                "alias_source": "manual_brand",
                "ingredient_guess": "acetaminophen",
                "strength_value": None,
                "strength_unit": "",
                "dose_form_guess": "",
                "is_clinical_drug": False,
            },
        ]
    )
    index.to_parquet(tmp_path / "rxnorm_index.parquet", index=False)
    aliases.to_parquet(tmp_path / "rxnorm_aliases.parquet", index=False)


def test_rxnorm_linker_links_only_drugs_and_preserves_fields(tmp_path) -> None:
    _write_minimal_processed(tmp_path)
    linker = RxNormLinker(tmp_path, {"retrieval": {"top_k_tfidf": 0, "top_k_bm25": 0}})
    raw_text = "Dùng metoprolol 25mg po bid do tăng huyết áp."
    drug = FinalEntity(
        text="metoprolol 25mg po bid",
        start=5,
        end=27,
        type="THUỐC",
        assertions=["isHistorical"],
        confidence=0.9,
        provenance={"phase": "test"},
    )
    diagnosis = FinalEntity(text="tăng huyết áp", start=31, end=44, type="CHẨN_ĐOÁN")

    linked = linker.link_entities([drug, diagnosis], raw_text=raw_text)

    assert linked[0].text == drug.text
    assert linked[0].start == drug.start
    assert linked[0].end == drug.end
    assert linked[0].type == drug.type
    assert linked[0].assertions == drug.assertions
    assert linked[0].confidence == drug.confidence
    assert linked[0].candidates == ["clinical_25"]
    assert linked[0].provenance["rxnorm_linking"]["parsed"]["strength_value"] == 25.0
    assert raw_text[linked[0].start : linked[0].end] == linked[0].text

    assert linked[1] is diagnosis
    assert linked[1].candidates == []


def test_rxnorm_linker_name_only_prefers_ingredient(tmp_path) -> None:
    _write_minimal_processed(tmp_path)
    linker = RxNormLinker(tmp_path, {"retrieval": {"top_k_tfidf": 0, "top_k_bm25": 0}})
    linked = linker.link_entity(FinalEntity(text="atenolol", start=0, end=8, type="THUỐC"), raw_text="atenolol")
    assert linked.candidates == ["atenolol_in"]


def test_rxnorm_linker_manual_brand_alias(tmp_path) -> None:
    _write_minimal_processed(tmp_path)
    linker = RxNormLinker(tmp_path, {"retrieval": {"top_k_tfidf": 0, "top_k_bm25": 0}})
    linked = linker.link_entity(FinalEntity(text="tylenol", start=0, end=7, type="THUỐC"), raw_text="tylenol")
    assert linked.candidates == ["acetaminophen_in"]


def test_rxnorm_linker_unknown_drug_returns_no_candidate(tmp_path) -> None:
    _write_minimal_processed(tmp_path)
    linker = RxNormLinker(tmp_path, {"retrieval": {"top_k_tfidf": 0, "top_k_bm25": 0}})
    linked = linker.link_entity(FinalEntity(text="notarealdrug 123mg", start=0, end=19, type="THUỐC"), raw_text="notarealdrug 123mg")
    assert linked.candidates == []
