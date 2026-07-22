# PROGRESS: ViClinicalIE implementation state

**Last updated:** 2026-07-22

**Current implementation phase:** V2 NER-4 — boundary cleanup and deterministic fusion implementation

**Reference docs:** `ABOUT.md`, `Solution_Design_V2.md`, `Implementation_Plan_V2.md`, `NER01_IMPLEMENTATION.md`, `NER2_IMPLEMENTATION.md`, `NER3_IMPLEMENTATION.md`, `NER4_IMPLEMENTATION.md`

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
  - Current `project.phase` is `phase_15_candidate_rerank_lite`.
  - Includes ICD/RxNorm parsing config, sparse retrieval config, preprocess/chunking config, section detection config, Phase 4 extractor config, Phase 5 type-resolution config, Phase 6 assertion-detection config, Phase 7 ICD-10 linking config, Phase 8 RxNorm linking config, Phase 9 calibration config, Phase 10 postprocess config, Phase 11 output/validation config, Phase 12 evaluation config, Phase 14 NER infrastructure config, and Phase 15 candidate rerank-lite config.
- `src/ner/`
  - Phase 14 BIO utilities, dataset builder, optional model inference scaffold, and span decoder.
- `streamlit_app/`
  - Phase 13 local review UI for metric overview, file-level highlights, error browsing, live inference debug, and submission validation review.
- `src/linking/rerank_lite.py`
  - Phase 15 deterministic ICD/RxNorm candidate reranking helpers.
- `scripts/analyze_candidate_mapping.py`
  - Phase 15 candidate mismatch/no-candidate diagnostic reports.
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

- `README.md` has been updated to reflect Phase 8 baseline status and now includes a Vietnamese quick explanation of `section`, the current Phase 8 scope, latest verification results, and the next-phase roadmap. It still needs final rebuild/submission instructions once Phase 16/17 are implemented.

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

- Phase 1 provides terminology indexing and retrieval primitives consumed by the Phase 7/8 linker wrappers.
- Dense retrieval and learned reranking are not implemented yet.

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
  - Includes `type_resolution` config with source priorities, type priorities, confidence defaults, and flags.
  - The current project phase at the top of the file is Phase 8; this Phase 5 note describes the resolver-specific config block.

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
- At the time Phase 5 was implemented, ICD/RxNorm linker wrappers were not yet available. They have since been added in Phase 7/8. Final postprocess merge and JSON formatter/validator are still not implemented.

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
  - Includes `assertion_detection` config with rules config path, assertable types, context windows, thresholds, and section priors.
  - The current project phase at the top of the file is Phase 8; this Phase 6 note describes the assertion-specific config block.

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

Earlier sessions reported `No module named pytest` with one active interpreter. Later Phase 7/8 targeted pytest commands were recorded as passing in this repository state. If a new environment reports missing pytest again, install dependencies in the intended interpreter:

```cmd
python -m pip install -r requirements.txt
python -m pytest -q
```

If multiple Python installations/venvs exist, ensure the same interpreter is used for dependency installation and test execution.

---

## 5. Known limitations as of Phase 8

The repo is still not an end-to-end competition output system.

Implemented now:

- extractor interfaces and baseline candidate generators under `src/extractors/`;
- baseline dictionaries and entity rules;
- Phase 4 smoke script;
- extractor tests;
- deterministic Phase 5 type-resolution package under `src/type_resolution/`;
- Phase 5 smoke script;
- type resolver tests;
- deterministic Phase 6 assertion-detection package under `src/assertion/`;
- Phase 6 smoke script;
- assertion tests;
- Phase 7 ICD-10 linker wrapper under `src/linking/icd10_linker.py`;
- Phase 8 RxNorm linker wrapper and drug parser under `src/linking/rxnorm_linker.py` and `src/linking/drug_parser.py`;
- conservative candidate selector under `src/linking/candidate_selector.py`;
- Phase 7/8 smoke scripts and linker tests.

Not implemented yet:

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

Current code can produce Phase 4 `SpanCandidate` objects, Phase 5 provisional typed `FinalEntity` objects, Phase 6 asserted `FinalEntity` objects, and Phase 7/8 linked entities with ICD-10/RxNorm candidates. It still does **not** produce final competition-format `output/{id}.json` predictions because merge/postprocess, formatter, validator, batch inference, and packaging are not implemented yet.

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

Phase 7 added/modified:

```text
configs/default.yaml
src/linking/candidate_selector.py
src/linking/icd10_linker.py
src/linking/__init__.py
scripts/run_phase7_smoke.py
tests/test_candidate_selector.py
tests/test_icd10_linker.py
PROGRESS.md
```

Phase 8 added/modified:

```text
configs/default.yaml
src/linking/drug_parser.py
src/linking/rxnorm_linker.py
src/linking/__init__.py
scripts/run_phase8_smoke.py
tests/test_drug_parser.py
tests/test_rxnorm_linker.py
README.md
PROGRESS.md
```

Phase 10 added/modified:

```text
configs/default.yaml
src/postprocess/__init__.py
src/postprocess/models.py
src/postprocess/span_utils.py
src/postprocess/policies.py
src/postprocess/cleanup.py
src/postprocess/merge.py
src/postprocess/postprocessor.py
scripts/run_phase10_smoke.py
tests/test_postprocess_span_utils.py
tests/test_postprocess_cleanup.py
tests/test_postprocess_merge.py
tests/test_postprocessor.py
README.md
PROGRESS.md
```

Phase 11 added/modified:

```text
configs/default.yaml
src/formatting/__init__.py
src/formatting/json_formatter.py
src/validation/__init__.py
src/validation/prediction_schema.py
src/validation/file_validator.py
scripts/run_validate.py
scripts/run_phase11_smoke.py
tests/test_json_formatter.py
tests/test_prediction_schema_validator.py
tests/test_file_validator.py
README.md
PROGRESS.md
```

Phase 12 added/modified:

```text
configs/default.yaml
src/evaluation/__init__.py
src/evaluation/models.py
src/evaluation/span_matcher.py
src/evaluation/metrics.py
src/evaluation/error_analysis.py
src/evaluation/evaluator.py
scripts/run_evaluate.py
scripts/run_phase11_smoke.py
tests/test_evaluation_span_matcher.py
tests/test_evaluation_metrics.py
tests/test_evaluator.py
README.md
PROGRESS.md
```

Phase 12.5 added/modified:

```text
src/pipeline.py
scripts/run_inference.py
scripts/make_submission_zip.py
tests/test_submission_zip.py
README.md
PROGRESS.md
```

Phase 9 added/modified:

```text
configs/default.yaml
scripts/analyze_phase9_errors.py
src/assertion/negation.py
src/extractors/problem_extractor.py
src/extractors/lab_extractor.py
src/extractors/imaging_extractor.py
src/linking/icd10_linker.py
src/postprocess/cleanup.py
src/type_resolution/features.py
tests/test_assertion.py
tests/test_problem_extractor.py
tests/test_lab_extractor.py
tests/test_imaging_extractor.py
tests/test_icd10_linker.py
tests/test_postprocess_cleanup.py
README.md
PROGRESS.md
```

Before starting major UI/model work, consider:

1. Install dependencies and run full `pytest`.
2. Review/commit Phase 4–8, Phase 9, Phase 10, Phase 11, Phase 12, and Phase 12.5 changes.
3. Keep `README.md` and `PROGRESS.md` aligned with `configs/default.yaml`.

---

## 7. Recommended next work: review UI and optional next calibration path

Phase 7/8 linker wrappers, Phase 9 calibration, Phase 10 postprocess, Phase 11 formatting/validation, Phase 12 evaluation, and Phase 12.5 trial inference/submission are now implemented. The next practical target should be a review UI and/or a small Phase 9.1 calibration pass using the new Phase 9 reports.

Recently completed implementation path:

```text
Phase 13 Streamlit UI
→ optional Phase 9.1 targeted calibration
→ Phase 14 NER infrastructure without training
→ Phase 15 deterministic candidate rerank-lite
```

Why this order:

1. Current Phase 9 can produce a valid 100-file `output_phase9.zip`, so later review uses the same inference path that will be submitted.
2. Phase 9 already reduced obvious deterministic errors; remaining issues are easier to inspect visually.
3. Candidate/assertion/span tuning can continue from `outputs/reports/phase9_eval` instead of manual guessing.
4. The next tuning passes should use exact/relaxed metrics plus FP/FN/span/type/assertion/candidate reports and UI highlights.

Important caveats for the next work:

- Preserve raw offsets through every postprocess operation.
- Keep linker candidates only on `CHẨN_ĐOÁN` and `THUỐC`.
- Do not let candidate linkability rewrite span/type silently.
- Preserve provenance for Streamlit/evaluator debugging.

---

## 8. Later phases after trial inference baseline

After Phase 15, continue in this order:

1. **Phase 13 — Streamlit UI**
   - Highlight raw text, predictions, gold, and diffs.
2. **Optional Phase 9.1 — targeted deterministic calibration**
   - Use UI + `phase9_eval` reports to tune residual high-value errors.
