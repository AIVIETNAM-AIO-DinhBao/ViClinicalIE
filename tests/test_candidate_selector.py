from __future__ import annotations

from src.data_types import MappingCandidate
from src.linking.candidate_selector import CandidateSelectionConfig, select_candidates


def _candidate(code: str, score: float) -> MappingCandidate:
    return MappingCandidate(code=code, name=code, terminology="ICD10", final_score=score, lexical_score=score)


def test_select_candidates_empty() -> None:
    result = select_candidates([])
    assert result.chosen_codes == []
    assert result.reason == "no_candidates"


def test_select_candidates_rejects_low_top1() -> None:
    result = select_candidates([_candidate("A00", 0.20)], CandidateSelectionConfig(min_score_top1=0.55))
    assert result.chosen_codes == []
    assert result.reason == "top1_below_threshold"


def test_select_candidates_keeps_top1_above_threshold() -> None:
    result = select_candidates([_candidate("A00", 0.80), _candidate("B00", 0.40)])
    assert result.chosen_codes == ["A00"]


def test_select_candidates_includes_near_tie() -> None:
    result = select_candidates(
        [_candidate("A00", 0.80), _candidate("B00", 0.77)],
        CandidateSelectionConfig(include_second_if_within=0.05),
    )
    assert result.chosen_codes == ["A00", "B00"]


def test_select_candidates_includes_high_confidence_additional() -> None:
    result = select_candidates(
        [_candidate("A00", 0.95), _candidate("B00", 0.72)],
        CandidateSelectionConfig(min_score_additional=0.70),
    )
    assert result.chosen_codes == ["A00", "B00"]


def test_select_candidates_deduplicates_and_respects_max() -> None:
    result = select_candidates(
        [_candidate("A00", 0.70), _candidate("A00", 0.90), _candidate("B00", 0.89), _candidate("C00", 0.88)],
        CandidateSelectionConfig(max_candidates=2, include_second_if_within=0.20),
    )
    assert result.chosen_codes == ["A00", "B00"]
