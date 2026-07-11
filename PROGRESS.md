# PROGRESS: ViClinicalIE implementation state

**Last updated:** 2026-07-10  
**Current implementation phase:** Phase 6 complete — deterministic assertion detection baseline  
**Reference docs:** `ABOUT.md`, `Solution Design.md`, `Implementation Plan.md`

---

## 1. Project goal

This repository is being built for the Viettel AI Race clinical text entity extraction and normalization task.

The final system must read free-form Vietnamese/mixed clinical notes and output one JSON file per input file containing detected medical concepts with:

- `text`: exact raw substring.
- `position`: `[start, end)` character offsets in raw input.
- `type`: one of:
  - `TRIỆU_CHỨNG`
  - `TÊN_XÉT_NGHIỆM`
  - `KẾT_QUẢ_XÉT_NGHIỆM`
  - `CHẨN_ĐOÁN`
  - `THUỐC`
- `assertions`: only meaningful for `TRIỆU_CHỨNG`, `CHẨN_ĐOÁN`, `THUỐC`; valid values are `isNegated`, `isFamily`, `isHistorical`.
- `candidates`: ICD-10 codes for `CHẨN_ĐOÁN`, RxNorm RxCUIs for `THUỐC`.

The solution direction remains:

```text
Rule-first + Retrieval-first + Encoder-assisted + Validator-enforced
```

Important design constraints from `Solution Design.md` and `Implementation Plan.md`:

1. **Never break raw offsets.** All final spans must satisfy `raw_text[start:end] == text`.
2. **Section detection is only a prior**, not a hard rule for type or assertion.
3. **Entity linking is downstream of extraction/type resolution.** ICD/RxNorm linkers should not invent spans.
4. **Candidate precision matters.** Do not return arbitrary top-k candidates.
5. **Local/self-host only for model inference.** No external APIs for final inference.

---

## 2. Current repository state

### 2.1 Data layout and foundation are in place

Implemented foundation files include:

- `src/config.py`
  - YAML config loading.
  - Project-root-relative path resolution.
- `src/io_utils.py`
  - Text, JSON, JSONL read/write helpers.
- `src/logging_utils.py`
  - Run report directory and summary helpers.
- `src/data_types.py`
  - Shared dataclasses and constants:
    - `TextViews`
    - `Chunk`
    - `PreprocessOutput`
    - `SpanCandidate`
    - `FinalEntity`
    - `MappingCandidate`
    - `VALID_ENTITY_TYPES`
    - `VALID_ASSERTIONS`
- `configs/default.yaml`
  - Current `project.phase` is `phase_6_assertion_detection`.
  - Includes ICD/RxNorm parsing config, sparse retrieval config, preprocess/chunking config, section detection config, Phase 4 extractor config, Phase 5 type-resolution config, and Phase 6 assertion-detection config.
- `configs/paths.yaml`
  - Canonical paths for raw input, terminology files, processed indices, golden data, predictions, reports, and submissions.

Current data status from `scripts/check_setup.py`:

- `data/raw/input/`: 100 raw `.txt` files detected.
- `data/golden/input/` + `data/golden/gold/`: 20 golden input/gold pairs detected.
- Golden annotation entity count: 370.

Root-level `golden_dataset/*.json` still exists, but canonical golden paths are under `data/golden/`.

---

## 3. Completed phases

## 3.1 Phase 0 — project foundation

**Status:** Complete for current scope.

Implemented:

- Repo skeleton and canonical folders:
  - `configs/`
  - `data/`
  - `outputs/`
  - `scripts/`
  - `src/`
  - `tests/`
  - `streamlit_app/`
- Config/path loading.
- IO utilities.
- Basic logging/report directory utilities.
- Data type definitions.
- Setup validation script:
  - `scripts/check_setup.py`

Known caveat:

- `README.md` still describes the project as Phase 0 only. It should be updated after Phase 6 documentation stabilizes.

---

## 3.2 Phase 1 — terminology resources and sparse retrieval

**Status:** Implemented and smoke-tested.

Implemented modules:

- `src/linking/terminology_normalizer.py`
  - Unicode normalization.
  - Whitespace normalization.
  - Vietnamese diacritics removal.
  - Lookup normalization.
  - Code normalization.
  - Lookup tokenization.
- `src/linking/icd10_index.py`
  - Reads `data/terminologies/icd10_byt.csv`.
  - Builds normalized ICD-10 concept/index table.
  - Builds alias table including manual aliases.
- `src/linking/rxnorm_index.py`
  - Reads `data/terminologies/RXNCONSO.RRF`.
  - Filters useful RxNorm rows by config.
  - Parses strength from drug strings.
  - Guesses ingredient and dose form.
  - Builds RxNorm concept/index and alias tables.
- `src/linking/sparse_retriever.py`
  - Builds TF-IDF char n-gram artifacts.
  - Builds BM25 artifacts.
  - Provides sparse alias retrievers returning `MappingCandidate` objects.

Implemented scripts:

- `scripts/build_icd10_index.py`
- `scripts/build_rxnorm_index.py`
- `scripts/build_all_indices.py`
- `scripts/run_phase1_smoke.py`

Expected/generated processed resources:

- `data/processed/icd10_index.parquet`
- `data/processed/icd10_aliases.parquet`
- `data/processed/rxnorm_index.parquet`
- `data/processed/rxnorm_aliases.parquet`
- `data/processed/vector_indices/` sparse artifacts

Smoke check result from current session:

```text
Phase 1 smoke checks passed.
```

Notes:

- Phase 1 currently provides terminology indexing and retrieval primitives, not final linker modules such as `icd10_linker.py` or `rxnorm_linker.py`.
- Dense retrieval and reranking are not implemented yet.

