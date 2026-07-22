from __future__ import annotations

import pytest

from src.data_types import SpanCandidate
from src.ner.thresholding import filter_proposals, merge_exact_proposals, parse_threshold_profile


def _proposal(score: float, pass_name: str, window_id: str) -> SpanCandidate:
    return SpanCandidate(
        text="sốt",
        start=0,
        end=3,
        raw_type="TRIỆU_CHỨNG",
        source="gliner",
        score=score,
        features={"pass_name": pass_name, "window_id": window_id, "prompt_label": "symptom"},
    )


def test_per_type_threshold_overrides_default() -> None:
    rows = [_proposal(0.3, "full", "w0"), _proposal(0.6, "full", "w1")]
    selected = filter_proposals(rows, default_threshold=0.2, threshold_by_type={"TRIỆU_CHỨNG": 0.5})
    assert [row.score for row in selected] == [0.6]


def test_exact_merge_retains_all_pass_and_window_evidence() -> None:
    rows = [_proposal(0.7, "full", "w0"), _proposal(0.8, "problem", "w1")]
    merged = merge_exact_proposals(rows)
    assert len(merged) == 1
    assert merged[0].score == 0.8
    assert merged[0].features["supporting_passes"] == ["full", "problem"]
    assert [row["window_id"] for row in merged[0].features["proposal_evidence"]] == ["w0", "w1"]


def test_threshold_profile_validates_range() -> None:
    assert parse_threshold_profile({"default": 0.4, "THUỐC": 0.5}) == (0.4, {"THUỐC": 0.5})
    with pytest.raises(ValueError):
        parse_threshold_profile({"default": 1.1})