# Span Extraction Report for ViClinicalIE

## 1. Scope and objective

This report documents the span-extraction component of the ViClinicalIE pipeline. The goal of span extraction is to identify raw-text character spans for clinical concepts and emit intermediate `SpanCandidate` records with exact offsets, entity type, section metadata, source markers, confidence, and review status.

The span extractor targets five output entity types:

- `TÊN_XÉT_NGHIỆM`
- `KẾT_QUẢ_XÉT_NGHIỆM`
- `THUỐC`
- `CHẨN_ĐOÁN`
- `TRIỆU_CHỨNG`

It also records internal `NON_TARGET` candidates for obvious medical/procedure terms that should be rejected from final output.

## 2. Implementation location

The main span-extraction implementation is in [`src/rule_extractors.py`](src/rule_extractors.py). The build script is [`scripts/build_span_candidates.py`](scripts/build_span_candidates.py), which loads input records, parses sections, runs all extractors, deduplicates candidates, validates offsets, and writes analysis artifacts.

Important functions:

| Function | Purpose |
|---|---|
| [`read_term_csv()`](src/rule_extractors.py:70) | Load one-term-per-row resource files. |
| [`unique_terms()`](src/rule_extractors.py:89) | Deduplicate terms after normalization. |
| [`normalized_find_spans()`](src/rule_extractors.py:165) | Match normalized terms and recover raw offsets. |
| [`make_candidate()`](src/rule_extractors.py:124) | Construct validated `SpanCandidate` objects. |
| [`extract_lab_candidates()`](src/rule_extractors.py:237) | Extract lab names and lab values. |
| [`extract_drug_candidates()`](src/rule_extractors.py:291) | Extract medications and nearby dose/frequency trails. |
| [`extract_diagnosis_candidates()`](src/rule_extractors.py:353) | Extract diagnoses from diagnosis-oriented sections. |
| [`extract_symptom_candidates()`](src/rule_extractors.py:410) | Extract symptoms from current-history symptom sections. |
| [`reject_non_target_candidates()`](src/rule_extractors.py:315) | Track explicit rejected spans such as imaging/procedure terms. |
| [`dedupe_candidates()`](src/rule_extractors.py:436) | Remove exact duplicate candidates. |
| [`validate_candidate_offsets()`](src/rule_extractors.py:449) | Keep only candidates whose offsets slice back to their text. |
| [`write_span_candidates_jsonl()`](src/rule_extractors.py:467) | Write intermediate candidates as JSON Lines. |
| [`extraction_summary()`](src/rule_extractors.py:476) | Build a compact extraction summary. |

## 3. Pipeline position

Span extraction runs after document loading, normalization, and section parsing:

```text
input/*.txt
  -> load_input_files
  -> normalize_with_mapping
  -> parse_documents / parse_document_sections
  -> rule-based span extraction
  -> dedupe_candidates
  -> validate_candidate_offsets
  -> span_candidates_v0.jsonl
```

The V0 span-candidate script is [`scripts/build_span_candidates.py`](scripts/build_span_candidates.py). Its orchestration function [`run_extraction()`](scripts/build_span_candidates.py:48) processes every document and applies the extractors in this order:

1. labs
2. drugs
3. diagnoses
4. symptoms
5. non-target rejection candidates

## 4. Data model

Each extracted span is represented by `SpanCandidate` in [`src/models.py`](src/models.py:57). Important fields include:

| Field | Meaning |
|---|---|
| `file_id` | Source input file id. |
| `text` | Exact raw text slice. |
| `start` / `end` | Character offsets in raw text. |
| `type_candidate` | Candidate entity type. |
| `section_type` / `subsection_type` | Parsed section metadata. |
| `line_id` / `line_text` | Parsed line provenance. |
| `source` | Rule/resource markers that produced the candidate. |
| `confidence` | Heuristic confidence score. |
| `should_output` | Whether the candidate may reach final output. |
| `span_status` | Candidate lifecycle state. |
| `reject_reason` | Reason for rejected internal candidates. |

