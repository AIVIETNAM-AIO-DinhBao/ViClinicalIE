"""Terminology indexing, retrieval, and linker utilities."""

from src.linking.candidate_selector import CandidateSelectionConfig, CandidateSelectionResult, select_candidates

__all__ = [
    "CandidateSelectionConfig",
    "CandidateSelectionResult",
    "ICD10Linker",
    "normalize_diagnosis_queries",
    "select_candidates",
]


def __getattr__(name: str):
    if name in {"ICD10Linker", "normalize_diagnosis_queries"}:
        from src.linking.icd10_linker import ICD10Linker, normalize_diagnosis_queries

        return {"ICD10Linker": ICD10Linker, "normalize_diagnosis_queries": normalize_diagnosis_queries}[name]
    raise AttributeError(f"module 'src.linking' has no attribute {name!r}")
