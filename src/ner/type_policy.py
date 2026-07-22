from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.data_types import SpanCandidate
from src.ner.evidence_cluster import EvidenceCluster
from src.type_resolution.features import build_type_features


DEFAULT_AUTHORITY = {
    "THUỐC": ("drug_rule", "gliner", "dictionary"),
    "TÊN_XÉT_NGHIỆM": ("lab_rule", "imaging_rule", "gliner", "dictionary"),
    "KẾT_QUẢ_XÉT_NGHIỆM": ("lab_result_rule", "gliner"),
    "TRIỆU_CHỨNG": ("gliner", "problem_rule", "dictionary"),
    "CHẨN_ĐOÁN": ("gliner", "problem_rule", "dictionary"),
}


@dataclass(frozen=True, slots=True)
class TypeSelection:
    cluster_id: str
    selected: SpanCandidate
    selected_type: str
    reason: str
    rejected_types: tuple[str, ...]


def select_cluster_candidate(
    cluster: EvidenceCluster, config: Mapping[str, Any] | None = None,
) -> TypeSelection:
    cfg = dict(config or {})
    if not bool(cfg.get("enabled", True)):
        selected = max(cluster.members, key=lambda item: _rank(item, DEFAULT_AUTHORITY))
        return TypeSelection(cluster.cluster_id, selected, str(selected.raw_type), "type_policy_disabled", ())
    configured = cfg.get("source_reliability_by_type", {})
    authority = {key: tuple(value) for key, value in DEFAULT_AUTHORITY.items()}
    if isinstance(configured, Mapping):
        authority.update({str(key): tuple(str(item) for item in value) for key, value in configured.items() if isinstance(value, Sequence) and not isinstance(value, str)})
    ranked = sorted(cluster.members, key=lambda item: _rank(item, authority), reverse=True)
    selected = ranked[0]
    selected_type = str(selected.raw_type)
    reasons = ["source_authority"]
    features = build_type_features(selected)
    if _is_named_imaging_diagnosis(selected, features):
        selected_type = "CHẨN_ĐOÁN"; reasons = ["imaging_named_diagnosis"]
    elif selected.source == "problem_rule" and features.has_disease_head:
        selected_type = "CHẨN_ĐOÁN"; reasons = ["disease_head"]
    elif selected.source == "problem_rule" and features.has_symptom_head:
        selected_type = "TRIỆU_CHỨNG"; reasons = ["symptom_head"]
    elif selected.source == "drug_rule":
        selected_type = "THUỐC"
        shorter = any(
            item is not selected and str(item.raw_type) == "THUỐC"
            and selected.start <= item.start and item.end <= selected.end
            for item in cluster.members
        )
        reasons = ["drug_formulation_anchor" if shorter else "drug_anchor"]
    elif selected.source in {"lab_rule", "imaging_rule"}:
        selected_type = "TÊN_XÉT_NGHIỆM"; reasons = ["test_anchor"]
    elif selected.source == "lab_result_rule":
        selected_type = "KẾT_QUẢ_XÉT_NGHIỆM"; reasons = ["result_anchor"]
    rejected = tuple(sorted({str(item.raw_type) for item in cluster.members} - {selected_type}))
    return TypeSelection(cluster.cluster_id, selected, selected_type, "+".join(reasons), rejected)


def _is_named_imaging_diagnosis(item: SpanCandidate, features: Any) -> bool:
    return (
        item.source == "lab_result_rule"
        and item.features.get("pattern") == "imaging_test_plus_result"
        and features.has_disease_head
    )


def _rank(item: SpanCandidate, authority: Mapping[str, Sequence[str]]) -> tuple[Any, ...]:
    entity_type = str(item.raw_type)
    sources = tuple(authority.get(entity_type, ()))
    try:
        source_rank = len(sources) - sources.index(item.source)
    except ValueError:
        source_rank = 0
    structural = int(item.source in {"drug_rule", "lab_rule", "lab_result_rule", "imaging_rule"})
    agreement = int(item.features.get("agreement_count", 1))
    # Scores from unrelated sources are never added. Score is only a final tie
    # breaker between candidates after categorical evidence and authority.
    return structural, source_rank, agreement, item.end - item.start, item.score, -item.start, item.source