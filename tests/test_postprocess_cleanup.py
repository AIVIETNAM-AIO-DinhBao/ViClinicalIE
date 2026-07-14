from __future__ import annotations

from src.data_types import FinalEntity
from src.postprocess.cleanup import cleanup_candidates_assertions, should_drop_entity, trim_entity


def _entity(raw_text: str, text: str, entity_type: str, **kwargs) -> FinalEntity:
    start = raw_text.index(text)
    return FinalEntity(text=text, start=start, end=start + len(text), type=entity_type, **kwargs)


def test_trim_leading_negation_adds_is_negated() -> None:
    raw_text = "Không sốt."
    entity = _entity(raw_text, "Không sốt", "TRIỆU_CHỨNG")

    trimmed, decision = trim_entity(entity, raw_text, {})

    assert decision is not None
    assert trimmed.text == "sốt"
    assert trimmed.assertions == ["isNegated"]
    assert raw_text[trimmed.start : trimmed.end] == trimmed.text


def test_trim_diagnosis_trigger_when_disease_head_follows() -> None:
    raw_text = "Bác sĩ chẩn đoán viêm phổi."
    entity = _entity(raw_text, "chẩn đoán viêm phổi", "CHẨN_ĐOÁN")

    trimmed, decision = trim_entity(entity, raw_text, {})

    assert decision is not None
    assert trimmed.text == "viêm phổi"


def test_does_not_trim_diagnosis_trigger_without_disease_head() -> None:
    raw_text = "Bác sĩ chẩn đoán đau bụng."
    entity = _entity(raw_text, "chẩn đoán đau bụng", "CHẨN_ĐOÁN")

    trimmed, decision = trim_entity(entity, raw_text, {})

    assert decision is None
    assert trimmed.text == "chẩn đoán đau bụng"


def test_drop_caffeine_in_coffee_context_without_dose() -> None:
    raw_text = "Một ngày uống cà phê có caffeine."
    entity = _entity(
        raw_text,
        "caffeine",
        "THUỐC",
        candidates=["1886"],
        provenance={"rxnorm_linking": {"parsed": {}}},
    )

    should_drop, reason = should_drop_entity(entity, raw_text, {})

    assert should_drop
    assert reason == "drug_without_dose_in_food_or_substance_context"


def test_drop_generic_problem_phrase() -> None:
    raw = "Bệnh nhân đau bụng."
    entity = _entity(raw, "Bệnh nhân", "CHẨN_ĐOÁN")

    should_drop, reason = should_drop_entity(entity, raw, {})

    assert should_drop
    assert reason == "generic_problem_phrase"


def test_trim_subject_verb_trigger_to_symptom_head() -> None:
    raw = "Bệnh nhân xuất hiện đau ngực."
    entity = _entity(raw, "Bệnh nhân xuất hiện đau ngực", "TRIỆU_CHỨNG")

    trimmed, decision = trim_entity(entity, raw, {})

    assert decision is not None
    assert trimmed.text == "đau ngực"
    assert raw[trimmed.start : trimmed.end] == trimmed.text


def test_keep_aspirin_with_dose() -> None:
    raw_text = "Dùng aspirin 81mg mỗi ngày."
    entity = _entity(
        raw_text,
        "aspirin 81mg",
        "THUỐC",
        candidates=["1191"],
        provenance={"rxnorm_linking": {"parsed": {"strength_value": 81.0}}},
    )

    should_drop, _ = should_drop_entity(entity, raw_text, {})

    assert not should_drop


def test_remove_candidates_from_non_linked_type() -> None:
    entity = FinalEntity(text="đau đầu", start=0, end=7, type="TRIỆU_CHỨNG", candidates=["R51", "R51"])

    cleaned, candidate_changed, assertion_changed = cleanup_candidates_assertions(entity, {})

    assert candidate_changed
    assert not assertion_changed
    assert cleaned.candidates == []


def test_remove_assertions_from_lab_type() -> None:
    entity = FinalEntity(text="Na", start=0, end=2, type="TÊN_XÉT_NGHIỆM", assertions=["isNegated"])

    cleaned, candidate_changed, assertion_changed = cleanup_candidates_assertions(entity, {})

    assert not candidate_changed
    assert assertion_changed
    assert cleaned.assertions == []