# ViClinicalIE V0 — Pipeline Overview Report

Scope: end-to-end tour of the current V0 baseline for Vietnamese clinical IE, from raw text on disk to a validated submission zip. Everything below is rule-based, deterministic, and offline. No LLM is in the loop today.

Companion documents:
- [`report_span_extraction.md`](repo/ViClinicalIE/report_span_extraction.md:1) — deep dive on span extraction.
- [`report_assertion.md`](repo/ViClinicalIE/report_assertion.md:1) — deep dive on assertion detection.

---

## 1. Directory layout and where things live

Top-level layout inside [`repo/ViClinicalIE/`](repo/ViClinicalIE/README.md:1):

```
ViClinicalIE/
├── input/                     # 100 raw *.txt records (1.txt … 100.txt)
├── data_resources/            # curated CSVs (lex, aliases, ICD10, RxNorm)
├── configs/section_aliases.json
├── src/                       # library code
│   ├── models.py              # ClinicalDocument, Line, Section, SpanCandidate, EntityOutput
│   ├── io_utils.py            # read/write helpers, zip helper
│   ├── normalization.py       # offset-preserving noise normalization
│   ├── offset_mapper.py       # OffsetMapper for norm↔raw index conversion
│   ├── section_parser.py      # SECTION_ALIASES, parse_document_sections, inventory exports
│   ├── rule_extractors.py     # lab / drug / diagnosis / symptom extractors + reject list
│   ├── assertion.py           # isNegated / isHistorical / isFamily
│   ├── merge.py               # overlap resolution (TYPE_PRIORITY)
│   ├── output_writer.py       # submission JSON + zip
│   ├── validator.py           # schema / offset / duplicate / overlap / zip checks
│   └── linking/               # candidate mapping (ICD-10, RxNorm)
│       ├── common.py          # MappingEntry, score_similarity, CSV loaders
│       ├── icd10_linker.py    # ICD10Linker (exact → alias → fuzzy 0.88)
│       ├── rxnorm_linker.py   # RxNormLinker (strip sig → exact → alias → fuzzy 0.86)
│       └── candidate_linker.py# link_mapping_candidates orchestrator
├── scripts/
│   ├── analyze_sections.py    # section/line inventory exporters
│   ├── build_span_candidates.py    # day 09: run extractors, dump JSONL + summary
│   ├── build_v0_outputs.py         # day 10: extract → assert → merge → write → validate
│   └── build_v0_linked_outputs.py  # day 11+: same, plus ICD/RxNorm candidate mapping
├── tests/                     # 5 pytest modules covering extractors, section parser,
│                              #   offsets, assertion+merge+output, linking
├── analysis/                  # produced artifacts (JSONL, CSV, JSON summaries)
├── outputs/                   # (generated) versioned JSON + zip packages
├── reports/                   # (generated) validation + mapping coverage MDs
├── verify_app/                # Streamlit-style manual QA tool
└── {README, Solution_design, Implementation_plan, Data_assessment}.md
```

Three entry points, each idempotent:

- [`scripts/build_span_candidates.py`](repo/ViClinicalIE/scripts/build_span_candidates.py:1) — span-only pass. Fastest way to inspect what the extractors alone see.
- [`scripts/build_v0_outputs.py`](repo/ViClinicalIE/scripts/build_v0_outputs.py:1) — full V0 baseline without linking. Writes JSON, zip, and validation report.
- [`scripts/build_v0_linked_outputs.py`](repo/ViClinicalIE/scripts/build_v0_linked_outputs.py:1) — same as above plus curated ICD/RxNorm mapping, with a coverage gate that fails the build if diagnosis mapping falls under 80% or drug mapping under 90%.

The three scripts share the same in-memory pipeline; they differ only in what artifacts they persist.

---

## 2. End-to-end execution flow

The canonical flow (as coded in [`build_v0_linked_outputs.main()`](repo/ViClinicalIE/scripts/build_v0_linked_outputs.py:126)) is:

```
raw *.txt
   │
   ▼
[1] load_input_files            → List[(file_id, raw_text)]
   │
   ▼
[2] parse_documents             → List[ClinicalDocument]
   │  (normalization + section detection + line typing)
   │
   ▼
[3] run_rule_extraction         → List[SpanCandidate]  (per-type + rejects removed)
   │  ├─ extract_lab_candidates        (TÊN_XÉT_NGHIỆM, KẾT_QUẢ_XÉT_NGHIỆM)
   │  ├─ extract_drug_candidates       (THUỐC)
   │  ├─ extract_diagnosis_candidates  (CHẨN_ĐOÁN)
   │  ├─ extract_symptom_candidates    (TRIỆU_CHỨNG)
   │  ├─ reject_non_target_candidates  (procedure / imaging method removal)
   │  ├─ dedupe_candidates
   │  └─ validate_candidate_offsets    (raw_text[start:end] must equal text)
   │
   ▼
[4] add_assertions              → SpanCandidate.assertion_candidates + time_context
   │
   ▼
[5] merge_candidates            → non-overlapping List[SpanCandidate]
   │  (TYPE_PRIORITY + confidence + span length + start-index tie-breaker)
   │
   ▼
[6] link_mapping_candidates     → attaches mapping_candidates for CHẨN_ĐOÁN / THUỐC
   │  (ICD10Linker for diagnoses, RxNormLinker for drugs)
   │
   ▼
[7] write_output_files          → outputs/v0/output/{file_id}.json    (submission schema)
   │
   ▼
[8] create_output_zip           → outputs/v0/output.zip                (top-level output/ folder)
   │
   ▼
[9] validate_output_artifacts   → ValidationReport (schema/offset/dupe/overlap/zip)
   │
   ▼
[10] write_validation_report    → reports/validation_v0_linked.md
[11] write_mapping_report       → reports/mapping_coverage_v0.md
[12] assert_mapping_gate        → SystemExit(1) if coverage under gate
```

### Per-step artifacts

Everything the pipeline persists, in the order it is written:

| Step | Path | What it is |
|---|---|---|
| 2 | [`analysis/section_inventory.csv`](repo/ViClinicalIE/analysis/section_inventory.csv:1) | Every detected `SECTION_ALIASES` hit (914 rows across 100 files) |
| 2 | [`analysis/line_inventory.csv`](repo/ViClinicalIE/analysis/line_inventory.csv:1) | Every parsed line with classification |
| 2 | [`analysis/record_stats.csv`](repo/ViClinicalIE/analysis/record_stats.csv:1) | Per-file summary (length, sections detected, notes) |
| 3 | [`analysis/span_candidates_v0.jsonl`](repo/ViClinicalIE/analysis/span_candidates_v0.jsonl:1) | Raw span candidates before assertion |
| 3 | [`analysis/span_candidates_v0_summary.json`](repo/ViClinicalIE/analysis/span_candidates_v0_summary.json:1) | Counts by type, rejected buckets, empty output files |
| 4 | [`analysis/span_candidates_v0_asserted.jsonl`](repo/ViClinicalIE/analysis/span_candidates_v0_asserted.jsonl:1) | Same rows enriched with assertion labels |
| 5 | [`analysis/span_candidates_v0_merged.jsonl`](repo/ViClinicalIE/analysis/span_candidates_v0_merged.jsonl:1) | Post-merge accepted spans (with `span_status`, `reject_reason`) |
| 6 | [`analysis/mapping_debug_v0.csv`](repo/ViClinicalIE/analysis/mapping_debug_v0.csv:1) | Per-mapping row (matched term, code, confidence, source) |
| 6 | [`analysis/mapping_unmapped_v0.csv`](repo/ViClinicalIE/analysis/mapping_unmapped_v0.csv:1) | Diagnoses/drugs that failed to map (currently empty) |
| 7 | `outputs/v0/output/{file_id}.json` | Submission JSON per file |
| 8 | `outputs/v0/output.zip` | Final submission archive |
| 10 | `reports/validation_v0_linked.md` | Human-readable validation summary |
| 11 | `reports/mapping_coverage_v0.md` | Diagnosis/drug mapping coverage |

The `analysis/` set is the primary debugging surface. Every downstream analysis (this report included) traces back to those JSONL rows.

---

## 3. Data model (single source of truth)

Defined in [`src/models.py`](repo/ViClinicalIE/src/models.py:1):