---

## 3.3 Phase 2 — preprocessing, offset mapping, and chunking

**Status:** Implemented and smoke/audit-tested.

Implemented modules:

- `src/preprocess/normalizer.py`
  - Main function: `build_text_views(raw_text, config)`.
  - Produces raw-preserving views:
    - `raw`
    - `normalized`
    - `search`
    - `no_diacritics`
    - `norm_to_raw`
    - `search_to_raw`
    - `no_diacritics_to_raw`
  - Important design decision: no unsafe abbreviation expansion in mapped views, because expansion can break one-to-one offset mapping.
- `src/preprocess/offset_mapper.py`
  - `map_view_span_to_raw(...)`
  - `safe_slice(...)`
  - `assert_raw_span(...)`
  - `repair_span_to_raw_text(...)`
- `src/preprocess/chunker.py`
  - `preprocess_text(raw_text, config)` returns `PreprocessOutput`.
  - `chunk_text(raw_text, config)` splits by line and long-span punctuation/whitespace while preserving raw offsets.

Tests exist for:

- `tests/test_normalizer.py`
- `tests/test_offset_mapper.py`
- `tests/test_chunker.py`

Smoke check result from current session:

```text
Phase 2 smoke checks passed.
files_checked: 6
chunks_created: 189
```

Audit result from `scripts/audit_phase2_phase3.py`:

```text
files_checked: 120
offset_error_count: 0
invalid_section_count: 0
```

This is an important milestone: current preprocessing/chunking did not produce raw offset mismatches across 100 raw files + 20 golden files in the audit.

---

## 3.4 Phase 3 — section detection

**Status:** Implemented and smoke/audit-tested.

Implemented files:

- `configs/section_patterns.yaml`
  - Pattern config for section labels such as:
    - `PAST_HISTORY`
    - `PAST_MEDICAL_HISTORY`
    - `PRE_ADMISSION_MEDICATION`
    - `CURRENT_ILLNESS`
    - `ADMISSION_REASON`
    - `CURRENT_SYMPTOM`
    - `SYMPTOM_CHARACTERISTIC`
    - `PRE_ADMISSION_EVENT`
    - `HOSPITAL_ASSESSMENT`
    - `PHYSICAL_EXAM`
    - `LAB_RESULT`
    - `IMAGING_RESULT`
    - `PROCEDURE`
    - `TREATMENT`
    - `DIAGNOSIS_FINDING`
    - `UNKNOWN`
- `src/section/section_detector.py`
  - `SECTION_LABELS`
  - `SectionMatch`
  - `SectionDetector`
  - `load_section_patterns(...)`
  - `detect_sections(...)`
  - Heading normalization/matching helpers.
- `src/section/__init__.py`
- `scripts/run_phase3_smoke.py`
- `scripts/audit_phase2_phase3.py`
- `tests/test_section_detector.py`

Current section detector behavior:

- Detects exact heading matches.
- Detects no-diacritics heading matches.
- Detects contained/inline headings before `:`.
- Strips common bullet/numbering markers.
- Carries the current section forward to subsequent chunks.
- Assigns:
  - `chunk.section`
  - `chunk.section_confidence`
  - `chunk.section_source`
  - optional `chunk.subsection`

Smoke check result from current session:

```text
Phase 3 smoke checks passed.
files_checked: 6
chunks_checked: 189
section_counts: {
  'ADMISSION_REASON': 4,
  'CURRENT_ILLNESS': 17,
  'CURRENT_SYMPTOM': 31,
  'DIAGNOSIS_FINDING': 8,
  'HOSPITAL_ASSESSMENT': 10,
  'IMAGING_RESULT': 9,
  'LAB_RESULT': 12,
  'PAST_HISTORY': 34,
  'PHYSICAL_EXAM': 3,
  'PRE_ADMISSION_EVENT': 25,
  'PRE_ADMISSION_MEDICATION': 11,
  'PROCEDURE': 8,
  'SYMPTOM_CHARACTERISTIC': 17
}
```

Full Phase 2/3 audit result:

```text
Phase 2/3 audit completed.
files_checked: 120
offset_error_count: 0
invalid_section_count: 0
section_counts: {
  'ADMISSION_REASON': 228,
  'CURRENT_ILLNESS': 312,
  'CURRENT_SYMPTOM': 499,
  'DIAGNOSIS_FINDING': 119,
  'HOSPITAL_ASSESSMENT': 109,
  'IMAGING_RESULT': 156,
  'LAB_RESULT': 211,
  'PAST_HISTORY': 576,
  'PAST_MEDICAL_HISTORY': 6,
  'PHYSICAL_EXAM': 118,
  'PRE_ADMISSION_EVENT': 420,
  'PRE_ADMISSION_MEDICATION': 101,
  'PROCEDURE': 150,
  'SYMPTOM_CHARACTERISTIC': 467,
  'TREATMENT': 48,
  'UNKNOWN': 8
}
source_counts: {
  'carry_forward': 2104,
  'containment_heading': 123,
  'containment_inline': 44,
  'containment_no_diacritics_heading': 3,
  'default': 8,
  'exact_heading': 675,
  'exact_inline': 571
}
unmatched_heading_like_count: 1165
report: outputs/reports/phase2_phase3_audit/summary.json
```

Interpretation of `unmatched_heading_like_count`:

- Many top examples are carried-forward bullet content such as `- mệt mỏi`, `- ho`, `- tăng huyết áp`, etc.
- These are often entity/list items under an already detected section, not necessarily missing headings.
- Keep this audit useful for future improvements, but do not treat all unmatched heading-like rows as section detector failures.

