from __future__ import annotations

from src.data_types import Chunk, TextViews
from src.extractors.base import ExtractionContext
from src.extractors.gliner_extractor import GLiNERExtractor
from src.ner.gliner_backend import GLiNERBackend


class PassScoreModel:
    def predict_entities(self, text, labels, *, threshold, **kwargs):
        score = .4 if len(labels) == 2 else .8
        return [{"start": 0, "end": len(text), "text": text, "label": labels[0], "score": score}] if score >= threshold else []


def test_pass_specific_threshold_filters_before_exact_merge() -> None:
    raw = "sốt"
    indexes = list(range(len(raw)))
    context = ExtractionContext(raw, TextViews(raw, raw, raw, raw, indexes, indexes, indexes), [Chunk(raw, 0, len(raw), section="CURRENT")])
    extractor = GLiNERExtractor(config={
        "enabled": True,
        "threshold": .35,
        "proposal_cache_mode": "raw_floor",
        "proposal_threshold": .15,
        "windowing": {"max_tokens": 20, "overlap_tokens": 0},
        "passes": [
            {"name": "full", "label_map": {"symptom": "TRIỆU_CHỨNG"}, "threshold": .5},
            {"name": "problem", "label_map": {"symptom": "TRIỆU_CHỨNG", "diagnosis": "CHẨN_ĐOÁN"}, "threshold": .45},
        ],
    }, backend=GLiNERBackend({}, model=PassScoreModel()))
    rows = extractor.extract(context)
    assert len(rows) == 1
    assert rows[0].features["supporting_passes"] == ["full"]
    assert rows[0].features["proposal_evidence"][0]["selection_threshold"] == .5