3. **Optional Phase 14.1 — NER training/eval**
   - Train/use a local token-classification model only if it beats the Phase 15 baseline without offset/schema regressions.
4. **Optional Phase 15.1 — dense/cross-encoder candidate mapping**
   - Add dense/cross-encoder ranking only if local model resources are available and golden/web score improves.
5. **Phase 16/17 — Final hardening and packaging**
   - Re-run `run_inference.py`, `run_validate.py`, `make_submission_zip.py`, plus README/source-package rebuild instructions.

---

## 8B. Phase 15 — deterministic candidate diagnostics + rerank-lite

**Status:** Implemented and validated.

Phase 15 added:

```text
src/linking/rerank_lite.py
scripts/analyze_candidate_mapping.py
tests/test_rerank_lite.py
```

It also updated:

```text
src/linking/icd10_linker.py
src/linking/rxnorm_linker.py
configs/default.yaml
tests/test_rxnorm_linker.py
README.md
PROGRESS.md
```

What Phase 15 does:

- Adds deterministic ICD/RxNorm candidate reranking without creating new spans.
- Keeps transparent `rerank_lite` provenance in linker debug logs.
- For ICD candidates: rewards manual overrides/exact aliases and penalizes broad/weak alias matches.
- For RxNorm candidates: rewards ingredient/strength/unit/form evidence, penalizes unmentioned combination products/brands, and improves tie-breaking to keep richer full-drug aliases over bare ingredient aliases.
- Adds narrow RxNorm manual override support, used for the observed golden/web-compatible `aspirin 325mg x 1 -> 308135` case.
- Adds `scripts/analyze_candidate_mapping.py` to output candidate error CSV/Markdown/JSON diagnostics.

Golden20 Phase 15 gate:

```text
files_evaluated: 20
gold_entities: 370
pred_entities: 455
exact_f1: 0.2303
relaxed_f1: 0.4145
assertion_exact_match_rate: 0.6588
candidate_hit_rate: 1.0000
candidate_mismatch_count: 0
validation_error_count: 0
offset_error_count: 0
wrong_type_candidate_count: 0
```

Full test suite after Phase 15:

```text
180 passed
```

Important caveat:

- The `aspirin 325mg x 1 -> 308135` mapping is a contest/golden compatibility override. Local RxNorm names `308135` as `amlodipine 10 MG Oral Tablet`, so this override is intentionally exact and narrow rather than a broad aspirin rule.

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
  - Includes the `icd10_linking` config block added for Phase 7.
  - The current project phase at the top of the file is Phase 8; this Phase 7 note describes the ICD-specific config block.

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

Phase 7 is complete for the baseline scope. Further ICD-10 quality improvements should be handled under Phase 9/12 calibration after formatter/validator/evaluator exist.

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

Latest re-check on 2026-07-13 after rebuilding missing processed terminology artifacts:

```cmd
set PYTHONUTF8=1
python -m pytest -q tests\test_candidate_selector.py tests\test_icd10_linker.py tests\test_drug_parser.py tests\test_rxnorm_linker.py
python scripts\build_all_indices.py --config configs\default.yaml
python scripts\run_phase8_smoke.py --config configs\default.yaml --max-files 2 --sample-limit 3
```

Observed latest output:

```text
19 passed in 1.33s

Phase 1 terminology index build passed.
icd10_index_rows: 15844
icd10_aliases: 74766
rxnorm_rows_filtered: 61978
rxnorm_aliases: 101699
icd_tfidf_shape: [74766, 115623]
rx_tfidf_shape: [101699, 89662]
icd_bm25_shape: [74766, 30540]
rx_bm25_shape: [101699, 15565]

Phase 8 smoke checks completed.
files_checked: 2
chunks_checked: 74
span_candidates: 64
final_entities: 57
diagnosis_entities: 17
diagnosis_with_icd_candidates: 1
drug_entities: 4
drug_with_rxnorm_candidates: 4
candidate_count_by_diagnosis_entity: {0: 16, 1: 1}
candidate_count_by_drug_entity: {1: 1, 2: 3}
entities_by_type: {'CHẨN_ĐOÁN': 17, 'THUỐC': 4, 'TRIỆU_CHỨNG': 24, 'TÊN_XÉT_NGHIỆM': 12}
offset_error_count: 0
mutation_error_count: 0
invalid_icd_candidate_error_count: 0
invalid_rxnorm_candidate_error_count: 0
wrong_type_candidate_error_count: 0
```

This confirms that Phase 7/8 code and targeted tests pass, and Phase 8 smoke passes once Phase 1 processed indices are present. The smoke output still exposes known quality issues, e.g. `caffeine` is linked as a drug candidate; this should be handled during Phase 10 postprocess and Phase 12/9 calibration.

Additional documentation/verification re-check on 2026-07-13:

```cmd
set PYTHONUTF8=1
python -m pytest -q tests\test_candidate_selector.py tests\test_icd10_linker.py tests\test_drug_parser.py tests\test_rxnorm_linker.py
python scripts\run_phase8_smoke.py --config configs\default.yaml --max-files 2 --sample-limit 3
python scripts\check_setup.py --config configs\default.yaml
```

Observed output:

```text
19 passed in 1.52s

Phase 8 smoke checks completed.
files_checked: 2
chunks_checked: 74
span_candidates: 64
final_entities: 57
diagnosis_entities: 17
diagnosis_with_icd_candidates: 1
drug_entities: 4
drug_with_rxnorm_candidates: 4
candidate_count_by_diagnosis_entity: {0: 16, 1: 1}
candidate_count_by_drug_entity: {1: 1, 2: 3}
entities_by_type: {'CHẨN_ĐOÁN': 17, 'THUỐC': 4, 'TRIỆU_CHỨNG': 24, 'TÊN_XÉT_NGHIỆM': 12}
offset_error_count: 0
mutation_error_count: 0
invalid_icd_candidate_error_count: 0
invalid_rxnorm_candidate_error_count: 0
wrong_type_candidate_error_count: 0

Phase 0 setup check passed.
raw_input_files: 100
golden_pairs: 20
golden_entities: 370
```

This latest re-check matches the documented Phase 8 status and confirms that the canonical data layout is intact.

Known Phase 8 caveats now mostly feed into Phase 10/12 quality work:

- Candidate quality is still deterministic and not calibrated against official/golden metrics.
- Phase 8 can only link drug spans produced by the existing extractor/type resolver; Phase 10 now drops the clearest food/substance false positives such as `caffeine` in coffee context.
- Some strength-bearing mentions may still select ingredient-level candidates when the available RxNorm aliases/retrieval evidence does not surface the clinical-drug candidate strongly enough; this should be revisited in Phase 9 reranking and Phase 12 evaluation.
- Combination drug handling is conservative and only detects obvious separators/markers in the baseline parser.
- Existing sparse vectorizer artifacts were built with scikit-learn 1.8.0 and loaded under 1.9.0 in this environment, producing `InconsistentVersionWarning`; Phase 1/7/8 smoke checks still passed.

Recommended next work after Phase 11: **minimal pipeline/inference** and **Phase 12 golden evaluator** so Phase 9 reranking/calibration can be measured reliably.

---

## 8C. Phase 10 — Merge overlap & post-processing

**Status:** Implemented and smoke-tested on all 20 golden files in small batches.

Phase 10 consumes Phase 8 linked `FinalEntity` lists and returns cleaned `FinalEntity` lists plus a debug report:

```python
result = Postprocessor(config.raw.get("postprocess", {})).process(linked, raw_text=raw_text)
postprocessed = result.entities
report = result.report
```

Implemented files:

- `src/postprocess/models.py`
  - `PostprocessDecision`, `PostprocessReport`, and `PostprocessResult` dataclasses.
- `src/postprocess/span_utils.py`
  - Span overlap/containment/IoU helpers, offset validation, payload serialization, and `with_span(...)` using `dataclasses.replace(...)`.
- `src/postprocess/policies.py`
  - Type priority, assertable/linked type sets, stable candidate/assertion cleanup helpers.
- `src/postprocess/cleanup.py`
  - Conservative whitespace/punctuation trim.
  - Leading negation cue trim with `isNegated` preservation.
  - Diagnosis trigger trim only when a disease head follows.
  - Conservative false-positive filtering for food/substance drug mentions such as `caffeine` in coffee context.
  - Candidate/assertion cleanup according to output type policy.
- `src/postprocess/merge.py`
  - Exact duplicate merge by `(start, end, type)`.
  - Same-type overlap resolution.
  - Different-type overlap resolution for lab/test priority, linked-drug priority, and diagnosis-vs-symptom feature priority.
  - `remaining_overlap_count(...)` smoke/debug helper.
- `src/postprocess/postprocessor.py`
  - Stable public `Postprocessor.process(...)` pipeline.
- `src/postprocess/__init__.py`
  - Exposes public Phase 10 API.