The key invariant is:

```python
raw_text[start:end] == candidate.text
```

This invariant is enforced during candidate construction and final validation.

## 5. Resource files

The extractors use CSV dictionaries from [`data_resources/`](data_resources/):

| Resource | Used by | Content |
|---|---|---|
| [`data_resources/lab_seed_terms.csv`](data_resources/lab_seed_terms.csv) | Lab extractor | Lab-test names and aliases. |
| [`data_resources/drug_aliases.csv`](data_resources/drug_aliases.csv) | Drug extractor | Drug names and aliases. |
| [`data_resources/drug_context_terms.csv`](data_resources/drug_context_terms.csv) | Future/extended drug context rules | Medication context words. |
| [`data_resources/diagnosis_seed_terms.csv`](data_resources/diagnosis_seed_terms.csv) | Diagnosis extractor | Diagnosis terms. |
| [`data_resources/symptom_seed_terms.csv`](data_resources/symptom_seed_terms.csv) | Symptom extractor | Symptom terms. |
| [`data_resources/non_target_medical_terms.csv`](data_resources/non_target_medical_terms.csv) | Diagnosis and rejection logic | Procedure/imaging/non-output terms. |

Terms are read with [`read_term_csv()`](src/rule_extractors.py:70), ignoring empty rows, comments, and a `term` header.

## 6. Normalized matching and raw-offset recovery

Clinical notes contain Vietnamese diacritics, English abbreviations, punctuation variation, and occasional noisy spacing. The extractor therefore matches terms on normalized text but always emits raw offsets.

[`normalized_find_spans()`](src/rule_extractors.py:165) performs this workflow:

1. Normalize the resource term using [`normalize_for_matching()`](src/normalization.py).
2. Search for normalized occurrences inside `ClinicalDocument.normalized_text`.
3. Use `OffsetMapper` from [`src/offset_mapper.py`](src/offset_mapper.py) to recover the corresponding raw span.
4. Trim punctuation/whitespace with [`trim_span()`](src/rule_extractors.py:106).
5. Validate word boundaries using [`is_word_boundary()`](src/rule_extractors.py:115).

This design keeps matching robust while preserving exact raw-text offsets for submission.

## 7. Candidate creation and offset safety

All extractors call [`make_candidate()`](src/rule_extractors.py:124). This function:

- trims leading/trailing punctuation and whitespace;
- rejects empty spans;
- slices `doc.raw_text[start:end]` to produce the candidate text;
- attaches line, section, source, confidence, and status metadata;
- marks rejected candidates with `span_status = "rejected"` when `should_output` is false.

After all extractors run, [`validate_candidate_offsets()`](src/rule_extractors.py:449) removes any candidate whose saved text no longer equals the raw slice.

## 8. Section-aware filtering

Span extraction is deliberately section-aware to reduce false positives.

Entity-specific section/subsection constants are defined near the top of [`src/rule_extractors.py`](src/rule_extractors.py:33):

| Constant | Meaning |
|---|---|
| `LAB_SUBSECTIONS` | Subsections where lab names/results are expected. |
| `DRUG_SUBSECTIONS` | Medication-history and medication-administered subsections. |
| `DIAGNOSIS_SUBSECTIONS` | Chronic disease, diagnostic finding, lab/imaging result sections. |
| `DIAGNOSIS_SECTIONS` | Broader sections that may contain diagnoses. |
| `SYMPTOM_SUBSECTIONS` | Admission reason/current symptom/detail/pre-admission status subsections. |

This means a dictionary term is not accepted everywhere in the note; it must appear in a plausible local context for its entity type.

## 9. Lab extraction

[`extract_lab_candidates()`](src/rule_extractors.py:237) targets lab result sections and emits two kinds of candidates:

