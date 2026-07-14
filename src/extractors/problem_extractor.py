from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from src.data_types import SpanCandidate
from src.extractors.base import BaseExtractor, ExtractionContext
from src.extractors.utils import dedupe_candidates, make_span_candidate, trim_trailing_punctuation
from src.linking.terminology_normalizer import normalize_for_lookup


_STOP_WORDS = r"và|hoặc|nhưng|tuy\s+nhiên|song|được|đã|đang|không|chưa|cho|thấy|ghi\s+nhận|gợi\s+ý|lo\s+ngại|theo|kể\s+từ|sau\s+khi|trong\s+khoảng|vào"
_TAIL = rf"(?:\s+(?!(?:{_STOP_WORDS})\b)[\wÀ-ỹ%/.-]+){{0,8}}"

_SYMPTOM_PATTERNS = [
    rf"\bkhó\s+thở{_TAIL}",
    rf"\bđau{_TAIL}",
    rf"\bđánh\s+trống\s+ngực{_TAIL}",
    rf"\bcảm\s+giác\s+(?:đánh\s+trống\s+ngực|thắt\s+chặt\s+ngực|khó\s+chịu\s+vùng\s+ngực){_TAIL}",
    rf"\bý\s+thức\s+suy\s+giảm{_TAIL}",
    rf"\b(?:ho|sốt|buồn\s+nôn|nôn|tiêu\s+chảy|táo\s+bón|chóng\s+mặt|mệt\s+mỏi|yếu|ngất|phù|sưng|chảy\s+máu|khó\s+nuốt|khò\s+khè|lo\s+âu|mất\s+ngủ|ảo\s+giác|lú\s+lẫn|nhìn\s+mờ){_TAIL}",
]
_DISEASE_PATTERNS = [
    rf"\brung\s+nhĩ{_TAIL}",
    rf"\bxơ\s+gan{_TAIL}",
    rf"\bphình\s+động\s+mạch{_TAIL}",
    rf"\b(?:viêm|ung\s+thư|u\s+ác|u\s+tuyến|suy|nhồi\s+máu|thuyên\s+tắc|xuất\s+huyết|hẹp|tắc|bóc\s+tách|gãy|áp\s+xe|nhiễm\s+khuẩn|nhiễm\s+trùng|bệnh|hội\s+chứng|loét|tràn\s+dịch){_TAIL}",
]

_GENERIC_PROBLEM_PHRASES = {
    "bệnh",
    "bệnh nhân",
    "người bệnh",
    "bệnh hiện tại",
    "yếu tố nguy cơ",
    "yếu tố nguy cơ liên quan",
    "tình trạng",
    "triệu chứng",
    "các triệu chứng hiện tại",
}

_BAD_PROBLEM_PREFIXES = (
    "bệnh nhân ",
    "người bệnh ",
    "theo lời bệnh nhân ",
    "các triệu chứng ",
    "triệu chứng hiện tại",
)


class ProblemExtractor(BaseExtractor):
    name = "problem_rule"

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config or {})
        self.symptom_regexes = [re.compile(pattern, flags=re.IGNORECASE) for pattern in _SYMPTOM_PATTERNS]
        self.disease_regexes = [re.compile(pattern, flags=re.IGNORECASE) for pattern in _DISEASE_PATTERNS]

    def extract(self, context: ExtractionContext) -> list[SpanCandidate]:
        candidates: list[SpanCandidate] = []
        for chunk in context.chunks:
            for regex in self.symptom_regexes:
                candidates.extend(self._extract_with_regex(context, chunk, regex, "TRIỆU_CHỨNG", "symptom_head"))
            for regex in self.disease_regexes:
                candidates.extend(self._extract_with_regex(context, chunk, regex, "CHẨN_ĐOÁN", "disease_head"))
        return dedupe_candidates(candidates)

    def _extract_with_regex(self, context: ExtractionContext, chunk, regex: re.Pattern[str], raw_type: str, rule: str) -> list[SpanCandidate]:
        output: list[SpanCandidate] = []
        for match in regex.finditer(chunk.text):
            start = chunk.start + match.start()
            end = chunk.start + match.end()
            start, end = trim_trailing_punctuation(context.raw_text, start, end)
            if end <= start:
                continue
            if self._should_skip_span(context.raw_text[start:end]):
                continue
            output.append(
                make_span_candidate(
                    context.raw_text,
                    start,
                    end,
                    raw_type=raw_type,
                    source=self.name,
                    score=0.76,
                    chunk=chunk,
                    features={"rule": rule},
                )
            )
        return output

    def _should_skip_span(self, text: str) -> bool:
        text_norm = normalize_for_lookup(text)
        if text_norm in _GENERIC_PROBLEM_PHRASES:
            return True
        if any(text_norm.startswith(prefix) for prefix in _BAD_PROBLEM_PREFIXES):
            return True
        token_count = len(text_norm.split())
        if text_norm.startswith("bệnh ") and token_count <= 2:
            return True
        return False