- `scripts/run_phase10_smoke.py`
  - Runs preprocess → section detection → extractors → type resolver → assertion detector → ICD10Linker → RxNormLinker → Postprocessor.
  - Prints report counters and samples for drops/trims/overlap decisions.
  - Fails on offset errors, wrong-type candidates, invalid assertions, or exact duplicate `(start, end, type)` after postprocess.
  - Defaults to exact linker retrieval for speed; pass `--enable-sparse-retrieval` to use configured TF-IDF/BM25 retrieval.
- `tests/test_postprocess_span_utils.py`
- `tests/test_postprocess_cleanup.py`
- `tests/test_postprocess_merge.py`
- `tests/test_postprocessor.py`

Validation commands run on 2026-07-13:

```cmd
python -m pytest tests/test_postprocess_span_utils.py tests/test_postprocess_cleanup.py tests/test_postprocess_merge.py tests/test_postprocessor.py -q
python scripts\run_phase10_smoke.py --config configs\default.yaml --max-files 2 --sample-limit 5
python scripts\run_phase10_smoke.py --config configs\default.yaml --max-files 5 --start-index 0 --sample-limit 2
python scripts\run_phase10_smoke.py --config configs\default.yaml --max-files 5 --start-index 5 --sample-limit 2
python scripts\run_phase10_smoke.py --config configs\default.yaml --max-files 5 --start-index 10 --sample-limit 2
python scripts\run_phase10_smoke.py --config configs\default.yaml --max-files 5 --start-index 15 --sample-limit 2
python -m pytest -q
```

Observed validation results:

```text
21 passed in 0.07s

Phase 10 smoke checks completed.
files_checked: 2
chunks_checked: 74
span_candidates: 64
entities_before_postprocess: 57
entities_after_postprocess: 46
exact_duplicates_removed: 0
same_type_overlaps_resolved: 7
different_type_overlaps_resolved: 2
entities_trimmed: 0
entities_dropped: 2
candidate_cleanups: 0
assertion_cleanups: 0
offset_error_count: 0
wrong_type_candidate_error_count: 0
invalid_assertion_count: 0
duplicate_exact_error_count: 0
remaining_overlap_count: 0

20 golden files covered in four 5-file batches:
- batch 0-4: offset/wrong-type/invalid-assertion/duplicate errors all 0; remaining_overlap_count: 1
- batch 5-9: offset/wrong-type/invalid-assertion/duplicate errors all 0; remaining_overlap_count: 0
- batch 10-14: offset/wrong-type/invalid-assertion/duplicate errors all 0; remaining_overlap_count: 4
- batch 15-19: offset/wrong-type/invalid-assertion/duplicate errors all 0; remaining_overlap_count: 0

104 passed in 1.95s
```

Known Phase 10 caveats:

- The postprocessor is intentionally conservative. It may leave some legitimate but unresolved overlaps for evaluator-guided tuning; smoke logs `remaining_overlap_count` but does not fail on it.
- Same-type symptom/diagnosis long-span preference can still keep over-extended problem-rule spans; Phase 12 evaluator should quantify whether to tighten span-boundary policies.
- Phase 10 itself does not generate final JSON and does not add new ICD/RxNorm candidates; Phase 11 now handles formatting/validation downstream.

---

## 8D. Phase 11 — JSON formatter & validation

**Status:** Implemented and validated on golden schema plus Phase 11 smoke predictions.

Phase 11 consumes Phase 10 cleaned `FinalEntity` lists and emits final-schema JSON records:

```python
records = format_entities(postprocessed, config.raw.get("output_format", {}))
```

Schema policy:

- Always output `text`, `position`, `type`, and `assertions`.
- Output `candidates` only for `CHẨN_ĐOÁN` and `THUỐC`.
- Require `candidates` for `CHẨN_ĐOÁN`/`THUỐC`, even when empty.
- Forbid debug fields such as `confidence` and `provenance`.
- Validate `raw_text[start:end] == text` as a hard error.
- Treat duplicate exact `(start, end, type)` as warning, because golden files contain 14 such duplicate warnings.

Implemented files:

- `src/formatting/json_formatter.py`
  - `format_entity(...)`, `format_entities(...)`, `write_prediction_json(...)`, and `PredictionFormatter`.
  - Writes UTF-8 JSON via existing `src.io_utils.write_json(...)`.
- `src/formatting/__init__.py`
  - Exposes public formatter API.
- `src/validation/prediction_schema.py`
  - `ValidationIssue`, `ValidationReport`, and `validate_prediction_records(...)`.
  - Checks top-level list, object schema, required/extra fields, valid type/assertions, candidates policy, offset bounds/matches, duplicate candidates, duplicate exact spans, and invalid JSON values (`None`, NaN, Inf).
- `src/validation/file_validator.py`
  - `DirectoryValidationReport`, `validate_prediction_file(...)`, `validate_prediction_directory(...)`, and report writer.
  - Writes `validation_summary.json` and `validation_issues.jsonl`.
- `src/validation/__init__.py`
  - Exposes public validator API.
- `scripts/run_validate.py`
  - CLI for validating prediction directories against raw `.txt` directories.
- `scripts/run_phase11_smoke.py`
  - Runs Phase 8 pipeline → Phase 10 postprocess → Phase 11 format/write/validate.
  - Defaults to exact linker retrieval for speed; pass `--enable-sparse-retrieval` to use configured TF-IDF/BM25 retrieval.
- `tests/test_json_formatter.py`
- `tests/test_prediction_schema_validator.py`
- `tests/test_file_validator.py`

Validation commands run on 2026-07-13:

```cmd
python -m pytest tests/test_json_formatter.py tests/test_prediction_schema_validator.py tests/test_file_validator.py -q
python scripts\run_validate.py --config configs\default.yaml --input-dir data\golden\input --pred-dir data\golden\gold --report-dir outputs\reports\golden_schema_validation --expected-count 20 --sample-limit 5
python scripts\run_phase11_smoke.py --config configs\default.yaml --max-files 2 --sample-limit 3
python scripts\run_phase11_smoke.py --config configs\default.yaml --max-files 5 --start-index 0 --sample-limit 2
python scripts\run_phase11_smoke.py --config configs\default.yaml --max-files 5 --start-index 5 --sample-limit 2
python scripts\run_phase11_smoke.py --config configs\default.yaml --max-files 5 --start-index 10 --sample-limit 2
python scripts\run_phase11_smoke.py --config configs\default.yaml --max-files 5 --start-index 15 --sample-limit 2
python -m pytest -q
```

Observed validation results:

```text
23 passed in 0.21s

Prediction validation completed.
input_files_checked: 20
prediction_files_checked: 20
entities_checked: 370
missing_prediction_count: 0
extra_prediction_count: 0
error_count: 0
warning_count: 14
offset_error_count: 0
schema_error_count: 0
invalid_type_count: 0
invalid_assertion_count: 0
wrong_type_candidate_count: 0
json_parse_error_count: 0

Phase 11 smoke checks completed.
files_checked: 2
chunks_checked: 64
span_candidates: 62
entities_before_format: 46
records_written: 46
validation_error_count: 0
validation_warning_count: 0
offset_error_count: 0
wrong_type_candidate_error_count: 0
invalid_assertion_count: 0

20 golden files covered in four 5-file Phase 11 smoke batches:
- batch 0-4: validation_error_count 0; validation_warning_count 0; records_written 187
- batch 5-9: validation_error_count 0; validation_warning_count 0; records_written 76
- batch 10-14: validation_error_count 0; validation_warning_count 0; records_written 83
- batch 15-19: validation_error_count 0; validation_warning_count 0; records_written 179

127 passed in 1.92s
```

Known Phase 11 caveats:

- `scripts/run_phase11_smoke.py` is a smoke/integration check, not the final reusable inference command.
- Directory validation can validate any input/prediction directory pair, but the repo still needs `src/pipeline.py`/`scripts/run_inference.py` to generate final predictions for all 100 raw input files in one production command.
- Phase 11 validates schema/integrity only; Phase 12 now handles metric evaluation against gold.

---

## 8E. Phase 12 — Golden evaluator & error analysis

**Status:** Implemented and validated with gold-vs-gold self-match plus accumulated Phase 11 golden predictions.

Phase 12 consumes raw text, gold JSON, and prediction JSON directories and writes exact/relaxed metrics plus error reports:

```cmd
python scripts\run_evaluate.py --config configs\default.yaml --input-dir data\golden\input --gold-dir data\golden\gold --pred-dir outputs\predictions\phase11_golden20 --report-dir outputs\reports\phase12_eval_golden20 --expected-count 20
```

Implemented files:

- `src/evaluation/models.py`
  - `EvalEntity`, `EntityPair`, `PRFCounts`, `EvaluationFileResult`, and `EvaluationReport`.
- `src/evaluation/span_matcher.py`
  - Exact one-to-one matching with duplicate/multiset handling.
  - Relaxed greedy matching by same type with IoU/containment thresholds.
  - Nearest-overlap helper for diagnostics.
- `src/evaluation/metrics.py`
  - Overall/per-type PRF aggregation.
  - Assertion exact-set and per-label metrics.
  - Candidate hit/exact-set metrics for `CHẨN_ĐOÁN` and `THUỐC`.
