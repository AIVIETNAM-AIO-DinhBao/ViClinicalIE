from __future__ import annotations

from src.data_types import Chunk, TextViews
from src.extractors.base import ExtractionContext
from src.extractors.gliner_extractor import GLiNERExtractor
from src.ner.gliner_backend import GLiNERBackend


class FakeModel:
    calls = 0

    def predict_entities(self, text, labels, *, threshold):
        self.calls += 1
        start = text.find("đau ngực")
        return [] if start < 0 else [{"start": start, "end": start + 8, "text": "đau ngực", "label": "symptom", "score": 0.9}]


class ThresholdModel:
    calls = 0

    def predict_entities(self, text, labels, *, threshold):
        self.calls += 1
        rows = [
            {"start": 0, "end": 3, "text": text[0:3], "label": labels[0], "score": 0.25},
            {"start": 4, "end": 8, "text": text[4:8], "label": labels[0], "score": 0.55},
        ]
        return [row for row in rows if row["score"] >= threshold]


def _context(raw: str) -> ExtractionContext:
    indexes = list(range(len(raw)))
    views = TextViews(raw, raw, raw, raw, indexes, indexes, indexes)
    return ExtractionContext(raw, views, [Chunk(raw, 0, len(raw), section="CURRENT")])


def test_extractor_restores_offset_and_provenance() -> None:
    raw = "Bệnh nhân đau ngực."
    backend = GLiNERBackend({}, model=FakeModel())
    extractor = GLiNERExtractor(config={"enabled": True, "threshold": 0.35, "windowing": {"max_tokens": 20, "overlap_tokens": 2}}, backend=backend)
    candidates = extractor.extract(_context(raw))
    assert len(candidates) == 1
    candidate = candidates[0]
    assert raw[candidate.start:candidate.end] == "đau ngực"
    assert candidate.raw_type == "TRIỆU_CHỨNG"
    assert candidate.source == "gliner"
    assert candidate.features["window_id"] == "c0:w0"


def test_overlap_windows_deduplicate_exact_prediction() -> None:
    raw = "a b đau ngực c d"
    backend = GLiNERBackend({}, model=FakeModel())
    extractor = GLiNERExtractor(config={"enabled": True, "threshold": 0.35, "windowing": {"max_tokens": 5, "overlap_tokens": 3}}, backend=backend)
    candidates = extractor.extract(_context(raw))
    assert len(candidates) == 1
    assert candidates[0].features["agreement_count"] == 2


def test_disabled_extractor_does_not_construct_backend() -> None:
    extractor = GLiNERExtractor(config={"enabled": False, "required": True, "model_name_or_path": "missing"})
    assert extractor.backend is None
    assert extractor.extract(_context("đau ngực")) == []


def test_cache_hit_does_not_call_model(tmp_path) -> None:
    raw = "Bệnh nhân đau ngực."
    model = FakeModel()
    backend = GLiNERBackend({}, model=model)
    config = {
        "enabled": True,
        "threshold": 0.35,
        "windowing": {"max_tokens": 20, "overlap_tokens": 2},
        "cache": {"enabled": True, "directory": str(tmp_path)},
    }
    first = GLiNERExtractor(config=config, backend=backend).extract(_context(raw))
    calls_after_first = model.calls
    second = GLiNERExtractor(config=config, backend=backend).extract(_context(raw))
    assert second == first
    assert model.calls == calls_after_first


def test_cache_hit_does_not_load_required_missing_model(tmp_path) -> None:
    raw = "Bệnh nhân đau ngực."
    model_path = tmp_path / "missing-model"
    cache_dir = tmp_path / "cache"
    config = {
        "enabled": True,
        "required": True,
        "model_name_or_path": str(model_path),
        "threshold": 0.35,
        "proposal_threshold": 0.2,
        "proposal_cache_mode": "raw_floor",
        "windowing": {"max_tokens": 20, "overlap_tokens": 2},
        "cache": {"enabled": True, "directory": str(cache_dir)},
    }
    seeded_backend = GLiNERBackend(config, model=FakeModel())
    expected = GLiNERExtractor(config=config, backend=seeded_backend).extract(_context(raw))
    lazy_backend = GLiNERBackend(config)
    actual = GLiNERExtractor(config=config, backend=lazy_backend).extract(_context(raw))
    assert actual == expected
    assert lazy_backend.load_count == 0


def test_proposal_cache_can_apply_different_selection_thresholds(tmp_path) -> None:
    raw = "sốt hooo"
    model = ThresholdModel()
    backend = GLiNERBackend({}, model=model)
    common = {
        "enabled": True,
        "proposal_threshold": 0.2,
        "proposal_cache_mode": "raw_floor",
        "windowing": {"max_tokens": 20, "overlap_tokens": 2},
        "cache": {"enabled": True, "directory": str(tmp_path)},
        "label_map": {"symptom": "TRIỆU_CHỨNG"},
    }
    low = GLiNERExtractor(config={**common, "threshold": 0.2}, backend=backend).extract(_context(raw))
    calls_after_low = model.calls
    high = GLiNERExtractor(config={**common, "threshold": 0.5}, backend=backend).extract(_context(raw))
    assert [candidate.text for candidate in low] == ["sốt", "hooo"]
    assert [candidate.text for candidate in high] == ["hooo"]
    assert model.calls == calls_after_low


def test_multi_pass_merges_exact_span_and_keeps_pass_provenance() -> None:
    raw = "đau ngực"
    backend = GLiNERBackend({}, model=FakeModel())
    extractor = GLiNERExtractor(
        config={
            "enabled": True,
            "proposal_threshold": 0.2,
            "proposal_cache_mode": "raw_floor",
            "threshold": 0.35,
            "windowing": {"max_tokens": 20, "overlap_tokens": 2},
            "passes": [
                {"name": "full", "label_map": {"symptom": "TRIỆU_CHỨNG"}},
                {"name": "problem", "label_map": {"symptom": "TRIỆU_CHỨNG"}},
            ],
        },
        backend=backend,
    )
    candidates = extractor.extract(_context(raw))
    assert len(candidates) == 1
    assert candidates[0].features["supporting_passes"] == ["full", "problem"]