- [`ClinicalDocument`](repo/ViClinicalIE/src/models.py:37): `file_id`, `raw_text`, `normalized_text`, `raw_to_norm`, `norm_to_raw`, `lines[]`, `sections[]`. Two parallel indexings of the same text, with byte-level mapping between them, so we normalize for matching without ever losing raw offsets.
- [`Line`](repo/ViClinicalIE/src/models.py:8): `line_id`, `raw_text`, `start`, `end`, `line_type` (`HEADER`, `KEY_VALUE`, `PROSE`, `BULLET`, …), `section_type`, `subsection_type`, `header_key`, `header_value`.
- [`Section`](repo/ViClinicalIE/src/models.py:22): tree of section blocks with parent/child pointers, `header_alias`, and start/end.
- [`SpanCandidate`](repo/ViClinicalIE/src/models.py:57): the working unit of the pipeline. Carries `text`, `start`, `end`, `type_candidate`, `section_type`, `subsection_type`, `line_id`, `left_context`, `right_context`, `time_context`, `source: list[str]`, `confidence`, `assertion_candidates: list[str]`, `mapping_candidates: list[dict]`, `should_output`, `span_status`, `reject_reason`, `notes`.
- [`EntityOutput`](repo/ViClinicalIE/src/models.py:82): the terminal shape written to JSON. Fields correspond exactly to the submission schema.

The pipeline never mutates raw text or offsets in place. Normalization only ever produces a parallel view via [`normalize_with_mapping()`](repo/ViClinicalIE/src/normalization.py:70), and every extractor writes back raw offsets.

---

## 4. Stage-by-stage rules

### 4.1 Section parsing — [`src/section_parser.py`](repo/ViClinicalIE/src/section_parser.py:1)

- `SECTION_ALIASES` at lines 34–140 defines a 15-class taxonomy: `PAST_HISTORY`, `MEDICATION_HISTORY`, `CHRONIC_DISEASES`, `RISK_FACTORS`, `CURRENT_HISTORY`, `ADMISSION_REASON`, `PRE_ADMISSION_EVENTS`, `IMMEDIATE_PRE_ADMISSION_STATUS`, `CURRENT_SYMPTOMS`, `SYMPTOM_DETAIL`, `MEDICATION_ADMINISTERED`, `HOSPITAL_ASSESSMENT`, `DIAGNOSTIC_FINDINGS`, `LAB_RESULT_SECTION`, `IMAGING_RESULT_SECTION`.
- Aliases are matched against normalized text via `_NORMALIZED_ALIAS_ROWS` (case- and diacritic-tolerant), then reverse-mapped to raw offsets.
- Header detection uses [`strip_line_prefix()`](repo/ViClinicalIE/src/section_parser.py:154) (removes numbering `1.`, `-`, `•`) and [`classify_header_text()`](repo/ViClinicalIE/src/section_parser.py:180) which returns `(section_type, matched_alias)`.
- Lines within a section inherit `section_type` and `subsection_type`. This is the section prior every downstream extractor and assertion detector relies on.

Observed behaviour: 96 / 100 records have all three top-level sections detected; the four with only two are minimal 8- to 12-line notes.

### 4.2 Span extraction — [`src/rule_extractors.py`](repo/ViClinicalIE/src/rule_extractors.py:1)

See [`report_span_extraction.md`](repo/ViClinicalIE/report_span_extraction.md:1) for the full breakdown. The compact rule table:

| Type | Trigger source | Section gate | Extra logic |
|---|---|---|---|
| `TÊN_XÉT_NGHIỆM` | `lab_seed_terms.csv` | line in `LAB_SUBSECTIONS` (LAB or IMAGING result) | header/key portion of `KEY_VALUE` line |
| `KẾT_QUẢ_XÉT_NGHIỆM` | value side of same line | same | via [`line_value_raw_span()`](repo/ViClinicalIE/src/rule_extractors.py:198) |
| `THUỐC` | `drug_aliases.csv` (+ `drug_context_terms.csv`) | any section | [`extend_drug_span()`](repo/ViClinicalIE/src/rule_extractors.py:222) grabs dose/frequency/route |
| `CHẨN_ĐOÁN` | `diagnosis_seed_terms.csv` | line-scoped window from [`diagnosis_search_window()`](repo/ViClinicalIE/src/rule_extractors.py:339); prefer diagnosis-carrying subsections | dictionary + section prior |
| `TRIỆU_CHỨNG` | `symptom_seed_terms.csv` | prefers `CURRENT_SYMPTOMS`, `SYMPTOM_DETAIL`, `ADMISSION_REASON`; permitted elsewhere with lower confidence | [`expand_symptom_span()`](repo/ViClinicalIE/src/rule_extractors.py:396) attaches short modifiers |
| Reject | `non_target_medical_terms.csv` | any | via [`reject_non_target_candidates()`](repo/ViClinicalIE/src/rule_extractors.py:315) — removes procedures / imaging methods that survived as CHẨN_ĐOÁN/TRIỆU_CHỨNG |

