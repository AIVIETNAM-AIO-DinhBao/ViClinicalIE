from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from src.data_types import Chunk, SpanCandidate, VALID_ENTITY_TYPES
from src.extractors.base import BaseExtractor, ExtractionContext
from src.extractors.utils import make_span_candidate
from src.ner.gliner_backend import GLiNERBackend
from src.ner.gliner_windows import TransformersTokenCounter, build_gliner_windows
from src.ner.prediction_cache import build_cache_key
from src.ner.proposal_store import PROPOSAL_SCHEMA_VERSION, ProposalStore
from src.ner.thresholding import filter_proposals, merge_exact_proposals, parse_threshold_profile


DEFAULT_LABEL_MAP = {
    "symptom": "TRIỆU_CHỨNG",
    "disease or diagnosis": "CHẨN_ĐOÁN",
    "medication or drug": "THUỐC",
    "medical test or lab name": "TÊN_XÉT_NGHIỆM",
    "test result or measurement value": "KẾT_QUẢ_XÉT_NGHIỆM",
}


class GLiNERExtractor(BaseExtractor):
    name = "gliner"

    def __init__(self, *, config: Mapping[str, Any] | None = None, backend: GLiNERBackend | None = None) -> None:
        self.config = dict(config or {})
        self.enabled = bool(self.config.get("enabled", False))
        self.label_map = {str(label): str(entity_type) for label, entity_type in dict(self.config.get("label_map", DEFAULT_LABEL_MAP)).items()}
        self.passes = _parse_passes(self.config, self.label_map)
        invalid_types = {entity_type for item in self.passes for entity_type in item["label_map"].values()} - VALID_ENTITY_TYPES
        if invalid_types:
            raise ValueError(f"Invalid GLiNER canonical types: {sorted(invalid_types)}")
        self.labels = list(self.label_map)
        self.default_threshold, self.threshold_by_type = parse_threshold_profile(self.config.get("threshold", 0.35))
        self.pass_thresholds = {
            item["name"]: parse_threshold_profile(item["threshold"], fallback=self.default_threshold)
            for item in self.passes if item.get("threshold") is not None
        }
        self.pass_name = str(self.config.get("pass_name", "full_five_type"))
        self.proposal_cache_mode = str(self.config.get("proposal_cache_mode", "inference_threshold"))
        if self.proposal_cache_mode not in {"inference_threshold", "raw_floor"}:
            raise ValueError("proposal_cache_mode must be inference_threshold or raw_floor")
        configured_floor = float(self.config.get("proposal_threshold", self._inference_threshold()))
        self.proposal_threshold = self._inference_threshold() if self.proposal_cache_mode == "inference_threshold" else configured_floor
        if self.proposal_threshold > self._inference_threshold():
            raise ValueError("proposal_threshold must be <= every selection threshold")
        window_cfg = dict(self.config.get("windowing", {}))
        self.max_tokens = int(window_cfg.get("max_tokens", 320))
        self.overlap_tokens = int(window_cfg.get("overlap_tokens", 64))
        self.window_strategy = str(window_cfg.get("strategy", "legacy_chunk"))
        inference_cfg = self.config.get("inference_options", {})
        if not isinstance(inference_cfg, Mapping):
            raise ValueError("GLiNER inference_options must be a mapping")
        self.inference_options = dict(inference_cfg)
        tokenizer_name = window_cfg.get("tokenizer_name_or_path")
        self.token_counter = TransformersTokenCounter(
            str(tokenizer_name),
            revision=str(window_cfg["tokenizer_revision"]) if window_cfg.get("tokenizer_revision") else None,
            local_files_only=bool(self.config.get("local_files_only", False)),
        ) if self.enabled and tokenizer_name else None
        self.backend = backend
        if self.enabled and self.backend is None:
            self.backend = GLiNERBackend(self.config)
        cache_cfg = dict(self.config.get("cache", {}))
        self.proposal_store = ProposalStore(cache_cfg.get("directory", "outputs/cache/gliner")) if cache_cfg.get("enabled", False) else None

    def extract(self, context: ExtractionContext) -> list[SpanCandidate]:
        if not self.enabled:
            return []
        if self.backend is None:
            raise RuntimeError("GLiNER extractor is enabled without a backend")
        cache_key = self._cache_key(context.raw_text)
        cached = self.proposal_store.get(cache_key) if self.proposal_store else None
        if cached is not None:
            return self._select_candidates([_candidate_from_cache(row, context.raw_text) for row in cached])

        proposals: list[SpanCandidate] = []
        windows = build_gliner_windows(
            context.raw_text,
            context.chunks,
            max_tokens=self.max_tokens,
            overlap_tokens=self.overlap_tokens,
            counter=self.token_counter,
            strategy=self.window_strategy,
        )
        for pass_config in self.passes:
            label_map = pass_config["label_map"]
            for window in windows:
                for prediction in self.backend.predict(
                    window.text,
                    list(label_map),
                    threshold=self.proposal_threshold,
                    inference_options=self.inference_options,
                ):
                    entity_type = label_map.get(prediction.label)
                    if entity_type is None:
                        continue
                    start, end = _trim_global_span(context.raw_text, window.start + prediction.start, window.start + prediction.end)
                    if start >= end or context.raw_text[start:end] != prediction.text.strip():
                        continue
                    proposals.append(
                        make_span_candidate(
                            context.raw_text,
                            start,
                            end,
                            raw_type=entity_type,
                            source="gliner",
                            score=prediction.score,
                            chunk=_chunk_for_position(context.chunks, start, end) or Chunk(
                                context.raw_text[start:end], start, end,
                                section=window.section, subsection=window.subsection,
                            ),
                            features={
                                "backend": "gliner",
                                "model": self.backend.metadata(),
                                "prompt_label": prediction.label,
                                "canonical_type": entity_type,
                                "pass_name": pass_config["name"],
                                "window_id": window.window_id,
                                "parent_chunk_id": window.parent_chunk_id,
                                "source_chunk_ids": list(window.source_chunk_ids),
                                "window_strategy": self.window_strategy,
                                "window_start": window.start,
                                "window_end": window.end,
                                "section": window.section,
                                "subsection": window.subsection,
                                "local_position": [prediction.start, prediction.end],
                                "global_position": [start, end],
                                "raw_model_score": prediction.score,
                            },
                        )
                    )
        if self.proposal_store:
            self.proposal_store.put(cache_key, [_candidate_to_cache(candidate) for candidate in proposals])
        return self._select_candidates(proposals)

    def _select_candidates(self, proposals: list[SpanCandidate]) -> list[SpanCandidate]:
        selected: list[SpanCandidate] = []
        for candidate in proposals:
            pass_name = str(candidate.features.get("pass_name", ""))
            pass_default, pass_types = self.pass_thresholds.get(pass_name, (self.default_threshold, self.threshold_by_type))
            threshold = pass_types.get(str(candidate.raw_type), pass_default)
            if candidate.score < threshold:
                continue
            candidate.features = {
                **candidate.features,
                "selection_threshold": threshold,
            }
            selected.append(candidate)
        return merge_exact_proposals(selected)

    def _inference_threshold(self) -> float:
        pass_values = [threshold for default, per_type in self.pass_thresholds.values() for threshold in (default, *per_type.values())]
        return min([self.default_threshold, *self.threshold_by_type.values(), *pass_values])

    def _cache_key(self, raw_text: str) -> str:
        model_metadata = self.backend.metadata() if self.backend else {}
        payload = {
            "input_hash": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            "proposal_schema_version": PROPOSAL_SCHEMA_VERSION,
            "model_hash": hashlib.sha256(repr(sorted(model_metadata.items())).encode("utf-8")).hexdigest(),
            "label_schema_hash": hashlib.sha256(repr([
                {"name": item["name"], "label_map": item["label_map"]} for item in self.passes
            ]).encode("utf-8")).hexdigest(),
            "chunking_hash": hashlib.sha256(repr({
                "strategy": self.window_strategy,
                "max_tokens": self.max_tokens,
                "overlap_tokens": self.overlap_tokens,
                "tokenizer": getattr(self.token_counter, "tokenizer_name_or_path", None),
                "tokenizer_revision": getattr(self.token_counter, "revision", None),
            }).encode("utf-8")).hexdigest(),
            "proposal_threshold_hash": hashlib.sha256(repr(self.proposal_threshold).encode("utf-8")).hexdigest(),
            "proposal_cache_mode": self.proposal_cache_mode,
            "inference_options_hash": hashlib.sha256(repr(sorted(self.inference_options.items())).encode("utf-8")).hexdigest(),
        }
        return build_cache_key(payload)