Important caveat:

- Section detection must remain a **feature/prior only**. Do not hardcode entity type or assertion purely from section labels.

---

## 3.5 Phase 4 — span extraction baseline

**Status:** Baseline implemented and smoke-tested.

Phase 4 adds the first extractor layer. It does **not** yet produce final competition JSON and does **not** yet perform final type resolution, assertion detection, overlap merging, or ICD/RxNorm final candidate selection. Its purpose is to emit raw-offset-safe `SpanCandidate` objects for later phases.

Implemented files:

- `configs/entity_rules.yaml`
  - Initial configurable rule lists for drug units/routes/frequencies, qualitative lab results, symptom heads, and disease heads.
- `data/dictionaries/lab_tests.csv`
  - Baseline lab/test aliases such as WBC, RBC, HGB, kali, creatinine, troponin, INR, UA, cấy máu, cấy nước tiểu.
- `data/dictionaries/symptoms_vi.csv`
  - Baseline symptom aliases such as đau bụng, đau ngực, khó thở, ho, sốt, buồn nôn, chóng mặt, mệt mỏi, phù, mất ngủ, lú lẫn.
- `src/extractors/base.py`
  - `ExtractionContext` and `BaseExtractor` interface.
- `src/extractors/utils.py`
  - Shared candidate construction, raw-span validation, context extraction, candidate deduplication, phrase matching, punctuation trimming, and overlap helpers.
- `src/extractors/dictionary_extractor.py`
  - Conservative exact/no-diacritics dictionary matcher.
  - Includes a guard against short accent-insensitive false positives such as matching `phù` to `phụ`.
- `src/extractors/drug_extractor.py`
  - RxNorm/manual alias based baseline drug extractor.
  - Expands adjacent dose/route/frequency tokens.
  - Handles sentence-final punctuation during alias prefiltering, e.g. `atenolol.`.
- `src/extractors/lab_extractor.py`
  - Lab/test alias matcher.
  - Emits separate `TÊN_XÉT_NGHIỆM` and `KẾT_QUẢ_XÉT_NGHIỆM` candidates when simple value/result patterns are found.
- `src/extractors/imaging_extractor.py`
  - Baseline cận lâm sàng/imaging test extractor for X-ray, CT, MRI, ultrasound, ECG/EKG, Holter, xạ hình, etc.
- `src/extractors/problem_extractor.py`
  - Baseline symptom/disease-head extractor emitting provisional `TRIỆU_CHỨNG` or `CHẨN_ĐOÁN` candidates.
- `src/extractors/ner_extractor.py`
  - No-op NER extractor interface, disabled by default.
- `src/extractors/__init__.py`
  - `build_default_extractors(config)` factory.
- `scripts/run_phase4_smoke.py`
  - Runs preprocess → section detection → enabled extractors → raw-offset validation.
- Tests added:
  - `tests/test_extractor_base.py`
  - `tests/test_dictionary_extractor.py`
  - `tests/test_drug_extractor.py`
  - `tests/test_lab_extractor.py`
  - `tests/test_imaging_extractor.py`
  - `tests/test_problem_extractor.py`

Manual validation in the current environment:

```text
python -m compileall -q src\extractors scripts\run_phase4_smoke.py
manual_tests_passed 11
```

`pytest` is still unavailable in the active interpreter, so extractor tests were executed manually by importing each `test_*` function.

Phase 4 smoke on 20 golden input files:

```text
Phase 4 smoke checks completed.
files_checked: 20
chunks_checked: 700
total_candidates: 667
candidate_count_by_source: {
  'dictionary': 134,
  'drug_rule': 23,
  'imaging_rule': 55,
  'lab_result_rule': 5,
  'lab_rule': 15,
  'problem_rule': 435
}
candidate_count_by_raw_type: {
  'CHẨN_ĐOÁN': 212,
  'KẾT_QUẢ_XÉT_NGHIỆM': 5,
  'THUỐC': 23,
  'TRIỆU_CHỨNG': 357,
  'TÊN_XÉT_NGHIỆM': 70
}
offset_error_count: 0
```

Known Phase 4 caveats:

- This is a **candidate generation baseline**, not a calibrated final extractor.
- `problem_extractor.py` is intentionally recall-oriented and can over-extend spans; Phase 5 type resolution and Phase 10 merge/postprocess must clean this up.
- `drug_extractor.py` uses broad RxNorm aliases and may detect non-medication substance mentions such as `caffeine`; later type/context filtering should reduce false positives.
- `imaging_extractor.py` can over-extend imaging test spans, e.g. including temporal/context words after `siêu âm tim`; later span-boundary tuning is needed.
- `lab_extractor.py` currently catches only simple adjacent result patterns; recall for lab values remains low.
- No final entity merging/deduplication across extractor sources yet.

---

## 3.6 Phase 5 — type resolution

**Status:** Implemented and smoke-tested.

Phase 5 converts exact-offset-safe `SpanCandidate` objects from Phase 4 into provisional `FinalEntity` objects. It remains intentionally narrow: it does **not** assign assertions, does **not** call ICD/RxNorm linkers, does **not** perform global overlap merge, and does **not** format final competition JSON.

Implemented files:

- `src/type_resolution/features.py`
  - `TypeFeatures` deterministic feature dataclass.
  - Feature builders for source, raw type, section, score, lab/drug/imaging evidence, drug context, symptom heads, disease heads, dictionary symptom evidence, and placeholder linkability scores.
  - Explicit helper functions:
    - `build_type_features(...)`
    - `has_drug_context(...)`
    - `has_symptom_head(...)`
    - `has_disease_head(...)`
