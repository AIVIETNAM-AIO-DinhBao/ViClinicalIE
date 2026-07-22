from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class GLiNERPrediction:
    start: int
    end: int
    label: str
    score: float
    text: str


class GLiNERModelProtocol(Protocol):
    def predict_entities(self, text: str, labels: list[str], *, threshold: float, **kwargs: Any) -> list[dict[str, Any]]: ...


class GLiNERBackend:
    """Thin GLiNER runtime wrapper. It only knows window-local offsets."""

    def __init__(self, config: Mapping[str, Any] | None = None, *, model: GLiNERModelProtocol | None = None) -> None:
        self.config = dict(config or {})
        self.model_name_or_path = str(self.config.get("model_name_or_path", "urchade/gliner_multi-v2.1"))
        self.model_revision = self.config.get("model_revision")
        self.local_files_only = bool(self.config.get("local_files_only", False))
        self.device = str(self.config.get("device", "auto"))
        self.required = bool(self.config.get("required", False))
        self.model: GLiNERModelProtocol | None = model
        self.error: str | None = None
        self.load_count = int(model is not None)
        self.lazy_load = bool(self.config.get("lazy_load", True))
        if self.model is None and not self.lazy_load:
            self._load_model()

    @property
    def available(self) -> bool:
        return self.model is not None

    def predict(
        self,
        text: str,
        labels: list[str],
        *,
        threshold: float,
        inference_options: Mapping[str, Any] | None = None,
    ) -> list[GLiNERPrediction]:
        if not text:
            return []
        if self.model is None and self.error is None:
            self._load_model()
        if self.model is None:
            message = self.error or "GLiNER model is unavailable"
            if self.required:
                raise RuntimeError(message)
            return []
        try:
            rows = self.model.predict_entities(text, labels, threshold=float(threshold), **dict(inference_options or {}))
        except Exception as exc:
            raise RuntimeError(f"GLiNER inference failed: {exc}") from exc
        predictions: list[GLiNERPrediction] = []
        for row in rows if isinstance(rows, list) else []:
            prediction = _parse_prediction(row, text)
            if prediction is not None:
                predictions.append(prediction)
        return predictions

    def metadata(self) -> dict[str, Any]:
        return {
            "backend": "gliner",
            "model_name_or_path": self.model_name_or_path,
            "model_revision": self.model_revision,
            "local_files_only": self.local_files_only,
            "device": self.device,
        }

    def _load_model(self) -> None:
        if self.model is not None:
            return
        if self.error:
            if self.required:
                raise RuntimeError(self.error)
            return
        self._validate_explicit_local_path()
        if self.error:
            if self.required:
                raise FileNotFoundError(self.error)
            return
        try:
            from gliner import GLiNER  # type: ignore
        except Exception as exc:
            self.error = f"gliner is not available: {exc}"
            if self.required:
                raise RuntimeError(self.error) from exc
            return

        kwargs: dict[str, Any] = {}
        if self.model_revision:
            kwargs["revision"] = str(self.model_revision)
        if self.local_files_only:
            kwargs["local_files_only"] = True
        try:
            self.model = GLiNER.from_pretrained(self.model_name_or_path, **kwargs)
            self.load_count += 1
            if self.device not in {"auto", "wait"}:
                try:
                    self.model = self.model.to(self.device)  # type: ignore[assignment, union-attr]
                except Exception:
                    pass
        except Exception as exc:
            self.model = None
            self.error = f"Could not load GLiNER model {self.model_name_or_path}: {exc}"
            if self.required:
                raise RuntimeError(self.error) from exc

    def _validate_explicit_local_path(self) -> None:
        model_path = Path(self.model_name_or_path)
        if (model_path.is_absolute() or model_path.parts[:1] == ("models",)) and not model_path.exists():
            self.error = f"GLiNER model path does not exist: {model_path}"
            if self.required:
                raise FileNotFoundError(self.error)


def _parse_prediction(row: Any, text: str) -> GLiNERPrediction | None:
    if not isinstance(row, Mapping):
        return None
    try:
        start = int(row["start"])
        end = int(row["end"])
        label = str(row["label"])
        score = float(row.get("score", 0.0))
    except (KeyError, TypeError, ValueError):
        return None
    if start < 0 or end <= start or end > len(text) or not label:
        return None
    predicted_text = str(row.get("text", text[start:end]))
    if predicted_text != text[start:end]:
        return None
    return GLiNERPrediction(start, end, label, score, predicted_text)