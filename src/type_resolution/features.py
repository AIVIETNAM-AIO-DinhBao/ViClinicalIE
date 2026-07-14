from __future__ import annotations

from dataclasses import dataclass

from src.data_types import SpanCandidate
from src.linking.terminology_normalizer import normalize_for_lookup


SYMPTOM_HEADS: tuple[str, ...] = (
    "đau",
    "khó thở",
    "đánh trống ngực",
    "cảm giác đánh trống ngực",
    "cảm giác thắt chặt ngực",
    "ý thức suy giảm",
    "ho",
    "sốt",
    "buồn nôn",
    "nôn",
    "tiêu chảy",
    "táo bón",
    "chóng mặt",
    "mệt mỏi",
    "yếu",
    "ngất",
    "phù",
    "sưng",
    "chảy máu",
    "khó nuốt",
    "khò khè",
    "lo âu",
    "mất ngủ",
    "ảo giác",
    "lú lẫn",
    "nhìn mờ",
)

DISEASE_HEADS: tuple[str, ...] = (
    "viêm",
    "ung thư",
    "u ác",
    "u tuyến",
    "suy",
    "xơ gan",
    "rung nhĩ",
    "nhồi máu",
    "thuyên tắc",
    "xuất huyết",
    "hẹp",
    "tắc",
    "bóc tách",
    "gãy",
    "áp xe",
    "nhiễm khuẩn",
    "nhiễm trùng",
    "bệnh",
    "hội chứng",
    "loét",
    "tràn dịch",
    "phình động mạch",
)

DRUG_CONTEXT_CUES: tuple[str, ...] = (
    "thuốc",
    "uống",
    "dùng",
    "sử dụng",
    "điều trị",
    "liều",
    "tiêm",
    "truyền",
    "po",
    "iv",
    "bid",
    "tid",
    "qid",
    "prn",
    "mg",
    "mcg",
    "ml",
)


@dataclass(slots=True)
class TypeFeatures:
    candidate_source: str
    raw_type: str | None
    section: str | None
    score: float
    has_lab_evidence: bool
    has_lab_result_evidence: bool
    has_imaging_evidence: bool
    has_drug_evidence: bool
    has_drug_context: bool
    has_symptom_head: bool
    has_disease_head: bool
    has_dictionary_symptom: bool
    ner_label: str | None = None
    icd_linkability_score: float = 0.0
    rxnorm_linkability_score: float = 0.0


def build_type_features(candidate: SpanCandidate) -> TypeFeatures:
    candidate_features = candidate.features or {}
    raw_type = candidate.raw_type
    source = candidate.source

    return TypeFeatures(
        candidate_source=source,
        raw_type=raw_type,
        section=candidate.section,
        score=candidate.score,
        has_lab_evidence=source == "lab_rule",
        has_lab_result_evidence=source == "lab_result_rule",
        has_imaging_evidence=source == "imaging_rule",
        has_drug_evidence=source == "drug_rule",
        has_drug_context=has_drug_context(candidate),
        has_symptom_head=has_symptom_head(candidate.text, candidate_features),
        has_disease_head=has_disease_head(candidate.text, candidate_features),
        has_dictionary_symptom=source == "dictionary" and raw_type == "TRIỆU_CHỨNG",
        ner_label=_non_empty_string(candidate_features.get("ner_label")),
    )


def has_drug_context(candidate: SpanCandidate) -> bool:
    candidate_features = candidate.features or {}
    if candidate_features.get("strength") or candidate_features.get("route") or candidate_features.get("frequency"):
        return True
    context = normalize_for_lookup(f"{candidate.context_left[-80:]} {candidate.text} {candidate.context_right[:80]}")
    return any(cue in context for cue in DRUG_CONTEXT_CUES)


def has_symptom_head(text: str, features: dict) -> bool:
    rule = str(features.get("rule", ""))
    if rule == "symptom_head":
        return True
    return starts_with_any(normalize_for_lookup(text), SYMPTOM_HEADS)


def has_disease_head(text: str, features: dict) -> bool:
    text_norm = normalize_for_lookup(text)
    if text_norm in {"bệnh", "bệnh nhân", "người bệnh", "bệnh hiện tại", "tình trạng"}:
        return False
    if text_norm.startswith("bệnh nhân ") or text_norm.startswith("người bệnh "):
        return False
    rule = str(features.get("rule", ""))
    if rule == "disease_head":
        return True
    return starts_with_any(text_norm, DISEASE_HEADS)


def starts_with_any(text_norm: str, heads: tuple[str, ...]) -> bool:
    return any(text_norm == head or text_norm.startswith(f"{head} ") for head in heads)


def _non_empty_string(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
