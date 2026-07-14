from __future__ import annotations

from src.extractors.base import ExtractionContext
from src.extractors.imaging_extractor import ImagingExtractor
from src.preprocess.chunker import preprocess_text


def test_imaging_extractor_catches_common_imaging_tests() -> None:
    raw = "Kết quả chẩn đoán hình ảnh: chụp x-quang ngực bình thường; chụp ct sọ não không xuất huyết."
    output = preprocess_text(raw)
    extractor = ImagingExtractor()

    candidates = extractor.extract(ExtractionContext(raw, output.views, output.chunks))
    texts = {cand.text.lower() for cand in candidates}

    assert "chụp x-quang ngực" in texts
    assert "chụp ct sọ não" in texts
    assert any(cand.text.lower() == "chụp x-quang ngực bình thường" and cand.raw_type == "KẾT_QUẢ_XÉT_NGHIỆM" for cand in candidates)
    for cand in candidates:
        assert raw[cand.start : cand.end] == cand.text