- `src/type_resolution/resolver.py`
  - `TypeResolver` deterministic resolver.
  - `ResolvedCandidate`, `TypeConflict`, and `TypeOverlap` debug dataclasses.
  - Exact same-span grouping and single provisional entity selection.
  - Explicit type priority and source priority.
  - Same-type exact-span duplicate tracking separate from true type conflicts.
  - Non-destructive overlap logging for Phase 10 analysis.
  - Provenance payloads with chosen source, confidence, priorities, type features, warnings, and all exact-span source candidates.
- `src/type_resolution/__init__.py`
- `tests/test_type_resolver.py`
- `scripts/run_phase5_smoke.py`
  - Runs preprocess → section detection → Phase 4 extractors → Phase 5 resolver.
  - Prints entity counts, true type conflicts, same-type exact duplicates, overlaps, unresolved count, offset errors, and sample entities/debug records.
- `configs/default.yaml`
  - `project.phase: phase_5_type_resolution`.
  - `type_resolution` config with source priorities, type priorities, confidence defaults, and flags.

Current resolver policy:

1. `lab_result_rule` → `KẾT_QUẢ_XÉT_NGHIỆM`.
2. `lab_rule` / `imaging_rule` → `TÊN_XÉT_NGHIỆM`.
3. `drug_rule` → `THUỐC` with lower confidence and `drug_without_context` warning if no medication context is detected.
4. `dictionary` with valid schema type → its raw type.
5. `problem_rule` disease-head evidence → `CHẨN_ĐOÁN`.
6. `problem_rule` symptom-head evidence → `TRIỆU_CHỨNG`.
7. `ner` fallback if enabled later.
8. Controlled fallback to valid raw type; otherwise unresolved log entry.

Exact same-span selection sorts by:

1. type priority;
2. confidence;
3. original candidate score;
4. source priority.

Important Phase 5 design choices:

- Phase 5 does not call ICD/RxNorm retrieval and does not let linkability override type decisions.
- `raw_type` is preserved and can be used as fallback evidence, but high-priority lab/drug evidence is source-driven.
- Same-type duplicate exact spans are not counted as `TypeConflict`; they are retained in provenance and counted as `duplicate_exact_span_count`.
- Different-span overlaps are kept in output and logged as `TypeOverlap` for later Phase 10 merge/postprocess work.

Manual validation in the current environment:

```text
python -m compileall -q src\type_resolution scripts\run_phase5_smoke.py
manual_type_resolver_tests_passed 10
```

`pytest` remains unavailable in the active interpreter, so resolver tests were also executed manually by importing each `test_*` function.

Phase 5 smoke on 20 golden input files:

```text
Phase 5 smoke checks completed.
files_checked: 20
chunks_checked: 700
span_candidates: 667
final_entities: 610
candidate_count_by_source: {
  'dictionary': 134,
  'drug_rule': 23,
  'imaging_rule': 55,
  'lab_result_rule': 5,
  'lab_rule': 15,
  'problem_rule': 435
}
entities_by_type: {
  'CHẨN_ĐOÁN': 212,
  'KẾT_QUẢ_XÉT_NGHIỆM': 5,
  'THUỐC': 23,
  'TRIỆU_CHỨNG': 300,
  'TÊN_XÉT_NGHIỆM': 70
}
conflict_count: 0
duplicate_exact_span_count: 57
overlap_count: 93
unresolved_count: 0
offset_error_count: 0
```

Known Phase 5 caveats:

- Many Phase 4 problem spans are still over-extended. Phase 5 preserves them because global span selection and trimming belong mostly to Phase 10.
- Overlap logs show expected cases like dictionary symptom spans contained in longer problem-rule spans.
- Some Phase 4 false positives remain, e.g. broad drug aliases such as `caffeine`; Phase 5 keeps these with confidence/provenance warnings rather than dropping them prematurely.
- No ICD/RxNorm linker wrappers, final postprocess merge, or JSON formatter/validator has been implemented yet.

---

## 3.7 Phase 6 — assertion detection

**Status:** Implemented and smoke-tested.

Phase 6 consumes Phase 5 provisional `FinalEntity` objects and assigns mention-level assertions for assertable entity types only:

```text
TRIỆU_CHỨNG / CHẨN_ĐOÁN / THUỐC
→ isNegated / isHistorical / isFamily
```

It does **not** change spans, does **not** assign assertions to lab/test/result entities, does **not** run ICD/RxNorm linking, and does **not** perform global merge or final JSON formatting.

Implemented files:

- `configs/assertion_rules.yaml`
  - Config-driven cue lists for:
    - pre/post negation;
    - pseudo-negation;
    - scope terminators;
    - historical cues;
    - current-event overrides;
    - family members;
    - family experiencer verbs;
    - reporter verbs.
- `src/assertion/context_rules.py`
  - Shared assertion dataclasses and utilities:
    - `ContextWindow`
    - `CueMatch`
    - `AssertionEvidence`
    - `AssertionDecision`
  - Cue loading/config merging.
  - Context-window extraction and cue matching with raw offsets.
- `src/assertion/negation.py`
  - Pre-negation scope detection.
  - Post-negation cue detection.
  - Pseudo-negation guard, e.g. `không loại trừ viêm phổi` is not treated as negated.
- `src/assertion/historical.py`
  - Historical cue detection.
  - Current-event override handling.
  - Drug-specific historical handling for pre-admission/home-med contexts.
  - Guard so drug/pre-admission medication headings do not automatically mark non-drug problems historical.
