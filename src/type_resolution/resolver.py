from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.data_types import FinalEntity, SpanCandidate, VALID_ENTITY_TYPES
from src.type_resolution.features import TypeFeatures, build_type_features


DEFAULT_SOURCE_PRIORITY: dict[str, int] = {
    "lab_result_rule": 100,
    "lab_rule": 95,
    "imaging_rule": 90,
    "drug_rule": 85,
    "problem_rule": 70,
    "dictionary": 60,
    "ner": 50,
}

DEFAULT_TYPE_PRIORITY: dict[str, int] = {
    "KẾT_QUẢ_XÉT_NGHIỆM": 100,
    "TÊN_XÉT_NGHIỆM": 90,
    "THUỐC": 85,
    "CHẨN_ĐOÁN": 70,
    "TRIỆU_CHỨNG": 65,
}

DEFAULT_CONFIDENCE: dict[str, float] = {
    "lab_result_rule": 0.95,
    "lab_rule": 0.92,
    "imaging_rule": 0.88,
    "drug_rule_with_context": 0.90,
    "drug_rule_without_context": 0.78,
    "problem_disease_head": 0.78,
    "problem_symptom_head": 0.78,
    "dictionary": 0.70,
    "ner_fallback": 0.65,
    "fallback": 0.50,
}


@dataclass(slots=True)
class ResolvedCandidate:
    candidate: SpanCandidate
    entity_type: str
    confidence: float
    reason: str
    features: TypeFeatures


@dataclass(slots=True)
class TypeConflict:
    start: int
    end: int
    text: str
    chosen_type: str
    rejected_types: list[str]
    sources: list[str]
    reason: str


@dataclass(slots=True)
class TypeOverlap:
    start: int
    end: int
    text: str
    other_start: int
    other_end: int
    other_text: str
    overlap_start: int
    overlap_end: int
    overlap_text: str
    types: list[str]
    sources: list[str]
    reason: str