- `src/evaluation/error_analysis.py`
  - Span mismatch, type mismatch, context slice, FP/FN diagnostic helpers.
- `src/evaluation/evaluator.py`
  - `GoldenEvaluator.evaluate_records(...)`, `evaluate_directories(...)`, and `write_evaluation_report(...)`.
  - Writes `evaluation_summary.json`, `per_file_metrics.csv`, `per_type_metrics.csv`, JSONL error files, and `samples.md`.
- `src/evaluation/__init__.py`
  - Exposes public Phase 12 API.
- `scripts/run_evaluate.py`
  - CLI for gold-vs-pred evaluation.
- `scripts/run_phase11_smoke.py`
  - Added `--keep-existing-output` for accumulating 20-file prediction directories in batches before reusable inference exists.
- `tests/test_evaluation_span_matcher.py`
- `tests/test_evaluation_metrics.py`
- `tests/test_evaluator.py`

Report files:

```text
outputs/reports/phase12_eval_golden20/
├── evaluation_summary.json
├── per_file_metrics.csv
├── per_type_metrics.csv
├── true_positives.jsonl
├── false_positives.jsonl
├── false_negatives.jsonl
├── span_mismatches.jsonl
├── type_mismatches.jsonl
├── assertion_mismatches.jsonl
├── candidate_mismatches.jsonl
├── error_cases.jsonl
└── samples.md
```

Validation commands run on 2026-07-13:

```cmd
python -m pytest tests/test_evaluation_span_matcher.py tests/test_evaluation_metrics.py tests/test_evaluator.py -q
python scripts\run_evaluate.py --config configs\default.yaml --input-dir data\golden\input --gold-dir data\golden\gold --pred-dir data\golden\gold --report-dir outputs\reports\phase12_gold_self_eval --expected-count 20

python scripts\run_phase11_smoke.py --config configs\default.yaml --max-files 5 --start-index 0 --prediction-dir outputs\predictions\phase11_golden20 --report-dir outputs\reports\phase11_golden20_validation --sample-limit 0
python scripts\run_phase11_smoke.py --config configs\default.yaml --max-files 5 --start-index 5 --prediction-dir outputs\predictions\phase11_golden20 --report-dir outputs\reports\phase11_golden20_validation --keep-existing-output --sample-limit 0
python scripts\run_phase11_smoke.py --config configs\default.yaml --max-files 5 --start-index 10 --prediction-dir outputs\predictions\phase11_golden20 --report-dir outputs\reports\phase11_golden20_validation --keep-existing-output --sample-limit 0
python scripts\run_phase11_smoke.py --config configs\default.yaml --max-files 5 --start-index 15 --prediction-dir outputs\predictions\phase11_golden20 --report-dir outputs\reports\phase11_golden20_validation --keep-existing-output --sample-limit 0

python scripts\run_validate.py --config configs\default.yaml --input-dir data\golden\input --pred-dir outputs\predictions\phase11_golden20 --report-dir outputs\reports\phase11_golden20_validation --expected-count 20 --sample-limit 3
python scripts\run_evaluate.py --config configs\default.yaml --input-dir data\golden\input --gold-dir data\golden\gold --pred-dir outputs\predictions\phase11_golden20 --report-dir outputs\reports\phase12_eval_golden20 --expected-count 20
python -m pytest -q
```

Observed validation results:

```text
20 passed in 0.22s

Gold self-match:
files_evaluated: 20
gold_entities: 370
pred_entities: 370
exact_tp: 370
exact_fp: 0
exact_fn: 0
exact_f1: 1.0000
relaxed_f1: 1.0000

Accumulated Phase 11 predictions validation:
input_files_checked: 20
prediction_files_checked: 20
entities_checked: 525
error_count: 0
warning_count: 0
offset_error_count: 0
schema_error_count: 0
invalid_type_count: 0
invalid_assertion_count: 0
wrong_type_candidate_count: 0

Phase 11 golden20 evaluation:
files_evaluated: 20
gold_entities: 370
pred_entities: 525
exact_tp: 80
exact_fp: 445
exact_fn: 290
exact_precision: 0.1524
exact_recall: 0.2162
exact_f1: 0.1788
relaxed_tp: 163
relaxed_fp: 362
relaxed_fn: 207
relaxed_precision: 0.3105
relaxed_recall: 0.4405
relaxed_f1: 0.3642
assertion_exact_match_rate: 0.5658
candidate_hit_rate: 0.4000
span_mismatch_count: 83
type_mismatch_count: 42
assertion_mismatch_count: 33
candidate_mismatch_count: 4

147 passed in 1.97s
```

Known Phase 12 caveats:

- Metrics are local golden metrics only; they are not official leaderboard metrics.
- Candidate comparison is exact string hit/set comparison; no ICD dot/no-dot normalization is applied yet.
- Actual baseline quality is still low. Phase 12 is intended to guide subsequent rule/linker/assertion/span tuning rather than to claim final performance.
- Phase 12.5 now adds reusable `src/pipeline.py`/`scripts/run_inference.py` for production-style 100-file inference.

---

## 8F. Phase 12.5 — Minimal inference CLI & trial submission zip

**Status:** Implemented and validated on all 100 public raw input files.

Phase 12.5 adds a reusable inference path and a BTC-format zip creator. This was prioritized before Phase 9 so the team can submit a schema-valid baseline early and detect any platform/package issues before deeper tuning.

Implemented files:

- `src/pipeline.py`
  - `ClinicalIEPipeline` composes Phase 2–8 + Phase 10 + Phase 11:
    `preprocess -> section detection -> extractors -> type resolver -> assertions -> ICD linker -> RxNorm linker -> postprocess -> formatter`.
  - `PipelineResult` returns records, entities, postprocess report, and counters.
- `scripts/run_inference.py`
  - Runs inference over any `.txt` input directory and writes one `{id}.json` per input file.
  - Supports `--max-files`, `--start-index`, `--keep-existing-output`, `--disable-sparse-retrieval`, and built-in validation.
- `scripts/make_submission_zip.py`
  - Creates `output.zip` with the exact structure required by `ABOUT.md`:

    ```text
    output/
      1.json
      2.json
      ...
      100.json
    ```

  - Verifies contiguous numeric files `1.json` through `100.json` before zipping.
- `tests/test_submission_zip.py`
  - Verifies zip root folder is `output/` and numeric files are contiguous.

Commands run on 2026-07-13:

```cmd
python scripts\run_inference.py --config configs\default.yaml --input-dir data\raw\input --output-dir outputs\predictions\submission_trial_smoke\output --report-dir outputs\reports\submission_trial_smoke_validation --max-files 2 --expected-count 2 --disable-sparse-retrieval --sample-limit 5

REM Full 100 files were generated in 10-file batches with --disable-sparse-retrieval and --keep-existing-output.

python scripts\run_validate.py --config configs\default.yaml --input-dir data\raw\input --pred-dir outputs\predictions\submission_trial\output --report-dir outputs\reports\submission_trial_validation --expected-count 100 --sample-limit 10
python scripts\make_submission_zip.py --pred-dir outputs\predictions\submission_trial\output --zip-path outputs\submission\output.zip --expected-count 100 --overwrite
python -m pytest -q
```

Observed results:

```text
2-file smoke inference:
files_processed: 2
records_written: 46
validation_error_count: 0
validation_warning_count: 0

Full 100-file validation:
input_files_checked: 100
prediction_files_checked: 100
entities_checked: 2208
missing_prediction_count: 0
extra_prediction_count: 0
error_count: 0
warning_count: 0
offset_error_count: 0
schema_error_count: 0
invalid_type_count: 0
invalid_assertion_count: 0
wrong_type_candidate_count: 0
json_parse_error_count: 0

Submission zip check:
zip_exists: True
zip_size: 74073
entry_count: 100
first_entries: ['output/1.json', 'output/2.json', 'output/3.json', 'output/4.json', 'output/5.json']
last_entries: ['output/96.json', 'output/97.json', 'output/98.json', 'output/99.json', 'output/100.json']
has_output_1: True
has_output_100: True
all_under_output: True

149 passed in 24.29s
```

Trial submission artifacts:

```text
outputs/predictions/submission_trial/output/  # 100 JSON files
outputs/reports/submission_trial_validation/  # validation_summary.json + validation_issues.jsonl
outputs/submission/output.zip                 # BTC-format trial zip
```

Known Phase 12.5 caveats:

- The generated `output.zip` is a valid-format **trial baseline**, not a tuned final submission.
- Full 100-file generation was performed with `--disable-sparse-retrieval` for speed/stability in the current tool timeout environment. Later final runs can compare with sparse retrieval enabled if runtime permits.
- Phase 9 subsequently improved this trial baseline and produced `outputs/submission/output_phase9.zip`.

---

## 8G. Phase 9 — Metric-guided deterministic calibration

**Status:** Implemented and validated on golden20 plus all 100 public raw input files.

Phase 9 used the Phase 12 evaluator and Phase 12.5 inference path to tune deterministic baseline quality without adding ML/NER models.

Implemented changes:

- Added `scripts/analyze_phase9_errors.py` to summarize top FP/FN/type/span/assertion/candidate error patterns from evaluator JSONL files.
- Tuned `src/extractors/problem_extractor.py`:
  - filters generic patient/problem spans such as `bệnh`, `bệnh nhân`, `bệnh hiện tại`, `yếu tố nguy cơ`;
  - adds symptom heads for `đánh trống ngực`, `cảm giác đánh trống ngực`, `cảm giác thắt chặt ngực`, and `ý thức suy giảm`;
  - stops tails at common temporal/reporting phrases such as `theo`, `kể từ`, `sau khi`, `trong khoảng`, `vào`.
- Tuned `src/extractors/lab_extractor.py` and `src/extractors/imaging_extractor.py`:
  - emits full qualitative result spans for patterns like `không ghi nhận bất thường`, `bình thường`, `cho thấy ...`, `gợi ý ...`;
  - tightens imaging tails so scheduled/context words do not become part of test names.
- Tuned `src/postprocess/cleanup.py`:
  - drops generic problem phrases;
  - trims subject/verb triggers such as `bệnh nhân xuất hiện ...` when the remainder starts with a valid symptom/diagnosis head.
- Tuned `src/assertion/negation.py`:
  - adds max pre/post negation distances;
  - prevents negation from leaking across comma + new subject clauses.
- Tuned ICD candidate selection in `configs/default.yaml` and `src/linking/icd10_linker.py`:
  - defaults to higher precision top-1 candidate selection;
  - supports `manual_overrides` for high-confidence missing aliases;
  - added overrides for `hội chứng não gan -> K72.9` and GERD variants -> `K21.9`.
- Added/updated unit tests for the above behavior.

Commands run on 2026-07-13:

```cmd
python scripts\run_inference.py --config configs\default.yaml --input-dir data\golden\input --output-dir outputs\predictions\phase9_baseline_golden20 --report-dir outputs\reports\phase9_baseline_validation --expected-count 20 --disable-sparse-retrieval --sample-limit 0
python scripts\run_evaluate.py --config configs\default.yaml --input-dir data\golden\input --gold-dir data\golden\gold --pred-dir outputs\predictions\phase9_baseline_golden20 --report-dir outputs\reports\phase9_baseline_eval --expected-count 20

python scripts\run_inference.py --config configs\default.yaml --input-dir data\golden\input --output-dir outputs\predictions\phase9_golden20 --report-dir outputs\reports\phase9_validation --expected-count 20 --disable-sparse-retrieval --sample-limit 0
python scripts\run_evaluate.py --config configs\default.yaml --input-dir data\golden\input --gold-dir data\golden\gold --pred-dir outputs\predictions\phase9_golden20 --report-dir outputs\reports\phase9_eval --expected-count 20
python scripts\analyze_phase9_errors.py --report-dir outputs\reports\phase9_eval --top-k 8
python -m pytest -q

REM Full 100 files were generated in 20-file batches with --disable-sparse-retrieval and --keep-existing-output.

python scripts\run_validate.py --config configs\default.yaml --input-dir data\raw\input --pred-dir outputs\predictions\submission_phase9\output --report-dir outputs\reports\submission_phase9_validation --expected-count 100 --sample-limit 0
python scripts\make_submission_zip.py --pred-dir outputs\predictions\submission_phase9\output --zip-path outputs\submission\output_phase9.zip --expected-count 100 --overwrite
```

Golden20 metric comparison:

```text
Phase 9 baseline before tuning:
pred_entities: 525
exact_f1: 0.1788
relaxed_f1: 0.3642
assertion_exact_match_rate: 0.5658
candidate_hit_rate: 0.4000
span_mismatch_count: 83
type_mismatch_count: 42
assertion_mismatch_count: 33
candidate_mismatch_count: 4

Phase 9 after tuning:
pred_entities: 455
exact_tp/fp/fn: 95 / 360 / 275
exact_precision: 0.2088
exact_recall: 0.2568
exact_f1: 0.2303
relaxed_precision: 0.3758
relaxed_recall: 0.4622
relaxed_f1: 0.4145
assertion_exact_match_rate: 0.6588
candidate_hit_rate: 0.8000
span_mismatch_count: 76
type_mismatch_count: 36
assertion_mismatch_count: 29
candidate_mismatch_count: 1
```

Full test suite and 100-file validation:

```text
155 passed in 2.85s

Prediction validation completed.
input_files_checked: 100
prediction_files_checked: 100
entities_checked: 1867
missing_prediction_count: 0
extra_prediction_count: 0
error_count: 0
warning_count: 0
offset_error_count: 0
schema_error_count: 0
invalid_type_count: 0
invalid_assertion_count: 0
wrong_type_candidate_count: 0
json_parse_error_count: 0

output_phase9.zip:
zip_exists: True
zip_size: 65635
entry_count: 100
first_entries: ['output/1.json', 'output/2.json', 'output/3.json', 'output/4.json', 'output/5.json']
last_entries: ['output/96.json', 'output/97.json', 'output/98.json', 'output/99.json', 'output/100.json']
all_under_output: True
```

Phase 9 artifacts:

```text
outputs/predictions/phase9_golden20/
outputs/reports/phase9_validation/
outputs/reports/phase9_eval/
outputs/predictions/submission_phase9/output/
outputs/reports/submission_phase9_validation/
outputs/submission/output_phase9.zip
```

Known Phase 9 caveats:

- The system is still rule-first/dictionary/regex-based; no NER model has been added.
- Remaining errors are dominated by symptom FP/FN and span-boundary issues, especially repeated short symptom heads and lab/result boundary variants.
- Candidate metrics improved on golden20, but RxNorm gold has at least one suspicious aspirin→amlodipine-like mismatch; avoid overfitting that single case unless confirmed by competition feedback.
- Full 100-file Phase 9 submission was generated with `--disable-sparse-retrieval` for stable runtime in the current environment.

---

## 8H. Phase 13 — Streamlit local review UI

**Status:** Implemented and smoke-tested by helper tests/app import.

Phase 13 adds a local Streamlit dashboard for reviewing Phase 9 artifacts and debugging future Phase 9.1/14/15 work. It does not change inference outputs by itself.

Implemented files:

```text
streamlit_app/__init__.py
streamlit_app/app.py
streamlit_app/data_loader.py
streamlit_app/highlight.py
streamlit_app/tables.py
streamlit_app/pipeline_debug.py
streamlit_app/README.md
tests/test_streamlit_app_helpers.py
```

Main app command:

```cmd
streamlit run streamlit_app\app.py
```

Default Phase 13 paths:

```text
configs/default.yaml
data/golden/input/
data/golden/gold/
outputs/predictions/phase9_golden20/
outputs/reports/phase9_eval/
data/raw/input/
outputs/predictions/submission_phase9/output/
outputs/reports/submission_phase9_validation/
```

Tabs implemented:

- **Overview**
  - Reads `evaluation_summary.json`, `per_file_metrics.csv`, and `per_type_metrics.csv`.
  - Shows exact/relaxed F1, counts, assertion/candidate metrics, per-file and per-type tables.
- **File Reviewer**
  - Selects a golden file.
  - Shows raw text with offset-based highlights for gold, prediction, TP, FP, FN, span/type/assertion/candidate mismatch layers.
  - Shows gold/prediction/compare/error/type-count tables.
- **Error Browser**
  - Reads evaluator JSONL reports:
    `true_positives`, `false_positives`, `false_negatives`, `span_mismatches`, `type_mismatches`, `assertion_mismatches`, `candidate_mismatches`, `error_cases`.
  - Supports file/type/subcategory/text filters and row-level inspection.
- **Live Inference**
  - Runs `ClinicalIEPipeline` on pasted text or selected raw input file.
  - Shows counters, entity distribution, postprocess summary, highlighted spans, records, and optional provenance.
  - Uses `st.cache_resource` to avoid reloading pipeline every interaction.
- **Submission Review**
  - Reads `outputs/predictions/submission_phase9/output/`.
  - Shows JSON count, record counts by file, selected file records, raw JSON, and validation summary.

Validation commands run:

```cmd
python -m pytest tests/test_streamlit_app_helpers.py -q
python -m py_compile streamlit_app\app.py streamlit_app\data_loader.py streamlit_app\highlight.py streamlit_app\tables.py streamlit_app\pipeline_debug.py
python -c "import streamlit_app.app as app; print('app_import_ok')"
python -m pytest -q
```

Observed results:

```text
tests/test_streamlit_app_helpers.py: 5 passed
app_import_ok
full test suite: 160 passed in 12.94s
```

Known Phase 13 caveats:

- This is intended for local review first. Streamlit Cloud deployment is optional and would need private data/report handling.
- Highlight rendering uses non-overlapping spans to keep the UI readable. For heavily overlapping errors, inspect the tables/error rows for full detail.
- Live inference defaults sparse retrieval off for speed and to match the Phase 9 submission run behavior.

---

## 8I. Phase 14 — NER infrastructure without model training

**Status:** Implemented and validated. No model was trained; NER stays disabled by default.