- `src/assertion/family.py`
  - High-precision family experiencer detection.
  - Reporter guard for cases like `vợ nhận thấy bệnh nhân ảo giác`.
- `src/assertion/assertion_detector.py`
  - Main `AssertionDetector` class.
  - Applies assertion detectors only to assertable types.
  - Returns new `FinalEntity` objects with updated `assertions` and `provenance["assertion"]`.
- `src/assertion/__init__.py`
- `tests/test_assertion.py`
- `scripts/run_phase6_smoke.py`
  - Runs preprocess → section detection → Phase 4 extractors → Phase 5 type resolver → Phase 6 assertion detector.
  - Prints entity counts, assertable entity count, assertion counts, asserted entities by type, offset errors, and sample asserted entities with evidence.
- `configs/default.yaml`
  - `project.phase: phase_6_assertion_detection`.
  - `assertion_detection` config with rules config path, assertable types, context windows, thresholds, and section priors.

Current assertion policy:

1. Assertions are applied only to `TRIỆU_CHỨNG`, `CHẨN_ĐOÁN`, and `THUỐC`.
2. `isNegated` uses cue + scope, including list scopes.
3. `isHistorical` uses nearby temporal/status cues, current-event overrides, and cautious drug-specific section priors.
4. `isFamily` requires family-member experiencer evidence and avoids reporter-only contexts.
5. Assertion evidence and scores are stored in provenance for debugging.

Manual validation in the current environment:

```text
python -m compileall -q src\assertion scripts\run_phase6_smoke.py
manual_assertion_tests_passed 12
```

`pytest` remains unavailable in the active interpreter, so assertion tests were executed manually by importing each `test_*` function.

Phase 6 smoke on 20 golden input files:

```text
Phase 6 smoke checks completed.
files_checked: 20
chunks_checked: 700
span_candidates: 667
final_entities: 610
assertable_entities: 535
entities_by_type: {
  'CHẨN_ĐOÁN': 212,
  'KẾT_QUẢ_XÉT_NGHIỆM': 5,
  'THUỐC': 23,
  'TRIỆU_CHỨNG': 300,
  'TÊN_XÉT_NGHIỆM': 70
}
assertion_counts: {
  'isFamily': 6,
  'isHistorical': 104,
  'isNegated': 144
}
asserted_entities_by_type: {
  'CHẨN_ĐOÁN': 101,
  'THUỐC': 13,
  'TRIỆU_CHỨNG': 140
}
offset_error_count: 0
```

Known Phase 6 caveats:

- Assertion quality is a deterministic baseline and has not yet been calibrated against official/golden metrics.
- Some assertion false positives can come from Phase 4 over-extended or false-positive spans; Phase 10 merge/postprocess and later golden evaluation will help reduce these.
- Historical detection is intentionally conservative for drug-specific cues, but generic cues like `tiền sử` can still apply within a local window and may need tuning after evaluator/UI review.
- Family detection is high precision but likely lower recall until more family experiencer patterns are added.

---

## 4. Validation and command reference

### 4.1 Commands that passed in this project state

Use Windows `cmd` command chaining in this environment instead of relying on PowerShell `&&` behavior:

```cmd
cmd /d /s /c "python scripts\check_setup.py --config configs\default.yaml & python scripts\run_phase1_smoke.py --config configs\default.yaml & python scripts\run_phase2_smoke.py --config configs\default.yaml --max-files 6 & python scripts\run_phase3_smoke.py --config configs\default.yaml --max-files 6 & python scripts\audit_phase2_phase3.py --config configs\default.yaml --max-unmatched 20"
```

Observed output summary:

```text
Phase 0 setup check passed.
raw_input_files: 100
golden_pairs: 20
golden_entities: 370

Phase 1 smoke checks passed.

Phase 2 smoke checks passed.
files_checked: 6
chunks_created: 189

Phase 3 smoke checks passed.
files_checked: 6
chunks_checked: 189

Phase 2/3 audit completed.
files_checked: 120
offset_error_count: 0
invalid_section_count: 0
```

Phase 4 validation commands passed:

```cmd
python -m compileall -q src\extractors scripts\run_phase4_smoke.py
python scripts\run_phase4_smoke.py --config configs\default.yaml --max-files 20 --sample-limit 5
```

Manual extractor tests passed by importing and running each `test_*` function because `pytest` is not installed in the active interpreter:

```text
manual_tests_passed 11
```

Phase 5 validation commands passed:

```cmd
python -m compileall -q src\type_resolution scripts\run_phase5_smoke.py
python scripts\run_phase5_smoke.py --config configs\default.yaml --max-files 20 --sample-limit 10
```

Manual type resolver tests passed by importing and running each `test_*` function because `pytest` is not installed in the active interpreter:

```text
manual_type_resolver_tests_passed 10
```

Phase 6 validation commands passed:

```cmd
python -m compileall -q src\assertion scripts\run_phase6_smoke.py
python scripts\run_phase6_smoke.py --config configs\default.yaml --max-files 20 --sample-limit 10
```

Manual assertion tests passed by importing and running each `test_*` function because `pytest` is not installed in the active interpreter:

```text
manual_assertion_tests_passed 12
```

### 4.2 Pytest status

Attempted command:

```cmd
python -m pytest -q
```

Current result:

```text
C:\Program Files\Python313\python.exe: No module named pytest
```

`pytest` is listed in `requirements.txt`, so this is an environment/setup issue, not necessarily a test failure. Before relying on tests in a new session, install dependencies in the intended environment:

```cmd
python -m pip install -r requirements.txt
python -m pytest -q
```

If multiple Python installations/venvs exist, ensure the same interpreter is used for dependency installation and test execution.

