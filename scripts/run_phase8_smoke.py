from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.assertion import AssertionDetector, load_assertion_rules
from src.config import load_config
from src.extractors import ExtractionContext, build_default_extractors
from src.io_utils import read_text
from src.linking.icd10_linker import ICD10Linker
from src.linking.rxnorm_linker import RxNormLinker
from src.preprocess.chunker import preprocess_text
from src.section.section_detector import detect_sections, load_section_patterns
from src.type_resolution import TypeResolver


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 8 RxNorm candidate generation smoke checks.")
    parser.add_argument("--config", default="configs/default.yaml", help="Path to YAML config.")
    parser.add_argument("--max-files", type=int, default=2, help="Maximum files to check.")
    parser.add_argument("--sample-limit", type=int, default=20, help="Maximum sample linked drugs to print.")
    args = parser.parse_args()

    config = load_config(args.config, project_root=PROJECT_ROOT)
    section_cfg = config.raw.get("section_detection", {})
    patterns_path = _resolve_patterns_path(config.config_path, section_cfg.get("patterns_config", "section_patterns.yaml"))
    patterns = load_section_patterns(patterns_path)
    extractors = build_default_extractors(config)
    resolver = TypeResolver(config.raw.get("type_resolution", {}))
    assertion_cfg = dict(config.raw.get("assertion_detection", {}))
    rules = _load_assertion_rules(config.config_path, assertion_cfg)
    detector = AssertionDetector(assertion_cfg, rules=rules)
    icd_linker = ICD10Linker.from_config(config)
    rx_linker = RxNormLinker.from_config(config)
    files = _sample_files(config, args.max_files)
    if not files:
        raise FileNotFoundError("No input files found for Phase 8 smoke check")

    valid_icd_codes = icd_linker.valid_codes
    valid_rx_codes = rx_linker.valid_codes
    entity_counts: Counter[str] = Counter()
    drug_candidate_count: Counter[int] = Counter()
    diagnosis_candidate_count: Counter[int] = Counter()
    offset_errors: list[str] = []
    mutation_errors: list[str] = []
    invalid_icd_errors: list[str] = []
    invalid_rx_errors: list[str] = []
    wrong_type_candidate_errors: list[str] = []
    sample_drugs: list[str] = []
    total_candidates = 0
    total_entities = 0
    total_diagnoses = 0
    total_drugs = 0
    diagnoses_with_candidates = 0
    drugs_with_candidates = 0
    total_chunks = 0

    for path in files:
        raw_text = read_text(path, encoding=str(config.raw.get("encoding", "utf-8")))
        output = preprocess_text(raw_text, config.raw)
        chunks = detect_sections(output.chunks, patterns, section_cfg)
        total_chunks += len(chunks)
        context = ExtractionContext(raw_text=raw_text, views=output.views, chunks=chunks, config=config.raw)
        span_candidates = []
        for extractor in extractors:
            span_candidates.extend(extractor.extract(context))
        entities = resolver.resolve(span_candidates, raw_text)
        asserted = detector.apply(entities, raw_text)
        icd_linked = icd_linker.link_entities(asserted, raw_text=raw_text)
        linked = rx_linker.link_entities(icd_linked, raw_text=raw_text)
        total_candidates += len(span_candidates)
        total_entities += len(linked)

        for before, after in zip(asserted, linked):
            entity_counts.update([str(after.type)])
            if raw_text[after.start : after.end] != after.text:
                offset_errors.append(f"{path.name}:{after.start}-{after.end}:{after.type}:{after.text!r}")
            if (before.text, before.start, before.end, before.type, before.assertions, before.confidence) != (
                after.text,
                after.start,
                after.end,
                after.type,
                after.assertions,
                after.confidence,
            ):
                mutation_errors.append(f"{path.name}:{before.position}:{before.type}:{before.text!r}")
            if str(after.type) == "CHẨN_ĐOÁN":
                total_diagnoses += 1
                diagnosis_candidate_count.update([len(after.candidates)])
                if after.candidates:
                    diagnoses_with_candidates += 1
                for code in after.candidates:
                    if code not in valid_icd_codes:
                        invalid_icd_errors.append(f"{path.name}:{after.position}:{after.text!r}:{code}")
            elif str(after.type) == "THUỐC":
                total_drugs += 1
                drug_candidate_count.update([len(after.candidates)])
                if after.candidates:
                    drugs_with_candidates += 1
                    if len(sample_drugs) < args.sample_limit:
                        rx_info = after.provenance.get("rxnorm_linking", {})
                        sample_drugs.append(
                            f"{path.name} | {after.position} | {after.text} | {after.candidates} | "
                            f"parsed={rx_info.get('parsed', {})} | evidence={rx_info.get('chosen', [])[:1]}"
                        )
                for code in after.candidates:
                    if code not in valid_rx_codes:
                        invalid_rx_errors.append(f"{path.name}:{after.position}:{after.text!r}:{code}")
            elif after.candidates:
                wrong_type_candidate_errors.append(f"{path.name}:{after.position}:{after.type}:{after.text!r}:{after.candidates}")

    print("Phase 8 smoke checks completed.")
    print(f"files_checked: {len(files)}")
    print(f"chunks_checked: {total_chunks}")
    print(f"span_candidates: {total_candidates}")
    print(f"final_entities: {total_entities}")
    print(f"diagnosis_entities: {total_diagnoses}")
    print(f"diagnosis_with_icd_candidates: {diagnoses_with_candidates}")
    print(f"drug_entities: {total_drugs}")
    print(f"drug_with_rxnorm_candidates: {drugs_with_candidates}")
    print(f"candidate_count_by_diagnosis_entity: {dict(sorted(diagnosis_candidate_count.items()))}")
    print(f"candidate_count_by_drug_entity: {dict(sorted(drug_candidate_count.items()))}")
    print(f"entities_by_type: {dict(sorted(entity_counts.items()))}")
    print(f"offset_error_count: {len(offset_errors)}")
    print(f"mutation_error_count: {len(mutation_errors)}")
    print(f"invalid_icd_candidate_error_count: {len(invalid_icd_errors)}")
    print(f"invalid_rxnorm_candidate_error_count: {len(invalid_rx_errors)}")
    print(f"wrong_type_candidate_error_count: {len(wrong_type_candidate_errors)}")
    for label, errors in (
        ("OFFSET_ERROR", offset_errors),
        ("MUTATION_ERROR", mutation_errors),
        ("INVALID_ICD_CANDIDATE_ERROR", invalid_icd_errors),
        ("INVALID_RXNORM_CANDIDATE_ERROR", invalid_rx_errors),
        ("WRONG_TYPE_CANDIDATE_ERROR", wrong_type_candidate_errors),
    ):
        for item in errors[:20]:
            print(f"{label}: {item}")
    if offset_errors or mutation_errors or invalid_icd_errors or invalid_rx_errors or wrong_type_candidate_errors:
        return 1
    print("sample_linked_drugs:")
    for sample in sample_drugs:
        print(f"  {sample}")
    return 0


def _resolve_patterns_path(config_path: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return config_path.parent / path


def _load_assertion_rules(config_path: Path, assertion_cfg: dict) -> dict[str, list[str]]:
    rules_value = assertion_cfg.get("rules_config")
    if not rules_value:
        return {}
    rules_path = _resolve_patterns_path(config_path, str(rules_value))
    return load_assertion_rules(rules_path)


def _sample_files(config, max_files: int) -> list[Path]:
    candidates: list[Path] = []
    for key in ("golden_input_dir", "raw_input_dir"):
        if key in config.paths and config.path(key).is_dir():
            candidates.extend(sorted(config.path(key).glob("*.txt"))[:max_files])
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
        if len(unique) >= max_files:
            break
    return unique


if __name__ == "__main__":
    raise SystemExit(main())