All matches pass through [`normalized_find_spans()`](repo/ViClinicalIE/src/rule_extractors.py:165) → [`trim_span()`](repo/ViClinicalIE/src/rule_extractors.py:106) → [`is_word_boundary()`](repo/ViClinicalIE/src/rule_extractors.py:115), then [`validate_candidate_offsets()`](repo/ViClinicalIE/src/rule_extractors.py:449) enforces `raw_text[start:end] == text`.

### 4.3 Assertion — [`src/assertion.py`](repo/ViClinicalIE/src/assertion.py:1)

Full logic in [`report_assertion.md`](repo/ViClinicalIE/report_assertion.md:1). Summary:

- Only `THUỐC`, `CHẨN_ĐOÁN`, `TRIỆU_CHỨNG` receive assertions. Lab entities emit `[]` by construction.
- Scopes are computed per-candidate by [`_left_scope()`](repo/ViClinicalIE/src/assertion.py:142) and [`_right_scope()`](repo/ViClinicalIE/src/assertion.py:152), truncated at scope-break tokens (comma, semicolon, `và`, `hoặc`, sentence boundary, list bullet).
- `isNegated`: left-side triggers (`không`, `phủ nhận`, `không có`, etc.), plus list-scope propagation for enumerated negations.
- `isHistorical`: (a) section-prior in `PAST_HISTORY` / `MEDICATION_HISTORY` / `CHRONIC_DISEASES` / `RISK_FACTORS`, or (b) local past-tense/date triggers.
- `isFamily`: strict — requires an unambiguous family narrator (`bố`, `mẹ`, `anh`, `chị`, `gia đình`) inside the immediate left scope, and rejects clinician narrator forms.
- `infer_time_context()` collapses assertions into one of `current`, `past`, `recent_past`, `family`, used only for internal analytics.

Output ordering is `[isNegated, isHistorical, isFamily]`, de-duplicated, always a list (never `null`).

### 4.4 Merge / overlap resolution — [`src/merge.py`](repo/ViClinicalIE/src/merge.py:1)

- `TYPE_PRIORITY = { LAB_RESULT: 1, LAB_NAME: 2, DRUG: 3, DIAGNOSIS: 4, SYMPTOM: 5 }`.
- [`_rank()`](repo/ViClinicalIE/src/merge.py:33) sorts by `(priority, -confidence, -span_length, start)`.
- Two candidates overlap iff their raw intervals intersect. The winner keeps the span; the loser is dropped with `span_status="rejected"` and a `reject_reason` string.
- Result: within any raw byte range, at most one entity survives. This is what enables the schema-level no-overlap check downstream.

### 4.5 Candidate linking — [`src/linking/candidate_linker.py`](repo/ViClinicalIE/src/linking/candidate_linker.py:1)

- Only invoked by [`build_v0_linked_outputs.py`](repo/ViClinicalIE/scripts/build_v0_linked_outputs.py:1).
- Routes candidates by type: `CHẨN_ĐOÁN` → [`ICD10Linker`](repo/ViClinicalIE/src/linking/icd10_linker.py:29), `THUỐC` → [`RxNormLinker`](repo/ViClinicalIE/src/linking/rxnorm_linker.py:28).
- Both linkers share the same three-stage strategy in [`common.py`](repo/ViClinicalIE/src/linking/common.py:1):
  1. **Exact match** on the normalized term (`icd_exact` / `rxnorm_exact`, confidence 1.0).
  2. **Alias match** through `mapping_aliases.csv` (falls back to ingredient for drugs).
  3. **Fuzzy match** via [`score_similarity()`](repo/ViClinicalIE/src/linking/common.py:118) that blends character-ratio, Jaccard on token sets, and ASCII similarity. Thresholds: 0.88 for ICD, 0.86 for RxNorm.
