from __future__ import annotations

from src.assertion import AssertionDetector
from src.data_types import FinalEntity


def _entity(raw: str, text: str, entity_type: str, *, section: str | None = None) -> FinalEntity:
    start = raw.index(text)
    return FinalEntity(
        text=text,
        start=start,
        end=start + len(text),
        type=entity_type,
        assertions=[],
        candidates=[],
        confidence=0.8,
        provenance={"section": section},
    )


def _assertions(raw: str, text: str, entity_type: str, *, section: str | None = None) -> list[str]:
    entity = _entity(raw, text, entity_type, section=section)
    return AssertionDetector().apply([entity], raw)[0].assertions


def test_negation_simple() -> None:
    raw = "Không sốt."

    assert "isNegated" in _assertions(raw, "sốt", "TRIỆU_CHỨNG")


def test_negation_list_scope() -> None:
    raw = "Không sốt, ớn lạnh, nôn, ho."

    for text in ("sốt", "nôn", "ho"):
        assert "isNegated" in _assertions(raw, text, "TRIỆU_CHỨNG")


def test_distant_negation_does_not_leak_to_later_entity() -> None:
    raw = "Không ghi nhận bất thường khi khám ban đầu, bệnh nhân được theo dõi sát trong nhiều giờ và sau đó đau bụng."

    assert "isNegated" not in _assertions(raw, "đau bụng", "TRIỆU_CHỨNG")


def test_pseudo_negation_guard() -> None:
    raw = "Không loại trừ viêm phổi."

    assert "isNegated" not in _assertions(raw, "viêm phổi", "CHẨN_ĐOÁN")


def test_historical_diagnosis() -> None:
    raw = "Tiền sử tăng huyết áp."

    assert "isHistorical" in _assertions(raw, "tăng huyết áp", "CHẨN_ĐOÁN")


def test_current_event_override_blocks_historical() -> None:
    raw = "Lý do nhập viện: đau bụng."

    assert "isHistorical" not in _assertions(raw, "đau bụng", "TRIỆU_CHỨNG", section="PAST_HISTORY")


def test_historical_drug_pre_admission() -> None:
    raw = "Thuốc trước khi nhập viện: atenolol."

    assert "isHistorical" in _assertions(raw, "atenolol", "THUỐC")


def test_pre_admission_drug_heading_does_not_make_problem_historical() -> None:
    raw = "Thuốc trước khi nhập viện: viêm phổi."

    assert "isHistorical" not in _assertions(raw, "viêm phổi", "CHẨN_ĐOÁN")


def test_current_drug_not_historical() -> None:
    raw = "Được cho aspirin 325mg x 1 tại cấp cứu."

    assert "isHistorical" not in _assertions(raw, "aspirin 325mg x 1", "THUỐC", section="PRE_ADMISSION_MEDICATION")


def test_family_positive() -> None:
    raw = "Mẹ mắc ung thư đại tràng."

    assert "isFamily" in _assertions(raw, "ung thư đại tràng", "CHẨN_ĐOÁN")


def test_family_reporter_guard() -> None:
    raw = "Vợ nhận thấy bệnh nhân ảo giác."

    assert "isFamily" not in _assertions(raw, "ảo giác", "TRIỆU_CHỨNG")


def test_non_assertable_type_guard() -> None:
    raw = "Không troponin 0.01."
    entities = [
        _entity(raw, "troponin", "TÊN_XÉT_NGHIỆM"),
        _entity(raw, "0.01", "KẾT_QUẢ_XÉT_NGHIỆM"),
    ]
    asserted = AssertionDetector().apply(entities, raw)

    assert [entity.assertions for entity in asserted] == [[], []]


def test_offsets_preserved() -> None:
    raw = "Không sốt."
    entity = AssertionDetector().apply([_entity(raw, "sốt", "TRIỆU_CHỨNG")], raw)[0]

    assert raw[entity.start : entity.end] == entity.text