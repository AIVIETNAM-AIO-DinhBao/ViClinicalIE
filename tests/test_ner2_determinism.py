from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_determinism_verifier_accepts_identical_and_rejects_changed_bytes(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir(); second.mkdir()
    (first / "1.json").write_bytes(b"[]\n")
    (second / "1.json").write_bytes(b"[]\n")
    command = [sys.executable, str(ROOT / "scripts/verify_ner2_determinism.py"), "--run1", str(first), "--run2", str(second), "--expected-count", "1", "--output", str(tmp_path / "report.json")]
    assert subprocess.run(command, cwd=ROOT).returncode == 0
    (second / "1.json").write_bytes(b"[ ]\n")
    assert subprocess.run(command, cwd=ROOT).returncode == 2