- `TÊN_XÉT_NGHIỆM` for test names such as WBC.
- `KẾT_QUẢ_XÉT_NGHIỆM` for values such as numeric results or qualitative values.

The lab value pattern is [`VALUE_PATTERN`](src/rule_extractors.py:49). It supports:

- numeric values such as `14,43`, `5.2`, `<10`;
- optional units such as `mg/dl`, `mmol/l`, `g/dl`, `%`;
- qualitative results such as `âm tính`, `dương tính`, `bình thường`, `tăng`, `giảm`, and `đang chờ`.

Example behavior is tested by [`test_lab_name_and_result_offsets()`](tests/test_rule_extractors.py:45), where `WBC:14,43` produces both a lab-name candidate and a lab-result candidate.

## 10. Drug extraction

[`extract_drug_candidates()`](src/rule_extractors.py:291) matches drug dictionary terms and expands the span to include nearby dosing/frequency descriptors.

The dose/frequency tail is controlled by [`DOSE_TRAIL_PATTERN`](src/rule_extractors.py:57), supporting examples such as:

- `25mg`
- `po`
- `bid`
- `daily`
- `q6h`
- `tablet`
- `capsule`
- formulation suffixes such as `xl`, `xr`, `sr`, `dr`, `ec`

Example test coverage:

- [`test_drug_span_with_dose_and_typo_recovery()`](tests/test_rule_extractors.py:57) verifies `metoprolol 25mg po bid` is extracted as one span.
- The same test verifies noisy text recovery for `atenololtrong` from the configured alias `atenolol trong`.

## 11. Diagnosis extraction

[`extract_diagnosis_candidates()`](src/rule_extractors.py:353) extracts diagnosis terms when they occur in diagnosis-oriented sections or subsections. It also receives non-target terms to help separate output-worthy findings from procedure/imaging action terms.

Common diagnosis contexts include:

- past medical history;
- chronic disease lists;
- hospital assessment;
- diagnostic findings;
- lab/imaging result sections when the finding itself is clinically relevant.

Example test coverage:

- [`test_diagnosis_from_chronic_disease_section()`](tests/test_rule_extractors.py:74) verifies `tăng huyết áp` is extracted from `Các bệnh lý mãn tính` with `PAST_HISTORY` and `CHRONIC_DISEASES` metadata.
- [`test_non_target_rejected_but_finding_extracted()`](tests/test_rule_extractors.py:105) verifies an imaging action can be rejected while a finding such as `sỏi ống mật chủ` is extracted.

## 12. Symptom extraction

[`extract_symptom_candidates()`](src/rule_extractors.py:410) extracts symptom terms mainly from current-history symptom subsections:

- admission reason;
- current symptoms;
- symptom detail;
- immediate pre-admission status.

The helper [`expand_symptom_span()`](src/rule_extractors.py:396) expands common symptom spans to include useful anatomical or timing qualifiers when appropriate.

Example test coverage:

- [`test_symptoms_from_admission_and_current_symptoms()`](tests/test_rule_extractors.py:88) verifies `đau ngực` from admission reason and `khó thở khi gắng sức` from a symptom bullet.

## 13. Non-target rejection

[`reject_non_target_candidates()`](src/rule_extractors.py:315) records obvious non-output medical terms as rejected internal candidates. These candidates are useful for auditing and debugging but should not appear in final output.

Rejected candidates are created with:

- `should_output = False`
- `span_status = "rejected"`
- a `reject_reason` such as non-target/procedure context

This helps reduce false positives from imaging procedures, diagnostic procedures, and other clinical actions that are not target concepts.

## 14. Deduplication and validation

After extractor execution, [`dedupe_candidates()`](src/rule_extractors.py:436) removes exact duplicate candidates while preserving order. Deduplication is important because multiple dictionary terms or rules can target the same raw span.

