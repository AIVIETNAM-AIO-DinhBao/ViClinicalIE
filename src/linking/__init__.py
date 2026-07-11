"""Terminology indexing, retrieval, and linker utilities."""

from src.linking.candidate_selector import CandidateSelectionConfig, CandidateSelectionResult, select_candidates

__all__ = [
    "CandidateSelectionConfig",
    "CandidateSelectionResult",
    "ICD10Linker",
    "ParsedDrug",
    "RxNormLinker",
    "normalize_diagnosis_queries",
    "parse_drug_mention",
    "select_candidates",
]


def __getattr__(name: str):
    if name in {"ICD10Linker", "normalize_diagnosis_queries"}:
        from src.linking.icd10_linker import ICD10Linker, normalize_diagnosis_queries

        return {"ICD10Linker": ICD10Linker, "normalize_diagnosis_queries": normalize_diagnosis_queries}[name]
    if name in {"ParsedDrug", "parse_drug_mention"}:
        from src.linking.drug_parser import ParsedDrug, parse_drug_mention

        return {"ParsedDrug": ParsedDrug, "parse_drug_mention": parse_drug_mention}[name]
    if name == "RxNormLinker":
        from src.linking.rxnorm_linker import RxNormLinker

        return RxNormLinker
    raise AttributeError(f"module 'src.linking' has no attribute {name!r}")