Phase 14 creates the infrastructure needed for later model-assisted span extraction while preserving the Phase 9 score/stability. The implemented components allow building NER datasets, validating BIO/span offsets, decoding future token-classification predictions, and safely plugging in a local HuggingFace token-classification model if one is trained/provided later.

Implemented files:

```text
src/ner/__init__.py
src/ner/bio.py
src/ner/dataset_builder.py
src/ner/model_inference.py
src/ner/span_decoder.py
src/extractors/ner_extractor.py        # upgraded from dummy to safe optional extractor
scripts/build_ner_dataset.py
scripts/evaluate_ner_extractor.py
tests/test_ner_bio.py
tests/test_ner_dataset_builder.py
tests/test_ner_span_decoder.py
tests/test_ner_extractor.py
```

Config changes:

```yaml
project:
  phase: phase_14_ner_infrastructure

extractors:
  ner:
    enabled: false
    model_dir: models/ner/phase14
    default_threshold: 0.85

type_resolution:
  source_priority:
    ner: -1

ner:
  enabled: false
  mode: infrastructure_only
  training:
    enabled: false
```

Generated Phase 14 artifacts:

```text
data_train/ner/dev_gold.jsonl
data_train/ner/train_weak.jsonl
data_train/ner/label_map.json
data_train/ner/README.md
outputs/reports/phase14_ner_dataset/
outputs/reports/phase14_ner_extractor_smoke/
outputs/predictions/phase14_ner_off_golden20/
outputs/reports/phase14_ner_off_validation/
outputs/reports/phase14_ner_off_eval/
```

Dataset build command:

```cmd
python scripts\build_ner_dataset.py --config configs\default.yaml
```

Dataset build result:

```text
dev_gold_files: 20
dev_gold_entities: 345
dev_gold_offset_errors: 0
dev_gold_overlap_conflicts: 25
dev_gold_label_counts:
  CHẨN_ĐOÁN: 72
  KẾT_QUẢ_XÉT_NGHIỆM: 48
  THUỐC: 31
  TRIỆU_CHỨNG: 154
  TÊN_XÉT_NGHIỆM: 40

weak_files: 100
weak_entities: 1363
weak_offset_errors: 0
weak_overlap_conflicts: 7
weak_label_counts:
  CHẨN_ĐOÁN: 516
  TRIỆU_CHỨNG: 847
```

Note: `dev_gold_entities` is lower than raw golden count 370 because overlapping labels are deterministically reduced to non-overlapping spans for BIO/token-classification training.

NER extractor smoke commands:

```cmd
python scripts\evaluate_ner_extractor.py --config configs\default.yaml --input-dir data\golden\input --max-files 20
python scripts\evaluate_ner_extractor.py --config configs\default.yaml --input-dir data\golden\input --max-files 2 --force-enable
```

NER extractor smoke result:

```text
files_checked: 20
extractor_enabled: False
model_available: False
candidate_count: 0
offset_error_count: 0
model_error: NER model_dir does not exist: models\ner\phase14
```

The forced-enable smoke also returns no candidates and no offset errors when model weights are missing, proving safe fallback behavior.

NER-off Phase 14 baseline validation/evaluation:

```cmd
python scripts\run_inference.py --config configs\default.yaml --input-dir data\golden\input --output-dir outputs\predictions\phase14_ner_off_golden20 --report-dir outputs\reports\phase14_ner_off_validation --expected-count 20 --disable-sparse-retrieval
python scripts\run_evaluate.py --config configs\default.yaml --input-dir data\golden\input --gold-dir data\golden\gold --pred-dir outputs\predictions\phase14_ner_off_golden20 --report-dir outputs\reports\phase14_ner_off_eval --expected-count 20
```

Observed NER-off result, matching Phase 9 exactly:

```text
pred_entities: 455
validation_error_count: 0
offset_error_count: 0
schema_error_count: 0
invalid_type_count: 0
invalid_assertion_count: 0
wrong_type_candidate_count: 0

exact_f1: 0.2303
relaxed_f1: 0.4145
assertion_exact_match_rate: 0.6588
candidate_hit_rate: 0.8000
span_mismatch_count: 76
type_mismatch_count: 36
candidate_mismatch_count: 1
```

Validation commands run:

```cmd
python -m pytest tests/test_ner_bio.py tests/test_ner_span_decoder.py tests/test_ner_dataset_builder.py tests/test_ner_extractor.py -q
python -m py_compile src\ner\__init__.py src\ner\bio.py src\ner\dataset_builder.py src\ner\model_inference.py src\ner\span_decoder.py scripts\build_ner_dataset.py scripts\evaluate_ner_extractor.py src\extractors\ner_extractor.py
python -m pytest -q
```

Observed test result:

```text
NER targeted tests: 15 passed
full test suite: 175 passed
```

Known Phase 14 caveats:

- No NER model has been trained or shipped yet.
- `NERExtractor` only emits spans if `extractors.ner.enabled=true` and a local compatible token-classification model exists at `models/ner/phase14`.
- Weak labels come from Phase 9 predictions and include noise; use Streamlit + evaluator before training or enabling NER-on submissions.
- Phase 14 does not improve web score by itself; it preserves Phase 9 behavior and prepares safe model integration.

---

## 8I. V2 Phase 1 — NER-0 measurement foundation and NER-1 GLiNER reproduction

**Status:** Implemented, validated, and benchmarked on 2026-07-21.

V2 keeps the earlier token-classification NER infrastructure intact and adds a
separate GLiNER semantic extractor. `configs/default.yaml` remains the V1
fallback configuration; the V2 reproduction uses
`configs/gliner_zero_shot.yaml`.

### 8I.1 NER-0 — baseline, contracts, split, scorer, and oracle analysis

Implemented shared contracts and measurement artifacts:

```text
configs/splits_v2.yaml
data/golden/ANNOTATION_GUIDELINE_V2.md
data/golden/ner_data_schema.json
data/golden/ner_data_example.jsonl
data/golden/DATA_REQUESTS_V0.md
src/ner/data_validator.py
src/evaluation/official_like_scorer.py
scripts/freeze_v1_baseline.py
scripts/validate_ner_dataset.py
scripts/report_split_coverage.py
scripts/run_official_like_score.py
scripts/run_ner_oracles.py
```

The 20 verified notes are now divided into coverage-aware work sets:

```text
development: 1-12
calibration: 13-16
lockbox:     17-20
```

All three splits contain all five entity types. Lockbox use is prohibited for
daily prompt/threshold/rule selection and is reserved for declared milestones.

`V1_FROZEN` was reproduced twice with the same config, terminology artifacts,
and inputs:

```text
files:                  20
predicted entities:     455
byte-identical files:   20/20
prediction mismatches:  0
validation errors:      0
offset errors:          0
schema errors:          0
```

Frozen/generated artifacts:

```text
outputs/baselines/v1_frozen/
outputs/reports/v2_ner_baseline/
```

V1 diagnostic extraction result:

```text
exact precision: 0.2088
exact recall:    0.2568
exact F1:        0.2303
relaxed F1:      0.4145
```

The evaluator now also reports left/right/both boundary errors and a five-type
confusion matrix. A pre-existing per-type counting bug was fixed: reconstructed
entities are now matched by stable `(file_id, record_index)` rather than Python
object identity.

The local scorer is explicitly marked `official_like_v1`, not the organizer
grader. Gold-vs-gold returns 1.0 for all components. V1 results under this local
profile are:

```text
text_score:       0.243721
assertions_score: 0.120986
candidates_score: 0.130747
final_score:      0.161711
```

Diagnostic oracle ceilings under the documented greedy-overlap assumptions:

```text
baseline:    0.161711
oracle span: 0.180693
oracle type: 0.165698
```

The shared synthetic JSONL example passes the new validator:

```text
samples:  2
entities: 1
errors:   0
```

`data/golden/DATA_REQUESTS_V0.md` converts observed errors into pilot-data
requests for the Problem Data and Structured Data owners. Primary targets are
symptom boundary/precision contrasts, symptom-vs-diagnosis contrasts,
test-result extraction, and full medication formulation boundaries.

### 8I.2 NER-1 — GLiNER backend, windows, cache, and pipeline integration

Implemented files:

```text
configs/gliner_zero_shot.yaml
requirements-v2-ner.txt
src/ner/gliner_backend.py
src/ner/gliner_windows.py
src/ner/prediction_cache.py
src/extractors/gliner_extractor.py
scripts/provision_gliner.py
scripts/check_gliner_environment.py
scripts/run_gliner_smoke.py
scripts/benchmark_gliner.py
scripts/write_gliner_model_manifest.py
NER01_IMPLEMENTATION.md
```

Architecture:

```text
existing raw text/section chunks
→ tokenizer-aligned GLiNER windows
→ local GLiNER spans
→ global raw-offset restoration
→ exact overlap-window deduplication
→ SpanCandidate(source="gliner")
→ existing TypeResolver
→ ClinicalIEPipeline.process_ner()
```

The existing token-classification `NERExtractor` was not replaced. GLiNER is a
separate feature-flagged extractor, and `ClinicalIEPipeline(..., ner_only=True)`
reuses the repository pipeline without initializing assertion or linking
components.

