from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


CACHE_SCHEMA_VERSION = "ner1-predictions-v3"


def build_cache_key(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class PredictionCache:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def get(self, key: str) -> list[dict[str, Any]] | None:
        path = self.directory / f"{key}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if data.get("schema_version") != CACHE_SCHEMA_VERSION or data.get("key") != key or not isinstance(data.get("predictions"), list):
            return None
        return list(data["predictions"])

    def put(self, key: str, predictions: list[dict[str, Any]]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{key}.json"
        payload = {"schema_version": CACHE_SCHEMA_VERSION, "key": key, "predictions": predictions}
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")