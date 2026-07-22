from __future__ import annotations

import json

from src.ner.proposal_store import PROPOSAL_SCHEMA_VERSION, ProposalStore


def test_proposal_store_round_trip_and_corruption(tmp_path) -> None:
    store = ProposalStore(tmp_path)
    rows = [{"text": "sốt", "start": 0, "end": 3, "raw_model_score": 0.51}]
    path = store.put("abc", rows)
    assert store.get("abc") == rows
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == PROPOSAL_SCHEMA_VERSION
    path.write_text("not-json", encoding="utf-8")
    assert store.get("abc") is None


def test_proposal_store_rejects_wrong_key_and_schema(tmp_path) -> None:
    store = ProposalStore(tmp_path)
    path = store.put("abc", [])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["key"] = "other"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert store.get("abc") is None