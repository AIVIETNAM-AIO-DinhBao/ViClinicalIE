from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from src.data_types import FinalEntity, SpanCandidate
from src.ner.boundary_cleanup import BoundaryEvent, cleanup_candidates
from src.ner.evidence_cluster import EvidenceCluster, build_evidence_clusters
from src.ner.simple_fusion import resolve_replay_trace
from src.ner.type_policy import TypeSelection, select_cluster_candidate
from src.type_resolution import TypeResolver


@dataclass(slots=True)
class NER4Trace:
    entities: list[FinalEntity]
    candidates: list[SpanCandidate]
    boundary_events: list[dict[str, Any]] = field(default_factory=list)
    cluster_events: list[dict[str, Any]] = field(default_factory=list)
    type_events: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[Any] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    offset_error_count: int = 0


def resolve_ner4_trace(
    candidates: list[SpanCandidate], raw_text: str, mode: str,
    resolver_config: Mapping[str, Any] | None = None,
    boundary_config: Mapping[str, Any] | None = None,
    cluster_config: Mapping[str, Any] | None = None,
    type_config: Mapping[str, Any] | None = None,
) -> NER4Trace:
    mode = str(mode).upper()
    if mode not in {"D", "E", "F", "G"}:
        raise ValueError(f"Unknown NER-4 mode: {mode}")
    if mode == "D":
        parent = resolve_replay_trace(candidates, raw_text, mode="simple_fusion", resolver_config=resolver_config)
        return NER4Trace(parent.entities, parent.candidates, conflicts=parent.conflicts, unresolved=parent.unresolved)

    working = list(candidates)
    boundary_events: list[BoundaryEvent] = []
    if mode in {"E", "G"}:
        working, boundary_events = cleanup_candidates(working, raw_text, boundary_config)

    if mode == "E":
        parent = resolve_replay_trace(working, raw_text, mode="simple_fusion", resolver_config=resolver_config)
        return NER4Trace(
            parent.entities, parent.candidates, [asdict(item) for item in boundary_events],
            conflicts=parent.conflicts, unresolved=parent.unresolved,
        )

    clusters, cluster_events = build_evidence_clusters(working, cluster_config)
    selections = [select_cluster_candidate(cluster, type_config) for cluster in clusters]
    selected_candidates = [_selection_candidate(item) for item in selections]
    resolver_cfg = dict(resolver_config or {})
    resolver_cfg["resolve_exact_span_conflicts"] = True
    resolver = TypeResolver(resolver_cfg)
    entities = resolver.resolve(selected_candidates, raw_text)
    entities = _attach_cluster_provenance(entities, clusters, selections)
    keys = [(item.start, item.end, str(item.type)) for item in entities]
    if len(keys) != len(set(keys)):
        raise ValueError("NER-4 fusion emitted exact duplicates")
    return NER4Trace(
        entities=entities, candidates=selected_candidates,
        boundary_events=[asdict(item) for item in boundary_events], cluster_events=cluster_events,
        type_events=[{
            "cluster_id": item.cluster_id, "selected_type": item.selected_type,
            "selected_span": [item.selected.start, item.selected.end], "selected_source": item.selected.source,
            "reason": item.reason, "rejected_types": list(item.rejected_types),
        } for item in selections],
        conflicts=list(resolver.conflicts), unresolved=list(resolver.unresolved),
    )


def _selection_candidate(selection: TypeSelection) -> SpanCandidate:
    candidate = selection.selected
    features = deepcopy(candidate.features)
    features["ner4_type_selection"] = {
        "cluster_id": selection.cluster_id, "selected_type": selection.selected_type,
        "reason": selection.reason, "rejected_types": list(selection.rejected_types),
    }
    features["ner4_selected_type"] = selection.selected_type
    return SpanCandidate(
        candidate.text, candidate.start, candidate.end, selection.selected_type, candidate.source, candidate.score,
        section=candidate.section, subsection=candidate.subsection,
        context_left=candidate.context_left, context_right=candidate.context_right, features=features,
    )


def _attach_cluster_provenance(
    entities: list[FinalEntity], clusters: Sequence[EvidenceCluster], selections: Sequence[TypeSelection],
) -> list[FinalEntity]:
    by_key = {
        (selection.selected.start, selection.selected.end, selection.selected_type): (cluster, selection)
        for cluster, selection in zip(clusters, selections)
    }
    output: list[FinalEntity] = []
    for entity in entities:
        cluster, selection = by_key[(entity.start, entity.end, str(entity.type))]
        provenance = deepcopy(entity.provenance)
        provenance["ner4_fusion"] = {
            "cluster_id": cluster.cluster_id, "reason": selection.reason,
            "members": [{
                "text": item.text, "position": [item.start, item.end], "type": item.raw_type,
                "source": item.source, "score": item.score, "features": deepcopy(item.features),
            } for item in cluster.members],
        }
        output.append(FinalEntity(
            entity.text, entity.start, entity.end, entity.type,
            assertions=list(entity.assertions), candidates=list(entity.candidates),
            confidence=entity.confidence, provenance=provenance,
        ))
    return sorted(output, key=lambda item: (item.start, item.end, str(item.type)))