from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.data_types import SpanCandidate
from src.linking.terminology_normalizer import normalize_for_lookup
from src.postprocess.cleanup import DIAGNOSIS_TRIGGERS, NEGATION_CUES, SUBJECT_VERB_TRIGGERS, TRIM_CHARS
from src.type_resolution.features import DISEASE_HEADS, SYMPTOM_HEADS


@dataclass(frozen=True, slots=True)
class BoundaryEvent:
    operation: str
    source: str
    entity_type: str | None
    before: list[int]
    after: list[int]
    before_text: str
    after_text: str
    removed_prefix: str = ""


def cleanup_candidates(
    candidates: Sequence[SpanCandidate], raw_text: str, config: Mapping[str, Any] | None = None,
) -> tuple[list[SpanCandidate], list[BoundaryEvent]]:
    cfg = dict(config or {})
    if not bool(cfg.get("enabled", True)):
        return list(candidates), []
    output: list[SpanCandidate] = []
    events: list[BoundaryEvent] = []
    for candidate in candidates:
        cleaned, candidate_events = cleanup_candidate(candidate, raw_text, cfg)
        output.append(cleaned)
        events.extend(candidate_events)
    if bool(cfg.get("split_test_result", True)):
        output, split_events = _split_test_result_candidates(output, raw_text)
        events.extend(split_events)
    return sorted(output, key=_sort_key), sorted(events, key=lambda row: (row.before, row.after, row.operation, row.source))


def cleanup_candidate(
    candidate: SpanCandidate, raw_text: str, config: Mapping[str, Any] | None = None,
) -> tuple[SpanCandidate, list[BoundaryEvent]]:
    cfg = dict(config or {})
    _validate(candidate, raw_text)
    start, end = candidate.start, candidate.end
    events: list[BoundaryEvent] = []
    if bool(cfg.get("trim_whitespace", True)) or bool(cfg.get("trim_punctuation", True)):
        new_start, new_end = _trim_outer(raw_text, start, end)
        if (new_start, new_end) != (start, end):
            events.append(_event("trim_outer", candidate, raw_text, start, end, new_start, new_end))
            start, end = new_start, new_end

    entity_type = str(candidate.raw_type)
    if entity_type in {"TRIỆU_CHỨNG", "CHẨN_ĐOÁN"}:
        if bool(cfg.get("trim_negation_cues", True)):
            new_start = _trim_cue_if_valid(raw_text, start, end, NEGATION_CUES, entity_type)
            if new_start != start:
                events.append(_event("trim_negation_cue", candidate, raw_text, start, end, new_start, end))
                start = new_start
        if entity_type == "CHẨN_ĐOÁN" and bool(cfg.get("trim_diagnosis_cues", True)):
            new_start = _trim_cue_if_valid(raw_text, start, end, DIAGNOSIS_TRIGGERS, entity_type)
            if new_start != start:
                events.append(_event("trim_diagnosis_cue", candidate, raw_text, start, end, new_start, end))
                start = new_start
        if bool(cfg.get("trim_subject_cues", True)):
            new_start = _trim_cue_if_valid(raw_text, start, end, SUBJECT_VERB_TRIGGERS, entity_type)
            if new_start != start:
                events.append(_event("trim_subject_cue", candidate, raw_text, start, end, new_start, end))
                start = new_start

    start, end = _trim_outer(raw_text, start, end)
    if start >= end:
        return candidate, []
    features = deepcopy(candidate.features)
    if events:
        history = list(features.get("ner4_boundary_history", []))
        history.extend({
            "operation": row.operation, "before": row.before, "after": row.after,
            "before_text": row.before_text, "after_text": row.after_text,
            "removed_prefix": row.removed_prefix,
        } for row in events)
        features["ner4_boundary_history"] = history
    cleaned = SpanCandidate(
        raw_text[start:end], start, end, candidate.raw_type, candidate.source, candidate.score,
        section=candidate.section, subsection=candidate.subsection,
        context_left=raw_text[max(0, start - 80):start], context_right=raw_text[end:min(len(raw_text), end + 80)],
        features=features,
    )
    _validate(cleaned, raw_text)
    return cleaned, events


