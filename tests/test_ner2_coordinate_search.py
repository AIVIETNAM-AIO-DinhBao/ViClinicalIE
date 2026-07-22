from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_coordinate_generator_changes_exactly_one_type_per_config(tmp_path: Path) -> None:
    base = tmp_path / "base.yaml"
    base.write_text("extractors:\n  gliner:\n    threshold:\n      default: 0.4\n", encoding="utf-8")
    output = tmp_path / "coordinates"
    subprocess.run([
        sys.executable, str(ROOT / "scripts/generate_ner2_coordinate_search.py"),
        "--base-config", str(base), "--values", "0.35", "0.4", "0.45", "--output-dir", str(output),
    ], cwd=ROOT, check=True, capture_output=True)
    configs = sorted(path for path in output.glob("coord_*.yaml"))
    assert len(configs) == 10
    for path in configs:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        profile = payload["extractors"]["gliner"]["threshold"]
        changed_type = payload["ner2_coordinate"]["changed_type"]
        assert sum(value != .4 for key, value in profile.items() if key != "default") == 1
        assert profile[changed_type] in {.35, .45}