# NER-4 — Deterministic Fusion

## Status

Completed and promoted on the 12-note development split. Calibration and lockbox
were not opened.

The selected production profile is `configs/ner4.yaml`. It adds complete-link
evidence clustering, deterministic type policy, full drug-formulation selection
from a `drug_rule` anchor, source-level provenance, and a downstream handoff that
does not edit finalized NER span/type.

Boundary cue trimming and structural test/result splitting are implemented and
raw-offset safe, but disabled in the selected profile. The verified development
gold sometimes keeps negation cues and full test-plus-result spans, so enabling
those operations currently reduces exact matching.

## Development result

| Metric | NER-3 D | Selected NER-4 | Delta |
|---|---:|---:|---:|
| Exact F1 | 0.256186 | 0.258389 | +0.002203 |
| Relaxed F1 | 0.404658 | 0.439597 | +0.034939 |
| Official-like score | 0.138422 | 0.194986 | +0.056564 |

The selected system produced zero raw-offset errors and zero exact duplicates.
Its entity density was 0.8112 relative to NER-3 D. The immutable decision record
is `outputs/reports/ner4_final/summary.json`.

## Verification

```powershell
python -m pytest tests/test_ner4.py tests/test_ner4_pipeline.py -q
python -m pytest -q
```

The final verification passed 12 focused NER-4 tests and all 268 repository tests.