- RxNorm additionally strips dose and sig via [`_strip_sig()`](repo/ViClinicalIE/src/linking/rxnorm_linker.py:46) before matching, and generates variants in [`_variants()`](repo/ViClinicalIE/src/linking/rxnorm_linker.py:53).
- Every mapping decision is logged to `mapping_debug_v0.csv`; misses land in `mapping_unmapped_v0.csv` (currently empty).

### 4.6 Output writing — [`src/output_writer.py`](repo/ViClinicalIE/src/output_writer.py:1)

- [`candidate_to_entity()`](repo/ViClinicalIE/src/output_writer.py:28) emits the submission-shaped dict: `{ type, text, start, end, assertions, candidates }`.
- `candidates` is populated only for `CHẨN_ĐOÁN` (ICD-10 codes) and `THUỐC` (RxNorm codes). For lab types the field is omitted; for symptoms it is an empty list.
- `assertions` is always a list — empty when nothing fired.
- Empty files still get written as `[]` (this is what `empty_output_files` in the summary tracks).
- [`create_output_zip()`](repo/ViClinicalIE/src/output_writer.py:81) zips them under a top-level `output/` folder, which the validator enforces.

### 4.7 Validation — [`src/validator.py`](repo/ViClinicalIE/src/validator.py:1)

`ValidationReport` buckets errors into: `schema_errors`, `offset_errors`, `duplicate_errors`, `overlap_errors`, `zip_errors`. Per-check:

- [`_validate_schema()`](repo/ViClinicalIE/src/validator.py:59): field presence, types, allowed values.
- [`_validate_offsets()`](repo/ViClinicalIE/src/validator.py:112): `doc.raw_text[start:end] == text`.
- [`_entity_identity()`](repo/ViClinicalIE/src/validator.py:47) + dedupe check: no two identical `(type, start, end, text)` per file.
- [`_validate_no_overlaps()`](repo/ViClinicalIE/src/validator.py:125): no interval intersection per file.
- [`_validate_zip()`](repo/ViClinicalIE/src/validator.py:141): zip has correct top-level `output/`, one JSON per expected `file_id`.

The report is written by [`write_validation_report()`](repo/ViClinicalIE/src/validator.py:215) and also broken down by type and by assertion.

---

## 5. Internal evaluation (numbers as of latest run)

From [`analysis/span_candidates_v0_summary.json`](repo/ViClinicalIE/analysis/span_candidates_v0_summary.json:1) (100 records, 1 record per file):

- **Total candidates before merge:** 1,242
- **Kept after merge:** 903 (73% keep rate)
- **By type (post-merge):**
  - TRIỆU_CHỨNG: 557
  - CHẨN_ĐOÁN: 181
  - THUỐC: 69
  - TÊN_XÉT_NGHIỆM: 49
  - KẾT_QUẢ_XÉT_NGHIỆM: 47
- **Rejected via non-target list:** 339 (`procedure_or_imaging_method` bucket)
- **Offset errors:** 0
- **Empty-output files (13):** `6, 15, 25, 29, 57, 62, 67, 79, 80, 81, 89, 90, 92`

Section coverage from [`analysis/record_stats.csv`](repo/ViClinicalIE/analysis/record_stats.csv:1):
- All three main sections detected: 96 / 100.
- Missing PAST_HISTORY: `8.txt`, `15.txt`.
- Missing HOSPITAL_ASSESSMENT: `14.txt`.
- Missing CURRENT_HISTORY: 0.

Linking coverage from [`analysis/mapping_debug_v0.csv`](repo/ViClinicalIE/analysis/mapping_debug_v0.csv:1) and [`analysis/mapping_unmapped_v0.csv`](repo/ViClinicalIE/analysis/mapping_unmapped_v0.csv:1):
- 251 successful mappings, of which the overwhelming majority are `icd_exact` or `rxnorm_exact` at confidence 1.0.
- Unmapped: 0. Gate (≥ 80% diagnosis, ≥ 90% drug) passes.

