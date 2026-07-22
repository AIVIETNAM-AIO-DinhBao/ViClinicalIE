from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.assertion import AssertionDetector, load_assertion_rules
from src.config import AppConfig
from src.data_types import Chunk, FinalEntity, SpanCandidate
from src.extractors import ExtractionContext, build_default_extractors
from src.formatting import PredictionFormatter
from src.linking.icd10_linker import ICD10Linker
from src.linking.rxnorm_linker import RxNormLinker
from src.postprocess import PostprocessReport, Postprocessor
from src.ner.evidence_adapter import normalize_candidates
from src.ner.deterministic_fusion import resolve_ner4_trace
from src.ner.simple_fusion import resolve_replay, resolve_replay_trace
from src.preprocess.chunker import preprocess_text
from src.section.section_detector import detect_sections, load_section_patterns
from src.type_resolution import TypeResolver
from src.type_resolution.resolver import TypeConflict, TypeOverlap


@dataclass(slots=True)
class PipelineResult:
    file_id: str
    raw_text: str
    entities: list[FinalEntity]
    records: list[dict[str, Any]]
    counters: dict[str, int] = field(default_factory=dict)
    entities_by_type: dict[str, int] = field(default_factory=dict)
    postprocess_report: PostprocessReport | None = None


@dataclass(slots=True)
class NERExtractionTrace:
    raw_text: str
    chunks: list[Chunk]
    candidates: list[SpanCandidate]
    entities: list[FinalEntity]
    resolver_conflicts: list[TypeConflict] = field(default_factory=list)
    resolver_overlaps: list[TypeOverlap] = field(default_factory=list)
    unresolved_candidates: list[dict[str, Any]] = field(default_factory=list)
    duplicate_exact_span_count: int = 0