def _trim_outer(raw_text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and raw_text[start] in TRIM_CHARS:
        start += 1
    while end > start and raw_text[end - 1] in TRIM_CHARS:
        end -= 1
    return start, end


def _trim_cue_if_valid(raw_text: str, start: int, end: int, cues: Sequence[str], entity_type: str) -> int:
    text_norm = normalize_for_lookup(raw_text[start:end])
    for cue in sorted({str(item) for item in cues}, key=len, reverse=True):
        cue_norm = normalize_for_lookup(cue)
        if not text_norm.startswith(f"{cue_norm} "):
            continue
        cursor = start + len(cue)
        while cursor < end and raw_text[cursor] in TRIM_CHARS:
            cursor += 1
        remainder = normalize_for_lookup(raw_text[cursor:end])
        heads = SYMPTOM_HEADS if entity_type == "TRIỆU_CHỨNG" else DISEASE_HEADS
        if any(remainder == head or remainder.startswith(f"{head} ") for head in heads):
            return cursor
    return start


def _split_test_result_candidates(
    candidates: Sequence[SpanCandidate], raw_text: str,
) -> tuple[list[SpanCandidate], list[BoundaryEvent]]:
    """Remove a contained test prefix from a result proposal.

    V1 lab/imaging experts intentionally emit a full qualitative proposal for
    recall. NER-4 turns it into the separate result span required by the shared
    annotation contract, but only when a concrete test proposal anchors the
    prefix in the same candidate set.
    """

    tests = [
        item for item in candidates
        if str(item.raw_type) == "TÊN_XÉT_NGHIỆM" and item.source in {"lab_rule", "imaging_rule"}
    ]
    output: list[SpanCandidate] = []
    events: list[BoundaryEvent] = []
    for candidate in candidates:
        if str(candidate.raw_type) != "KẾT_QUẢ_XÉT_NGHIỆM":
            output.append(candidate)
            continue
        anchors = [
            test for test in tests
            if candidate.start == test.start and test.end < candidate.end
            and _same_structural_pair_or_imaging(candidate, test)
        ]
        if not anchors:
            output.append(candidate)
            continue
        anchor = max(anchors, key=lambda item: (item.end, item.start))
        new_start = _skip_result_connector(raw_text, anchor.end, candidate.end)
        new_start, new_end = _trim_outer(raw_text, new_start, candidate.end)
        if new_start >= new_end:
            output.append(candidate)
            continue
        event = _event(
            "split_test_result", candidate, raw_text,
            candidate.start, candidate.end, new_start, new_end,
        )
        features = deepcopy(candidate.features)
        history = list(features.get("ner4_boundary_history", []))
        history.append({
            "operation": event.operation, "before": event.before, "after": event.after,
            "before_text": event.before_text, "after_text": event.after_text,
            "removed_prefix": event.removed_prefix,
        })
        features["ner4_boundary_history"] = history
        features["ner4_test_anchor"] = {
            "text": anchor.text, "position": [anchor.start, anchor.end], "source": anchor.source,
        }
        split = SpanCandidate(
            raw_text[new_start:new_end], new_start, new_end, candidate.raw_type,
            candidate.source, candidate.score, section=candidate.section,
            subsection=candidate.subsection,
            context_left=raw_text[max(0, new_start - 80):new_start],
            context_right=raw_text[new_end:min(len(raw_text), new_end + 80)],
            features=features,
        )
        _validate(split, raw_text)
        output.append(split)
        events.append(event)
    return output, events


def _same_structural_pair_or_imaging(result: SpanCandidate, test: SpanCandidate) -> bool:
    result_pair = _pair_id(result)
    test_pair = _pair_id(test)
    if result_pair and test_pair:
        return result_pair == test_pair
    return result.features.get("pattern") == "imaging_test_plus_result" and test.source == "imaging_rule"


def _pair_id(candidate: SpanCandidate) -> str | None:
    evidence = candidate.features.get("evidence", {})
    value = evidence.get("structural_pair_id") if isinstance(evidence, Mapping) else None
    value = value or candidate.features.get("pair_id")
    return str(value) if value else None


def _skip_result_connector(raw_text: str, start: int, end: int) -> int:
    cursor = start
    while cursor < end and raw_text[cursor] in TRIM_CHARS + "=":
        cursor += 1
    lowered = raw_text[cursor:end].lower()
    for connector in ("cho thấy", "ghi nhận", "phát hiện", "kết quả", "là", "la", "is"):
        if lowered == connector or lowered.startswith(f"{connector} "):
            cursor += len(connector)
            while cursor < end and raw_text[cursor] in TRIM_CHARS + "=":
                cursor += 1
            break
    return cursor


def _event(operation: str, candidate: SpanCandidate, raw: str, start: int, end: int, new_start: int, new_end: int) -> BoundaryEvent:
    return BoundaryEvent(
        operation, candidate.source, candidate.raw_type, [start, end], [new_start, new_end],
        raw[start:end], raw[new_start:new_end], raw[start:new_start],
    )


def _validate(candidate: SpanCandidate, raw_text: str) -> None:
    if candidate.start < 0 or candidate.end > len(raw_text) or candidate.start >= candidate.end:
        raise ValueError(f"Invalid NER-4 boundary: {candidate.start}:{candidate.end}")
    if raw_text[candidate.start:candidate.end] != candidate.text:
        raise ValueError(f"NER-4 boundary offset mismatch: {candidate}")


def _sort_key(item: SpanCandidate) -> tuple[Any, ...]:
    return item.start, item.end, item.source, item.raw_type or "", -item.score