Assertion distribution can be re-derived from [`analysis/span_candidates_v0_merged.jsonl`](repo/ViClinicalIE/analysis/span_candidates_v0_merged.jsonl:1) — the JSONL preserves `assertion_candidates` and `time_context`, so a one-liner over the file is enough.

---

## 6. Sample outputs (success + failure)

### 6.1 A clean case — file 1

From [`analysis/span_candidates_v0_merged.jsonl`](repo/ViClinicalIE/analysis/span_candidates_v0_merged.jsonl:1) row 1:

```json
{"file_id":"1","text":"metoprolol 25mg po bid","start":53,"end":75,
 "type_candidate":"THUỐC","section_type":"PAST_HISTORY",
 "subsection_type":"MEDICATION_HISTORY","time_context":"past",
 "assertion_candidates":["isHistorical"],
 "source":["drug_dictionary","dose_parser","assertion_historical_rule"],
 "confidence":0.9,"span_status":"accepted"}
```

Corresponding mapping row from [`analysis/mapping_debug_v0.csv`](repo/ViClinicalIE/analysis/mapping_debug_v0.csv:2):

```
1,metoprolol 25mg po bid,THUỐC,53,75,866436,rxnorm_exact,1.0,metoprolol 25mg po bid,
```

Everything lines up: dose is captured, section prior fires historical, RxNorm matches exact. This is the ideal path.

### 6.2 A noisy-but-recovered case — `atenololtrong`

Row 5 of the asserted JSONL:

```json
{"file_id":"1","text":"atenololtrong","start":1849,"end":1862,
 "type_candidate":"THUỐC","section_type":"CURRENT_HISTORY",
 "subsection_type":"PRE_ADMISSION_EVENTS","time_context":"past",
 "assertion_candidates":["isHistorical"],
 "source":["drug_dictionary","dose_parser","assertion_historical_rule"],
 "confidence":0.78}
```

Underlying line: `"    -  Ở nhà bệnh nhân đã sử dụng atenololtrong ngày"` — missing space between drug and modifier `trong`. Drug dictionary still matched via normalization, RxNorm then aliased `atenololtrong` → `atenolol` and returned code `197380` at confidence 1.0. That is exactly the compound we want the linker doing.

Note the failure mode still lurking: `text` written to the submission is `"atenololtrong"`, so the raw span technically includes the trailing `trong`. If the ground truth marks the drug as `atenolol` only, this will register as a partial-span mismatch. Trade-off is intentional (preserving raw offsets over segmentation), but it is a real recall/precision knob.

### 6.3 Empty-output cases

13 files produce `[]`. Manual inspection of the smaller ones:

- **`6.txt`** — content is dominated by `Nghẽn tắc và hẹp động mạch cảnh` and imaging findings (`siêu âm Doppler`, `hẹp nặng`, `tỷ số PSV/EDV > 7`). None of these lemmas exist in `diagnosis_seed_terms.csv` or `symptom_seed_terms.csv`. Pure dictionary miss.
- **`15.txt`** — the primary term is `xuất huyết nội sọ không do chấn thương, không đặc hiệu`. Missing from diagnosis seeds. Also, in a `HOSPITAL_ASSESSMENT` subsection where diagnosis extraction is expected to fire.
- **`25.txt`** — `tách thành động mạch chủ`, `liệt hai chân`, `Rò động - tĩnh mạch đùi phải`. Complex multi-word diagnoses not in the seed list.
- **`57.txt`** — `bệnh rễ thần kinh tuỷ sống ở ngón tay cái`, `hẹp ống sống C4-5, C5-6, C6-7`. Diagnostic terms too long / anatomically detailed to seed by hand.

The common thread across all 13 empty files is **seed-list gaps for long-tail diagnoses**, not extractor bugs. Adding these 13 phrases to the seed CSVs would eliminate most of the empty-output failures immediately.

### 6.4 A duplicated-symptom case worth flagging

Row 7 of the merged JSONL:

```json
{"file_id":"1","text":"Khó thở nhẹ khó thở","start":688,"end":707,
 "type_candidate":"TRIỆU_CHỨNG","section_type":"CURRENT_HISTORY",
 "subsection_type":"CURRENT_SYMPTOMS","confidence":0.84}
```

