from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from src.data_types import SpanCandidate


def parse_threshold_profile(value: Any, *, fallback: float = 0.35) -> tuple[float, dict[str, float]]:
    if isinstance(value, Mapping):
        default = float(value.get("default", fallback))
        per_type = {str(key): float(threshold) for key, threshold in value.items() if key != "default"}
    else:
        default = float(value)
        per_type = {}
    for threshold in [default, *per_type.values()]:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("GLiNER thresholds must be within [0, 1]")
    return default, per_type


def filter_proposals(
    proposals: list[SpanCandidate],
    *,
    default_threshold: float,
    threshold_by_type: Mapping[str, float] | None = None,
) -> list[SpanCandidate]:
    per_type = dict(threshold_by_type or {})
    return [candidate for candidate in proposals if candidate.score >= per_type.get(str(candidate.raw_type), default_threshold)]


def merge_exact_proposals(proposals: list[SpanCandidate]) -> list[SpanCandidate]:
    """Merge only identical span/type proposals while retaining every evidence row."""

    grouped: dict[tuple[int, int, str | None], list[SpanCandidate]] = defaultdict(list)
    for candidate in proposals:
        grouped[(candidate.start, candidate.end, candidate.raw_type)].append(candidate)
    output: list[SpanCandidate] = []
    for key in sorted(grouped, key=lambda item: (item[0], item[1], item[2] or "")):
        group = grouped[key]
        winner = max(group, key=lambda candidate: (candidate.score, str(candidate.features.get("pass_name", "")), str(candidate.features.get("window_id", ""))))
        features = dict(winner.features)
        evidence = [
            {
                "pass_name": item.features.get("pass_name"),
                "prompt_label": item.features.get("prompt_label"),
                "raw_model_score": item.score,
                "selection_threshold": item.features.get("selection_threshold"),
                "window_id": item.features.get("window_id"),
                "window_start": item.features.get("window_start"),
                "window_end": item.features.get("window_end"),
            }
            for item in sorted(group, key=lambda candidate: (str(candidate.features.get("pass_name", "")), str(candidate.features.get("window_id", "")), -candidate.score))
        ]
        features["proposal_evidence"] = evidence
        features["supporting_windows"] = sorted({str(item.features.get("window_id")) for item in group})
        features["supporting_passes"] = sorted({str(item.features.get("pass_name")) for item in group})
        features["agreement_count"] = len(group)
        winner.features = features
        output.append(winner)
    return output