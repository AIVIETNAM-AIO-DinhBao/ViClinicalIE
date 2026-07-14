from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from src.data_types import FinalEntity
from src.linking.terminology_normalizer import normalize_for_lookup
from src.postprocess.models import PostprocessDecision
from src.postprocess.policies import dedupe_stable, is_assertable, is_linked_type, ordered_valid_assertions
from src.postprocess.span_utils import entity_payload, with_span
from src.type_resolution.features import DISEASE_HEADS, SYMPTOM_HEADS


NEGATION_CUES = ("không có", "không thấy", "không ghi nhận", "chưa phát hiện", "phủ nhận", "không")
DIAGNOSIS_TRIGGERS = ("lo ngại", "nghi ngờ", "chẩn đoán", "phát hiện", "cho thấy", "gợi ý")
SUBJECT_VERB_TRIGGERS = (
    "bệnh nhân xuất hiện",
    "bệnh nhân có",
    "bệnh nhân còn",
    "người bệnh xuất hiện",
    "người bệnh có",
    "xuất hiện",
    "ghi nhận",
    "cảm thấy",
)
TRIM_CHARS = " \t\r\n-•*:;,。."
QUALITATIVE_RESULTS = {"bình thường", "âm tính", "dương tính"}
GENERIC_ENTITY_TEXTS = {
    "bệnh",
    "bệnh nhân",
    "người bệnh",
    "bệnh hiện tại",
    "yếu tố nguy cơ",
    "yếu tố nguy cơ liên quan",
    "các triệu chứng hiện tại",
    "triệu chứng hiện tại",
    "tình trạng",
}
STRONG_MED_CONTEXT_CUES = (
    "thuốc",
    "dùng",
    "điều trị",
    "được cho",
    "bắt đầu",
    "ngừng",
    "kê đơn",
    "liều",
    "tiêm",
    "truyền",
    "mg",
    "mcg",
    "iv",
    "po",
)
SUBSTANCE_CONTEXT_PHRASES = ("thuốc lá", "hút thuốc")


def trim_entity(entity: FinalEntity, raw_text: str, config: dict[str, Any]) -> tuple[FinalEntity, PostprocessDecision | None]:
    trim_cfg = config.get("span_trim", {}) if isinstance(config.get("span_trim"), dict) else {}
    if not bool(trim_cfg.get("enabled", True)):
        return entity, None

    start, end = _trim_outer(raw_text, entity.start, entity.end)
    assertions = list(entity.assertions)

    if bool(trim_cfg.get("trim_leading_negation_cues", True)) and is_assertable(entity):
        new_start = _trim_leading_cue(raw_text, start, end, NEGATION_CUES)
        if new_start != start:
            start = new_start
            if "isNegated" not in assertions:
                assertions.append("isNegated")

    if bool(trim_cfg.get("trim_leading_diagnosis_triggers", True)) and str(entity.type) == "CHẨN_ĐOÁN":
        new_start = _trim_diagnosis_trigger(raw_text, start, end)
        if new_start != start:
            start = new_start

    if str(entity.type) in {"TRIỆU_CHỨNG", "CHẨN_ĐOÁN"}:
        new_start = _trim_subject_verb_trigger(raw_text, start, end, str(entity.type))
        if new_start != start:
            start = new_start

    start, end = _trim_outer(raw_text, start, end)
    if start == entity.start and end == entity.end and assertions == entity.assertions:
        return entity, None
    if start >= end:
        return entity, None
    updated = with_span(replace(entity, assertions=ordered_valid_assertions(assertions)), raw_text, start, end)
    return updated, PostprocessDecision(
        action="trim_entity",
        reason="conservative_span_trim",
        before=entity_payload(entity),
        after=entity_payload(updated),
    )


def should_drop_entity(entity: FinalEntity, raw_text: str, config: dict[str, Any]) -> tuple[bool, str]:
    if _is_generic_problem_entity(entity):
        return True, "generic_problem_phrase"
    if _is_non_lab_qualitative_result(entity):
        return True, "non_lab_qualitative_result"
    if _is_food_context_drug_false_positive(entity, raw_text, config):
        return True, "drug_without_dose_in_food_or_substance_context"
    return False, ""


def cleanup_candidates_assertions(entity: FinalEntity, config: dict[str, Any] | None = None) -> tuple[FinalEntity, bool, bool]:
    cleanup_cfg = (config or {}).get("cleanup", {}) if isinstance((config or {}).get("cleanup"), dict) else {}
    candidates = list(entity.candidates)
    assertions = list(entity.assertions)
    if bool(cleanup_cfg.get("remove_candidates_from_non_linked_types", True)) and not is_linked_type(entity):
        candidates = []
    elif bool(cleanup_cfg.get("dedupe_candidates", True)):
        candidates = dedupe_stable(candidates)
    if bool(cleanup_cfg.get("remove_assertions_from_non_assertable_types", True)) and not is_assertable(entity):
        assertions = []
    elif bool(cleanup_cfg.get("dedupe_assertions", True)):
        assertions = ordered_valid_assertions(assertions)
    changed_candidates = candidates != entity.candidates
    changed_assertions = assertions != entity.assertions
    return replace(entity, candidates=candidates, assertions=assertions), changed_candidates, changed_assertions


