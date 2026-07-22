from __future__ import annotations

from src.assertion import AssertionDetector
from src.data_types import FinalEntity, SpanCandidate
from src.postprocess import PostprocessReport, Postprocessor
from src.pipeline import ClinicalIEPipeline


def test_trimmed_negation_context_is_still_detected_before_linking() -> None:
    raw = "Không đau ngực."
    start = raw.index("đau ngực")
    entity = FinalEntity(
        text="đau ngực", start=start, end=start + len("đau ngực"), type="TRIỆU_CHỨNG",
        provenance={"ner4_boundary": {"removed_prefix": "Không "}},
    )
    asserted = AssertionDetector().apply([entity], raw)[0]
    assert asserted.assertions == ["isNegated"]
    assert raw[asserted.start:asserted.end] == asserted.text


def test_finalized_postprocess_preserves_boundary_and_type() -> None:
    raw = "Không đau ngực."
    start = raw.index("đau ngực")
    entity = FinalEntity(
        text="đau ngực", start=start, end=start + len("đau ngực"), type="TRIỆU_CHỨNG",
        assertions=["isNegated"], provenance={"chosen_source": "gliner"},
    )
    result = Postprocessor().process_finalized_ner([entity], raw)
    assert [(item.start, item.end, item.text, item.type) for item in result.entities] == [
        (start, start + len("đau ngực"), "đau ngực", "TRIỆU_CHỨNG")
    ]


def test_finalized_postprocess_rejects_exact_duplicates() -> None:
    raw = "sốt"
    entities = [
        FinalEntity(raw, 0, 3, "TRIỆU_CHỨNG"),
        FinalEntity(raw, 0, 3, "TRIỆU_CHỨNG"),
    ]
    try:
        Postprocessor().process_finalized_ner(entities, raw)
    except ValueError as exc:
        assert "exact duplicates" in str(exc)
    else:
        raise AssertionError("Expected duplicate finalized NER to fail")


def test_pipeline_feature_flag_routes_to_ner4(monkeypatch) -> None:
    raw = "sốt"
    expected = [FinalEntity(raw, 0, 3, "TRIỆU_CHỨNG")]
    called = []

    class Trace:
        entities = expected

    def fake_resolver(*args, **kwargs):
        called.append(kwargs.get("mode"))
        return Trace()

    monkeypatch.setattr("src.pipeline.resolve_ner4_trace", fake_resolver)
    pipeline = ClinicalIEPipeline.__new__(ClinicalIEPipeline)
    pipeline.raw_config = {
        "ner4": {"enabled": True, "mode": "deterministic"},
        "type_resolution": {},
    }
    entities = pipeline.resolve_candidates(
        raw, [SpanCandidate(raw, 0, 3, "TRIỆU_CHỨNG", "gliner", .8)], mode="simple_fusion",
    )
    assert entities == expected
    assert called == ["G"]


def test_ner4_end_to_end_uses_finalized_postprocess(monkeypatch) -> None:
    raw = "sốt"
    entity = FinalEntity(raw, 0, 3, "TRIỆU_CHỨNG")
    pipeline = ClinicalIEPipeline.__new__(ClinicalIEPipeline)
    pipeline.raw_config = {"ner4": {"enabled": True, "mode": "deterministic"}}
    pipeline.ner_only = False
    pipeline.assertion_detector = type("Assertion", (), {"apply": lambda self, rows, text: rows})()
    pipeline.icd_linker = type("ICD", (), {"link_entities": lambda self, rows, raw_text: rows})()
    pipeline.rx_linker = type("RX", (), {"link_entities": lambda self, rows, raw_text: rows})()
    calls = []

    class Processor:
        def process(self, rows, raw_text):
            raise AssertionError("legacy postprocess must not run for NER-4")

        def process_finalized_ner(self, rows, raw_text):
            calls.append("finalized")
            return type("Result", (), {
                "entities": rows,
                "report": PostprocessReport(input_count=len(rows), output_count=len(rows)),
            })()

    pipeline.postprocessor = Processor()
    pipeline.formatter = type("Formatter", (), {"format_entities": lambda self, rows: []})()
    monkeypatch.setattr(pipeline, "_extract_and_resolve", lambda text: ([entity], [], []))
    result = pipeline.process_text(raw)
    assert calls == ["finalized"]
    assert result.entities == [entity]