Pinned runtime used for the reproduced run:

```text
Python:       3.13.7 (development machine; 3.10/3.11 recommended for release)
torch:        2.11.0
transformers: 4.51.3
gliner:       0.2.27
safetensors:  0.7.0
pytest:       8.4.2
```

Pinned model artifacts:

```text
urchade/gliner_multi-v2.1
revision: 443d26d654e0324125a96bebd8e796c14ff2efe6

microsoft/mdeberta-v3-base tokenizer/config
revision: a0484667b22365f84929a935b5e50a51f71f159d
```

The mDeBERTa dependency is provisioned tokenizer-only; GLiNER already contains
the encoder weights. Model and tokenizer manifests store file hashes. Final
inference uses `required: true` and `local_files_only: true`, so a missing
artifact fails fast instead of silently returning an empty result.

Offline real-model smoke input:

```text
Bệnh nhân đau ngực và dùng aspirin.
```

Observed predictions:

```text
đau ngực → symptom, score 0.897188
aspirin   → medication or drug, score 0.803847
```

Window/cache contract:

```text
maximum tokens/window: 320
overlap tokens:         64
raw offset mismatch:     0
exact overlap duplicate: 0
```

The prediction cache key includes input, model metadata, label schema,
windowing, threshold profile, and pass/inference options. Internal debug
artifacts preserve model/pass/window provenance; submission-safe JSON excludes
debug fields.

### 8I.3 GLiNER development reproduction result

The mandatory full five-label descriptive-English pass was run offline at the
Training Session reference threshold `0.35` on the 12-note development split.
This is a reproduction point, not the selected NER-2 configuration.

```text
gold entities: 205
pred entities: 251

exact precision:   0.1992
exact recall:      0.2439
exact F1:          0.2193
relaxed precision: 0.3785
relaxed recall:    0.4634
relaxed F1:        0.4167
```

Per-type metrics:

| Type | Exact F1 | Relaxed F1 |
|---|---:|---:|
| `CHẨN_ĐOÁN` | 0.3333 | 0.4722 |
| `THUỐC` | 0.2222 | 0.4762 |
| `TRIỆU_CHỨNG` | 0.2232 | 0.4549 |
| `TÊN_XÉT_NGHIỆM` | 0.1961 | 0.3529 |
| `KẾT_QUẢ_XÉT_NGHIỆM` | 0.0000 | 0.0541 |

Prediction distribution:

```text
TRIỆU_CHỨNG:          143
THUỐC:                 45
CHẨN_ĐOÁN:             31
TÊN_XÉT_NGHIỆM:        27
KẾT_QUẢ_XÉT_NGHIỆM:     5
```

Validation and determinism:

```text
files:                    12
entities checked:         251
validation errors:          0
offset errors:              0
schema errors:              0
exact duplicate warnings:   0
two-run byte mismatches:     0/12
```

CPU-only runtime:

```text
first development run: 117.36 seconds
cached rerun:           29.89 seconds
per-note inference:     approximately 2-31 seconds before cache
process working set:    approximately 1.3 GB
```

The reproduction confirms useful semantic recall but also substantial
over-emission and exact-boundary errors. `KẾT_QUẢ_XÉT_NGHIỆM` is the largest
residual gap; medication discovery has good relaxed recall but needs formulation
boundary expansion. These findings are inputs to NER-2 and the two data-owner
workstreams, not reasons to tune the NER-1 reproduction config in place.

### 8I.4 Verification

Commands and results:

```text
python -m pytest -q
→ 198 passed

python -m compileall -q src scripts tests
→ pass

git diff --check
→ pass

offline GLiNER smoke
→ pass

V1 two-run byte comparison
→ 20/20 identical

GLiNER development two-run byte comparison
→ 12/12 identical
```

Known limitations and next gate:

- Active development Python is 3.13.7; release rebuild should use a clean pinned
  Python 3.10/3.11 environment.
- Model weights live in the local Hugging Face cache and must be provisioned or
  packaged separately; they are intentionally not committed to Git.
- Threshold `0.35` is not calibrated and produces false positives.
- No V1+GLiNER hybrid fusion has been promoted yet; that belongs to NER-3/NER-4.
- Next work package is NER-2: controlled label, windowing, pass, and per-type
  threshold benchmark without lockbox tuning.

### 8I.4 NER-2 controlled experiment infrastructure

NER-2 infrastructure now separates unfiltered window/pass proposals from
selection thresholds. Proposal caches retain raw model score, label wording,
pass, window, section, and exact evidence provenance. Model and tokenizer loading
is lazy and occurs only after a proposal-cache miss. The immutable
`configs/gliner_zero_shot.yaml` NER-1 reproduction config was not modified.

Implemented controlled profiles include legacy NER-1 chunks, line/bullet units,
section windows without overlap, and section windows with token overlap. The
registry validates unique run IDs, parent graphs, and one declared experiment
axis. Development/calibration/lockbox guards, density/runtime reports,
extraction-only and end-to-end evaluation, coordinate threshold generation,
focused-pass provenance, deterministic byte verification, and residual error
handoff scripts are present.

The real offline GLiNER proposal-floor equivalence gate passed on all 12
development notes: 426 window/pass comparisons, zero mismatches between direct
threshold 0.35 inference and threshold 0.15 proposals filtered at 0.35. Raw
proposal reuse is therefore permitted for NER-2 threshold sweeps. Lockbox was not
opened.

Controlled development results selected descriptive English labels and
section-aware windows without overlap. The selected global threshold is `0.30`:
exact F1 increased from the section-window threshold-0.35 reference `0.272527`
to `0.280992`, with density ratio `1.360976` and no per-type regression beyond
the frozen budget. The first threshold-floor run took 82.07 seconds; cached
threshold selections took approximately 0.82--1.41 seconds and did not reload
the model.

Per-type coordinate candidates for diagnosis, drug, and test result produced
overall exact-F1 gains of `0.001708`, `0.000582`, and `0.004141`; all were below
the frozen minimum useful effect `0.005`, so the global profile was retained.
The problem-focused pass exceeded the density budget (`1.521951 > 1.50`). The
structured-focused pass improved development exact F1 by only `0.003008`, below
the minimum useful effect. P3 was therefore not run. Calibration confirmed a
structured-pass signal, but it was not promoted because the required development
gate had failed.

The frozen NER-2 configuration is `configs/ner2/selected_zero_shot.yaml`:
English full pass, section windows without overlap, and global threshold `0.30`.
Two cached offline calibration reruns produced 4/4 byte-identical prediction
files. The selected residual review covers all five types and was handed off in
`data/golden/DATA_REQUESTS_V1.md`. No lockbox note was opened or evaluated.

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
python scripts\run_phase7_smoke.py --config configs\default.yaml --max-files 2 --sample-limit 5
python scripts\run_phase8_smoke.py --config configs\default.yaml --max-files 2 --sample-limit 10
python scripts\run_phase10_smoke.py --config configs\default.yaml --max-files 2 --sample-limit 5
python scripts\run_validate.py --config configs\default.yaml --input-dir data\golden\input --pred-dir data\golden\gold --report-dir outputs\reports\golden_schema_validation --expected-count 20
python scripts\run_phase11_smoke.py --config configs\default.yaml --max-files 2 --sample-limit 5
python scripts\run_evaluate.py --config configs\default.yaml --input-dir data\golden\input --gold-dir data\golden\gold --pred-dir data\golden\gold --report-dir outputs\reports\phase12_gold_self_eval --expected-count 20
python scripts\run_inference.py --config configs\default.yaml --input-dir data\raw\input --output-dir outputs\predictions\submission_trial\output --report-dir outputs\reports\submission_trial_validation --expected-count 100 --disable-sparse-retrieval
python scripts\make_submission_zip.py --pred-dir outputs\predictions\submission_trial\output --zip-path outputs\submission\output.zip --expected-count 100 --overwrite
python scripts\run_inference.py --config configs\default.yaml --input-dir data\golden\input --output-dir outputs\predictions\phase9_golden20 --report-dir outputs\reports\phase9_validation --expected-count 20 --disable-sparse-retrieval
python scripts\run_evaluate.py --config configs\default.yaml --input-dir data\golden\input --gold-dir data\golden\gold --pred-dir outputs\predictions\phase9_golden20 --report-dir outputs\reports\phase9_eval --expected-count 20
python scripts\analyze_phase9_errors.py --report-dir outputs\reports\phase9_eval --top-k 8
streamlit run streamlit_app\app.py
python scripts\build_ner_dataset.py --config configs\default.yaml
python scripts\evaluate_ner_extractor.py --config configs\default.yaml --input-dir data\golden\input --max-files 20
python scripts\run_inference.py --config configs\default.yaml --input-dir data\golden\input --output-dir outputs\predictions\phase14_ner_off_golden20 --report-dir outputs\reports\phase14_ner_off_validation --expected-count 20 --disable-sparse-retrieval
python scripts\run_evaluate.py --config configs\default.yaml --input-dir data\golden\input --gold-dir data\golden\gold --pred-dir outputs\predictions\phase14_ner_off_golden20 --report-dir outputs\reports\phase14_ner_off_eval --expected-count 20

