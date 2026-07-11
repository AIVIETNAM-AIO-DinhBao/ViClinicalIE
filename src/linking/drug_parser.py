from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from src.linking.rxnorm_index import parse_strength
from src.linking.terminology_normalizer import normalize_for_lookup, normalize_whitespace


ROUTE_RE = re.compile(r"\b(?:po|iv|im|sc|oral|uống|tiêm|truyền|nebs?|nebulizer)\b", re.IGNORECASE)
FREQUENCY_RE = re.compile(r"\b(?:daily|bid|tid|qid|q\d+h|prn|qam|qhs|x\s*\d+|ngày|lần/ngày)\b|/ngày", re.IGNORECASE)
DOSE_FORM_RE = re.compile(
    r"\b(?:tablet|tab|capsule|cap|solution|suspension|cream|gel|spray|patch|ointment|nebs?|nebulizer)\b",
    re.IGNORECASE,
)
STRENGTH_TOKEN_RE = re.compile(
    r"\b\d+(?:[\.,]\d+)?(?:\s*-\s*\d+(?:[\.,]\d+)?)?\s*(?:mg/ml|mg(?:\s*/\s*(?:\d+(?:[\.,]\d+)?)?\s*ml)?|mcg|g|gm|gram|ml|unt|units?|đơn\s*vị|%)\b",
    re.IGNORECASE,
)
NOISE_TOKEN_RE = re.compile(
    r"\b(?:dose|doses|liều|viên|ống|chai|lọ|lần|at\s+a\s+time|taking)\b",
    re.IGNORECASE,
)
COMBINATION_RE = re.compile(r"\s*(?:/|\+|\band\b|\bvà\b|\bwith\b)\s*", re.IGNORECASE)


@dataclass(slots=True)
class ParsedDrug:
    raw: str
    ingredient_or_brand: str | None
    normalized_name: str | None
    strength_value: float | None
    strength_unit: str | None
    route: str | None
    frequency: str | None
    dose_form: str | None
    is_combination: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_drug_mention(text: str, config: dict[str, Any] | None = None) -> ParsedDrug:
    cfg = config or {}
    raw = normalize_whitespace(text)
    strength_value: float | None = None
    strength_unit: str | None = None
    if bool(cfg.get("parse_strength", True)):
        strength_value, strength_unit = parse_strength(_normalize_gram_units(raw))

    route = _first_match(ROUTE_RE, raw) if bool(cfg.get("parse_route", True)) else None
    frequency = _first_match(FREQUENCY_RE, raw) if bool(cfg.get("parse_frequency", True)) else None
    dose_form = _first_match(DOSE_FORM_RE, raw) if bool(cfg.get("parse_dose_form", True)) else None
    raw_has_combination_marker = bool(COMBINATION_RE.search(raw))
    normalized_name = _extract_name(raw)
    is_combination = raw_has_combination_marker or bool(normalized_name and COMBINATION_RE.search(normalized_name))
    ingredient_or_brand = normalized_name or None
    return ParsedDrug(
        raw=raw,
        ingredient_or_brand=ingredient_or_brand,
        normalized_name=normalize_for_lookup(normalized_name) if normalized_name else None,
        strength_value=strength_value,
        strength_unit=strength_unit,
        route=route.lower() if route else None,
        frequency=frequency.lower() if frequency else None,
        dose_form=dose_form.lower() if dose_form else None,
        is_combination=is_combination,
    )


def _extract_name(text: str) -> str:
    value = _normalize_gram_units(text)
    value = COMBINATION_RE.sub(" ", value)
    value = STRENGTH_TOKEN_RE.sub(" ", value)
    value = ROUTE_RE.sub(" ", value)
    value = FREQUENCY_RE.sub(" ", value)
    value = DOSE_FORM_RE.sub(" ", value)
    value = NOISE_TOKEN_RE.sub(" ", value)
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" -,:;./")
    tokens = []
    for token in value.split():
        cleaned = token.strip("-,:;()[]{}<>\"'")
        if not cleaned:
            continue
        if cleaned.replace(".", "", 1).isdigit():
            continue
        tokens.append(cleaned)
    return normalize_whitespace(" ".join(tokens))


def _normalize_gram_units(text: str) -> str:
    return re.sub(r"\b(\d+(?:[\.,]\d+)?)\s*gram\b", r"\1 G", text, flags=re.IGNORECASE)


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(0).strip() if match else None