def _trim_global_span(raw_text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and raw_text[start].isspace():
        start += 1
    while end > start and raw_text[end - 1].isspace():
        end -= 1
    return start, end


def _chunk_for_position(chunks, start, end):
    for chunk in chunks:
        if chunk.start <= start and end <= chunk.end:
            return chunk
    return None


def _candidate_to_cache(candidate: SpanCandidate) -> dict[str, Any]:
    return {
        "text": candidate.text,
        "start": candidate.start,
        "end": candidate.end,
        "raw_type": candidate.raw_type,
        "source": candidate.source,
        "score": candidate.score,
        "section": candidate.section,
        "subsection": candidate.subsection,
        "context_left": candidate.context_left,
        "context_right": candidate.context_right,
        "features": candidate.features,
    }


def _candidate_from_cache(row: Mapping[str, Any], raw_text: str) -> SpanCandidate:
    start, end = int(row["start"]), int(row["end"])
    if raw_text[start:end] != row["text"]:
        raise ValueError("Cached GLiNER candidate offset mismatch")
    return SpanCandidate(
        text=str(row["text"]), start=start, end=end, raw_type=row.get("raw_type"), source=str(row["source"]), score=float(row["score"]),
        section=row.get("section"), subsection=row.get("subsection"), context_left=str(row.get("context_left", "")),
        context_right=str(row.get("context_right", "")), features=dict(row.get("features", {})),
    )


def _parse_passes(config: Mapping[str, Any], default_label_map: dict[str, str]) -> list[dict[str, Any]]:
    configured = config.get("passes")
    if not configured:
        return [{"name": str(config.get("pass_name", "full_five_type")), "label_map": dict(default_label_map)}]
    if not isinstance(configured, list):
        raise ValueError("GLiNER passes must be a list")
    passes: list[dict[str, Any]] = []
    for index, item in enumerate(configured):
        if not isinstance(item, Mapping):
            raise ValueError(f"GLiNER pass {index} must be a mapping")
        name = str(item.get("name", f"pass_{index}"))
        label_map = {str(label): str(entity_type) for label, entity_type in dict(item.get("label_map", {})).items()}
        if not label_map:
            raise ValueError(f"GLiNER pass {name} has no labels")
        passes.append({"name": name, "label_map": label_map, "threshold": item.get("threshold")})
    return passes