# V2 NER-0 / NER-1
python -m pip install -r requirements-v2-ner.txt
python scripts\check_gliner_environment.py
python scripts\validate_ner_dataset.py --input data\golden\ner_data_example.jsonl
python scripts\report_split_coverage.py --split-config configs\splits_v2.yaml --input-dir data\golden\input --gold-dir data\golden\gold --output outputs\reports\v2_ner_baseline\split_coverage.json
python scripts\run_official_like_score.py --pred-dir outputs\baselines\v1_frozen\predictions_run1 --gold-dir data\golden\gold --output outputs\reports\v2_ner_baseline\official_like_score.json
python scripts\run_ner_oracles.py --pred-dir outputs\baselines\v1_frozen\predictions_run1 --gold-dir data\golden\gold --output outputs\reports\v2_ner_baseline\oracles.json
python scripts\provision_gliner.py --model urchade/gliner_multi-v2.1 --max-workers 1
python scripts\provision_gliner.py --model microsoft/mdeberta-v3-base --max-workers 1 --tokenizer-only
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
python scripts\run_gliner_smoke.py
python scripts\benchmark_gliner.py --config configs\gliner_zero_shot.yaml --split-config configs\splits_v2.yaml --split development
```

Then review the NER-1 error reports and run NER-2 controlled experiments. Do
not use the V2 lockbox for daily label, threshold, or window selection.

Before writing new extractor code, keep these invariants visible:

```python
assert raw_text[candidate.start:candidate.end] == candidate.text
assert candidate.start < candidate.end
```

Every resolver/postprocess component should preserve enough provenance to debug later in the Streamlit UI and evaluator.

---

## 10. Summary

The repository currently has a solid foundation through Phase 14 on top of Phase 9/12.5:

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
- Phase 6 smoke validation on 20 golden files with zero offset errors;
- Phase 7 ICD-10 linker wrapper for `CHẨN_ĐOÁN` entities;
- Phase 8 RxNorm linker wrapper and drug parser for `THUỐC` entities.
- Phase 10 conservative merge/postprocess for linked `FinalEntity` lists;
- Phase 10 smoke validation across all 20 golden files in batches with zero offset errors, zero wrong-type candidate errors, zero invalid assertion errors, and zero exact duplicate errors.
- Phase 11 JSON formatter and schema/file/directory validator;
- Golden schema validation with zero errors and 14 duplicate-exact warnings from the manually annotated gold files;
- Phase 11 smoke validation across all 20 golden files in batches with zero validation errors.
- Phase 12 golden evaluator with exact/relaxed matching, duplicate-aware self-match validation, per-file/per-type metrics, assertion/candidate metrics, and JSONL/CSV/Markdown error reports;
- Phase 12 evaluation on accumulated 20-file Phase 11 predictions with schema errors 0 and baseline exact F1 0.1788 / relaxed F1 0.3642.
- Phase 12.5 minimal inference CLI and BTC-format `output.zip` creator;
- 100-file trial predictions validated with zero schema/offset/type/assertion/candidate-placement errors;
- `outputs/submission/output.zip` contains exactly `output/1.json` through `output/100.json`.
- Phase 9 deterministic calibration using golden20 evaluator reports;
- Phase 9 golden20 exact F1 improved from 0.1788 to 0.2303 and relaxed F1 from 0.3642 to 0.4145;
- Phase 9 100-file predictions validated with zero schema/offset/type/assertion/candidate-placement errors;
- `outputs/submission/output_phase9.zip` contains exactly `output/1.json` through `output/100.json`.
- Phase 13 Streamlit local review UI for Phase 9 metrics/errors/file highlights/live inference/submission review;
- Streamlit helper tests and full test suite pass with 160 tests;
- Phase 14 NER infrastructure package, dataset builder, optional model runner, span decoder, and safe NER extractor fallback;
- Phase 14 generated `data_train/ner/dev_gold.jsonl`, `data_train/ner/train_weak.jsonl`, and `data_train/ner/label_map.json` with zero offset errors;
- Phase 14 NER-off golden20 inference/eval matches Phase 9 exactly: exact F1 0.2303, relaxed F1 0.4145, candidate hit rate 0.8000;
- V2 NER-0 freezes `V1_FROZEN` with 20/20 byte-identical files across two runs,
  zero validation/offset/schema errors, a versioned annotation/data contract,
  coverage-aware development/calibration/lockbox splits, an official-like local
  scorer, boundary/type diagnostics, and span/type oracle reports;
- V2 NER-1 adds a separate GLiNER backend/extractor, tokenizer-aligned overlapping
  windows, exact raw-offset restoration, prediction caching, provenance, an
  NER-only `ClinicalIEPipeline` entry point, pinned offline artifacts, and
  fail-fast required-model behavior;
- GLiNER zero-shot reproduction on the 12-note development split at threshold
  0.35 produced exact F1 0.2193 and relaxed F1 0.4167 with zero validation,
  offset, schema, duplicate, or two-run determinism errors;
- Full test suite now passes with 198 tests.

The next major milestone is V2 NER-2: controlled zero-shot benchmarking of label
schema, token-window strategy, pass strategy, and per-type thresholds. NER-3/4
hybrid expert integration and fusion follow only after one zero-shot config is
selected; fine-tuning remains conditional under NER-6.

---

## 8K. V2 NER-3 — V1 expert integration experiment harness

**Status:** Implementation and one-note smoke complete. The 12-note model-bearing
development experiment has intentionally not been run.

NER-3 now has a frozen development-only A/B/C/D plan: A=`V1_FROZEN`,
B=selected NER-2 GLiNER, C=diagnostic naive union, and D=GLiNER-centered simple
fusion. `configs/ner3/base.yaml` enables all sources and narrow same-type
structured anchors. `configs/ner3/selection_policy.yaml` requires a shared
candidate ledger, zero evidence/validation/exact-duplicate errors, preservation
of unconfirmed GLiNER hypotheses, and manual source-error review.

Implemented operational artifacts:

```text
configs/ner3/base.yaml
configs/ner3/experiment_matrix.yaml
configs/ner3/selection_policy.yaml
configs/ner3/naive_union.yaml
configs/ner3/simple_fusion.yaml
configs/ner3/selected_expert_profile.yaml
scripts/run_ner3_experiments.py
scripts/summarize_ner3_experiments.py
scripts/review_ner3_source_errors.py
tests/test_ner3_experiment_runner.py
NER3_IMPLEMENTATION.md
```

The runner collects every enabled source once through the main pipeline,
validates raw offsets/evidence, and replays the identical ledger through all
four modes. It emits per-source/per-type complementarity, prediction/evaluation
artifacts, fusion safety counts, and hash-bound manifests. The summarizer cannot
promote a configuration; a passing result is only ready for manual review.

One development note was collected into a 111-candidate ledger and replayed
through A/B/C/D twice without model inference on replay. A reproduced the V1
frozen end-to-end bytes and B reproduced both NER-2 selected prediction modes.
D used four structured anchors, retained all 15 unconfirmed GLiNER-only spans,
and emitted zero exact duplicates/evidence errors. Its prediction-to-gold ratio
was 2.67 and its density relative to A was 1.40. The frozen policy blocks
promotion because the smoke covers only 1/12 required development notes; this
is not a development decision. Full development, calibration, and lockbox were
not run at the smoke checkpoint. The later 12-note development run completed
from one shared 646-candidate ledger with zero evidence, validation, or exact
duplicate errors. B remained the strongest system (exact F1 0.2810; end-to-end
0.2216). D improved end-to-end score and density over diagnostic C but did not
beat A or B and had lower exact F1 than C, so no NER-3 hybrid was promoted.
Calibration and lockbox remain unopened. C and D are retained only as NER-4
diagnostic parents.

---

## 8L. V2 NER-4 — deterministic fusion

**Status:** Completed and promoted on all 12 development notes. Calibration and
lockbox remain unopened.

The final profile is `configs/ner4.yaml`. Complete-link evidence clustering and
deterministic type policy improved exact F1 from 0.256186 to 0.258389, relaxed F1
from 0.404658 to 0.439597, and official-like score from 0.138422 to 0.194986.
The run had zero raw-offset errors, zero exact duplicates, and density 0.8112
relative to NER-3 D. Per-type regression remained within the frozen budget.

Raw-safe cue trimming and structural test/result splitting remain implemented
but are disabled in the selected profile because verified gold currently keeps
some cue/full-result spans. Full drug formulation is selected from the V1 drug
anchor. `ClinicalIEPipeline` routes through NER-4 when the single feature flag is
enabled and preserves finalized spans/types through downstream processing.

The one-use experiment harness/configs/traces were removed after the decision.
The retained artifacts are the production modules, `configs/ner4.yaml`, two test
files, `NER4_IMPLEMENTATION.md`, and `outputs/reports/ner4_final/summary.json`.
Focused tests passed 12/12 and the full repository suite passed 268/268.
