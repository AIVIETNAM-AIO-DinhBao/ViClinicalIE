# Assertion Report for ViClinicalIE

## 1. Scope and objective

This report documents the assertion-detection design implemented for the ViClinicalIE pipeline. Assertions are generated after span extraction and before merge/output generation. They are attached only to clinical concept types that require contextual interpretation:

- `CHẨN_ĐOÁN`
- `THUỐC`
- `TRIỆU_CHỨNG`

Laboratory entities, `TÊN_XÉT_NGHIỆM` and `KẾT_QUẢ_XÉT_NGHIỆM`, are intentionally emitted with an empty assertion list because assertion scoring is not meaningful for lab names/results in the current schema.

The implemented assertion labels are:

- `isNegated`: the concept is explicitly denied or excluded.
- `isHistorical`: the concept belongs to past history, chronic history, or pre-admission medication context.
- `isFamily`: the concept belongs to a family member rather than the patient.

## 2. Implementation location

The assertion module is implemented in [`src/assertion.py`](src/assertion.py). It is consumed by the V0 pipeline after candidates are extracted by [`src/rule_extractors.py`](src/rule_extractors.py), and before candidates are merged and written to JSON outputs.

Primary entry point:

- [`add_assertions()`](src/assertion.py:243): iterates through span candidates, skips lab entities, applies assertion rules, stores ordered assertion labels in `assertion_candidates`, and updates `time_context`.

Supporting logic:

- [`is_negated()`](src/assertion.py:165): detects local negation cues.
- [`is_historical()`](src/assertion.py:189): combines section/subsection priors with temporal triggers.
- [`is_family()`](src/assertion.py:210): detects strict family-history patterns.
- [`infer_time_context()`](src/assertion.py:230): derives coarse time context for review/debugging.

## 3. Design approach

The current implementation uses a deterministic ConText/NegEx-style strategy adapted to Vietnamese clinical notes:

1. Use parsed line/section metadata as a strong prior.
2. Match normalized lexical triggers within local scope.
3. Stop assertion scope at sentence or contrast boundaries.
4. Keep output reproducible and offline, with no model/API dependency.

This is appropriate for the available notes because the corpus is semi-structured, includes repeated section headers, and uses relatively consistent Vietnamese cue phrases such as `không`, `tiền sử`, `âm tính`, and family-member terms.

## 4. Entity eligibility

Assertion rules are restricted by entity type:

| Entity type | Assertion behavior |
|---|---|
| `CHẨN_ĐOÁN` | Eligible for `isNegated`, `isHistorical`, `isFamily` |
| `THUỐC` | Eligible for `isNegated`, `isHistorical`; not eligible for `isFamily` |
| `TRIỆU_CHỨNG` | Eligible for `isNegated`, `isHistorical`, `isFamily` |
| `TÊN_XÉT_NGHIỆM` | Always `[]` |
| `KẾT_QUẢ_XÉT_NGHIỆM` | Always `[]` |

The entity filter is defined by `ASSERTION_TYPES` and `LAB_TYPES` in [`src/assertion.py`](src/assertion.py:19).

## 5. Negation assertion

### 5.1 Trigger set

Negation is detected using left-side and right-side cues.

Left-side cues include:

- `không ghi nhận`
- `không phát hiện`
- `không thấy`
- `không có`
- `phủ nhận`
- `loại trừ`
- `không`
- `chưa`

Right-side cue:

- `âm tính`

The trigger lists are defined in [`NEGATION_TRIGGERS`](src/assertion.py:24) and [`RIGHT_NEGATION_TRIGGERS`](src/assertion.py:34).

### 5.2 Scope rule

[`is_negated()`](src/assertion.py:165) checks:

1. Text to the left of a candidate inside the same line/scope.
2. Text to the right of a candidate for post-negation cues such as `âm tính`.

Scope is limited by [`SCOPE_BREAK_MARKERS`](src/assertion.py:86):

- period
- semicolon
- newline
- contrast conjunctions such as `nhưng`, `tuy nhiên`, and `song`

This allows list-style negation such as:

```text
Không có sốt, ớn lạnh, nôn, táo bón, ho
```

All listed symptoms after the cue can receive `isNegated` until a scope break is reached.

## 6. Historical assertion

### 6.1 Section and subsection priors

Historical context is assigned when a candidate appears in strong past-history sections/subsections. The implemented priors are handled by [`_has_historical_prior()`](src/assertion.py:178):

- `PAST_HISTORY` section marks eligible entities as historical.
- `MEDICATION_HISTORY` marks `THUỐC` as historical.
- `CHRONIC_DISEASES` marks `CHẨN_ĐOÁN` as historical.

These priors cover common note regions such as chronic disease history and medications before admission.

### 6.2 Lexical triggers

If no section prior applies, [`is_historical()`](src/assertion.py:189) scans the line and left context for temporal/history triggers, including:

- `thuốc trước khi nhập viện`
- `bệnh lý mãn tính`
- `bệnh lý mạn tính`
- `bệnh mãn tính`
- `bệnh mạn tính`
- `tiền sử`
- `trước đây`
- `đã từng`
- `từng bị`
- `mạn tính`
- `mãn tính`
- `đã điều trị`
- `đã sử dụng`
- `đã ngừng`

The list is defined in [`HISTORICAL_TRIGGERS`](src/assertion.py:36).

### 6.3 Time context

[`infer_time_context()`](src/assertion.py:230) assigns a coarse debug context:

| Condition | `time_context` |
|---|---|
| Has `isHistorical` | `past` |
| Section is `HOSPITAL_ASSESSMENT` | `in_hospital` |
| Subsection is `PRE_ADMISSION_EVENTS` | `recent_past` |
| Section is `CURRENT_HISTORY` | `current` |
| Otherwise | previous value or `unknown` |

This field is useful for downstream review and future error analysis.

## 7. Family assertion

Family assertion is intentionally strict to avoid false positives from narrator phrases.

### 7.1 Positive pattern

[`is_family()`](src/assertion.py:210) only applies to `CHẨN_ĐOÁN` and `TRIỆU_CHỨNG`. It requires a family term before the candidate and a relation trigger between the family term and the candidate.

Family terms are defined in [`FAMILY_TERMS`](src/assertion.py:53), including examples such as:

- `gia đình`
- `người nhà`
- `bố`
- `cha`
- `mẹ`
- `con trai`
- `con gái`
- `anh trai`
- `chị gái`

Relation triggers are defined in [`FAMILY_RELATION_TRIGGERS`](src/assertion.py:72):

- `chẩn đoán`
- `tiền sử`
- `mắc`
- `bị`
- `có`

Valid examples:

```text
Mẹ bệnh nhân bị đái tháo đường.
Bố bệnh nhân có tiền sử hen.
Nhiều người trong gia đình có triệu chứng tương tự.
```

### 7.2 Narrator rejection

The rule explicitly rejects family-member mentions that only describe who reported the patient’s symptoms. This is handled by [`_looks_like_family_narrator()`](src/assertion.py:201) using [`FAMILY_NARRATOR_PATTERNS`](src/assertion.py:73).

Rejected examples:

```text
Người nhà nhận thấy bệnh nhân khó thở.
Theo lời gia đình, bệnh nhân đau ngực.
Con trai phát hiện bệnh nhân ngất.
```

These should not receive `isFamily`, because the concept still belongs to the patient.

## 8. Ordering and output policy

Assertions are deduplicated and emitted in stable order by [`_unique_ordered()`](src/assertion.py:94):

1. `isNegated`
2. `isHistorical`
3. `isFamily`

The stable order avoids noisy diffs and keeps JSON outputs deterministic.

Source markers are appended when a rule fires:

| Assertion | Source marker |
|---|---|
| `isNegated` | `assertion_negation_rule` |
| `isHistorical` | `assertion_historical_rule` |
| `isFamily` | `assertion_family_rule` |

This allows later analysis of which rule family produced each assertion.

## 9. Test coverage

Assertion behavior is covered in [`tests/test_assertion_merge_output.py`](tests/test_assertion_merge_output.py). Key tests include:

- [`test_negation_list_scope()`](tests/test_assertion_merge_output.py:60): verifies that one negation cue can cover multiple symptoms in a list.
- [`test_negation_trigger_scope()`](tests/test_assertion_merge_output.py:81): verifies `phủ nhận` and `không ghi nhận` cue behavior.
- [`test_historical_diagnosis_and_drug()`](tests/test_assertion_merge_output.py:108): verifies chronic diagnosis and medication-history priors.
- [`test_family_strict_not_narrator()`](tests/test_assertion_merge_output.py:128): verifies true family disease mention versus family narrator mention.
- [`test_output_writer_and_validator_schema()`](tests/test_assertion_merge_output.py:171): verifies that lab assertions remain empty in final JSON schema.

Recommended command from the ViClinicalIE directory:

```powershell
python tests\test_assertion_merge_output.py
```

## 10. Strengths

- Fully deterministic and reproducible.
- Uses Vietnamese clinical cues directly.
- Combines section priors and local trigger scope.
- Keeps assertions off lab entities.
- Handles common negated symptom lists.
- Avoids common `isFamily` false positives caused by narrator phrases.
- Adds source markers and time context for review/debugging.

## 11. Known limitations

1. Negation trigger matching is broad; a cue such as `không` can over-scope if punctuation or section parsing is noisy.
2. Scope break handling is line-oriented and may miss complex sentence structures with long coordination.
3. `PRE_ADMISSION_EVENTS` currently maps to `recent_past` in `time_context`, but it is not automatically `isHistorical`, which is conservative but can miss some past events.
4. The rule set does not yet model double negation or nuanced expressions such as `không còn`.
5. Family-history detection only looks before the candidate in the same line, so uncommon postposed family patterns may be missed.
6. Historical cues such as `đã ngừng` may be ambiguous between past medication usage and resolved/non-current symptoms.

## 12. Recommended improvements

Priority improvements for the next iteration:

1. Add a configurable assertion trigger resource file, for example `data_resources/assertion_triggers.json`, instead of hard-coding all cue lists in [`src/assertion.py`](src/assertion.py).
2. Add more unit tests for `không còn`, `đã hết`, `đã ngừng`, and contrastive cases with `nhưng`.
3. Add an assertion error-analysis artifact that counts assertion labels by entity type, section, and source marker.
4. Expand family-history patterns for more Vietnamese kinship terms while keeping narrator rejection strict.
5. Add optional confidence or rule-strength metadata for review, without changing the final submission schema.
6. Evaluate assertion Jaccard against a small manually reviewed dev set.

## 13. Summary

The current ViClinicalIE assertion system is a rule-based, section-aware baseline centered in [`src/assertion.py`](src/assertion.py). It supports `isNegated`, `isHistorical`, and `isFamily` for diagnosis, drug, and symptom entities while leaving lab entities assertion-free. The design is conservative, reproducible, and aligned with the project’s priority of exact span offsets and deterministic JSON generation.