class ClinicalIEPipeline:
    """Reusable end-to-end inference pipeline for one clinical note.

    The pipeline composes the validated Phase 2-8, Phase 10, and Phase 11 modules:
    preprocess -> section detection -> span extraction -> type resolution -> assertions ->
    ICD/RxNorm linking -> postprocess -> final JSON formatting.
    """

    def __init__(self, config: AppConfig, *, enable_sparse_retrieval: bool = True, ner_only: bool = False) -> None:
        self.config = config
        self.raw_config = copy.deepcopy(config.raw)
        if not enable_sparse_retrieval:
            _disable_sparse_linker_retrieval(self.raw_config)

        section_cfg = self.raw_config.get("section_detection", {})
        patterns_path = _resolve_patterns_path(config.config_path, section_cfg.get("patterns_config", "section_patterns.yaml"))
        self.section_patterns = load_section_patterns(patterns_path)
        self.section_config = section_cfg
        self.extractors = build_default_extractors(config)
        self.resolver = TypeResolver(self.raw_config.get("type_resolution", {}))

        self.ner_only = ner_only
        assertion_cfg = dict(self.raw_config.get("assertion_detection", {}))
        assertion_rules = _load_assertion_rules(config.config_path, assertion_cfg)
        self.assertion_detector = None if ner_only else AssertionDetector(assertion_cfg, rules=assertion_rules)

        self.icd_linker = None if ner_only else ICD10Linker(config.path("processed_dir"), self.raw_config.get("icd10_linking", {}))
        self.rx_linker = None if ner_only else RxNormLinker(config.path("processed_dir"), self.raw_config.get("rxnorm_linking", {}))
        self.postprocessor = Postprocessor(self.raw_config.get("postprocess", {}))
        self.formatter = PredictionFormatter(self.raw_config.get("output_format", {}))

    def process_text(self, raw_text: str, *, file_id: str = "") -> PipelineResult:
        if self.ner_only:
            return self.process_ner(raw_text, file_id=file_id)
        if self.assertion_detector is None or self.icd_linker is None or self.rx_linker is None:
            raise RuntimeError("Downstream pipeline components are not initialized")
        resolved, chunks, span_candidates = self._extract_and_resolve(raw_text)
        asserted = self.assertion_detector.apply(resolved, raw_text)
        icd_linked = self.icd_linker.link_entities(asserted, raw_text=raw_text)
        rx_linked = self.rx_linker.link_entities(icd_linked, raw_text=raw_text)
        postprocessed = (
            self.postprocessor.process_finalized_ner(rx_linked, raw_text=raw_text)
            if self._ner4_config() is not None
            else self.postprocessor.process(rx_linked, raw_text=raw_text)
        )
        records = self.formatter.format_entities(postprocessed.entities)

        entity_counts = Counter(str(entity.type) for entity in postprocessed.entities)
        counters = {
            "chunks": len(chunks),
            "span_candidates": len(span_candidates),
            "entities_before_postprocess": len(rx_linked),
            "entities_after_postprocess": len(postprocessed.entities),
            "records": len(records),
            "postprocess_entities_dropped": postprocessed.report.entities_dropped,
            "postprocess_overlap_resolutions": postprocessed.report.same_type_overlaps_resolved + postprocessed.report.different_type_overlaps_resolved,
        }
        return PipelineResult(
            file_id=file_id,
            raw_text=raw_text,
            entities=postprocessed.entities,
            records=records,
            counters=counters,
            entities_by_type=dict(sorted(entity_counts.items())),
            postprocess_report=postprocessed.report,
        )

    def process_ner(self, raw_text: str, *, file_id: str = "") -> PipelineResult:
        resolved, chunks, span_candidates = self._extract_and_resolve(raw_text)
        records = self.formatter.format_entities(resolved)
        entity_counts = Counter(str(entity.type) for entity in resolved)
        return PipelineResult(
            file_id=file_id,
            raw_text=raw_text,
            entities=resolved,
            records=records,
            counters={
                "chunks": len(chunks),
                "span_candidates": len(span_candidates),
                "entities_after_ner": len(resolved),
                "records": len(records),
            },
            entities_by_type=dict(sorted(entity_counts.items())),
        )

    def extract_ner_trace(self, raw_text: str) -> NERExtractionTrace:
        chunks, candidates = self._collect_ner_candidates(raw_text)
        ner4_cfg = self._ner4_config()
        if ner4_cfg is not None:
            replay = resolve_ner4_trace(
                candidates, raw_text, mode="G",
                resolver_config=self.raw_config.get("type_resolution", {}),
                boundary_config=ner4_cfg.get("boundary", {}),
                cluster_config=ner4_cfg.get("cluster", {}),
                type_config=ner4_cfg.get("type_policy", {}),
            )
            return NERExtractionTrace(
                raw_text=raw_text, chunks=chunks, candidates=candidates, entities=replay.entities,
                resolver_conflicts=list(replay.conflicts),
                unresolved_candidates=list(replay.unresolved),
                duplicate_exact_span_count=0,
            )
        mode = self._ner3_mode()
        if mode is not None:
            replay = resolve_replay_trace(
                candidates, raw_text, mode=mode,
                resolver_config=self.raw_config.get("type_resolution", {}),
                fusion_config=self.raw_config.get("ner3", {}).get("fusion", {}),
            )
            return NERExtractionTrace(
                raw_text=raw_text, chunks=chunks, candidates=candidates, entities=replay.entities,
                resolver_conflicts=list(replay.conflicts), resolver_overlaps=list(replay.overlaps),
                unresolved_candidates=list(replay.unresolved),
                duplicate_exact_span_count=replay.duplicate_exact_span_count,
            )
        resolver = self._new_resolver()
        entities = resolver.resolve(candidates, raw_text)
        return NERExtractionTrace(
            raw_text=raw_text,
            chunks=chunks,
            candidates=candidates,
            entities=entities,
            resolver_conflicts=list(resolver.conflicts),
            resolver_overlaps=list(resolver.overlaps),
            unresolved_candidates=list(resolver.unresolved),
            duplicate_exact_span_count=resolver.duplicate_exact_span_count,
        )

    def collect_ner_candidates(self, raw_text: str) -> tuple[list[Chunk], list[SpanCandidate]]:
        """Collect every enabled source once without resolving final entities."""
        return self._collect_ner_candidates(raw_text)

    def _collect_ner_candidates(self, raw_text: str) -> tuple[list[Chunk], list[SpanCandidate]]:
        output = preprocess_text(raw_text, self.raw_config)
        chunks = detect_sections(output.chunks, self.section_patterns, self.section_config)
        context = ExtractionContext(raw_text=raw_text, views=output.views, chunks=chunks, config=self.raw_config)
        candidates: list[SpanCandidate] = []
        for extractor in self.extractors:
            candidates.extend(extractor.extract(context))
        ner3_cfg = self.raw_config.get("ner3", {})
        if isinstance(ner3_cfg, dict) and bool(ner3_cfg.get("enabled", False)):
            evidence_cfg = ner3_cfg.get("evidence", {})
            if not isinstance(evidence_cfg, dict) or bool(evidence_cfg.get("enabled", True)):
                candidates = normalize_candidates(candidates)
        return chunks, candidates

    def resolve_candidates(
        self, raw_text: str, candidates: list[SpanCandidate], *, mode: str | None = None,
    ) -> list[FinalEntity]:
        ner4_cfg = self._ner4_config()
        if ner4_cfg is not None:
            return resolve_ner4_trace(
                candidates, raw_text, mode="G",
                resolver_config=self.raw_config.get("type_resolution", {}),
                boundary_config=ner4_cfg.get("boundary", {}),
                cluster_config=ner4_cfg.get("cluster", {}),
                type_config=ner4_cfg.get("type_policy", {}),
            ).entities
        if mode is not None:
            return resolve_replay(
                candidates, raw_text, mode=mode,
                resolver_config=self.raw_config.get("type_resolution", {}),
                fusion_config=self.raw_config.get("ner3", {}).get("fusion", {}),
            )
        return self._new_resolver().resolve(candidates, raw_text)

    def process_resolved_ner(self, raw_text: str, entities: list[FinalEntity], *, file_id: str = "") -> PipelineResult:
        records = self.formatter.format_entities(entities)
        counts = Counter(str(entity.type) for entity in entities)
        return PipelineResult(file_id, raw_text, entities, records, entities_by_type=dict(sorted(counts.items())))

    def process_resolved_end_to_end(self, raw_text: str, entities: list[FinalEntity], *, file_id: str = "") -> PipelineResult:
        if self.assertion_detector is None or self.icd_linker is None or self.rx_linker is None:
            raise RuntimeError("Downstream pipeline components are not initialized")
        asserted = self.assertion_detector.apply(entities, raw_text)
        icd_linked = self.icd_linker.link_entities(asserted, raw_text=raw_text)
        rx_linked = self.rx_linker.link_entities(icd_linked, raw_text=raw_text)
        postprocessed = self.postprocessor.process(rx_linked, raw_text=raw_text)
        records = self.formatter.format_entities(postprocessed.entities)
        counts = Counter(str(entity.type) for entity in postprocessed.entities)
        return PipelineResult(
            file_id, raw_text, postprocessed.entities, records,
            entities_by_type=dict(sorted(counts.items())), postprocess_report=postprocessed.report,
        )

    def process_finalized_ner_end_to_end(self, raw_text: str, entities: list[FinalEntity], *, file_id: str = "") -> PipelineResult:
        """Run frozen downstream components without re-editing NER boundaries."""

        if self.assertion_detector is None or self.icd_linker is None or self.rx_linker is None:
            raise RuntimeError("Downstream pipeline components are not initialized")
        frozen = [(item.start, item.end, item.text, str(item.type)) for item in entities]
        asserted = self.assertion_detector.apply(entities, raw_text)
        icd_linked = self.icd_linker.link_entities(asserted, raw_text=raw_text)
        rx_linked = self.rx_linker.link_entities(icd_linked, raw_text=raw_text)
        postprocessed = self.postprocessor.process_finalized_ner(rx_linked, raw_text=raw_text)
        after = [(item.start, item.end, item.text, str(item.type)) for item in postprocessed.entities]
        if sorted(frozen) != sorted(after):
            raise ValueError("Frozen NER span/type changed in downstream pipeline")
        records = self.formatter.format_entities(postprocessed.entities)
        counts = Counter(str(entity.type) for entity in postprocessed.entities)
        return PipelineResult(
            file_id, raw_text, postprocessed.entities, records,
            counters={"entities_after_ner": len(entities), "records": len(records)},
            entities_by_type=dict(sorted(counts.items())), postprocess_report=postprocessed.report,
        )

    def _extract_and_resolve(self, raw_text: str) -> tuple[list[FinalEntity], list[Any], list[SpanCandidate]]:
        chunks, candidates = self._collect_ner_candidates(raw_text)
        mode = self._ner3_mode()
        entities = self.resolve_candidates(raw_text, candidates, mode=mode)
        return entities, chunks, candidates

    def _new_resolver(self) -> TypeResolver:
        return TypeResolver(self.raw_config.get("type_resolution", {}))

    def _ner3_mode(self) -> str | None:
        ner3_cfg = self.raw_config.get("ner3", {})
        if not isinstance(ner3_cfg, dict) or not bool(ner3_cfg.get("enabled", False)):
            return None
        mode = ner3_cfg.get("mode")
        return str(mode) if mode is not None else None

    def _ner4_config(self) -> dict[str, Any] | None:
        ner4_cfg = self.raw_config.get("ner4", {})
        if not isinstance(ner4_cfg, dict) or not bool(ner4_cfg.get("enabled", False)):
            return None
        mode = str(ner4_cfg.get("mode", "deterministic"))
        if mode != "deterministic":
            raise ValueError(f"Unsupported NER-4 mode: {mode}")
        return ner4_cfg

    def process_file(self, path: str | Path) -> PipelineResult:
        file_path = Path(path)
        raw_text = file_path.read_text(encoding=str(self.raw_config.get("encoding", "utf-8")))
        return self.process_text(raw_text, file_id=file_path.stem)


def _resolve_patterns_path(config_path: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return config_path.parent / path


def _load_assertion_rules(config_path: Path, assertion_cfg: dict[str, Any]) -> dict[str, list[str]]:
    rules_value = assertion_cfg.get("rules_config")
    if not rules_value:
        return {}
    rules_path = _resolve_patterns_path(config_path, str(rules_value))
    return load_assertion_rules(rules_path)


def _disable_sparse_linker_retrieval(raw_config: dict[str, Any]) -> None:
    for section_name in ("icd10_linking", "rxnorm_linking"):
        section = raw_config.get(section_name, {})
        if not isinstance(section, dict):
            continue
        retrieval = section.setdefault("retrieval", {})
        if not isinstance(retrieval, dict):
            retrieval = {}
            section["retrieval"] = retrieval
        retrieval["top_k_tfidf"] = 0
        retrieval["top_k_bm25"] = 0