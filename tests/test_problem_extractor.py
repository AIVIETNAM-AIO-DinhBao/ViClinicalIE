from __future__ import annotations

from src.extractors.base import ExtractionContext
from src.extractors.problem_extractor import ProblemExtractor
from src.preprocess.chunker import preprocess_text


def test_problem_extractor_catches_symptoms_without_negation_trigger() -> None:
    raw = "Không sốt, bệnh nhân đau bụng vùng hạ sườn phải và khó thở khi gắng sức."
    output = preprocess_text(raw)
    extractor = ProblemExtractor()

    candidates = extractor.extract(ExtractionContext(raw, output.views, output.chunks))
    texts = {cand.text for cand in candidates}

    assert "sốt" in texts
    assert "không sốt" not in {text.lower() for text in texts}
    assert "đau bụng vùng hạ sườn phải" in texts
    assert "khó thở khi gắng sức" in texts
    for cand in candidates:
        assert raw[cand.start : cand.end] == cand.text


def test_problem_extractor_catches_diagnosis_like_mentions() -> None:
    raw = "Chẩn đoán: viêm túi mật cấp, rung nhĩ kèm đáp ứng thất nhanh."
    output = preprocess_text(raw)
    extractor = ProblemExtractor()

    candidates = extractor.extract(ExtractionContext(raw, output.views, output.chunks))
    texts_by_type = {(cand.text, cand.raw_type) for cand in candidates}

    assert ("viêm túi mật cấp", "CHẨN_ĐOÁN") in texts_by_type
    assert ("rung nhĩ kèm đáp ứng thất nhanh", "CHẨN_ĐOÁN") in texts_by_type


def test_problem_extractor_filters_generic_patient_problem_spans() -> None:
    raw = "Bệnh nhân xuất hiện cảm giác đánh trống ngực. Bệnh hiện tại ổn."
    output = preprocess_text(raw)
    extractor = ProblemExtractor()

    candidates = extractor.extract(ExtractionContext(raw, output.views, output.chunks))
    texts = {cand.text for cand in candidates}

    assert "Bệnh nhân xuất hiện cảm giác đánh trống ngực" not in texts
    assert "Bệnh hiện tại" not in texts
    assert any("đánh trống ngực" in text for text in texts)