class TypeResolver:
    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config or {})
        self.source_priority = {**DEFAULT_SOURCE_PRIORITY, **dict(self.config.get("source_priority", {}))}
        self.type_priority = {**DEFAULT_TYPE_PRIORITY, **dict(self.config.get("type_priority", {}))}
        self.confidence = {**DEFAULT_CONFIDENCE, **dict(self.config.get("confidence", {}))}
        self.resolve_exact_span_conflicts = bool(self.config.get("resolve_exact_span_conflicts", True))
        self.keep_unresolved = bool(self.config.get("keep_unresolved", False))
        self.conflicts: list[TypeConflict] = []
        self.overlaps: list[TypeOverlap] = []
        self.unresolved: list[dict[str, Any]] = []
        self.duplicate_exact_span_count = 0

    def resolve(self, candidates: list[SpanCandidate], raw_text: str) -> list[FinalEntity]:
        self.conflicts = []
        self.overlaps = []
        self.unresolved = []
        self.duplicate_exact_span_count = 0

        if not self.resolve_exact_span_conflicts:
            return self._resolve_without_exact_grouping(candidates, raw_text)

        grouped: dict[tuple[int, int], list[SpanCandidate]] = defaultdict(list)
        for candidate in candidates:
            self._validate_candidate_offsets(candidate, raw_text)
            grouped[(candidate.start, candidate.end)].append(candidate)

        entities: list[FinalEntity] = []
        for (_start, _end), group in sorted(grouped.items()):
            resolved = [item for item in (self.resolve_candidate(candidate) for candidate in group) if item is not None]
            if not resolved:
                continue
            chosen = self._choose_best(resolved)
            resolved_types = {item.entity_type for item in resolved}
            if len(resolved_types) > 1:
                self._record_conflict(chosen, resolved)
            elif len(resolved) > 1:
                self.duplicate_exact_span_count += len(resolved) - 1
            entities.append(self._to_entity(chosen, resolved, raw_text))
        entities = sorted(entities, key=lambda entity: (entity.start, entity.end, entity.type))
        self._record_overlaps(entities, raw_text)
        return entities

    def resolve_candidate(self, candidate: SpanCandidate) -> ResolvedCandidate | None:
        features = build_type_features(candidate)
        source = candidate.source
        raw_type = candidate.raw_type

        ner4_type = candidate.features.get("ner4_selected_type")
        if ner4_type in VALID_ENTITY_TYPES:
            return self._resolved(candidate, str(ner4_type), "ner4_type_policy", features)

        if features.has_lab_result_evidence:
            return self._resolved(candidate, "KẾT_QUẢ_XÉT_NGHIỆM", "lab_result_rule", features)
        if features.has_lab_evidence or features.has_imaging_evidence:
            key = "imaging_rule" if source == "imaging_rule" else "lab_rule"
            return self._resolved(candidate, "TÊN_XÉT_NGHIỆM", key, features)
        if features.has_drug_evidence:
            key = "drug_rule_with_context" if features.has_drug_context else "drug_rule_without_context"
            return self._resolved(candidate, "THUỐC", key, features)
        if source == "dictionary" and raw_type in VALID_ENTITY_TYPES:
            return self._resolved(candidate, str(raw_type), "dictionary", features)
        if source == "problem_rule" and features.has_disease_head:
            return self._resolved(candidate, "CHẨN_ĐOÁN", "problem_disease_head", features)
        if source == "problem_rule" and features.has_symptom_head:
            return self._resolved(candidate, "TRIỆU_CHỨNG", "problem_symptom_head", features)
        if source in {"ner", "gliner"} and raw_type in VALID_ENTITY_TYPES:
            return self._resolved(candidate, str(raw_type), "ner_fallback", features)
        if raw_type in VALID_ENTITY_TYPES:
            return self._resolved(candidate, str(raw_type), "fallback", features)
        self.unresolved.append(self._unresolved_payload(candidate, "invalid_or_missing_raw_type"))
        return None

    def _resolved(self, candidate: SpanCandidate, entity_type: str, reason: str, features: TypeFeatures) -> ResolvedCandidate:
        return ResolvedCandidate(
            candidate=candidate,
            entity_type=entity_type,
            confidence=float(self.confidence.get(reason, self.confidence["fallback"])),
            reason=reason,
            features=features,
        )

    def _choose_best(self, resolved: list[ResolvedCandidate]) -> ResolvedCandidate:
        return sorted(
            resolved,
            key=lambda item: (
                self.type_priority.get(item.entity_type, 0),
                item.confidence,
                item.candidate.score,
                self.source_priority.get(item.candidate.source, 0),
            ),
            reverse=True,
        )[0]

    def _record_conflict(self, chosen: ResolvedCandidate, resolved: list[ResolvedCandidate]) -> None:
        rejected = [item for item in resolved if item is not chosen]
        self.conflicts.append(
            TypeConflict(
                start=chosen.candidate.start,
                end=chosen.candidate.end,
                text=chosen.candidate.text,
                chosen_type=chosen.entity_type,
                rejected_types=sorted({item.entity_type for item in rejected if item.entity_type != chosen.entity_type}),
                sources=sorted({item.candidate.source for item in resolved}),
                reason=chosen.reason,
            )
        )

    def _to_entity(self, chosen: ResolvedCandidate, resolved_group: list[ResolvedCandidate], raw_text: str) -> FinalEntity:
        candidate = chosen.candidate
        warnings = self._warnings(chosen)
        return FinalEntity(
            text=raw_text[candidate.start : candidate.end],
            start=candidate.start,
            end=candidate.end,
            type=chosen.entity_type,
            assertions=[],
            candidates=[],
            confidence=chosen.confidence,
            provenance={
                "type_resolution_reason": chosen.reason,
                "chosen_source": candidate.source,
                "candidate_score": candidate.score,
                "section": candidate.section,
                "type_priority": self.type_priority.get(chosen.entity_type, 0),
                "source_priority": self.source_priority.get(candidate.source, 0),
                "chosen_candidate_features": dict(candidate.features),
                "warnings": warnings,
                "type_features": self._features_payload(chosen.features),
                "exact_span_candidate_count": len(resolved_group),
                "exact_span_resolved_types": sorted({item.entity_type for item in resolved_group}),
                "source_candidates": [
                    {
                        "source": item.candidate.source,
                        "raw_type": item.candidate.raw_type,
                        "score": item.candidate.score,
                        "resolved_type": item.entity_type,
                        "confidence": item.confidence,
                        "reason": item.reason,
                        "features": dict(item.candidate.features),
                    }
                    for item in resolved_group
                ],
            },
        )

    def _unresolved_payload(self, candidate: SpanCandidate, reason: str) -> dict[str, Any]:
        return {
            "start": candidate.start,
            "end": candidate.end,
            "text": candidate.text,
            "source": candidate.source,
            "raw_type": candidate.raw_type,
            "reason": reason,
        }

    def _resolve_without_exact_grouping(self, candidates: list[SpanCandidate], raw_text: str) -> list[FinalEntity]:
        entities: list[FinalEntity] = []
        for candidate in sorted(candidates, key=lambda item: (item.start, item.end, item.source)):
            self._validate_candidate_offsets(candidate, raw_text)
            resolved = self.resolve_candidate(candidate)
            if resolved is None:
                continue
            entities.append(self._to_entity(resolved, [resolved], raw_text))
        entities = sorted(entities, key=lambda entity: (entity.start, entity.end, entity.type))
        self._record_overlaps(entities, raw_text)
        return entities

    def _validate_candidate_offsets(self, candidate: SpanCandidate, raw_text: str) -> None:
        if raw_text[candidate.start : candidate.end] != candidate.text:
            raise ValueError(f"Candidate offset mismatch: {candidate}")

    def _record_overlaps(self, entities: list[FinalEntity], raw_text: str) -> None:
        self.overlaps = []
        for index, first in enumerate(entities):
            for second in entities[index + 1 :]:
                if second.start >= first.end:
                    break
                if first.start == second.start and first.end == second.end:
                    continue
                overlap_start = max(first.start, second.start)
                overlap_end = min(first.end, second.end)
                if overlap_start >= overlap_end:
                    continue
                self.overlaps.append(
                    TypeOverlap(
                        start=first.start,
                        end=first.end,
                        text=first.text,
                        other_start=second.start,
                        other_end=second.end,
                        other_text=second.text,
                        overlap_start=overlap_start,
                        overlap_end=overlap_end,
                        overlap_text=raw_text[overlap_start:overlap_end],
                        types=[str(first.type), str(second.type)],
                        sources=sorted(
                            {
                                str(first.provenance.get("chosen_source", "")),
                                str(second.provenance.get("chosen_source", "")),
                            }
                            - {""}
                        ),
                        reason="different_span_overlap_kept_for_phase10",
                    )
                )

    def _warnings(self, resolved: ResolvedCandidate) -> list[str]:
        warnings: list[str] = []
        if resolved.entity_type == "THUỐC" and not resolved.features.has_drug_context:
            warnings.append("drug_without_context")
        if resolved.reason == "fallback":
            warnings.append("fallback_raw_type")
        return warnings

    def _features_payload(self, features: TypeFeatures) -> dict[str, Any]:
        return {
            "candidate_source": features.candidate_source,
            "raw_type": features.raw_type,
            "section": features.section,
            "score": features.score,
            "has_lab_evidence": features.has_lab_evidence,
            "has_lab_result_evidence": features.has_lab_result_evidence,
            "has_imaging_evidence": features.has_imaging_evidence,
            "has_drug_evidence": features.has_drug_evidence,
            "has_drug_context": features.has_drug_context,
            "has_symptom_head": features.has_symptom_head,
            "has_disease_head": features.has_disease_head,
            "has_dictionary_symptom": features.has_dictionary_symptom,
            "ner_label": features.ner_label,
            "icd_linkability_score": features.icd_linkability_score,
            "rxnorm_linkability_score": features.rxnorm_linkability_score,
        }