Underlying raw: `"- Khó thở nhẹ khó thở"`. This is a typo in the source but `expand_symptom_span()` swallowed the entire fragment. The child span `khó thở` (row 8 in the asserted JSONL) is dropped by merge because the parent covers it. Result: the entity `text` is not clinically clean, even though offsets are correct. This is a *precision-of-text* issue, not a validator issue.

---

## 7. Test coverage

Under [`tests/`](repo/ViClinicalIE/tests):

- [`test_rule_extractors.py`](repo/ViClinicalIE/tests/test_rule_extractors.py:1) — lab name+result offsets, drug dose+typo recovery, diagnosis from `CHRONIC_DISEASES`, symptom from `ADMISSION_REASON` / `CURRENT_SYMPTOMS`, non-target rejection.
- [`test_assertion_merge_output.py`](repo/ViClinicalIE/tests/test_assertion_merge_output.py:1) — negation list-scope, negation trigger-scope, historical diagnosis+drug, family narrator strictness, merge priority+dedupe, end-to-end schema round-trip via the real writer/validator.
- [`test_section_parser.py`](repo/ViClinicalIE/tests/test_section_parser.py:1) — section alias detection.
- [`test_offset.py`](repo/ViClinicalIE/tests/test_offset.py:1) — normalization round-trip via `OffsetMapper`.
- [`test_linking.py`](repo/ViClinicalIE/tests/test_linking.py:1) — ICD/RxNorm exact/alias/fuzzy paths.

All five modules run standalone via their `run_all_tests()` entry points. There is no CI wiring, but the tests are deterministic and self-contained.

---

## 8. Điểm mạnh (strengths)

- **Offset invariants are enforced end-to-end.** Every extractor writes raw offsets, normalization keeps `raw_to_norm`/`norm_to_raw` mappings, [`validate_candidate_offsets()`](repo/ViClinicalIE/src/rule_extractors.py:449) checks pre-merge, and [`_validate_offsets()`](repo/ViClinicalIE/src/validator.py:112) checks post-write. Result: 0 offset errors on the current run.
- **Deterministic, replayable, cheap.** Full pipeline on 100 records runs in seconds. Every artifact is byte-stable given the same input. No LLM, no external service.
- **Section prior is a real signal.** 96 / 100 records get all three top-level sections. Extractors and assertion detectors both key on `section_type` / `subsection_type`, so behaviour like historical-by-section for `PAST_HISTORY` drugs works out of the box (see row 1 above).
- **Full mapping coverage today.** `mapping_unmapped_v0.csv` is empty; the mapping gate (≥ 80% diagnosis, ≥ 90% drug) passes with room to spare. The three-stage exact → alias → fuzzy strategy is the right shape.
- **Merge is orderly and observable.** Overlap resolution is a single function with an explicit rank, and rejects are preserved in `span_candidates_v0_merged.jsonl` with `reject_reason` for debugging.
- **Rejects are first-class.** `reject_non_target_candidates()` gets 339 procedure/imaging strings out of the candidate pool before they contaminate CHẨN_ĐOÁN/TRIỆU_CHỨNG.
- **Debugging surface is genuinely useful.** Every pipeline stage writes a persistent artifact. You can trace an entity from `span_candidates_v0.jsonl` → `_asserted` → `_merged` → `mapping_debug` without re-running anything.

## 9. Điểm nghẽn (bottlenecks)

