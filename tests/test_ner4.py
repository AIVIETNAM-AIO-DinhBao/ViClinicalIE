from __future__ import annotations

import pytest

from src.data_types import SpanCandidate
from src.ner.boundary_cleanup import cleanup_candidate, cleanup_candidates
from src.ner.deterministic_fusion import resolve_ner4_trace
from src.ner.evidence_cluster import EvidenceCluster, build_evidence_clusters
from src.ner.type_policy import select_cluster_candidate


def candidate(raw, text, entity_type, source="gliner", score=.8, features=None):
    start = raw.index(text)
    return SpanCandidate(
        text, start, start + len(text), entity_type, source, score,
        features=dict(features or {}),
    )


def test_boundary_cleanup_is_raw_safe() -> None:
    raw = "Không đau ngực."
    cleaned, events = cleanup_candidate(candidate(raw, "Không đau ngực", "TRIỆU_CHỨNG"), raw)
    assert cleaned.text == "đau ngực"
    assert raw[cleaned.start:cleaned.end] == cleaned.text
    assert events[0].operation == "trim_negation_cue"


def test_test_result_split_requires_structural_anchor() -> None:
    raw = "CRP dương tính"
    pair = {"pair_id": "p1"}
    anchored, events = cleanup_candidates([
        SpanCandidate("CRP", 0, 3, "TÊN_XÉT_NGHIỆM", "lab_rule", .82, features=pair),
        SpanCandidate(raw, 0, len(raw), "KẾT_QUẢ_XÉT_NGHIỆM", "lab_result_rule", .86, features=pair),
    ], raw)
    assert {(item.text, item.raw_type) for item in anchored} == {
        ("CRP", "TÊN_XÉT_NGHIỆM"), ("dương tính", "KẾT_QUẢ_XÉT_NGHIỆM"),
    }
    assert any(item.operation == "split_test_result" for item in events)

    unanchored = SpanCandidate(raw, 0, len(raw), "KẾT_QUẢ_XÉT_NGHIỆM", "gliner", .8)
    assert cleanup_candidates([unanchored], raw)[0] == [unanchored]


def test_complete_link_prevents_transitive_cluster() -> None:
    items = [
        SpanCandidate("đau bụng", 0, 8, "TRIỆU_CHỨNG", "gliner", .8),
        SpanCandidate("đau bụng dữ", 0, 12, "TRIỆU_CHỨNG", "problem_rule", .8),
        SpanCandidate("bụng dữ dội", 4, 16, "TRIỆU_CHỨNG", "dictionary", .8),
    ]
    clusters, _ = build_evidence_clusters(items, {"high_overlap_iou": .4})
    assert sorted(len(cluster.members) for cluster in clusters) == [1, 2]
    with pytest.raises(ValueError, match="complete-link"):
        build_evidence_clusters(items, {"linkage": "single"})


def test_test_result_pair_and_repeated_positions_remain_separate() -> None:
    pair = {"evidence": {"structural_pair_id": "p1"}}
    items = [
        SpanCandidate("Na", 0, 2, "TÊN_XÉT_NGHIỆM", "lab_rule", .8, features=pair),
        SpanCandidate("140", 3, 6, "KẾT_QUẢ_XÉT_NGHIỆM", "lab_result_rule", .8, features=pair),
        SpanCandidate("Na", 10, 12, "TÊN_XÉT_NGHIỆM", "gliner", .8),
    ]
    clusters, _ = build_evidence_clusters(items)
    assert len(clusters) == 3


def test_structured_type_anchors_override_wrong_semantic_type() -> None:
    drug_cluster = EvidenceCluster("d", (
        SpanCandidate("aspirin", 0, 7, "CHẨN_ĐOÁN", "gliner", .99),
        SpanCandidate("aspirin", 0, 7, "THUỐC", "drug_rule", .70),
    ), "exact_span")
    assert select_cluster_candidate(drug_cluster).selected_type == "THUỐC"

    imaging = SpanCandidate(
        "nhồi máu cũ", 0, 12, "KẾT_QUẢ_XÉT_NGHIỆM", "lab_result_rule", .86,
        features={"pattern": "imaging_test_plus_result"},
    )
    selection = select_cluster_candidate(EvidenceCluster("i", (imaging,), "singleton"))
    assert selection.selected_type == "CHẨN_ĐOÁN"


def test_fusion_keeps_gliner_only_and_full_drug_formulation() -> None:
    raw = "chóng mặt và metoprolol 25 mg po bid"
    items = [
        candidate(raw, "chóng mặt", "TRIỆU_CHỨNG"),
        candidate(raw, "metoprolol", "THUỐC", score=.95),
        candidate(raw, "metoprolol 25 mg po bid", "THUỐC", "drug_rule", .86, {"strength": "25 mg"}),
    ]
    result = resolve_ner4_trace(items, raw, "G")
    assert [(item.text, item.type) for item in result.entities] == [
        ("chóng mặt", "TRIỆU_CHỨNG"),
        ("metoprolol 25 mg po bid", "THUỐC"),
    ]
    assert len({(item.start, item.end, str(item.type)) for item in result.entities}) == len(result.entities)


def test_fusion_is_deterministic_for_candidate_order() -> None:
    raw = "Không đau ngực"
    items = [
        candidate(raw, raw, "TRIỆU_CHỨNG"),
        candidate(raw, "đau ngực", "TRIỆU_CHỨNG", "problem_rule", .8, {"rule": "symptom_head"}),
    ]
    first = resolve_ner4_trace(items, raw, "G")
    second = resolve_ner4_trace(list(reversed(items)), raw, "G")
    assert [(x.start, x.end, x.type) for x in first.entities] == [
        (x.start, x.end, x.type) for x in second.entities
    ]