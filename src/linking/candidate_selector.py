from __future__ import annotations

from dataclasses import dataclass

from src.data_types import MappingCandidate


@dataclass(slots=True)
class CandidateSelectionConfig:
    max_candidates: int = 3
    min_score_top1: float = 0.55
    include_second_if_within: float = 0.05
    min_score_additional: float = 0.70

    @classmethod
    def from_dict(cls, config: dict | None) -> "CandidateSelectionConfig":
        cfg = config or {}
        defaults = cls()
        return cls(
            max_candidates=int(cfg.get("max_candidates", defaults.max_candidates)),
            min_score_top1=float(cfg.get("min_score_top1", defaults.min_score_top1)),
            include_second_if_within=float(cfg.get("include_second_if_within", defaults.include_second_if_within)),
            min_score_additional=float(cfg.get("min_score_additional", defaults.min_score_additional)),
        )


@dataclass(slots=True)
class CandidateSelectionResult:
    chosen_codes: list[str]
    chosen_candidates: list[MappingCandidate]
    rejected_candidates: list[MappingCandidate]
    reason: str


def select_candidates(
    candidates: list[MappingCandidate],
    config: CandidateSelectionConfig | None = None,
) -> CandidateSelectionResult:
    """Select conservative final terminology codes from ranked candidates.

    Phase 7/8 linking should prefer precision over arbitrary top-k output. This
    selector keeps top-1 only when it clears a confidence threshold and adds more
    codes only when they are near-ties or independently high confidence.
    """

    cfg = config or CandidateSelectionConfig()
    ranked = _deduplicate_by_code(candidates)
    if not ranked:
        return CandidateSelectionResult([], [], [], "no_candidates")

    top = ranked[0]
    if top.final_score < cfg.min_score_top1:
        return CandidateSelectionResult([], [], ranked, "top1_below_threshold")

    chosen = [top]
    rejected: list[MappingCandidate] = []
    top_score = top.final_score

    for candidate in ranked[1:]:
        if len(chosen) >= cfg.max_candidates:
            rejected.append(candidate)
            continue
        score_delta = top_score - candidate.final_score
        is_near_tie = score_delta <= cfg.include_second_if_within
        is_high_confidence = candidate.final_score >= cfg.min_score_additional
        if is_near_tie or is_high_confidence:
            chosen.append(candidate)
        else:
            rejected.append(candidate)

    return CandidateSelectionResult(
        chosen_codes=[candidate.code for candidate in chosen],
        chosen_candidates=chosen,
        rejected_candidates=rejected,
        reason="selected",
    )


def _deduplicate_by_code(candidates: list[MappingCandidate]) -> list[MappingCandidate]:
    best_by_code: dict[str, MappingCandidate] = {}
    for candidate in candidates:
        code = str(candidate.code).strip()
        if not code:
            continue
        current = best_by_code.get(code)
        if current is None or candidate.final_score > current.final_score:
            best_by_code[code] = candidate
    return sorted(best_by_code.values(), key=lambda item: item.final_score, reverse=True)