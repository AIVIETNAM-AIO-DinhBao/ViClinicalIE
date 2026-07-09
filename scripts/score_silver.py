"""Score ViClinicalIE predictions against a silver/gold output directory.

The ABOUT.md task schema defines one JSON array per input note. Each entity has:
  - text
  - position: [start, end]
  - type
  - assertions
  - candidates only for CHẨN_ĐOÁN and THUỐC

This scorer reports micro precision/recall/F1 for:
  1) span_type: exact (file_id, start, end, type)
  2) span_text_type: exact (file_id, text, start, end, type)
  3) full_entity: exact schema entity including assertions and candidates
  4) assertion labels on matched span_type entities
  5) mapping candidates on matched diagnosis/drug span_type entities

It also writes a Markdown report and optional JSON summary for debugging.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.assertion import ALLOWED_ASSERTIONS
from src.output_writer import MAPPING_TYPES
from src.rule_extractors import ENTITY_LAB_NAME, ENTITY_LAB_RESULT, TARGET_ENTITY_TYPES

LAB_TYPES = {ENTITY_LAB_NAME, ENTITY_LAB_RESULT}
ASSERTION_ORDER = ("isNegated", "isHistorical", "isFamily")


@dataclass
class PRF:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p = self.precision
        r = self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


@dataclass
class FileScore:
    file_id: str
    gold_entities: int
    pred_entities: int
    span_type: PRF
    full_entity: PRF


@dataclass
class OfficialMetrics:
    """Approximation of ABOUT.md section #6 scoring.

    text_score uses word-level WER on the assembled `text` field per sample
    (organizer confirmation: "WER(i) là WER của trường text trong sample i").
    assertions_score / candidates_score use concept identity (type + text) so a
    correct text with the wrong type is counted as a different concept, matching
    the note in ABOUT.md.
    """

    text_score: float
    assertions_score: float
    candidates_score: float
    final_score: float


@dataclass
class ScoreReport:
    file_ids: List[str]
    missing_pred_files: List[str]
    missing_gold_files: List[str]
    schema_errors: List[str]
    offset_errors: List[str]
    official: OfficialMetrics
    metrics: Dict[str, PRF]
    by_type: Dict[str, PRF]
    by_file: List[FileScore]
    top_false_positives: List[Dict[str, Any]]
    top_false_negatives: List[Dict[str, Any]]


def _numeric_sort_key(path_or_id: str | Path) -> Tuple[int, str]:
    stem = Path(path_or_id).stem if isinstance(path_or_id, Path) else str(path_or_id)
    return (int(stem), stem) if stem.isdigit() else (1 << 30, stem)


def _load_json_array(path: Path, errors: List[str], label: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"{label}: missing file {path}")
        return []
    except json.JSONDecodeError as exc:
        errors.append(f"{label}: invalid JSON in {path}: {exc}")
        return []
    if not isinstance(data, list):
        errors.append(f"{label}: {path} must contain a JSON array")
        return []
    output: List[Dict[str, Any]] = []
    for i, item in enumerate(data):
        if isinstance(item, dict):
            output.append(item)
        else:
            errors.append(f"{label}: {path.name}[{i}] is not an object")
    return output


def _normalize_assertions(value: Any) -> Tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    found = {item for item in value if isinstance(item, str) and item in ALLOWED_ASSERTIONS}
    return tuple(assertion for assertion in ASSERTION_ORDER if assertion in found)


def _normalize_candidates(value: Any) -> Tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(sorted({item for item in value if isinstance(item, str)}))


def _position(entity: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    pos = entity.get("position")
    if not isinstance(pos, list) or len(pos) != 2:
        return None
    start, end = pos
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    return start, end


def validate_entity(
    entity: Dict[str, Any],
    *,
    file_id: str,
    index: int,
    raw_text: Optional[str],
    label: str,
    schema_errors: List[str],
    offset_errors: List[str],
) -> bool:
    prefix = f"{label}:{file_id}.json[{index}]"
    ok = True
    required = {"text", "position", "type", "assertions"}
    missing = required - set(entity)
    if missing:
        schema_errors.append(f"{prefix}: missing fields {sorted(missing)}")
        ok = False
    if not isinstance(entity.get("text"), str) or not entity.get("text"):
        schema_errors.append(f"{prefix}: text must be a non-empty string")
        ok = False
    entity_type = entity.get("type")
    if entity_type not in TARGET_ENTITY_TYPES:
        schema_errors.append(f"{prefix}: invalid type {entity_type!r}")
        ok = False
    pos = _position(entity)
    if pos is None:
        schema_errors.append(f"{prefix}: position must be [int, int]")
        ok = False
    assertions = entity.get("assertions")
    if not isinstance(assertions, list):
        schema_errors.append(f"{prefix}: assertions must be a list")
        ok = False
    else:
        invalid_assertions = [item for item in assertions if item not in ALLOWED_ASSERTIONS]
        if invalid_assertions:
            schema_errors.append(f"{prefix}: invalid assertions {invalid_assertions}")
            ok = False
        if len(assertions) != len(set(assertions)):
            schema_errors.append(f"{prefix}: duplicate assertions")
            ok = False
        if entity_type in LAB_TYPES and assertions:
            schema_errors.append(f"{prefix}: lab entity must have empty assertions")
            ok = False
    if entity_type in MAPPING_TYPES:
        candidates = entity.get("candidates")
        if not isinstance(candidates, list) or not all(isinstance(item, str) for item in candidates):
            schema_errors.append(f"{prefix}: diagnosis/drug candidates must be a string list")
            ok = False
    elif "candidates" in entity:
        schema_errors.append(f"{prefix}: candidates only allowed for diagnosis/drug")
        ok = False
    if raw_text is not None and pos is not None and isinstance(entity.get("text"), str):
        start, end = pos
        if not (0 <= start < end <= len(raw_text)):
            offset_errors.append(f"{prefix}: invalid offset range {start}:{end}")
            ok = False
        elif raw_text[start:end] != entity["text"]:
            offset_errors.append(f"{prefix}: offset text mismatch at {start}:{end}")
            ok = False
    return ok


def span_type_key(file_id: str, entity: Dict[str, Any]) -> Tuple[Any, ...]:
    pos = _position(entity) or (-1, -1)
    return (file_id, pos[0], pos[1], entity.get("type"))


def span_text_type_key(file_id: str, entity: Dict[str, Any]) -> Tuple[Any, ...]:
    pos = _position(entity) or (-1, -1)
    return (file_id, entity.get("text"), pos[0], pos[1], entity.get("type"))


def full_entity_key(file_id: str, entity: Dict[str, Any]) -> Tuple[Any, ...]:
    pos = _position(entity) or (-1, -1)
    entity_type = entity.get("type")
    candidates = _normalize_candidates(entity.get("candidates")) if entity_type in MAPPING_TYPES else ()
    return (
        file_id,
        entity.get("text"),
        pos[0],
        pos[1],
        entity_type,
        _normalize_assertions(entity.get("assertions")),
        candidates,
    )


def _counter_for(entities_by_file: Dict[str, List[Dict[str, Any]]], key_fn: Callable[[str, Dict[str, Any]], Tuple[Any, ...]]) -> Counter:
    counter: Counter = Counter()
    for file_id, entities in entities_by_file.items():
        for entity in entities:
            counter[key_fn(file_id, entity)] += 1
    return counter


def _prf(gold: Counter, pred: Counter) -> PRF:
    tp = sum((gold & pred).values())
    fp = sum((pred - gold).values())
    fn = sum((gold - pred).values())
    return PRF(tp=tp, fp=fp, fn=fn)


def _concept_id(entity: Dict[str, Any]) -> Tuple[str, str]:
    """Concept identity for diagnostic metrics: type + normalized text."""
    text = " ".join(str(entity.get("text", "")).split())
    return (str(entity.get("type", "")), text)


def _text_words(entities: Sequence[Dict[str, Any]]) -> List[str]:
    """Assemble the sample-level word sequence from entity `text` fields.

    ABOUT.md #6 defines WER(i) as the word-level Word Error Rate of the `text`
    field in sample i (organizer confirmation: "WER(i) là WER của trường text
    trong sample i"). We therefore concatenate every entity's `text` into one
    ordered word sequence and score at word granularity, NOT per-concept.

    Assembly assumptions (documented so they can be aligned to the official
    checker if it differs):
      - Order: entities sorted by (start, end) position so the sequence follows
        document reading order. Entities without a valid position sort last.
      - Tokenization: Unicode-aware whitespace split on each `text` value.
      - Casing/diacritics: preserved (no lowercasing, no diacritic stripping).
    """
    ordered = sorted(
        entities,
        key=lambda entity: _position(entity) or (1 << 30, 1 << 30),
    )
    words: List[str] = []
    for entity in ordered:
        words.extend(str(entity.get("text", "")).split())
    return words


def _edit_distance(a: Sequence[Any], b: Sequence[Any]) -> int:
    """Levenshtein distance between two token sequences (words or concepts)."""
    previous = list(range(len(b) + 1))
    for i, item_a in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        for j, item_b in enumerate(b, start=1):
            cost = 0 if item_a == item_b else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
        previous = current
    return previous[-1]


def _wer_score(gold_items: Sequence[Any], pred_items: Sequence[Any]) -> float:
    """Return 1 - WER for one sample, clipped to [0, 1].

    `gold_items` / `pred_items` are the word sequences produced by
    `_text_words`. WER = edit_distance(gold, pred) / len(gold), so the
    denominator is the number of gold words in the sample's text field.
    """
    if not gold_items and not pred_items:
        return 1.0
    if not gold_items:
        return 0.0
    wer = _edit_distance(gold_items, pred_items) / len(gold_items)
    return max(0.0, 1.0 - wer)


def _jaccard(gold: set[Tuple[Any, ...]], pred: set[Tuple[Any, ...]]) -> float:
    """ABOUT.md J_X(i): 1 for both empty, 0 for gold empty/pred non-empty, else |∩|/|∪|."""
    if not gold and not pred:
        return 1.0
    if not gold and pred:
        return 0.0
    union = gold | pred
    return len(gold & pred) / len(union) if union else 1.0


def _assertion_set(entities: Sequence[Dict[str, Any]]) -> set[Tuple[Any, ...]]:
    rows: set[Tuple[Any, ...]] = set()
    for entity in entities:
        if entity.get("type") in LAB_TYPES:
            continue
        concept = _concept_id(entity)
        for assertion in _normalize_assertions(entity.get("assertions")):
            rows.add((*concept, assertion))
    return rows


def _candidate_set(entities: Sequence[Dict[str, Any]]) -> set[Tuple[Any, ...]]:
    rows: set[Tuple[Any, ...]] = set()
    for entity in entities:
        if entity.get("type") not in MAPPING_TYPES:
            continue
        concept = _concept_id(entity)
        for candidate in _normalize_candidates(entity.get("candidates")):
            rows.add((*concept, candidate))
    return rows


def _candidate_weight(gold_entities: Sequence[Dict[str, Any]]) -> int:
    """ABOUT.md denominator weight: sum_k (len(ground_truth(k)) + 1)."""
    total = 0
    for entity in gold_entities:
        if entity.get("type") in MAPPING_TYPES:
            total += len(_normalize_candidates(entity.get("candidates"))) + 1
    return total


def compute_official_metrics(
    gold_by_file: Dict[str, List[Dict[str, Any]]],
    pred_by_file: Dict[str, List[Dict[str, Any]]],
    file_ids: Sequence[str],
) -> OfficialMetrics:
    """Compute the ABOUT.md #6 weighted final_score approximation."""
    text_scores: List[float] = []
    assertion_scores: List[float] = []
    weighted_candidate_sum = 0.0
    candidate_weight_sum = 0

    for file_id in file_ids:
        gold_entities = gold_by_file.get(file_id, [])
        pred_entities = pred_by_file.get(file_id, [])
        gold_words = _text_words(gold_entities)
        pred_words = _text_words(pred_entities)
        text_scores.append(_wer_score(gold_words, pred_words))
        assertion_scores.append(_jaccard(_assertion_set(gold_entities), _assertion_set(pred_entities)))

        weight = _candidate_weight(gold_entities)
        candidate_score_i = _jaccard(_candidate_set(gold_entities), _candidate_set(pred_entities))
        if weight > 0:
            weighted_candidate_sum += candidate_score_i * weight
            candidate_weight_sum += weight

    text_score = sum(text_scores) / len(file_ids) if file_ids else 0.0
    assertions_score = sum(assertion_scores) / len(file_ids) if file_ids else 0.0
    candidates_score = weighted_candidate_sum / candidate_weight_sum if candidate_weight_sum else 1.0
    final_score = 0.3 * text_score + 0.3 * assertions_score + 0.4 * candidates_score
    return OfficialMetrics(
        text_score=text_score,
        assertions_score=assertions_score,
        candidates_score=candidates_score,
        final_score=final_score,
    )


def _counter_examples(counter: Counter, limit: int) -> List[Dict[str, Any]]:
    rows = []
    for key, count in counter.most_common(limit):
        rows.append({"count": count, "key": list(key) if isinstance(key, tuple) else key})
    return rows


def _label_counter(
    entities_by_file: Dict[str, List[Dict[str, Any]]],
    labels: Iterable[str],
    *,
    only_matched_span_keys: Optional[set[Tuple[Any, ...]]] = None,
) -> Counter:
    wanted = set(labels)
    counter: Counter = Counter()
    for file_id, entities in entities_by_file.items():
        for entity in entities:
            if only_matched_span_keys is not None and span_type_key(file_id, entity) not in only_matched_span_keys:
                continue
            entity_type = entity.get("type")
            base = span_type_key(file_id, entity)
            if wanted == set(ASSERTION_ORDER):
                for assertion in _normalize_assertions(entity.get("assertions")):
                    counter[(*base, assertion)] += 1
            elif entity_type in MAPPING_TYPES:
                for candidate in _normalize_candidates(entity.get("candidates")):
                    counter[(*base, candidate)] += 1
    return counter


def _load_entities_by_file(
    directory: Path,
    file_ids: Sequence[str],
    label: str,
    raw_texts: Dict[str, str],
    schema_errors: List[str],
    offset_errors: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    output: Dict[str, List[Dict[str, Any]]] = {}
    load_errors = schema_errors
    for file_id in file_ids:
        path = directory / f"{file_id}.json"
        entities = _load_json_array(path, load_errors, label)
        valid_entities = []
        for index, entity in enumerate(entities):
            if validate_entity(
                entity,
                file_id=file_id,
                index=index,
                raw_text=raw_texts.get(file_id),
                label=label,
                schema_errors=schema_errors,
                offset_errors=offset_errors,
            ):
                valid_entities.append(entity)
        output[file_id] = valid_entities
    return output


def _read_raw_texts(input_dir: Optional[Path], file_ids: Sequence[str]) -> Dict[str, str]:
    if input_dir is None:
        return {}
    raw_texts: Dict[str, str] = {}
    for file_id in file_ids:
        path = input_dir / f"{file_id}.txt"
        if path.exists():
            raw_texts[file_id] = path.read_text(encoding="utf-8")
    return raw_texts


def score_directories(gold_dir: Path, pred_dir: Path, input_dir: Optional[Path], file_ids: Sequence[str]) -> ScoreReport:
    schema_errors: List[str] = []
    offset_errors: List[str] = []
    raw_texts = _read_raw_texts(input_dir, file_ids)

    gold_by_file = _load_entities_by_file(gold_dir, file_ids, "gold", raw_texts, schema_errors, offset_errors)
    pred_by_file = _load_entities_by_file(pred_dir, file_ids, "pred", raw_texts, schema_errors, offset_errors)

    missing_pred_files = [file_id for file_id in file_ids if not (pred_dir / f"{file_id}.json").exists()]
    missing_gold_files = [file_id for file_id in file_ids if not (gold_dir / f"{file_id}.json").exists()]

    gold_span = _counter_for(gold_by_file, span_type_key)
    pred_span = _counter_for(pred_by_file, span_type_key)
    gold_span_text = _counter_for(gold_by_file, span_text_type_key)
    pred_span_text = _counter_for(pred_by_file, span_text_type_key)
    gold_full = _counter_for(gold_by_file, full_entity_key)
    pred_full = _counter_for(pred_by_file, full_entity_key)

    matched_span_keys = set((gold_span & pred_span).keys())
    gold_assertions = _label_counter(gold_by_file, ASSERTION_ORDER, only_matched_span_keys=matched_span_keys)
    pred_assertions = _label_counter(pred_by_file, ASSERTION_ORDER, only_matched_span_keys=matched_span_keys)
    gold_mappings = _label_counter(gold_by_file, ["candidate"], only_matched_span_keys=matched_span_keys)
    pred_mappings = _label_counter(pred_by_file, ["candidate"], only_matched_span_keys=matched_span_keys)

    official = compute_official_metrics(gold_by_file, pred_by_file, file_ids)

    metrics = {
        "span_type": _prf(gold_span, pred_span),
        "span_text_type": _prf(gold_span_text, pred_span_text),
        "full_entity": _prf(gold_full, pred_full),
        "assertions_on_matched_spans": _prf(gold_assertions, pred_assertions),
        "candidates_on_matched_spans": _prf(gold_mappings, pred_mappings),
    }

    by_type: Dict[str, PRF] = {}
    for entity_type in sorted(TARGET_ENTITY_TYPES):
        by_type[entity_type] = _prf(
            Counter({key: count for key, count in gold_span.items() if key[3] == entity_type}),
            Counter({key: count for key, count in pred_span.items() if key[3] == entity_type}),
        )

    by_file: List[FileScore] = []
    for file_id in file_ids:
        gold_one = {file_id: gold_by_file.get(file_id, [])}
        pred_one = {file_id: pred_by_file.get(file_id, [])}
        by_file.append(
            FileScore(
                file_id=file_id,
                gold_entities=len(gold_one[file_id]),
                pred_entities=len(pred_one[file_id]),
                span_type=_prf(_counter_for(gold_one, span_type_key), _counter_for(pred_one, span_type_key)),
                full_entity=_prf(_counter_for(gold_one, full_entity_key), _counter_for(pred_one, full_entity_key)),
            )
        )

    return ScoreReport(
        file_ids=list(file_ids),
        missing_pred_files=missing_pred_files,
        missing_gold_files=missing_gold_files,
        schema_errors=schema_errors,
        offset_errors=offset_errors,
        official=official,
        metrics=metrics,
        by_type=by_type,
        by_file=by_file,
        top_false_positives=_counter_examples(pred_span_text - gold_span_text, 50),
        top_false_negatives=_counter_examples(gold_span_text - pred_span_text, 50),
    )


def _fmt_score(score: PRF) -> str:
    return f"P={score.precision:.4f} R={score.recall:.4f} F1={score.f1:.4f} TP={score.tp} FP={score.fp} FN={score.fn}"


def write_markdown_report(report: ScoreReport, path: Path, gold_dir: Path, pred_dir: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# ViClinicalIE Silver Evaluation",
        "",
        f"Gold directory: `{gold_dir}`",
        f"Prediction directory: `{pred_dir}`",
        f"Files scored: {len(report.file_ids)}",
        "",
        "## ABOUT.md #6 Metric",
        "",
        f"- text_score = **{report.official.text_score:.6f}**",
        f"- assertions_score = **{report.official.assertions_score:.6f}**",
        f"- candidates_score = **{report.official.candidates_score:.6f}**",
        f"- final_score = 0.3*text + 0.3*assertions + 0.4*candidates = **{report.official.final_score:.6f}**",
        "",
        "## Diagnostic Exact-match Metrics",
        "",
        "| Metric | Precision | Recall | F1 | TP | FP | FN |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, score in report.metrics.items():
        lines.append(
            f"| {name} | {score.precision:.4f} | {score.recall:.4f} | {score.f1:.4f} | {score.tp} | {score.fp} | {score.fn} |"
        )

    lines.extend([
        "",
        "## Span+Type Metrics by Entity Type",
        "",
        "| Type | Precision | Recall | F1 | TP | FP | FN |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for entity_type, score in report.by_type.items():
        lines.append(
            f"| {entity_type} | {score.precision:.4f} | {score.recall:.4f} | {score.f1:.4f} | {score.tp} | {score.fp} | {score.fn} |"
        )

    lines.extend([
        "",
        "## Per-file Metrics",
        "",
        "| File | Gold | Pred | Span+Type F1 | Full Entity F1 | Span TP/FP/FN | Full TP/FP/FN |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for item in report.by_file:
        lines.append(
            f"| {item.file_id} | {item.gold_entities} | {item.pred_entities} | "
            f"{item.span_type.f1:.4f} | {item.full_entity.f1:.4f} | "
            f"{item.span_type.tp}/{item.span_type.fp}/{item.span_type.fn} | "
            f"{item.full_entity.tp}/{item.full_entity.fp}/{item.full_entity.fn} |"
        )

    lines.extend(["", "## Validation Issues", ""])
    if not report.schema_errors and not report.offset_errors and not report.missing_gold_files and not report.missing_pred_files:
        lines.append("- None")
    else:
        for file_id in report.missing_gold_files:
            lines.append(f"- Missing gold file: `{file_id}.json`")
        for file_id in report.missing_pred_files:
            lines.append(f"- Missing prediction file: `{file_id}.json`")
        for error in report.schema_errors[:100]:
            lines.append(f"- Schema: {error}")
        for error in report.offset_errors[:100]:
            lines.append(f"- Offset: {error}")

    lines.extend(["", "## Top False Positives (span_text_type)", ""])
    if report.top_false_positives:
        for row in report.top_false_positives[:25]:
            lines.append(f"- {row['count']} × `{row['key']}`")
    else:
        lines.append("- None")

    lines.extend(["", "## Top False Negatives (span_text_type)", ""])
    if report.top_false_negatives:
        for row in report.top_false_negatives[:25]:
            lines.append(f"- {row['count']} × `{row['key']}`")
    else:
        lines.append("- None")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json_report(report: ScoreReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def prf_to_dict(score: PRF) -> Dict[str, Any]:
        return {
            "tp": score.tp,
            "fp": score.fp,
            "fn": score.fn,
            "precision": score.precision,
            "recall": score.recall,
            "f1": score.f1,
        }

    data = {
        "file_ids": report.file_ids,
        "missing_pred_files": report.missing_pred_files,
        "missing_gold_files": report.missing_gold_files,
        "schema_errors": report.schema_errors,
        "offset_errors": report.offset_errors,
        "official": asdict(report.official),
        "metrics": {name: prf_to_dict(score) for name, score in report.metrics.items()},
        "by_type": {name: prf_to_dict(score) for name, score in report.by_type.items()},
        "by_file": [
            {
                "file_id": item.file_id,
                "gold_entities": item.gold_entities,
                "pred_entities": item.pred_entities,
                "span_type": prf_to_dict(item.span_type),
                "full_entity": prf_to_dict(item.full_entity),
            }
            for item in report.by_file
        ],
        "top_false_positives": report.top_false_positives,
        "top_false_negatives": report.top_false_negatives,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def discover_file_ids(gold_dir: Path, pred_dir: Path, limit: Optional[int], only: Optional[str]) -> List[str]:
    if only:
        ids = [item.strip() for item in only.split(",") if item.strip()]
    else:
        ids = sorted({path.stem for path in gold_dir.glob("*.json")}, key=_numeric_sort_key)
        if not ids:
            ids = sorted({path.stem for path in pred_dir.glob("*.json")}, key=_numeric_sort_key)
    if limit is not None:
        ids = ids[:limit]
    return ids


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score ViClinicalIE JSON outputs against silver/gold JSON outputs.")
    parser.add_argument("--gold-dir", type=Path, default=ROOT / "silver_test" / "output", help="Directory containing silver/gold *.json files.")
    parser.add_argument("--pred-dir", type=Path, default=ROOT / "outputs" / "v0_linked" / "output", help="Directory containing predicted *.json files.")
    parser.add_argument("--input-dir", type=Path, default=ROOT / "input", help="Optional raw input directory for offset validation.")
    parser.add_argument("--limit", type=int, default=None, help="Score only the first N file ids after numeric sorting.")
    parser.add_argument("--only", default=None, help="Comma-separated file ids to score, e.g. 1,2,3.")
    parser.add_argument("--report-md", type=Path, default=ROOT / "reports" / "silver_eval.md", help="Markdown report output path.")
    parser.add_argument("--report-json", type=Path, default=ROOT / "reports" / "silver_eval.json", help="JSON report output path.")
    parser.add_argument("--fail-on-schema-error", action="store_true", help="Exit non-zero if schema/offset/missing-file issues are found.")
    return parser.parse_args()


def configure_stdout() -> None:
    """Make Vietnamese console output safe on Windows."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    configure_stdout()
    args = parse_args()
    file_ids = discover_file_ids(args.gold_dir, args.pred_dir, args.limit, args.only)
    if not file_ids:
        raise SystemExit("No JSON files found to score.")

    report = score_directories(args.gold_dir, args.pred_dir, args.input_dir, file_ids)
    write_markdown_report(report, args.report_md, args.gold_dir, args.pred_dir)
    write_json_report(report, args.report_json)

    print("=" * 72)
    print("ViClinicalIE Silver Evaluation")
    print("=" * 72)
    print(f"Files scored: {len(report.file_ids)}")
    print("ABOUT.md #6 metric:")
    print(f"  text_score       = {report.official.text_score:.6f}")
    print(f"  assertions_score = {report.official.assertions_score:.6f}")
    print(f"  candidates_score = {report.official.candidates_score:.6f}")
    print(f"  final_score      = {report.official.final_score:.6f}")
    print("Diagnostic exact-match metrics:")
    for name, score in report.metrics.items():
        print(f"{name:30s} {_fmt_score(score)}")
    print("By type:")
    for entity_type, score in report.by_type.items():
        print(f"  {entity_type:22s} {_fmt_score(score)}")
    print(f"Markdown report: {args.report_md}")
    print(f"JSON report    : {args.report_json}")
    if report.schema_errors or report.offset_errors or report.missing_gold_files or report.missing_pred_files:
        print(
            "Validation issues: "
            f"schema={len(report.schema_errors)} offset={len(report.offset_errors)} "
            f"missing_gold={len(report.missing_gold_files)} missing_pred={len(report.missing_pred_files)}"
        )
        if args.fail_on_schema_error:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
