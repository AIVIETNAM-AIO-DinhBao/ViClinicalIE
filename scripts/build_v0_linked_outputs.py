"""Build V0 outputs with local ICD-10/RxNorm candidate mappings."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_v0_outputs import run_rule_extraction
from src.assertion import add_assertions
from src.io_utils import load_input_files
from src.linking.candidate_linker import link_mapping_candidates, load_default_linkers
from src.merge import merge_candidates
from src.models import ClinicalDocument, SpanCandidate
from src.output_writer import create_output_zip, write_output_files
from src.rule_extractors import ENTITY_DIAGNOSIS, ENTITY_DRUG, TARGET_ENTITY_TYPES, write_span_candidates_jsonl
from src.section_parser import parse_documents
from src.validator import validate_output_artifacts, write_validation_report


MAPPING_TYPES = {ENTITY_DIAGNOSIS, ENTITY_DRUG}


def configure_stdout() -> None:
    """Make Vietnamese output safe on Windows consoles."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def write_csv(path: Path, rows: Sequence[dict], fieldnames: Sequence[str]) -> None:
    """Write UTF-8 CSV debug artifacts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def mapping_summary(candidates: Iterable[SpanCandidate], debug_rows: Sequence[dict]) -> dict:
    """Build mapping coverage summary."""
    mappable = [candidate for candidate in candidates if candidate.type_candidate in MAPPING_TYPES]
    mapped = [candidate for candidate in mappable if candidate.mapping_candidates]
    by_type = Counter(candidate.type_candidate for candidate in mappable)
    mapped_by_type = Counter(candidate.type_candidate for candidate in mapped)
    source_counts = Counter(row["source"] for row in debug_rows)
    unmapped_rows = [row for row in debug_rows if not row["codes"]]
    unmapped_counter = Counter((row["type"], row["text"].lower(), row["reason"]) for row in unmapped_rows)

    coverage = {}
    for entity_type, total in by_type.items():
        coverage[entity_type] = (mapped_by_type[entity_type] / total) if total else 1.0

    return {
        "total_mappable": len(mappable),
        "total_mapped": len(mapped),
        "by_type": dict(by_type),
        "mapped_by_type": dict(mapped_by_type),
        "coverage": coverage,
        "source_counts": dict(source_counts),
        "unmapped_count": len(unmapped_rows),
        "top_unmapped": [
            {
                "type": entity_type,
                "text": text,
                "reason": reason,
                "count": count,
            }
            for (entity_type, text, reason), count in unmapped_counter.most_common(50)
        ],
    }


def write_mapping_report(summary: dict, validation_ok: bool, path: Path) -> None:
    """Write Markdown mapping coverage report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Mapping Coverage V0",
        "",
        f"Validation: {'PASS' if validation_ok else 'FAIL'}",
        f"Total mappable diagnosis/drug spans: {summary['total_mappable']}",
        f"Mapped spans: {summary['total_mapped']}",
        f"Unmapped spans: {summary['unmapped_count']}",
        "",
        "## Coverage",
        "",
    ]
    for entity_type, total in summary["by_type"].items():
        mapped = summary["mapped_by_type"].get(entity_type, 0)
        coverage = summary["coverage"].get(entity_type, 0.0)
        lines.append(f"- {entity_type}: {mapped}/{total} ({coverage:.1%})")

    lines.extend(["", "## Mapping Sources", ""])
    for source, count in sorted(summary["source_counts"].items()):
        lines.append(f"- {source}: {count}")

    lines.extend(["", "## Top Unmapped", ""])
    if summary["top_unmapped"]:
        for row in summary["top_unmapped"][:30]:
            lines.append(f"- {row['count']} x {row['type']} | {row['text']} | {row['reason']}")
    else:
        lines.append("- None")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def assert_mapping_gate(summary: dict) -> None:
    """Enforce V0 mapping coverage gates."""
    diagnosis_coverage = summary["coverage"].get(ENTITY_DIAGNOSIS, 1.0)
    drug_coverage = summary["coverage"].get(ENTITY_DRUG, 1.0)
    failures = []
    if diagnosis_coverage < 0.80:
        failures.append(f"diagnosis coverage {diagnosis_coverage:.1%} < 80%")
    if drug_coverage < 0.90:
        failures.append(f"drug coverage {drug_coverage:.1%} < 90%")
    if failures:
        raise SystemExit("Mapping gate failed: " + "; ".join(failures))


