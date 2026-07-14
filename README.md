# ViClinicalIE

Rule-first clinical information extraction and normalization system for the Viettel AI Race clinical text task.

Current repository status: **Phase 9 metric-guided calibration complete on top of Phase 12.5 inference** — the project can run the modular pipeline through:

```text
preprocess + offset mapping
→ section detection
→ baseline span extraction
→ deterministic type resolution
→ deterministic assertion detection
→ ICD-10 candidate generation for CHẨN_ĐOÁN
→ RxNorm candidate generation for THUỐC
→ conservative merge/postprocess cleanup
→ final JSON formatting + schema/offset validation
→ golden evaluation and error reports
→ reusable inference CLI + BTC-format output.zip trial package
→ deterministic Phase 9 rule/candidate/assertion calibration
```

It is still **not a final model-based solution**, but the rule-first baseline has now been tuned with golden metrics and can generate a schema-valid 100-file Phase 9 submission zip.

## Setup

```bash
pip install -r requirements.txt
```

Optional ML dependencies for later NER and dense retrieval phases:

```bash
pip install -r requirements-ml.txt
```

## Smoke Checks

Validate the canonical data layout and golden offsets:

```bash
python scripts/check_setup.py --config configs/default.yaml
```

Run tests:

```bash
python -m pytest -q
```

For the current Phase 9 baseline, useful targeted checks are:

```cmd
set PYTHONUTF8=1
python -m pytest -q tests\test_candidate_selector.py tests\test_icd10_linker.py tests\test_drug_parser.py tests\test_rxnorm_linker.py tests\test_postprocess_span_utils.py tests\test_postprocess_cleanup.py tests\test_postprocess_merge.py tests\test_postprocessor.py tests\test_json_formatter.py tests\test_prediction_schema_validator.py tests\test_file_validator.py tests\test_evaluation_span_matcher.py tests\test_evaluation_metrics.py tests\test_evaluator.py
python scripts\run_phase8_smoke.py --config configs\default.yaml --max-files 2 --sample-limit 10
python scripts\run_phase10_smoke.py --config configs\default.yaml --max-files 2 --sample-limit 5
python scripts\run_validate.py --config configs\default.yaml --input-dir data\golden\input --pred-dir data\golden\gold --report-dir outputs\reports\golden_schema_validation --expected-count 20
python scripts\run_phase11_smoke.py --config configs\default.yaml --max-files 2 --sample-limit 5
python scripts\run_evaluate.py --config configs\default.yaml --input-dir data\golden\input --gold-dir data\golden\gold --pred-dir data\golden\gold --report-dir outputs\reports\phase12_gold_self_eval --expected-count 20
python scripts\run_inference.py --config configs\default.yaml --input-dir data\raw\input --output-dir outputs\predictions\submission_trial\output --report-dir outputs\reports\submission_trial_validation --expected-count 100 --disable-sparse-retrieval
python scripts\make_submission_zip.py --pred-dir outputs\predictions\submission_trial\output --zip-path outputs\submission\output.zip --expected-count 100 --overwrite
python scripts\run_inference.py --config configs\default.yaml --input-dir data\golden\input --output-dir outputs\predictions\phase9_golden20 --report-dir outputs\reports\phase9_validation --expected-count 20 --disable-sparse-retrieval
python scripts\run_evaluate.py --config configs\default.yaml --input-dir data\golden\input --gold-dir data\golden\gold --pred-dir outputs\predictions\phase9_golden20 --report-dir outputs\reports\phase9_eval --expected-count 20
python scripts\analyze_phase9_errors.py --report-dir outputs\reports\phase9_eval --top-k 12
```

`PYTHONUTF8=1` is recommended on Windows when paths/usernames contain Vietnamese characters.

## Canonical Data Layout

- `data/raw/input/`: public input files `1.txt` through `100.txt`.
- `data/golden/input/`: copied raw input files for the 20 golden examples.
- `data/golden/gold/`: golden annotations for IDs `1` through `20`.
- `data/terminologies/icd10_byt.csv`: ICD-10 source table.
- `data/terminologies/RXNCONSO.RRF`: RxNorm source table.

