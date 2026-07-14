from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from src.data_types import SpanCandidate
from src.extractors.base import BaseExtractor, ExtractionContext
from src.extractors.utils import dedupe_candidates, make_span_candidate, trim_trailing_punctuation


_IMAGING_RE = re.compile(
    r"""
    (?P<test>
      (?:chụp\s+)?(?:x\s*-?\s*quang|ct|mri)
      |cộng\s+hưởng\s+từ
      |siêu\s+âm
      |điện\s+tâm\s+đồ
      |ecg|ekg
      |monitor\s+holter
      |xạ\s+hình
    )
    (?P<tail>(?:\s+(?!(?:bình\s+thường|không|cho|thấy|ghi\s+nhận|âm\s+tính|dương\s+tính|gợi\s+ý|hôm\s+nay|được|vào|có|hình\s+ảnh)\b)[\wÀ-ỹ%/.-]+){0,6})
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)

_IMAGING_RESULT_RE = re.compile(
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


class ImagingExtractor(BaseExtractor):
    name = "imaging_rule"

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def extract(self, context: ExtractionContext) -> list[SpanCandidate]:
        candidates: list[SpanCandidate] = []
        for chunk in context.chunks:
            for match in _IMAGING_RE.finditer(chunk.text):
                start = chunk.start + match.start()
                end = chunk.start + match.end()
                start, end = trim_trailing_punctuation(context.raw_text, start, end)
                candidates.append(
                    make_span_candidate(
                        context.raw_text,
                        start,
                        end,
                        raw_type="TÊN_XÉT_NGHIỆM",
                        source=self.name,
                        score=0.78,
                        chunk=chunk,
                        features={"pattern": "imaging_test"},
                    )
                )
                result_span = _find_imaging_result_span(context.raw_text, start, end, chunk.end)
                if result_span is not None:
                    result_start, result_end = result_span
                    candidates.append(
                        make_span_candidate(
                            context.raw_text,
                            result_start,
                            result_end,
                            raw_type="KẾT_QUẢ_XÉT_NGHIỆM",
                            source="lab_result_rule",
                            score=0.86,
                            chunk=chunk,
                            features={"pattern": "imaging_test_plus_result"},
                        )
                    )
        return dedupe_candidates(candidates)


def _find_imaging_result_span(raw_text: str, test_start: int, test_end: int, chunk_end: int) -> tuple[int, int] | None:
    lookahead_end = min(chunk_end, test_end + 80)
    segment = raw_text[test_end:lookahead_end]
    match = _IMAGING_RESULT_RE.match(segment)
    if not match:
        return None
    end = test_end + match.end("result")
    while end > test_start and raw_text[end - 1] in " ,;:.\n\t\r":
        end -= 1
    return (test_start, end) if end > test_start else None
