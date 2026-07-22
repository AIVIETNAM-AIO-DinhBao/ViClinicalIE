from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


PROPOSAL_SCHEMA_VERSION = "ner2-proposals-v1"


class ProposalStore:
    """Content-addressed storage for unfiltered, window-level model proposals."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def get(self, key: str) -> list[dict[str, Any]] | None:
        path = self.directory / f"{key}.json"
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        if payload.get("schema_version") != PROPOSAL_SCHEMA_VERSION or payload.get("key") != key:
            return None
        proposals = payload.get("proposals")
        return [dict(row) for row in proposals] if isinstance(proposals, list) and all(isinstance(row, Mapping) for row in proposals) else None

    def put(self, key: str, proposals: list[dict[str, Any]]) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{key}.json"
        payload = {"schema_version": PROPOSAL_SCHEMA_VERSION, "key": key, "proposals": proposals}
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{key}.", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        return target