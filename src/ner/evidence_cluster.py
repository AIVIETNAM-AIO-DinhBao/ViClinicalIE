from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.data_types import SpanCandidate
from src.linking.terminology_normalizer import normalize_for_lookup


_GENERIC_HEAD_TOKENS = {
    "bệnh", "có", "không", "kết", "quả", "xét", "nghiệm", "tăng", "giảm",
    "cho", "thấy", "ghi", "nhận", "phát", "hiện", "là",
}


@dataclass(frozen=True, slots=True)
class EvidenceCluster:
    cluster_id: str
    members: tuple[SpanCandidate, ...]
    reason: str


def build_evidence_clusters(
    candidates: Sequence[SpanCandidate], config: Mapping[str, Any] | None = None,
) -> tuple[list[EvidenceCluster], list[dict[str, Any]]]:
    cfg = dict(config or {})
    if not bool(cfg.get("enabled", True)):
        clusters = [EvidenceCluster(f"cluster_{index}", (item,), "disabled_singleton") for index, item in enumerate(sorted(candidates, key=_sort_key))]
        return clusters, []
    linkage = str(cfg.get("linkage", "complete"))
    if linkage != "complete":
        raise ValueError("NER-4 only supports complete-link evidence clustering")
    threshold = float(cfg.get("high_overlap_iou", cfg.get("overlap_iou", 0.5)))
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("NER-4 overlap IoU must be between 0 and 1")
    clusters: list[list[SpanCandidate]] = []
    reasons: list[str] = []
    for candidate in sorted(candidates, key=_sort_key):
        placed = False
        for index, members in enumerate(clusters):
            compatible = [_compatible(candidate, member, threshold) for member in members]
            if compatible and all(reason is not None for reason in compatible):
                members.append(candidate)
                reasons[index] = "+".join(sorted(set([reasons[index], *[str(reason) for reason in compatible]])))
                placed = True
                break
        if not placed:
            clusters.append([candidate]); reasons.append("singleton")
    result = [EvidenceCluster(f"cluster_{index}", tuple(sorted(members, key=_sort_key)), reasons[index]) for index, members in enumerate(clusters)]
    events = [{
        "cluster_id": cluster.cluster_id, "reason": cluster.reason, "member_count": len(cluster.members),
        "members": [_candidate_payload(item) for item in cluster.members],
    } for cluster in result]
    return result, events


def _compatible(first: SpanCandidate, second: SpanCandidate, threshold: float) -> str | None:
    if _structural_pair(first) and _structural_pair(first) == _structural_pair(second):
        if first.raw_type == second.raw_type:
            return "same_structural_pair"
        return None  # test and result are related but remain separate entities
    same_span = first.start == second.start and first.end == second.end
    if same_span:
        return "exact_span"
    compatible_types = first.raw_type == second.raw_type or {str(first.raw_type), str(second.raw_type)} == {"TRIỆU_CHỨNG", "CHẨN_ĐOÁN"}
    if not compatible_types:
        return None
    iou = _iou(first, second)
    if iou >= threshold:
        return "high_overlap"
    if (_contains(first, second) or _contains(second, first)) and _shared_head(first, second):
        return "containment_shared_head"
    return None


def _shared_head(first: SpanCandidate, second: SpanCandidate) -> bool:
    left = set(normalize_for_lookup(first.text).split()) - _GENERIC_HEAD_TOKENS
    right = set(normalize_for_lookup(second.text).split()) - _GENERIC_HEAD_TOKENS
    return bool(left & right)


def _structural_pair(item: SpanCandidate) -> str | None:
    evidence = item.features.get("evidence", {})
    value = evidence.get("structural_pair_id") if isinstance(evidence, Mapping) else None
    return str(value) if value else str(item.features.get("pair_id")) if item.features.get("pair_id") else None


def _iou(first: SpanCandidate, second: SpanCandidate) -> float:
    overlap = max(0, min(first.end, second.end) - max(first.start, second.start))
    union = (first.end - first.start) + (second.end - second.start) - overlap
    return overlap / union if union else 0.0


def _contains(first: SpanCandidate, second: SpanCandidate) -> bool:
    return first.start <= second.start and first.end >= second.end


def _sort_key(item: SpanCandidate) -> tuple[Any, ...]:
    return item.start, item.end, item.source, item.raw_type or "", -item.score, item.text


def _candidate_payload(item: SpanCandidate) -> dict[str, Any]:
    return {"text": item.text, "position": [item.start, item.end], "type": item.raw_type, "source": item.source, "score": item.score}