---

## 5. Known limitations as of Phase 6

The repo is still not an end-to-end competition output system.

Implemented now:

- extractor interfaces and baseline candidate generators under `src/extractors/`;
- baseline dictionaries and entity rules;
- Phase 4 smoke script;
- extractor tests.
- deterministic Phase 5 type-resolution package under `src/type_resolution/`;
- Phase 5 smoke script;
- type resolver tests.
- deterministic Phase 6 assertion-detection package under `src/assertion/`;
- Phase 6 smoke script;
- assertion tests.

Not implemented yet:

- final `src/linking/icd10_linker.py` / `rxnorm_linker.py` wrappers
- deterministic reranker module beyond sparse retrieval primitives
- `src/postprocess/`
  - merge overlap
  - calibration
  - output formatter
- `src/validation/`
  - schema validator
  - offset validator for predictions
  - evaluator/diff
- `src/pipeline.py`
- inference CLI for final predictions:
  - `scripts/run_inference.py`
- final validation CLI:
  - `scripts/run_validate.py`
- golden evaluator:
  - `scripts/run_eval_golden.py`
- submission zipper:
  - `scripts/make_submission_zip.py`
- Streamlit validation UI beyond placeholder folder:
  - `streamlit_app/.gitkeep` exists, but no app yet.

Current code can produce Phase 4 `SpanCandidate` objects, Phase 5 provisional typed `FinalEntity` objects, and Phase 6 asserted `FinalEntity` objects. It still does **not** produce final competition-format `output/{id}.json` predictions.

---

## 6. Current git/worktree notes

Phase 4 added/modified a significant set of files:

```text
configs/default.yaml
configs/paths.yaml
configs/entity_rules.yaml
data/dictionaries/lab_tests.csv
data/dictionaries/symptoms_vi.csv
src/extractors/
scripts/run_phase4_smoke.py
tests/test_extractor_base.py
tests/test_dictionary_extractor.py
tests/test_drug_extractor.py
tests/test_lab_extractor.py
tests/test_imaging_extractor.py
tests/test_problem_extractor.py
PROGRESS.md
```

Phase 5 added/modified:

```text
configs/default.yaml
src/type_resolution/__init__.py
src/type_resolution/features.py
src/type_resolution/resolver.py
scripts/run_phase5_smoke.py
tests/test_type_resolver.py
PROGRESS.md
```

Phase 6 added/modified:

```text
configs/default.yaml
configs/assertion_rules.yaml
src/assertion/__init__.py
src/assertion/context_rules.py
src/assertion/negation.py
src/assertion/historical.py
src/assertion/family.py
src/assertion/assertion_detector.py
scripts/run_phase6_smoke.py
tests/test_assertion.py
PROGRESS.md
```

Before starting major Phase 7/8 work, consider:

1. Install dependencies and run full `pytest`.
2. Review/commit Phase 4–6 changes.
3. Update `README.md` to reflect Phase 1–6 progress.

---

## 7. Recommended next work: Phase 7/8 ICD-10 and RxNorm linker wrappers

The next implementation target should be **Phase 7/8 — ICD-10 and RxNorm candidate generation**, following `Implementation Plan.md` sections 13 and 14.

Goal: attach candidate codes to asserted Phase 6 `FinalEntity` objects without inventing spans or changing entity offsets.

Recommended Phase 7/8 deliverables:

```text
src/linking/
  icd10_linker.py
  rxnorm_linker.py
  drug_parser.py
  candidate_selector.py
tests/test_linking.py
tests/test_icd10_linker.py
tests/test_rxnorm_linker.py
scripts/run_phase7_8_smoke.py
```

Initial linking policy:

1. Link only `CHẨN_ĐOÁN` to ICD-10.
2. Link only `THUỐC` to RxNorm.
3. Use existing Phase 1 sparse retrieval resources.
4. Keep candidate count conservative.
5. Do not return codes outside terminology indices.
6. Preserve linker scores/provenance for later reranking and error analysis.

Important Phase 7/8 caveats:

- Linking must not create, remove, or move spans.
- ICD/RxNorm linkability must not retroactively override Phase 5 type resolution in this phase.
- Candidate precision matters; avoid arbitrary top-k output.

---

## 8. Later phases after extraction baseline

After Phase 6, continue in this order:

1. **Phase 7/8 — ICD-10 and RxNorm linker wrappers**
   - Use existing Phase 1 sparse retrievers.
   - Add candidate thresholding and score logs.
2. **Phase 9 — Reranking**
   - Start deterministic; dense/cross-encoder later if needed.
3. **Phase 10 — Merge and postprocess**
   - Deduplication and overlap conflict handling.
4. **Phase 11 — JSON formatter and validator**
   - Enforce schema and `raw_text[start:end] == text`.
5. **Phase 12 — Golden evaluation loop**
   - Validate 20 gold files.
   - Run approximate local metrics and error reports.
6. **Phase 13 — Streamlit UI**
   - Highlight raw text, predictions, gold, and diffs.
7. **Phase 14/15 — NER and dense retrieval/reranker**
   - Only after rule baseline and evaluator are stable.
8. **Phase 16/17 — End-to-end inference and packaging**
   - `run_inference.py`, `run_validate.py`, `make_submission_zip.py`, README rebuild instructions.

---

## 8A. Phase 7 — ICD-10 candidate generation

**Status:** Implemented and smoke-tested on a small golden subset.

Phase 7 consumes Phase 6 asserted `FinalEntity` objects and attaches ICD-10 candidates only to diagnosis entities:

```text
CHẨN_ĐOÁN → ICD-10 candidates
```

