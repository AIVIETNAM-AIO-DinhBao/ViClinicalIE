from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.data_types import FinalEntity
from src.assertion.context_rules import AssertionEvidence, find_cues, get_context_window, has_terminator_between
from src.linking.terminology_normalizer import normalize_for_lookup


def detect_negation(entity: FinalEntity, raw_text: str, config: Mapping[str, Any]) -> AssertionEvidence | None:
    rules = dict(config.get("rules", {}))
    left = int(config.get("window_chars_left", 160))
    right = int(config.get("window_chars_right", 160))
    max_pre_distance = int(config.get("max_negation_pre_distance", 90))
    max_post_distance = int(config.get("max_negation_post_distance", 48))
    window = get_context_window(raw_text, entity, left=left, right=right)
    terminators = rules.get("scope_terminators", [])

    pre_matches = [match for match in find_cues(raw_text, rules.get("negation_pre", []), window.start, entity.start)]
    for match in sorted(pre_matches, key=lambda item: item.end, reverse=True):
        if entity.start - match.end > max_pre_distance:
            continue
        if _is_pseudo_negation(raw_text, window.start, match.start, match.end, entity.start, rules.get("pseudo_negation", [])):
            continue
        if has_terminator_between(raw_text, match.end, entity.start, terminators):
            continue
        if _has_clause_subject_break(raw_text, match.end, entity.start):
            continue
        return AssertionEvidence(
            assertion="isNegated",
            score=0.90,
            cue=match.cue,
            cue_start=match.start,
            cue_end=match.end,
            reason="pre_negation_scope",
        )

    post_matches = [match for match in find_cues(raw_text, rules.get("negation_post", []), entity.end, window.end)]
    for match in sorted(post_matches, key=lambda item: item.start):
        if match.start - entity.end > max_post_distance:
            continue
        if has_terminator_between(raw_text, entity.end, match.start, terminators):
            continue
        return AssertionEvidence(
            assertion="isNegated",
            score=0.72,
            cue=match.cue,
            cue_start=match.start,
            cue_end=match.end,
            reason="post_negation_scope",
        )
    return None


def _has_clause_subject_break(raw_text: str, start: int, end: int) -> bool:
    if start >= end:
        return False
    segment = normalize_for_lookup(raw_text[start:end])
    if "," not in raw_text[start:end]:
        return False
    subject_cues = ("bệnh nhân", "người bệnh", "bn", "bệnh nhi")
    return any(cue in segment for cue in subject_cues)


def _is_pseudo_negation(
    raw_text: str,
    window_start: int,
    cue_start: int,
    cue_end: int,
    entity_start: int,
    pseudo_cues: list[str],
) -> bool:
    pseudo_start = max(window_start, cue_start - 30)
    for pseudo in find_cues(raw_text, pseudo_cues, pseudo_start, entity_start):
        if pseudo.start <= cue_start and pseudo.end >= cue_end:
            return True
        if pseudo.end > cue_start and pseudo.start < cue_end:
            return True
    return False