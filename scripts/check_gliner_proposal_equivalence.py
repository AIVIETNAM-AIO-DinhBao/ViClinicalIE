from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config, load_yaml
from src.extractors.gliner_extractor import DEFAULT_LABEL_MAP
from src.io_utils import write_json
from src.ner.gliner_backend import GLiNERBackend, GLiNERPrediction
from src.ner.gliner_windows import TransformersTokenCounter, build_gliner_windows
from src.preprocess.chunker import preprocess_text
from src.section.section_detector import detect_sections, load_section_patterns


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify direct GLiNER threshold inference against proposal-floor filtering.")
    parser.add_argument("--config", default="configs/gliner_zero_shot.yaml")
    parser.add_argument("--split-config", default="configs/splits_v2.yaml")
    parser.add_argument("--split", choices=("development", "calibration"), default="development")
    parser.add_argument("--input-dir", default="data/golden/input")
    parser.add_argument("--proposal-threshold", type=float, default=0.15)
    parser.add_argument("--selection-threshold", type=float, default=0.35)
    parser.add_argument("--max-files", type=int, default=None)
    parser.add_argument("--output", default="outputs/reports/ner2_proposal_equivalence.json")
    args = parser.parse_args()

    config = load_config(args.config, project_root=PROJECT_ROOT)
    gliner_cfg = dict(config.raw.get("extractors", {}).get("gliner", {}))
    label_map = {str(key): str(value) for key, value in dict(gliner_cfg.get("label_map", DEFAULT_LABEL_MAP)).items()}
    passes = gliner_cfg.get("passes") or [{"name": gliner_cfg.get("pass_name", "full_five_type"), "label_map": label_map}]
    window_cfg = dict(gliner_cfg.get("windowing", {}))
    tokenizer = None
    if window_cfg.get("tokenizer_name_or_path"):
        tokenizer = TransformersTokenCounter(
            str(window_cfg["tokenizer_name_or_path"]),
            revision=str(window_cfg["tokenizer_revision"]) if window_cfg.get("tokenizer_revision") else None,
            local_files_only=bool(gliner_cfg.get("local_files_only", False)),
        )
    backend = GLiNERBackend(gliner_cfg)
    section_cfg = dict(config.raw.get("section_detection", {}))
    patterns_value = Path(str(section_cfg.get("patterns_config", "section_patterns.yaml")))
    patterns_path = patterns_value if patterns_value.is_absolute() else config.config_path.parent / patterns_value
    section_patterns = load_section_patterns(patterns_path)
    ids = [str(value) for value in load_yaml(args.split_config)[args.split]["ids"]]
    if args.max_files is not None:
        ids = ids[: args.max_files]

    mismatches: list[dict[str, Any]] = []
    comparisons = 0
    for file_id in ids:
        raw_text = (Path(args.input_dir) / f"{file_id}.txt").read_text(encoding="utf-8")
        preprocessed = preprocess_text(raw_text, config.raw)
        chunks = detect_sections(preprocessed.chunks, section_patterns, section_cfg)
        windows = build_gliner_windows(
            raw_text,
            chunks,
            max_tokens=int(window_cfg.get("max_tokens", 320)),
            overlap_tokens=int(window_cfg.get("overlap_tokens", 64)),
            counter=tokenizer,
            strategy=str(window_cfg.get("strategy", "legacy_chunk")),
        )
        for pass_cfg in passes:
            labels = list(dict(pass_cfg["label_map"]))
            for window in windows:
                direct = backend.predict(window.text, labels, threshold=args.selection_threshold)
                floor = backend.predict(window.text, labels, threshold=args.proposal_threshold)
                filtered = [row for row in floor if row.score >= args.selection_threshold]
                comparisons += 1
                if _signature(direct) != _signature(filtered):
                    mismatches.append({
                        "file_id": file_id,
                        "pass_name": str(pass_cfg.get("name", "")),
                        "window_id": window.window_id,
                        "window_position": [window.start, window.end],
                        "direct": _rows(direct),
                        "floor_filtered": _rows(filtered),
                    })

    report = {
        "equivalent": not mismatches,
        "proposal_threshold": args.proposal_threshold,
        "selection_threshold": args.selection_threshold,
        "files": ids,
        "window_pass_comparisons": comparisons,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "policy": "raw_proposal_cache_allowed" if not mismatches else "keep_inference_threshold_in_cache_key",
    }
    write_json(args.output, report)
    print(json.dumps({key: report[key] for key in ("equivalent", "window_pass_comparisons", "mismatch_count", "policy")}, ensure_ascii=False))
    return 0 if not mismatches else 2


def _signature(rows: list[GLiNERPrediction]) -> list[tuple[int, int, str, float]]:
    return sorted((row.start, row.end, row.label, round(row.score, 7)) for row in rows)


def _rows(rows: list[GLiNERPrediction]) -> list[dict[str, Any]]:
    return [{"start": row.start, "end": row.end, "text": row.text, "label": row.label, "score": row.score} for row in rows]


if __name__ == "__main__":
    raise SystemExit(main())