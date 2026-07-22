from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from src.data_types import FinalEntity
from src.postprocess.cleanup import cleanup_candidates_assertions, should_drop_entity, trim_entity
from src.postprocess.merge import merge_exact_duplicates, resolve_different_type_overlaps, resolve_same_type_overlaps
from src.postprocess.models import PostprocessDecision, PostprocessReport, PostprocessResult
from src.postprocess.policies import type_priority
from src.postprocess.span_utils import entity_payload, validate_entity_offset


class Postprocessor:
    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config or {})
        self.validation_config = self.config.get("validation", {}) if isinstance(self.config.get("validation"), dict) else {}

    def process(self, entities: list[FinalEntity], raw_text: str) -> PostprocessResult:
        report = PostprocessReport(input_count=len(entities))
        working = self._validate_initial(entities, raw_text, report)
        working, decisions = merge_exact_duplicates(working, raw_text, self.config)
        report.exact_duplicates_removed += sum(len(decision.removed) for decision in decisions)
        report.decisions.extend(decisions)

        working = self._trim_and_drop(working, raw_text, report)
        working, decisions = resolve_same_type_overlaps(working, raw_text, self.config)
        report.same_type_overlaps_resolved += len(decisions)
        report.decisions.extend(decisions)

        working, decisions = resolve_different_type_overlaps(working, raw_text, self.config)
        report.different_type_overlaps_resolved += len(decisions)
        report.decisions.extend(decisions)

        working = self._cleanup_candidates_assertions(working, report)
        working = sorted(working, key=lambda item: (item.start, item.end, type_priority(str(item.type), self.config), item.text))
        self._validate_final(working, raw_text, report)
        report.output_count = len(working)
        return PostprocessResult(working, report)

    def process_finalized_ner(self, entities: list[FinalEntity], raw_text: str) -> PostprocessResult:
        """Validate and clean fields without changing finalized NER spans/types.

        NER-4 freezes boundary and type before assertion/linking.  The legacy
        postprocessor remains unchanged for V1/NER-2/NER-3, while this path
        deliberately skips trimming, dropping, and overlap resolution.
        """

        report = PostprocessReport(input_count=len(entities))
        working = self._validate_initial(entities, raw_text, report)
        keys = [(entity.start, entity.end, str(entity.type)) for entity in working]
        duplicate_count = len(keys) - len(set(keys))
        if duplicate_count:
            raise ValueError(f"Finalized NER contains {duplicate_count} exact duplicates")
        frozen = [(entity.start, entity.end, entity.text, str(entity.type)) for entity in working]
        working = self._cleanup_candidates_assertions(working, report)
        working = sorted(working, key=lambda item: (item.start, item.end, type_priority(str(item.type), self.config), item.text))
        after = [(entity.start, entity.end, entity.text, str(entity.type)) for entity in working]
        if sorted(frozen) != sorted(after):
            raise ValueError("Finalized NER span/type changed during postprocess")
        self._validate_final(working, raw_text, report)
        report.output_count = len(working)
        return PostprocessResult(working, report)

    def _validate_initial(self, entities: list[FinalEntity], raw_text: str, report: PostprocessReport) -> list[FinalEntity]:
        output: list[FinalEntity] = []
        keep_invalid = bool(self.validation_config.get("keep_invalid_entities", False))
        for entity in entities:
            error = validate_entity_offset(entity, raw_text)
            if error:
                report.offset_errors.append(error)
                if keep_invalid:
                    output.append(entity)
                continue
            output.append(entity)
        if report.offset_errors and bool(self.validation_config.get("fail_on_offset_error", True)):
            raise ValueError(f"Initial postprocess offset errors: {report.offset_errors[:3]}")
        return output

    def _trim_and_drop(self, entities: list[FinalEntity], raw_text: str, report: PostprocessReport) -> list[FinalEntity]:
        output: list[FinalEntity] = []
        for entity in entities:
            trimmed, decision = trim_entity(entity, raw_text, self.config)
            if decision:
                report.entities_trimmed += 1
                report.decisions.append(decision)
            should_drop, reason = should_drop_entity(trimmed, raw_text, self.config)
            if should_drop:
                report.entities_dropped += 1
                report.decisions.append(PostprocessDecision(action="drop_entity", reason=reason, removed=[entity_payload(trimmed)]))
                continue
            output.append(trimmed)
        return output

    def _cleanup_candidates_assertions(self, entities: list[FinalEntity], report: PostprocessReport) -> list[FinalEntity]:
        output: list[FinalEntity] = []
        for entity in entities:
            updated, candidate_changed, assertion_changed = cleanup_candidates_assertions(entity, self.config)
            if candidate_changed:
                report.candidate_cleanups += 1
            if assertion_changed:
                report.assertion_cleanups += 1
            if candidate_changed or assertion_changed:
                provenance = dict(updated.provenance)
                postprocess = dict(provenance.get("postprocess", {}))
                postprocess["cleanup_candidates_assertions"] = True
                provenance["postprocess"] = postprocess
                updated = replace(updated, provenance=provenance)
            output.append(updated)
        return output

    def _validate_final(self, entities: list[FinalEntity], raw_text: str, report: PostprocessReport) -> None:
        final_errors = [error for entity in entities if (error := validate_entity_offset(entity, raw_text))]
        report.offset_errors.extend(final_errors)
        if final_errors and bool(self.validation_config.get("fail_on_offset_error", True)):
            raise ValueError(f"Final postprocess offset errors: {final_errors[:3]}")
