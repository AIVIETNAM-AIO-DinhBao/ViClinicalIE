from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from src.data_types import SpanCandidate
from src.extractors.base import BaseExtractor, ExtractionContext
from src.extractors.utils import dedupe_candidates, find_phrase_matches, make_span_candidate, span_overlaps


_VALUE_RE = re.compile(
    r"""
    (?P<value>
      âm\s+tính|dương\s+tính|bình\s+thường|tăng\s+nhẹ|giảm\s+nhẹ|tăng|giảm|cao|thấp|
      \d+(?:[\.,]\d+)?(?:\s*(?:->|→|đến|to|-)\s*\d+(?:[\.,]\d+)?)?
    )
    (?P<unit>\s*(?:mg/dl|mmol/l|g/l|g/dl|%|u/l|ng/ml|mmhg|meq/l|mmol|mg|ml))?
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)

_QUALITATIVE_RESULT_RE = re.compile(
    r"""
    \s*(?::|=|là|la|is)?\s*
    (?P<result>
      không\s+(?:ghi\s+nhận|có|thấy|phát\s+hiện)(?:\s+gì)?(?:\s+đáng\s+chú\s+ý|\s+bất\s+thường)?|
      không\s+bất\s+thường|
      bình\s+thường|âm\s+tính|dương\s+tính|
      cho\s+thấy\s+[^\n.;]+|
      gợi\s+ý\s+[^\n.;]+
    )
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


class LabExtractor(BaseExtractor):
    name = "lab_rule"

    def __init__(
        self,
        *,
        lab_tests_path: str | Path | None = None,
        lab_rows: Sequence[Mapping[str, Any]] | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.max_result_lookahead_chars = int(self.config.get("max_result_lookahead_chars", 60))
        rows: list[dict[str, Any]] = []
        if lab_tests_path and Path(lab_tests_path).exists():
            rows.extend(pd.read_csv(lab_tests_path, dtype=str, keep_default_na=False).to_dict("records"))
        rows.extend(dict(row) for row in lab_rows or [])
        self.lab_rows = sorted(rows, key=lambda row: (-len(str(row.get("alias", ""))), str(row.get("alias", "")).lower()))

    def extract(self, context: ExtractionContext) -> list[SpanCandidate]:
        candidates: list[SpanCandidate] = []
        pair_index = 0
        emitted_test_spans: list[tuple[int, int]] = []
        for chunk in context.chunks:
            for row in self.lab_rows:
                alias = str(row.get("alias", "")).strip()
                if not alias:
                    continue
                min_length = 1 if len(alias) <= 2 else 2
                for start, end in find_phrase_matches(
                    context.raw_text,
                    context.views,
                    alias,
                    chunk=chunk,
                    min_length=min_length,
                    require_boundaries=True,
                ):
                    if any(span_overlaps(start, end, prev_start, prev_end) for prev_start, prev_end in emitted_test_spans):
                        continue
                    pair_id = f"lab_pair_{pair_index}"
                    pair_index += 1
                    emitted_test_spans.append((start, end))
                    candidates.append(
                        make_span_candidate(
                            context.raw_text,
                            start,
                            end,
                            raw_type="TÊN_XÉT_NGHIỆM",
                            source=self.name,
                            score=0.82,
                            chunk=chunk,
                            features={"alias": alias, "canonical": row.get("canonical", ""), "pair_id": pair_id},
                        )
                    )
                    result_span = self._find_result_span(context.raw_text, end, chunk.end)
                    if result_span is not None:
                        result_start, result_end = result_span
                        candidates.append(
                            make_span_candidate(
                                context.raw_text,
                                result_start,
                                result_end,
                                raw_type="KẾT_QUẢ_XÉT_NGHIỆM",
                                source="lab_result_rule",
                                score=0.82,
                                chunk=chunk,
                                features={"canonical_test": row.get("canonical", ""), "pair_id": pair_id},
                            )
                        )
                    full_result_span = self._find_full_qualitative_result_span(context.raw_text, start, end, chunk.end)
                    if full_result_span is not None:
                        result_start, result_end = full_result_span
                        candidates.append(
                            make_span_candidate(
                                context.raw_text,
                                result_start,
                                result_end,
                                raw_type="KẾT_QUẢ_XÉT_NGHIỆM",
                                source="lab_result_rule",
                                score=0.86,
                                chunk=chunk,
                                features={"canonical_test": row.get("canonical", ""), "pair_id": pair_id, "result_scope": "test_plus_qualitative"},
                            )
                        )
        return dedupe_candidates(candidates)

    def _find_result_span(self, raw_text: str, test_end: int, chunk_end: int) -> tuple[int, int] | None:
        lookahead_end = min(chunk_end, test_end + self.max_result_lookahead_chars)
        segment = raw_text[test_end:lookahead_end]
        separator = re.match(r"\s*(?::|=|là|la|is|là\s*)?\s*", segment, flags=re.IGNORECASE)
        offset = separator.end() if separator else 0
        match = _VALUE_RE.match(segment[offset:])
        if not match:
            return None
        start = test_end + offset + match.start("value")
        end = test_end + offset + match.end("value")
        if match.group("unit"):
            end = test_end + offset + match.end("unit")
        return start, end

    def _find_full_qualitative_result_span(self, raw_text: str, test_start: int, test_end: int, chunk_end: int) -> tuple[int, int] | None:
        lookahead_end = min(chunk_end, test_end + self.max_result_lookahead_chars)
        segment = raw_text[test_end:lookahead_end]
        match = _QUALITATIVE_RESULT_RE.match(segment)
        if not match:
            return None
        end = test_end + match.end("result")
        while end > test_start and raw_text[end - 1] in " ,;:.\n\t\r":
            end -= 1
        return (test_start, end) if end > test_start else None
