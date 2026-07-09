from __future__ import annotations

import pandas as pd

from src.linking.sparse_retriever import SparseAliasRetriever, build_tfidf_artifacts


def test_build_tfidf_artifacts_and_query_icd(tmp_path) -> None:
    aliases = pd.DataFrame(
        [
            {
                "code": "K21.9",
                "canonical_name_vi": "Bệnh trào ngược dạ dày thực quản",
                "canonical_name_en": "Gastro-esophageal reflux disease",
                "alias": "bệnh trào ngược dạ dày thực quản",
                "alias_norm": "bệnh trào ngược dạ dày thực quản",
                "alias_no_diacritics": "benh trao nguoc da day thuc quan",
                "alias_source": "manual",
            },
            {
                "code": "A00",
                "canonical_name_vi": "Bệnh tả",
                "canonical_name_en": "Cholera",
                "alias": "cholera",
                "alias_norm": "cholera",
                "alias_no_diacritics": "cholera",
                "alias_source": "disease_name_en",
            },
        ]
    )
    summary = build_tfidf_artifacts(aliases, tmp_path, "icd")
    retriever = SparseAliasRetriever(
        aliases,
        summary["icd_tfidf_vectorizer_path"],
        summary["icd_tfidf_matrix_path"],
        terminology="ICD10",
    )

    results = retriever.query("trao nguoc da day", top_k=1)
    assert results
    assert results[0].code == "K21.9"
    assert results[0].terminology == "ICD10"


def test_build_tfidf_artifacts_and_query_rxnorm(tmp_path) -> None:
    aliases = pd.DataFrame(
        [
            {
                "rxcui": "123",
                "tty": "SCD",
                "alias": "metoprolol 25 MG Oral Tablet",
                "alias_norm": "metoprolol 25 mg oral tablet",
                "alias_no_diacritics": "metoprolol 25 mg oral tablet",
                "alias_source": "rxnorm_str",
            },
            {
                "rxcui": "456",
                "tty": "IN",
                "alias": "aspirin",
                "alias_norm": "aspirin",
                "alias_no_diacritics": "aspirin",
                "alias_source": "rxnorm_str",
            },
        ]
    )
    summary = build_tfidf_artifacts(aliases, tmp_path, "rx")
    retriever = SparseAliasRetriever(
        aliases,
        summary["rx_tfidf_vectorizer_path"],
        summary["rx_tfidf_matrix_path"],
        terminology="RXNORM",
    )

    results = retriever.query("metoprolol 25mg", top_k=1)
    assert results
    assert results[0].code == "123"
    assert results[0].terminology == "RXNORM"
