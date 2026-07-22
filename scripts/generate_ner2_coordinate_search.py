from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.data_types import VALID_ENTITY_TYPES
from src.ner.experiment_registry import canonical_hash


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate one-axis per-type NER-2 threshold coordinate candidates.")
    parser.add_argument("--base-config", required=True, help="Config containing the selected global threshold profile.")
    parser.add_argument("--values", nargs="+", type=float, default=[.2, .25, .3, .35, .4, .45, .5, .6])
    parser.add_argument("--round", type=int, choices=(1, 2), default=1)
    parser.add_argument("--output-dir", default="configs/ner2/coordinate_search")
    args = parser.parse_args()
    config = load_config(args.base_config, project_root=PROJECT_ROOT)
    gliner = config.raw.get("extractors", {}).get("gliner", {})
    threshold = gliner.get("threshold", .35)
    if isinstance(threshold, dict):
        default = float(threshold.get("default", .35))
        current = {entity_type: float(threshold.get(entity_type, default)) for entity_type in VALID_ENTITY_TYPES}
    else:
        default = float(threshold)
        current = {entity_type: default for entity_type in VALID_ENTITY_TYPES}
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"round": args.round, "base_config": str(Path(args.base_config).resolve()), "base_threshold": {"default": default, **current}, "candidates": []}
    for entity_type in sorted(VALID_ENTITY_TYPES):
        for value in sorted(set(args.values)):
            if not 0 <= value <= 1 or value == current[entity_type]:
                continue
            normalized = unicodedata.normalize("NFKD", entity_type.lower().replace("đ", "d"))
            safe_type = "".join(character for character in normalized if character.isascii() and (character.isalnum() or character == "_"))
            run_id = f"coord_r{args.round}_{safe_type}_{int(value * 100):03d}"
            profile = {"default": default, **current, entity_type: value}
            path = output_dir / f"{run_id}.yaml"
            payload = {
                "extends": str(Path(args.base_config).resolve()),
                "extractors": {"gliner": {"threshold": profile}},
                "ner2_coordinate": {"run_id": run_id, "round": args.round, "changed_type": entity_type, "from": current[entity_type], "to": value},
            }
            path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
            manifest["candidates"].append({"run_id": run_id, "entity_type": entity_type, "value": value, "config": str(path), "profile_hash": canonical_hash(profile)})
    (output_dir / f"round_{args.round}_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"round": args.round, "candidates": len(manifest["candidates"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())