def display_path(path: Path) -> str:
    """Return a readable path without failing for relative CLI paths."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def parse_args() -> argparse.Namespace:
    """Parse V0 linked-output build options."""
    parser = argparse.ArgumentParser(description="Build V0 linked ViClinicalIE outputs.")
    parser.add_argument("--input-dir", type=Path, default=ROOT / "input", help="Input *.txt directory.")
    parser.add_argument("--outputs-dir", type=Path, default=ROOT / "outputs" / "v0_linked", help="Output artifact directory.")
    parser.add_argument("--analysis-dir", type=Path, default=ROOT / "analysis", help="Analysis artifact directory.")
    parser.add_argument("--reports-dir", type=Path, default=ROOT / "reports", help="Report artifact directory.")
    parser.add_argument("--resource-dir", type=Path, default=ROOT / "data_resources", help="Data resource directory.")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N input files by numeric file id.")
    parser.add_argument("--only", default=None, help="Comma-separated input file ids to process, e.g. 1,2,3.")
    parser.add_argument("--skip-mapping-gate", action="store_true", help="Do not fail the run if mapping coverage is below V0 gates.")
    return parser.parse_args()


def select_documents(documents: Sequence[ClinicalDocument], only: str | None, limit: int | None) -> list[ClinicalDocument]:
    """Select documents by explicit ids or first-N numeric order."""
    ordered = sorted(documents, key=lambda doc: int(doc.file_id) if doc.file_id.isdigit() else 1 << 30)
    if only:
        requested = {item.strip() for item in only.split(",") if item.strip()}
        ordered = [doc for doc in ordered if doc.file_id in requested]
    if limit is not None:
        ordered = ordered[:limit]
    return ordered


def main() -> None:
    configure_stdout()
    args = parse_args()

    analysis_dir = args.analysis_dir
    outputs_dir = args.outputs_dir
    output_json_dir = outputs_dir / "output"
    reports_dir = args.reports_dir
    resource_dir = args.resource_dir

    linked_candidates_path = analysis_dir / "span_candidates_v0_linked.jsonl"
    mapping_debug_path = analysis_dir / "mapping_debug_v0.csv"
    mapping_unmapped_path = analysis_dir / "mapping_unmapped_v0.csv"
    zip_path = outputs_dir / "output.zip"
    validation_report_path = reports_dir / "validation_v0_linked.md"
    mapping_report_path = reports_dir / "mapping_coverage_v0.md"

    documents = parse_documents(load_input_files(str(args.input_dir)))
    documents = select_documents(documents, args.only, args.limit)
    if not documents:
        raise SystemExit("No input/*.txt files found for the requested selection; cannot build linked V0 outputs.")

    documents_by_id = {doc.file_id: doc for doc in documents}
    raw_candidates, _ = run_rule_extraction(documents)
    asserted_candidates = add_assertions(raw_candidates, documents_by_id)
    merged_candidates = merge_candidates(asserted_candidates)
    icd_linker, rxnorm_linker = load_default_linkers(resource_dir)
    linked_candidates, debug_rows = link_mapping_candidates(merged_candidates, icd_linker, rxnorm_linker)

    analysis_dir.mkdir(parents=True, exist_ok=True)
    write_span_candidates_jsonl(linked_candidates, str(linked_candidates_path))

    debug_fields = ["file_id", "text", "type", "start", "end", "codes", "source", "confidence", "matched_term", "reason"]
    write_csv(mapping_debug_path, debug_rows, debug_fields)
    write_csv(mapping_unmapped_path, [row for row in debug_rows if not row["codes"]], debug_fields)

    if output_json_dir.exists():
        for old_json in output_json_dir.glob("*.json"):
            old_json.unlink()
    write_output_files(linked_candidates, documents, output_json_dir)
    create_output_zip(output_json_dir, zip_path)

    validation_report = validate_output_artifacts(
        output_json_dir,
        zip_path,
        documents_by_id,
        [doc.file_id for doc in documents],
    )
    missing_types = sorted(TARGET_ENTITY_TYPES - set(validation_report.by_type))
    if missing_types:
        validation_report.mark_error(validation_report.schema_errors, f"missing output entity types: {missing_types}")
    write_validation_report(validation_report, validation_report_path)

    summary = mapping_summary(linked_candidates, debug_rows)
    write_mapping_report(summary, validation_report.ok, mapping_report_path)
    if not args.skip_mapping_gate:
        assert_mapping_gate(summary)

    print("=" * 70)
    print("V0 Linked Output Build Complete")
    print("=" * 70)
    print(f"Input dir: {args.input_dir}")
    print(f"Documents: {len(documents)}")
    print(f"Linked candidates: {len(linked_candidates)}")
    print(f"Validation: {'PASS' if validation_report.ok else 'FAIL'}")
    print(f"Mapping total: {summary['total_mapped']}/{summary['total_mappable']}")
    print(f"Coverage: {summary['coverage']}")
    print(f"Sources: {summary['source_counts']}")
    print(f"Saved {display_path(linked_candidates_path)}")
    print(f"Saved {display_path(mapping_debug_path)}")
    print(f"Saved {display_path(mapping_unmapped_path)}")
    print(f"Saved {display_path(output_json_dir)}")
    print(f"Saved {display_path(zip_path)}")
    print(f"Saved {display_path(mapping_report_path)}")

    if not validation_report.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