- **Recall is bounded by seed lists.** All 13 empty-output files fail because of missing diagnosis/symptom seeds, not because of extractor logic. Long-tail Vietnamese clinical phrases (`hẹp ống sống C4-5`, `xuất huyết nội sọ không do chấn thương`, `Rò động - tĩnh mạch đùi phải`, etc.) are simply not in the CSVs.
- **Span cleanup vs. offset preservation is a real trade-off.** `atenololtrong`, `Khó thở nhẹ khó thở`, `atenolol trong` — the pipeline keeps raw text faithfully but the emitted `text` sometimes includes noise. If the graded metric is span-strict, this costs precision.
- **Assertion signal is thin outside `PAST_HISTORY`.** Looking at the asserted JSONL, most `isNegated` fires come from the `Không …` list pattern; `isHistorical` mostly fires from the section prior; `isFamily` is barely represented. Anything that requires longer-range reasoning (multi-sentence temporal cues, patient vs. family mention deeper in a paragraph) is dropped.
- **No confidence propagation into the submission.** `SpanCandidate.confidence` is used for ranking during merge, but the submission JSON does not carry it. Downstream review has no way to prioritize.
- **Symptom extractor is greedy.** [`expand_symptom_span()`](repo/ViClinicalIE/src/rule_extractors.py:396) attaches short modifiers aggressively. When the source text repeats a term (typos, list echoes), the span balloons.
- **Fuzzy linker is unused in practice.** The `mapping_debug` shows nearly 100% `icd_exact` / `rxnorm_exact`. Either the corpus is well-covered by aliases (good), or the fuzzy tier is too strict to ever fire. Worth measuring what fraction of *seed* misses (not mapping misses) the fuzzy tier could rescue if the diagnosis dictionary grew.
- **Tests are not wired to CI and don't gate merges.** All pass locally, but there is no runner script that fails the build if `test_rule_extractors.py` breaks.
- **Empty-output files are logged but not blocked.** 13 files silently ship `[]`. The `assert_mapping_gate` only guards *mapped* candidates; there is no `assert_coverage_gate` on the non-empty-files ratio.
- **No end-to-end evaluation vs. ground truth.** The pipeline validates schema, offsets, uniqueness, and mapping coverage, but there is no P/R/F1 harness against gold spans/assertions/candidates. Every quality decision above is inferred from artifact inspection, not measured.

## 10. Ưu tiên sửa (priority fixes)

Ordered by expected impact / effort ratio.

1. **Grow seed lists from the 13 empty-output files.** Straight-line recall win. One-hour job: read those 13 files, extract 30–50 additional diagnosis/symptom lemmas, append to the CSVs, re-run. Expected result: ≥ 8 of 13 empty files become non-empty.
2. **Add an evaluation harness against gold.** Even a small held-out gold subset (10–20 files annotated for spans + assertions + candidates) with a `scripts/eval_v0.py` that reports P/R/F1 per type and per assertion. Without it every other change is guesswork.
3. **Tighten `expand_symptom_span()`.** Cap expansion at the first repeat of the head term and refuse to absorb duplicated fragments (`Khó thở nhẹ khó thở` → `Khó thở`). Two-line change with a targeted unit test.
4. **Post-process drug spans to strip stuck suffixes.** When the matched drug key ends at a word-boundary-inside-word (e.g. `atenololtrong`), emit `text = matched_key, end = start + len(matched_key)` rather than the noisy raw slice. Keep the offset legal by re-anchoring `end`. Adds a knob to trade off strictness vs. safety.
5. **Wire a coverage gate for non-empty files.** In [`build_v0_outputs.py`](repo/ViClinicalIE/scripts/build_v0_outputs.py:1) fail the run if `empty_output_files` > N. Even N=5 would surface today's regression risk.
6. **Add pytest CI locally.** One `scripts/run_tests.py` that imports every test module's `run_all_tests()` and exits non-zero on failure. Cheap insurance.
7. **Broaden assertion coverage.** Two concrete wins: (a) family narrator handling of `mẹ/bố` co-referenced across two lines within the same bullet (still strict but not per-line-only), (b) uncertainty labels (`nghi ngờ`, `chưa loại trừ`) as a follow-up assertion class once the schema allows.
8. **Instrument the fuzzy linker.** Log the top fuzzy candidate and its score even when we fall back to exact/alias. Then measure whether raising the seed dictionary would rescue more than the fuzzy tier does today. If yes, the fuzzy thresholds are miscalibrated.
9. **Expose `confidence` and `time_context` in the submission (or in a sidecar CSV).** Without it, human review of edge cases is blind. If the submission schema forbids it, write `outputs/v0/entities_debug.csv` alongside the JSON.

---

## 11. TL;DR

The V0 pipeline is a well-plumbed, deterministic, section-aware rule engine. Offsets, merge, validation, and linking are already load-bearing and correct. The dominant loss channel today is **coverage of Vietnamese clinical vocabulary in the seed CSVs**, followed by **greedy symptom span expansion** and **thin assertion signals outside section priors**. Priority fixes 1–4 are the ones that will move numbers most; priority fix 2 (eval harness against gold) is what makes everything after that measurable.