It does **not** create spans, move offsets, change entity type, alter assertions, or link non-diagnosis entities.

Implemented files:

- `src/linking/candidate_selector.py`
  - Conservative candidate-selection utility.
  - Deduplicates by code.
  - Keeps top-1 only above threshold.
  - Adds extra candidates only for near-ties or high-confidence candidates.
- `src/linking/icd10_linker.py`
  - Main `ICD10Linker` class.
  - Loads existing Phase 1 ICD-10 processed artifacts.
  - Uses exact alias lookup, TF-IDF sparse retrieval, and BM25 sparse retrieval.
  - Normalizes diagnosis query variants and expands a small high-precision abbreviation map (`GERD`, `COPD`, `UTI`, `MI`).
  - Filters sparse candidates by query/alias lexical similarity to reduce generic noisy matches.
  - Caches per-mention candidate generation inside one linker instance.
  - Writes selected and top-candidate evidence to `provenance["icd10_linking"]`.
- `src/linking/__init__.py`
  - Exports selector utilities and lazily exposes `ICD10Linker` to avoid forcing pandas imports for selector-only tests.
- `tests/test_candidate_selector.py`
- `tests/test_icd10_linker.py`
- `scripts/run_phase7_smoke.py`
  - Runs preprocess → section detection → extractors → type resolver → assertion detector → ICD-10 linker.
  - Validates unchanged offsets, unchanged entity fields, valid ICD codes, and no non-diagnosis candidates.
- `configs/default.yaml`
  - `project.phase: phase_7_icd10_candidate_generation`.
  - Added `icd10_linking` config.

Current default Phase 7 candidate policy:

```yaml
icd10_linking:
  selection:
    max_candidates: 3
    min_score_top1: 0.65
    include_second_if_within: 0.05
    min_score_additional: 0.70
    min_retrieval_similarity: 0.55
    min_sparse_query_tokens: 2
    min_sparse_query_chars: 6
```

Validation commands run in this environment required UTF-8 mode because the active Python executable path contains Vietnamese characters:

```cmd
set PYTHONUTF8=1
python -m compileall -q src\linking scripts\run_phase7_smoke.py
python -m pytest -q tests\test_candidate_selector.py tests\test_icd10_linker.py
python scripts\run_phase1_smoke.py --config configs\default.yaml
python scripts\run_phase6_smoke.py --config configs\default.yaml --max-files 5 --sample-limit 5
python scripts\run_phase7_smoke.py --config configs\default.yaml --max-files 2 --sample-limit 10
```

Observed validation results:

```text
10 passed in 2.54s

Phase 1 smoke checks passed.

Phase 6 smoke checks completed.
files_checked: 5
offset_error_count: 0

Phase 7 smoke checks completed.
files_checked: 2
chunks_checked: 74
span_candidates: 64
final_entities: 57
diagnosis_entities: 17
diagnosis_with_candidates: 1
offset_error_count: 0
mutation_error_count: 0
invalid_candidate_error_count: 0
non_diagnosis_candidate_error_count: 0
```

Known Phase 7 caveats:

- Candidate quality is still a deterministic sparse baseline and is not calibrated against official metrics.
- Some noisy diagnosis spans from Phase 5 can still receive ICD candidates when lexical overlap is strong enough; Phase 10 merge/postprocess and Phase 12 evaluation should handle further calibration.
- A 5-file Phase 7 smoke attempt exceeded the 30-second tool timeout in the current environment, although the 2-file smoke passed all integrity checks.
- Existing sparse vectorizer artifacts were built with scikit-learn 1.8.0 and loaded under 1.9.0 in this environment, producing `InconsistentVersionWarning`; Phase 1 smoke still passed.

Recommended next work: **Phase 8 — RxNorm candidate generation for `THUỐC` entities**, following the same linker wrapper pattern and conservative candidate selector.

---

## 8B. Phase 8 — RxNorm candidate generation

**Status:** Implemented and smoke-tested on a small golden subset.

Phase 8 consumes Phase 7/Phase 6 `FinalEntity` objects and attaches RxNorm RxCUI candidates only to drug entities:

```text
THUỐC → RxNorm candidates
```

It does **not** create spans, move offsets, change entity type, alter assertions, or link non-drug entities. The Phase 8 smoke script runs both Phase 7 ICD-10 linking and Phase 8 RxNorm linking so the current linked baseline can be checked end-to-end through both terminology wrappers.

Implemented files:

- `src/linking/drug_parser.py`
  - Adds `ParsedDrug` and `parse_drug_mention(...)`.
  - Extracts normalized drug name, strength value/unit, route, frequency, dose form, and simple combination marker.
  - Reuses existing `parse_strength(...)` from `src/linking/rxnorm_index.py`.
- `src/linking/rxnorm_linker.py`
  - Main `RxNormLinker` class.
  - Loads existing Phase 1 RxNorm processed artifacts.
  - Uses exact alias lookup, ingredient+strength matching, TF-IDF sparse retrieval, and BM25 sparse retrieval.
  - Applies deterministic constraints/boosts for strength match, unit match, TTY preference, manual brand aliases, and name-only mentions.
  - Suppresses ingredient-only/no-strength candidates when a strength-bearing mention has matching-strength candidate evidence.
  - Caches per-mention candidate generation inside one linker instance.
  - Writes parsed slots and candidate evidence to `provenance["rxnorm_linking"]`.
- `src/linking/__init__.py`
  - Lazily exposes `ParsedDrug`, `parse_drug_mention`, and `RxNormLinker`.