def _trim_outer(raw_text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and raw_text[start] in TRIM_CHARS:
        start += 1
    while end > start and raw_text[end - 1] in TRIM_CHARS:
        end -= 1
    return start, end


def _trim_leading_cue(raw_text: str, start: int, end: int, cues: tuple[str, ...]) -> int:
    text_norm = normalize_for_lookup(raw_text[start:end])
    for cue in sorted(cues, key=len, reverse=True):
        cue_norm = normalize_for_lookup(cue)
        if text_norm == cue_norm:
            return start
        if text_norm.startswith(f"{cue_norm} "):
            cursor = start + len(cue)
            while cursor < end and raw_text[cursor] in TRIM_CHARS:
                cursor += 1
            return cursor
    return start


def _trim_diagnosis_trigger(raw_text: str, start: int, end: int) -> int:
    text_norm = normalize_for_lookup(raw_text[start:end])
    for trigger in sorted(DIAGNOSIS_TRIGGERS, key=len, reverse=True):
        trigger_norm = normalize_for_lookup(trigger)
        if text_norm.startswith(f"{trigger_norm} "):
            cursor = start + len(trigger)
            while cursor < end and raw_text[cursor] in TRIM_CHARS:
                cursor += 1
            remainder_norm = normalize_for_lookup(raw_text[cursor:end])
            if any(remainder_norm == head or remainder_norm.startswith(f"{head} ") for head in DISEASE_HEADS):
                return cursor
    return start


def _trim_subject_verb_trigger(raw_text: str, start: int, end: int, entity_type: str) -> int:
    text_norm = normalize_for_lookup(raw_text[start:end])
    for trigger in sorted(SUBJECT_VERB_TRIGGERS, key=len, reverse=True):
        trigger_norm = normalize_for_lookup(trigger)
        if not text_norm.startswith(f"{trigger_norm} "):
            continue
        cursor = start + len(trigger)
        while cursor < end and raw_text[cursor] in TRIM_CHARS:
            cursor += 1
        remainder_norm = normalize_for_lookup(raw_text[cursor:end])
        heads = SYMPTOM_HEADS if entity_type == "TRIỆU_CHỨNG" else DISEASE_HEADS
        if any(remainder_norm == head or remainder_norm.startswith(f"{head} ") for head in heads):
            return cursor
    return start


def _is_generic_problem_entity(entity: FinalEntity) -> bool:
    if str(entity.type) not in {"TRIỆU_CHỨNG", "CHẨN_ĐOÁN"}:
        return False
    text_norm = normalize_for_lookup(entity.text)
    if text_norm in GENERIC_ENTITY_TEXTS:
        return True
    if text_norm.startswith("bệnh nhân ") or text_norm.startswith("người bệnh "):
        return True
    if text_norm.startswith("bệnh ") and len(text_norm.split()) <= 2:
        return True
    return False


def _is_non_lab_qualitative_result(entity: FinalEntity) -> bool:
    return str(entity.type) != "KẾT_QUẢ_XÉT_NGHIỆM" and normalize_for_lookup(entity.text) in QUALITATIVE_RESULTS


def _is_food_context_drug_false_positive(entity: FinalEntity, raw_text: str, config: dict[str, Any]) -> bool:
    fp_cfg = config.get("drug_false_positive", {}) if isinstance(config.get("drug_false_positive"), dict) else {}
    if not bool(fp_cfg.get("enabled", True)) or str(entity.type) != "THUỐC":
        return False
    parsed = entity.provenance.get("rxnorm_linking", {}).get("parsed", {})
    if _has_drug_slots(parsed):
        return False
    context = normalize_for_lookup(_local_sentence_context(raw_text, entity.start, entity.end, radius=80))
    food_terms = tuple(str(item) for item in fp_cfg.get("food_or_substance_terms", [])) or (
        "caffeine",
        "cà phê",
        "rượu bia",
        "thuốc lá",
        "hút thuốc",
    )
    has_food_context = any(normalize_for_lookup(term) in context for term in food_terms)
    medication_context = context
    for phrase in SUBSTANCE_CONTEXT_PHRASES:
        medication_context = medication_context.replace(phrase, " ")
    has_strong_med_context = any(cue in medication_context for cue in STRONG_MED_CONTEXT_CUES)
    return has_food_context and not has_strong_med_context


def _has_drug_slots(parsed: dict[str, Any]) -> bool:
    if not isinstance(parsed, dict):
        return False
    strength = parsed.get("strength_value")
    has_strength = strength not in (None, "") and not (isinstance(strength, float) and math.isnan(strength))
    return bool(has_strength or parsed.get("route") or parsed.get("frequency"))


def _local_sentence_context(raw_text: str, start: int, end: int, radius: int = 80) -> str:
    left = max(0, start - radius)
    right = min(len(raw_text), end + radius)
    for index in range(start - 1, left - 1, -1):
        if raw_text[index] in ".!?\n\r":
            left = index + 1
            break
    for index in range(end, right):
        if raw_text[index] in ".!?\n\r":
            right = index
            break
    return raw_text[left:right]