Root-level source files are intentionally left in place.

## Implemented Modules by Phase

- **Phase 0:** repo skeleton, config/path loading, IO utilities, logging helpers, shared dataclasses.
- **Phase 1:** ICD-10/RxNorm parsing, alias tables, sparse BM25/TF-IDF retrieval artifacts.
- **Phase 2:** raw-preserving preprocessing, offset mapping, chunking.
- **Phase 3:** section detection. A section is a structural part of a clinical note such as `Tiền sử bệnh`, `Thuốc trước khi nhập viện`, `Kết quả xét nghiệm`; it is used as a prior/context feature, not as a hard rule.
- **Phase 4:** baseline span candidates from dictionary, drug, lab, imaging, problem extractors; NER interface is present but disabled.
- **Phase 5:** deterministic type resolution to one of the five required entity types.
- **Phase 6:** deterministic mention-level assertion detection (`isNegated`, `isHistorical`, `isFamily`).
- **Phase 7:** ICD-10 linker wrapper for `CHẨN_ĐOÁN` entities.
- **Phase 8:** RxNorm linker wrapper and drug parser for `THUỐC` entities.
- **Phase 10:** Conservative postprocessor for exact duplicates, overlap cleanup, safe span trimming, clear false-positive filtering, and candidate/assertion cleanup.
- **Phase 11:** Final JSON formatter plus schema/file/directory validator and Phase 11 smoke writer.
- **Phase 12:** Golden evaluator with exact/relaxed matching, per-file/per-type metrics, assertion/candidate metrics, and JSONL/CSV/Markdown error reports.
- **Phase 12.5:** Minimal reusable inference pipeline/CLI and BTC-format `output.zip` creator for 100-file trial submission.
- **Phase 9:** Metric-guided deterministic calibration for extractor precision, qualitative lab/imaging results, negation scope, and ICD candidate selection.

## Ghi chú nhanh bằng tiếng Việt

### `section` là gì?

Trong repo này, **section** là nhãn cấu trúc cho từng đoạn/chunk của bệnh án, ví dụ:

- `PAST_HISTORY`: tiền sử bệnh.
- `PRE_ADMISSION_MEDICATION`: thuốc trước khi nhập viện.
- `CURRENT_SYMPTOM`: triệu chứng hiện tại / triệu chứng khi nhập viện.
- `LAB_RESULT`: kết quả xét nghiệm.
- `IMAGING_RESULT`: kết quả chẩn đoán hình ảnh.
- `PROCEDURE`: thủ thuật.
- `DIAGNOSIS_FINDING`: phát hiện/chẩn đoán khác.

Section giúp pipeline hiểu **ngữ cảnh** của một span, nhưng **không được dùng như luật cứng**. Ví dụ:

- Một thuốc trong section `Thuốc trước khi nhập viện` thường có khả năng `isHistorical` cao.
- Nhưng một thuốc kiểu `aspirin 325mg x 1` trong diễn biến cấp cứu không nên bị gán historical chỉ vì nằm gần đoạn trước nhập viện.
- Một triệu chứng trong section tiền sử có thể là tiền sử thật, nhưng cũng có thể là triệu chứng hiện tại được nhắc lại.

Vì vậy, section hiện được dùng như **feature/prior** cho extractor, type resolver và assertion detector. Quyết định cuối vẫn dựa trên mention-level context: cue phủ định, cue tiền sử, người trải nghiệm triệu chứng, thuốc đang dùng hay thuốc mới được chỉ định, v.v.

### Hiện tại đã done Phase 9 chưa?

**Có:** repo đã done **Phase 9 deterministic metric-guided calibration** sau Phase 12.5. Vẫn chưa dùng model NER/ML thật, nhưng baseline rule-first đã được tune bằng golden20 evaluator.

Đã có thể chạy chuỗi module:

```text
raw text
→ preprocess/chunk/offset mapping
→ section detection
→ span extraction baseline
→ type resolution
→ assertion detection
→ ICD-10 linking cho CHẨN_ĐOÁN
→ RxNorm linking cho THUỐC
→ postprocess merge/cleanup
→ format JSON + validate schema/offset
→ evaluate against golden + write error reports
→ run inference trên 100 raw files + package output.zip
→ tune deterministic rules/candidates/assertions bằng Phase 9
```

Phase 9 đã tạo được `outputs/submission/output_phase9.zip` để nộp thử. Đây là bản tốt hơn `outputs/submission/output.zip` baseline Phase 12.5.

### Kết quả re-check mới nhất

Ngày 2026-07-13, targeted checks cho Phase 12 đã chạy thành công:

```text
147 passed in 1.97s

Prediction validation completed.
input_files_checked: 20
prediction_files_checked: 20
entities_checked: 525
error_count: 0
warning_count: 0

Golden evaluation completed.
files_evaluated: 20
gold_entities: 370
pred_entities: 525
exact_f1: 0.1788
relaxed_f1: 0.3642
assertion_exact_match_rate: 0.5658
candidate_hit_rate: 0.4000
```

Điều này xác nhận:

- Full test suite pass.
- Golden schema validation pass với `error_count = 0`; 14 warnings là duplicate exact có sẵn trong golden.
- Phase 11 smoke format/write/validate predictions chạy được trên subset golden và theo batch 20 golden files.
- Formatter không xuất debug fields như `confidence`/`provenance`.
- Validator bắt schema, offset, assertion, candidate và file-level errors.
- Predictions sinh từ Phase 11 smoke có offset đúng, không candidate sai type, không assertion invalid.
- Phase 12 gold-vs-gold self-match đạt exact/relaxed F1 = 1.0, chứng minh matcher xử lý duplicate gold đúng.
- Phase 12 evaluator chạy trên 20 accumulated Phase 11 predictions và sinh reports tại `outputs/reports/phase12_eval_golden20`.

Trial submission re-check mới nhất:

```text
Prediction validation completed.
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

output.zip:
entry_count: 100
first_entries: ['output/1.json', 'output/2.json', 'output/3.json', 'output/4.json', 'output/5.json']
last_entries: ['output/96.json', 'output/97.json', 'output/98.json', 'output/99.json', 'output/100.json']
all_under_output: True
```

Known issue vẫn còn: quality baseline vẫn chưa cao; nên dùng `outputs/reports/phase9_eval` và UI Phase 13 để tune tiếp có kiểm soát.

Phase 9 re-check mới nhất:

```text
Golden20 evaluation:
pred_entities: 455
exact_f1: 0.2303       # baseline cũ 0.1788
relaxed_f1: 0.4145     # baseline cũ 0.3642
assertion_exact_match_rate: 0.6588
candidate_hit_rate: 0.8000
span_mismatch_count: 76
type_mismatch_count: 36
candidate_mismatch_count: 1

100-file Phase 9 validation:
prediction_files_checked: 100
entities_checked: 1867
error_count: 0
warning_count: 0
offset_error_count: 0
schema_error_count: 0
invalid_assertion_count: 0
wrong_type_candidate_count: 0

output_phase9.zip:
entry_count: 100
all_under_output: True
```

## Not Yet Implemented

- Streamlit validation UI.
- NER/dense/cross-encoder model components.
- Source-package rebuild instructions for BTC source-code review.

## Recommended Next Work

The practical next milestone is UI/error review and optional next calibration pass:

```text
Phase 13 Streamlit UI
→ optional Phase 9.1 targeted calibration
→ Phase 14/15 model-assisted components
```

Roadmap chi tiết hơn:

1. **Phase 13 — Streamlit UI:** xem raw text, highlight prediction/gold, debug section/span/candidate/assertion.
2. **Optional Phase 9.1 calibration:** tune tiếp dựa trên UI + `outputs/reports/phase9_eval`.
3. **Phase 14/15 — NER + dense/reranker:** chỉ nên làm sau khi rule baseline và evaluator ổn.
4. **Phase 16/17 — Final hardening + packaging:** chạy lại inference đủ 100, validate, zip, và README rebuild cho BTC.