Then [`validate_candidate_offsets()`](src/rule_extractors.py:449) enforces exact offset correctness. This is the main safety gate before analysis artifacts or downstream output generation.

## 15. Analysis artifacts

[`scripts/build_span_candidates.py`](scripts/build_span_candidates.py) writes:

- `analysis/span_candidates_v0.jsonl`
- `analysis/span_candidates_v0_summary.json`

The summary is produced by [`extraction_summary()`](src/rule_extractors.py:476) and includes:

- total candidate count;
- output candidate count;
- count by entity type;
- rejection counts;
- files with no output candidates;
- offset error count added by the build script.

These artifacts support quick quality review before assertion, linking, merge, and output generation.

## 16. Test coverage

Span extraction is covered by [`tests/test_rule_extractors.py`](tests/test_rule_extractors.py). Current tests verify:

| Test | Coverage |
|---|---|
| [`test_lab_name_and_result_offsets()`](tests/test_rule_extractors.py:45) | Lab name/result extraction and offsets. |
| [`test_drug_span_with_dose_and_typo_recovery()`](tests/test_rule_extractors.py:57) | Drug span expansion and noisy alias recovery. |
| [`test_diagnosis_from_chronic_disease_section()`](tests/test_rule_extractors.py:74) | Diagnosis extraction from chronic disease section. |
| [`test_symptoms_from_admission_and_current_symptoms()`](tests/test_rule_extractors.py:88) | Symptom extraction from admission/current symptom contexts. |
| [`test_non_target_rejected_but_finding_extracted()`](tests/test_rule_extractors.py:105) | Non-target rejection while preserving valid findings. |

Recommended command from the ViClinicalIE directory:

```powershell
python tests\test_rule_extractors.py
```

## 17. Strengths

- Exact raw-offset preservation is a first-class invariant.
- Matching is robust to normalization differences while output remains raw-text based.
- Section-aware filters reduce many dictionary false positives.
- Drug extraction includes dose/frequency expansion.
- Symptom extraction can include useful qualifiers.
- Non-target rejected candidates support transparent error analysis.
- The pipeline is deterministic, offline, reproducible, and easy to debug.

## 18. Known limitations

1. Dictionary coverage is limited by the contents of [`data_resources/`](data_resources/).
2. Section-aware filtering can miss valid entities that appear in unexpected sections.
3. Rule-based expansion may under-capture long multi-clause symptom or medication descriptions.
4. Word-boundary rules can reject useful matches in noisy glued text or accept rare boundary edge cases.
5. Lab result extraction is regex-based and may miss unusual units or complex result formats.
6. Diagnosis/symptom distinction can be ambiguous for phrases that behave as both complaint and clinical finding.
7. The current `line_context` helper returns empty context placeholders, so candidate records do not yet include rich left/right context.

## 19. Recommended improvements

1. Expand dictionaries using error analysis from `analysis/span_candidates_v0.jsonl`.
2. Add a generated report of false positives and false negatives by section/subsection.
3. Add more lab unit patterns and structured key-value parsing for complex lab lines.
4. Add a phrase-expansion rule for longer diagnosis findings in imaging/lab result sections.
5. Add richer left/right context extraction in [`line_context()`](src/rule_extractors.py:101).
6. Add conflict handling between diagnosis and symptom candidates before merge.
7. Add regression tests for glued Vietnamese/English text, punctuation noise, and multiline lists.
8. Consider a hybrid candidate generator for recall, but keep rule validation and offset checks as mandatory gates.

## 20. Summary

The ViClinicalIE span extractor is a rule-based, dictionary-driven, section-aware component centered in [`src/rule_extractors.py`](src/rule_extractors.py). It produces exact-offset `SpanCandidate` records for labs, drugs, diagnoses, and symptoms, while recording rejected non-target medical terms for auditing. The implementation prioritizes deterministic behavior, offset correctness, and transparent intermediate artifacts for downstream assertion detection, linking, merging, and final JSON generation.
