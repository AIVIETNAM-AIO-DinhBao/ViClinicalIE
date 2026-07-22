from __future__ import annotations

from src.ner.gliner_backend import GLiNERBackend
import pytest


class FakeModel:
    def predict_entities(self, text, labels, *, threshold):
        return [{"start": 0, "end": 3, "text": text[:3], "label": labels[0], "score": threshold + 0.1}]


def test_backend_parses_valid_local_prediction() -> None:
    backend = GLiNERBackend({"model_name_or_path": "fake"}, model=FakeModel())
    predictions = backend.predict("sốt cao", ["symptom"], threshold=0.35)
    assert predictions[0].text == "sốt"
    assert predictions[0].start == 0
    assert backend.load_count == 1


def test_optional_missing_backend_returns_empty() -> None:
    backend = object.__new__(GLiNERBackend)
    backend.model = None
    backend.required = False
    backend.error = "missing"
    assert backend.predict("sốt", ["symptom"], threshold=0.35) == []


def test_required_missing_local_model_is_validated_on_inference(tmp_path) -> None:
    backend = GLiNERBackend({"required": True, "model_name_or_path": str(tmp_path / "missing")})
    assert backend.load_count == 0
    with pytest.raises((FileNotFoundError, RuntimeError)):
        backend.predict("sốt", ["symptom"], threshold=0.15)