from __future__ import annotations

from src.extractors.base import ExtractionContext
from src.extractors.lab_extractor import LabExtractor
from src.preprocess.chunker import preprocess_text


LABS = [
    {"alias": "troponin", "canonical": "troponin"},
    {"alias": "kali", "canonical": "kali"},
    {"alias": "creatinine", "canonical": "creatinine"},
    {"alias": "tổng phân tích nước tiểu", "canonical": "tổng phân tích nước tiểu"},
]


def test_lab_extractor_emits_test_and_numeric_results() -> None:
    raw = "Kết quả xét nghiệm: troponin 0.01; kali là 6.3; creatinine 2.0 -> 3.2"
    output = preprocess_text(raw)
    extractor = LabExtractor(lab_rows=LABS)

    candidates = extractor.extract(ExtractionContext(raw, output.views, output.chunks))
    texts_by_type = {(cand.text, cand.raw_type) for cand in candidates}

    assert ("troponin", "TÊN_XÉT_NGHIỆM") in texts_by_type
    assert ("0.01", "KẾT_QUẢ_XÉT_NGHIỆM") in texts_by_type
    assert ("kali", "TÊN_XÉT_NGHIỆM") in texts_by_type
    assert ("6.3", "KẾT_QUẢ_XÉT_NGHIỆM") in texts_by_type
    assert ("2.0 -> 3.2", "KẾT_QUẢ_XÉT_NGHIỆM") in texts_by_type
    for cand in candidates:
        assert raw[cand.start : cand.end] == cand.text


def test_lab_extractor_emits_qualitative_result() -> None:
    raw = "tổng phân tích nước tiểu bình thường"
    output = preprocess_text(raw)
    extractor = LabExtractor(lab_rows=LABS)

    candidates = extractor.extract(ExtractionContext(raw, output.views, output.chunks))

    assert any(cand.text == "bình thường" and cand.raw_type == "KẾT_QUẢ_XÉT_NGHIỆM" for cand in candidates)


def test_lab_extractor_emits_full_qualitative_result_phrase() -> None:
    raw = "phân tích nước tiểu không ghi nhận bất thường"
    output = preprocess_text(raw)
    extractor = LabExtractor(lab_rows=[{"alias": "phân tích nước tiểu", "canonical": "phân tích nước tiểu"}])

    candidates = extractor.extract(ExtractionContext(raw, output.views, output.chunks))

    assert any(cand.text == raw and cand.raw_type == "KẾT_QUẢ_XÉT_NGHIỆM" for cand in candidates)