- `tests/test_drug_parser.py`
- `tests/test_rxnorm_linker.py`
- `scripts/run_phase8_smoke.py`
  - Runs preprocess → section detection → extractors → type resolver → assertion detector → ICD10Linker → RxNormLinker.
  - Validates unchanged offsets, unchanged entity fields, valid ICD codes, valid RxCUIs, and no wrong-type candidates.
- `configs/default.yaml`
  - `project.phase: phase_8_rxnorm_candidate_generation`.
  - Added `rxnorm_linking` config.

Current default Phase 8 candidate policy:

```yaml
rxnorm_linking:
  selection:
    max_candidates: 2
    min_score_top1: 0.60
    include_second_if_within: 0.04
    min_score_additional: 0.75
    min_retrieval_similarity: 0.55
    min_sparse_query_chars: 3
```

Validation commands run in this environment required UTF-8 mode because the active Python executable path contains Vietnamese characters:

```cmd
set PYTHONUTF8=1
python -m compileall -q src\linking scripts\run_phase8_smoke.py
python -m pytest -q tests\test_drug_parser.py tests\test_rxnorm_linker.py
python -m pytest -q tests\test_candidate_selector.py tests\test_icd10_linker.py tests\test_rxnorm_index.py tests\test_sparse_retriever.py
python scripts\run_phase1_smoke.py --config configs\default.yaml
python scripts\run_phase6_smoke.py --config configs\default.yaml --max-files 5 --sample-limit 5
python scripts\run_phase7_smoke.py --config configs\default.yaml --max-files 2 --sample-limit 5
python scripts\run_phase8_smoke.py --config configs\default.yaml --max-files 2 --sample-limit 10
```

Observed validation results:

```text
9 passed in 2.04s
18 passed, 4 warnings in 2.24s

Phase 1 smoke checks passed.

Phase 6 smoke checks completed.
files_checked: 5
offset_error_count: 0

Phase 7 smoke checks completed.
files_checked: 2
offset_error_count: 0
mutation_error_count: 0
invalid_candidate_error_count: 0
non_diagnosis_candidate_error_count: 0

Phase 8 smoke checks completed.
files_checked: 2
chunks_checked: 74
span_candidates: 64
final_entities: 57
diagnosis_entities: 17
diagnosis_with_icd_candidates: 1
drug_entities: 4
drug_with_rxnorm_candidates: 4
offset_error_count: 0
mutation_error_count: 0
invalid_icd_candidate_error_count: 0
invalid_rxnorm_candidate_error_count: 0
wrong_type_candidate_error_count: 0
```

Known Phase 8 caveats:

- Candidate quality is still deterministic and not calibrated against official/golden metrics.
- Phase 8 can only link drug spans produced by the existing extractor/type resolver; false-positive drug spans such as broad `caffeine` mentions may still receive exact RxNorm candidates.
- Some strength-bearing mentions may still select ingredient-level candidates when the available RxNorm aliases/retrieval evidence does not surface the clinical-drug candidate strongly enough; this should be revisited in Phase 9 reranking and Phase 12 evaluation.
- Combination drug handling is conservative and only detects obvious separators/markers in the baseline parser.
- Existing sparse vectorizer artifacts were built with scikit-learn 1.8.0 and loaded under 1.9.0 in this environment, producing `InconsistentVersionWarning`; Phase 1/7/8 smoke checks still passed.

Recommended next work: **Phase 9 — deterministic candidate reranking/calibration**, followed by Phase 10 merge/postprocess.

---

## 9. Practical continuation checklist

For a future session, start here:

```cmd
python -m pip install -r requirements.txt
python -m pytest -q
python scripts\check_setup.py --config configs\default.yaml
python scripts\run_phase1_smoke.py --config configs\default.yaml
python scripts\run_phase2_smoke.py --config configs\default.yaml --max-files 6
python scripts\run_phase3_smoke.py --config configs\default.yaml --max-files 6
python scripts\audit_phase2_phase3.py --config configs\default.yaml --max-unmatched 20
python scripts\run_phase4_smoke.py --config configs\default.yaml --max-files 20 --sample-limit 5
python scripts\run_phase5_smoke.py --config configs\default.yaml --max-files 20 --sample-limit 5
python scripts\run_phase6_smoke.py --config configs\default.yaml --max-files 20 --sample-limit 5
```

Then proceed with Phase 7/8 ICD-10 and RxNorm linker wrapper implementation.

Before writing new extractor code, keep these invariants visible:

```python
assert raw_text[candidate.start:candidate.end] == candidate.text
assert candidate.start < candidate.end
```

Every resolver/postprocess component should preserve enough provenance to debug later in the Streamlit UI and evaluator.

---

## 10. Summary

The repository currently has a solid foundation through Phase 6:

- canonical config/data layout;
- ICD/RxNorm terminology parsing and sparse retrieval artifacts;
- raw-preserving preprocessing and offset maps;
- chunking with zero observed audit offset errors;
- section detection with pattern config, carry-forward behavior, smoke tests, and audit reporting.
- baseline span extraction package with dictionary, drug, lab, imaging, problem, and no-op NER extractors;
- Phase 4 smoke validation on 20 golden files with zero offset errors.
- deterministic Phase 5 type resolution producing provisional `FinalEntity` objects;
- Phase 5 smoke validation on 20 golden files with zero offset errors.
- deterministic Phase 6 assertion detection producing asserted `FinalEntity` objects;
- Phase 6 smoke validation on 20 golden files with zero offset errors.

The next major milestone is to implement Phase 7/8 linker wrappers, attaching ICD-10 candidates to `CHẨN_ĐOÁN` entities and RxNorm candidates to `THUỐC` entities while preserving offsets and keeping candidate output conservative.