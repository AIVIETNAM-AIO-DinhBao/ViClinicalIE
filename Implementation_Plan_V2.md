# Implementation Plan V2.2
## Kế hoạch nâng cấp ViClinicalIE theo ba năng lực: Medical NER, Assertion Detection và Entity Linking

**Dự án:** Viettel AI Competition – Clinical Text Entity Extraction & Normalization  
**Ngày cập nhật:** 2026-07-20  
**Trạng thái chương trình:** đang chuẩn bị triển khai **Phase 1 – Medical NER**  
**Điểm xuất phát:** repo V1 đã có pipeline rule-based, assertion rules, sparse linking, validation và packaging  
**Baseline repo chính:** khoảng 24–25 điểm; phải được tái tạo và freeze trước khi dùng làm mốc so sánh  
**Kiến trúc mục tiêu:** GLiNER-centered Hybrid Clinical NLP Pipeline  

---

# 1. Mục đích tài liệu

Tài liệu này chuyển `Solution_Design_V2.md` thành kế hoạch triển khai có thứ tự, đầu ra, dependency, thí nghiệm và acceptance gate cụ thể.

Chương trình chỉ có **ba phase chính**, tương ứng với ba năng lực nối tiếp nhau:

1. **Medical NER:** tìm span y khoa và gán một trong năm entity types.
2. **Assertion Detection:** xác định phủ định, tiền sử và người trải nghiệm cho entity đã tìm được.
3. **Entity Linking:** ánh xạ chẩn đoán sang ICD-10 và thuốc sang RxNorm.

Các công việc như freeze baseline, chốt annotation contract, scorer, split, validation, reproducibility và release hardening là **điều kiện nền/xuyên suốt**, không phải một phase thứ tư.

Mục tiêu gần của chương trình là hoàn thành Medical NER trước. Assertion Detection và Entity Linking chỉ bắt đầu triển khai chính thức khi contract đầu ra của phase trước đã được freeze.

---

# 2. Nguồn tham chiếu và thứ tự ưu tiên

Kế hoạch được tổng hợp từ các nguồn sau:

| Ưu tiên | Nguồn | Vai trò |
|---|---|---|
| 1 | `Training Session.pdf` | Tài liệu xương sống: phân rã ba bước NER → assertion → linking; GLiNER, ConText, SapBERT/FAISS và retrieve-then-rerank |
| 2 | `Solution_Design_V2.md` | Kiến trúc V2 đã hợp nhất Training Session với production guardrails của V1 |
| 3 | `Implementation Plan.md` | Tài liệu support tương đương `Implementation Plan.pdf`: module, test, data contract và kinh nghiệm triển khai V1 |
| 4 | Trạng thái repo hiện tại | Nguồn sự thật về code đã có, đường dẫn, config, test và baseline thực tế |

Quy tắc xử lý khác biệt:

1. Training Session quyết định **trục ba phase** và foundational kernel.
2. Solution Design V2 quyết định **kiến trúc mục tiêu và acceptance gates**.
3. Implementation Plan V1 cung cấp **chi tiết triển khai có thể tái sử dụng**.
4. Repo hiện tại quyết định **cái gì đã có, cái gì phải sửa và cái gì phải tạo mới**.
5. Mọi điểm số được báo cáo từ môi trường khác chỉ là tham chiếu cho đến khi chạy cùng input, terminology snapshot, scorer, config và phần cứng/model conditions.

---

# 3. Executive summary

## 3.1 Pipeline mục tiêu

```text
Raw Vietnamese clinical note
        │
        ▼
Preprocessing + raw-offset mapping + structure parsing
        │
        ▼
PHASE 1 — MEDICAL NER
GLiNER semantic backbone
+ V1 drug/lab/imaging/problem experts
+ boundary cleanup + evidence fusion + type resolution
        │
        │  output: frozen span + type + confidence + provenance
        ▼
PHASE 2 — ASSERTION DETECTION
ConText-style rules + section/clause scope
+ conditional wider-context classifier
        │
        │  output: frozen entity + assertion set
        ▼
PHASE 3 — ENTITY LINKING
exact/sparse + SapBERT/FAISS dense retrieval
+ structured constraints + conditional reranker
        │
        ▼
Overlap cleanup + strict validation + deterministic packaging
```

## 3.2 Quyết định đã cam kết

Các nội dung sau thuộc **committed path**:

- GLiNER là semantic backbone của nhánh V2.
- Rule/parsers V1 được giữ làm precision experts, structural parsers và safety guards.
- Raw text bất biến; mọi output span phải map đúng raw offset.
- Assertion và linking không được phát minh hoặc thay đổi span của Medical NER.
- ConText-style rules là assertion baseline bắt buộc.
- Semantic dense retrieval là thành phần lõi của Entity Linking theo Training Session, không phải optional afterthought.
- Reranker chỉ được chọn trong candidate pool đã retrieve; không được sinh code tự do.
- V1 được giữ làm production fallback nếu nhánh V2 không qua release gate.
- Final inference phải offline, deterministic và fail-fast.

## 3.3 Nâng cấp có điều kiện

Chỉ thực hiện hoặc promote khi error analysis và acceptance gate chứng minh có ích:

- focused/multi-pass GLiNER;
- GLiNER fine-tuning;
- learned evidence fusion/type resolver;
- transformer assertion classifier;
- cross-encoder hoặc local-LLM reranker;
- top-2 candidate output;
- advanced boundary alternatives.

## 3.4 Deferred mặc định

Không nằm trên critical path ban đầu:

- external API inference;
- LLM tự sinh ICD/RxNorm code;
- LLM assertion trên mọi entity;
- large-scale silver generation trước khi task-aligned data được chứng minh chưa đủ;
- cross-lingual projection trước khi hoàn tất baseline và data audit;
- end-to-end generative model thay toàn bộ pipeline;
- learned overlap optimizer trước deterministic selector.

---

# 4. Current state và gap analysis

## 4.1 Thành phần repo chính đã có

| Năng lực | Hiện trạng | Module chính |
|---|---|---|
| Pipeline | Đã chạy end-to-end | `src/pipeline.py` |
| Raw-preserving preprocessing | Đã có | `src/preprocess/` |
| Section parsing | Đã có | `src/section/` |
| Rule extractors | Đã có drug/lab/imaging/problem/dictionary | `src/extractors/` |
| Type resolution | Đã có deterministic resolver | `src/type_resolution/` |
| Token-classification NER infra | Đã có nhưng disabled; chưa phải GLiNER | `src/extractors/ner_extractor.py`, `src/ner/` |
| Assertion rules | Đã có | `src/assertion/` |
| ICD/RxNorm sparse linking | Đã có | `src/linking/` |
| Deterministic rerank-lite | Đã có | `src/linking/rerank_lite.py` |
| Evaluation/error analysis | Đã có nền tảng | `src/evaluation/` |
| Validation/formatting | Đã có | `src/validation/`, `src/formatting/` |
| CLI/packaging | Đã có | `scripts/run_inference.py`, `scripts/run_validate.py`, `scripts/make_submission_zip.py` |

## 4.2 Training repo tham chiếu đã có

`training-repo` cung cấp implementation tham chiếu cho:

- zero-shot GLiNER;
- ConText assertions;
- SapBERT + FAISS retrieval;
- constrained local-LLM reranking;
- scorer và sample configs.

Không copy nguyên package `training-repo/src/medextract` vào repo chính. Chỉ port logic đã kiểm chứng qua interface hiện tại của repo chính:

```text
BaseExtractor
→ SpanCandidate
→ TypeResolver
→ FinalEntity
→ AssertionDetector
→ ICD10Linker/RxNormLinker
```

## 4.3 Khoảng trống phải xử lý

| Gap | Ảnh hưởng | Phase xử lý |
|---|---|---|
| Chưa có GLiNER adapter trong pipeline chính | Chưa tái tạo được backbone từ Training Session | Medical NER |
| NER hiện tại là token classifier disabled | Không thể coi hạ tầng hiện có là GLiNER baseline | Medical NER |
| Chưa có controlled GLiNER benchmark | Không biết checkpoint/labels/chunking/threshold tốt nhất | Medical NER |
| Union rule + model chưa có evidence fusion rõ ràng | Dễ tăng false positive và duplicate | Medical NER |
| Annotation contract còn quyết định mở | Metric và training label có thể không nhất quán | Foundation trước Medical NER |
| Assertion rules cần đánh giá scope có cấu trúc | Sai long-range/list/family context | Assertion Detection |
| Dense linking chưa là core ở repo chính | Chưa tái tạo foundational linker | Entity Linking |
| Chưa audit candidate recall trước rerank | Không biết bottleneck là retrieval hay ranking | Entity Linking |

---

# 5. Scope và output contract

## 5.1 Entity schema

Medical NER phải trả đúng một trong năm types:

```text
TRIỆU_CHỨNG
CHẨN_ĐOÁN
THUỐC
TÊN_XÉT_NGHIỆM
KẾT_QUẢ_XÉT_NGHIỆM
```

Assertion Detection chỉ áp dụng cho:

```text
TRIỆU_CHỨNG
CHẨN_ĐOÁN
THUỐC
```

và chỉ serialize:

```text
isNegated
isHistorical
isFamily
```

Entity Linking chỉ serialize candidates cho:

```text
CHẨN_ĐOÁN → ICD-10
THUỐC     → RxNorm
```

## 5.2 Invariants xuyên suốt

Mọi phase phải bảo toàn:

```python
raw_text[entity.start:entity.end] == entity.text
```

Các invariant khác:

- position dùng zero-based, end-exclusive offsets;
- normalized/search/no-diacritics views không được dùng trực tiếp làm output offset;
- mọi quyết định merge, type, assertion và linking phải giữ provenance;
- không phase downstream nào được âm thầm sửa text/start/end/type đã freeze;
- candidate code phải thuộc terminology snapshot đã khai báo;
- config/model/data/terminology snapshot phải có hash;
- cùng input + artifact + seed phải tạo output byte-identical ở final mode.

## 5.3 Handoff contracts

### Medical NER → Assertion Detection

```python
FinalEntity(
    text=...,
    start=...,
    end=...,
    type=...,
    assertions=[],
    candidates=[],
    confidence=...,
    provenance=...,
)
```

### Assertion Detection → Entity Linking

Giữ nguyên toàn bộ trường từ Medical NER, chỉ cập nhật `assertions` và assertion provenance.

### Entity Linking → Formatter

Giữ nguyên text, offsets, type và assertions; chỉ cập nhật `candidates` cho diagnosis/drug và linking provenance.

---

# 6. Program foundations và workstreams xuyên suốt

Các công việc trong phần này bắt buộc nhưng không được tính thành một phase riêng.

## 6.1 Freeze `V1_FROZEN`

Phải lưu immutable snapshot gồm:

- Git commit;
- `configs/default.yaml` và config được resolve;
- Python/dependency environment;
- ICD-10/RxNorm processed artifacts và hashes;
- predictions trên development/calibration;
- local metrics và error report;
- submission/leaderboard score nếu có;
- runtime, RAM và VRAM.

Khi toàn bộ feature flags V2 tắt, output phải tái tạo `V1_FROZEN`.

## 6.2 Chốt annotation contract

Trước benchmark/training phải version hóa guideline cho:

- exact boundary của drug formulation;
- test name và result boundary;
- imaging finding là result hay diagnosis;
- symptom so với diagnosis;
- uncertain/suspected/screening/ruled-out concept có emit không;
- chronic disease và home medication có `isHistorical` không;
- family experiencer khác family reporter;
- ICD category hay leaf code;
- RxNorm IN/SCD/SBD convention;
- overlap/nesting/duplicate policy;
- field presence cho từng entity type.

Không thay guideline giữa experiment mà không tăng version và đánh dấu run bị ảnh hưởng.

## 6.3 Evaluation split

Không áp dụng máy móc split `12/4/4`. Với dữ liệu nhỏ, split phải coverage-aware theo:

- năm entity types;
- `isNegated`, `isHistorical`, `isFamily`;
- diagnosis/drug có gold candidates;
- độ dài và mức noise của note;
- section/structure;
- rare surface/concept family.

Khuyến nghị:

- grouped cross-validation cho development;
- calibration set cố định để tune threshold;
- sealed lockbox chỉ mở ở milestone;
- không dùng lockbox để viết rule, thêm alias, chọn checkpoint hoặc mine hard negative.

## 6.4 Scorer fidelity và oracle analysis

Trước tối ưu model:

1. Gold-vs-gold phải cho điểm hoàn hảo.
2. Kiểm tra entity matching, ordering, duplicate, empty sets và per-document aggregation.
3. Tách diagnostic views: exact span/type, WER-like text, assertion Jaccard và candidate Jaccard.
4. Chạy oracle experiments:

```text
Oracle span:      thay span bằng gold, giữ downstream hiện tại
Oracle type:      sửa type, giữ phần còn lại
Oracle assertion: dùng gold assertions trên matched entities
Oracle candidate: dùng gold candidates trên matched entities
```

Oracle chỉ dùng để xác định ceiling/bottleneck, không dùng làm production component.

## 6.5 Experiment governance

Mỗi run chỉ thay một nhóm chính:

- checkpoint;
- label schema;
- chunking;
- threshold;
- fusion/type resolution;
- assertion rules/model;
- retrieval channel;
- reranker;
- candidate count.

Mỗi run phải ghi:

```text
run_id
git_commit
config_hash
model_hash
dataset_hash
terminology_hash
seed
hardware
runtime/memory
development/calibration metrics
prediction diff
decision: keep | reject | investigate
```

Leaderboard submission phải có budget và one-change-at-a-time ledger.

## 6.6 Feature flags và rollback

V2 additions phải tắt/bật độc lập:

```yaml
gliner:
  enabled: false

ner_fusion:
  enabled: false

assertion_classifier:
  enabled: false

dense_linking:
  enabled: false

linking_reranker:
  enabled: false
```

Tên config cuối có thể được điều chỉnh theo loader hiện tại, nhưng semantics của flags phải được giữ.

---

# 7. Roadmap ba phase

| Phase | Năng lực | Trạng thái | Entry gate | Exit artifact |
|---|---|---|---|---|
| 1 | Medical NER | **Active / next focus** | Foundation tối thiểu đã freeze | Span + type + confidence + provenance đã freeze |
| 2 | Assertion Detection | Planned | Medical NER contract pass | Entity + assertion set đã freeze |
| 3 | Entity Linking | Planned | Entity/assertion contract pass | Diagnosis/drug + calibrated candidate codes |

Dependency bắt buộc:

```text
Medical NER
    ↓ freeze spans/types
Assertion Detection
    ↓ freeze assertions
Entity Linking
    ↓
final validation/package
```

Không dùng linking để phát minh span. Linkability probe có thể cung cấp feature cho type resolution, nhưng chỉ là diagnostic evidence và không được tạo final candidate trong Medical NER.

---

# 8. Phase 1 – Medical NER

## 8.1 Mục tiêu

Xây dựng và freeze hệ thống tìm span + type y khoa với GLiNER làm semantic backbone, kết hợp production guardrails đã có của V1.

Phase này phải trả lời được:

1. GLiNER zero-shot từ Training Session đạt chất lượng nào trên dữ liệu hiện tại?
2. Label, chunking và threshold nào phù hợp nhất?
3. GLiNER bổ sung recall gì so với rule experts V1?
4. Fusion nào tăng score mà không làm prediction density/false positives mất kiểm soát?
5. Lỗi còn lại nằm ở span boundary, missing entity hay type confusion?
6. Fine-tuning có thực sự cần và có vượt zero-shot không?

## 8.2 In scope

- freeze extraction baseline;
- annotation contract cho boundary/type;
- GLiNER zero-shot reproduction;
- section-aware token windowing và raw-offset restoration;
- checkpoint/label/chunk/threshold benchmark;
- V1 extractor adapter;
- minimal boundary cleanup;
- deterministic evidence fusion;
- type resolution và overlap handling;
- task-aligned data generation;
- audit external medical NER data;
- conditional GLiNER fine-tuning;
- extraction metrics, error analysis và handoff contract.

## 8.3 Out of scope

- final assertion assignment;
- final ICD/RxNorm candidate generation;
- assertion classifier training;
- dense KB index/reranker productionization;
- LLM-generated code;
- sửa span downstream sau khi NER exit gate đã freeze.

Assertion cues có thể được dùng để trim boundary hoặc làm context feature, nhưng Phase này không chịu trách nhiệm serialize assertion labels.

## 8.4 Target design

```text
raw note
  → existing text views + section/chunk parser
  → GLiNER full five-label pass
  → V1 rule/dictionary expert proposals
  → raw-offset validation
  → minimal type-specific boundary cleanup
  → exact/near-overlap evidence clusters
  → deterministic evidence fusion
  → type resolution
  → compatibility/duplicate selection
  → frozen NER entities
```

GLiNER-only predictions được phép trở thành entity nếu vượt calibrated gate và không vi phạm safety guards. Không yêu cầu rule confirmation cho mọi GLiNER prediction.

## 8.5 Work package NER-0 — Baseline, contract và measurement

### NER-0 tasks

1. Freeze `V1_FROZEN` theo Section 6.1.
2. Chốt boundary/type guideline theo Section 6.2.
3. Freeze coverage-aware development/calibration/lockbox split.
4. Nâng evaluator để tách:
   - missing span;
   - extra span;
   - left/right/both boundary error;
   - wrong type;
   - duplicate/overlap conflict;
   - per-source contribution.
5. Chạy oracle span và oracle type.
6. Tạo baseline report theo five-type confusion matrix.

### Deliverables

```text
outputs/baselines/v1_frozen/
outputs/reports/v2_ner_baseline/
configs/splits_v2.yaml                         # cần tạo
data/golden/ANNOTATION_GUIDELINE_V2.md         # cần tạo
```

### NER-0 completion criteria

- V1 score và predictions tái tạo được.
- Gold-vs-gold scorer sanity check pass.
- Zero invalid raw offsets trong baseline artifacts.
- Split manifest có coverage report và lockbox policy.
- Có per-type metrics, confusion matrix và error buckets.
- Oracle report xác định span/type ceiling.

## 8.6 Work package NER-1 — Reproduce GLiNER zero-shot baseline

### Baseline bắt buộc

Tái tạo cấu hình gần Training Session trước khi thử nâng cấp:

```text
checkpoint: urchade/gliner_multi-v2.1
mode: zero-shot
threshold initial reference: 0.35
pass: full five-label
```

Label map đầu tiên:

```text
symptom                          → TRIỆU_CHỨNG
disease or diagnosis             → CHẨN_ĐOÁN
medication or drug               → THUỐC
medical test or lab name         → TÊN_XÉT_NGHIỆM
test result or measurement value → KẾT_QUẢ_XÉT_NGHIỆM
```

`0.35` chỉ là reproduction starting point, không phải threshold cuối.

### Integration tasks

1. Port GLiNER loading/inference logic từ `training-repo/src/medextract/ner/gliner_ner.py` qua adapter của repo chính.
2. Không thay token-classification `NERExtractor` hiện có một cách âm thầm; chọn một trong hai cách rõ ràng:
   - thêm `GLiNERExtractor` riêng; hoặc
   - refactor `NERExtractor` thành backend-pluggable với `backend: gliner | token_classification`.
3. Output phải là `SpanCandidate` hiện có, kèm:
   - proposed/raw type;
   - model confidence;
   - model/checkpoint hash;
   - label/pass provenance;
   - chunk/window id.
4. Tích hợp qua `build_default_extractors`, không fork một pipeline end-to-end mới.
5. Model phải load offline sau khi artifact đã provision.

### Chunking và offsets

1. Ưu tiên section, line/bullet, sentence và clause boundaries.
2. Với unit dài, dùng token windows có overlap.
3. Mỗi window giữ raw `window_start/window_end`.
4. Restore local prediction về global raw offset.
5. Deduplicate prediction tại vùng overlap.
6. Reject prediction không thỏa raw slice; không đoán offset gần nhất một cách không kiểm soát.

### Cache

Prediction cache key tối thiểu gồm:

```text
input_hash
model_hash
label_schema_hash
chunking_hash
threshold_profile_hash
inference_options_hash
```

### Files dự kiến

| Action | Path |
|---|---|
| Sửa | `src/extractors/__init__.py` |
| Sửa | `src/pipeline.py` hoặc extractor builder được gọi từ pipeline |
| Tạo hoặc refactor | `src/extractors/gliner_extractor.py` hoặc `src/extractors/ner_extractor.py` |
| Tạo | `src/ner/gliner_backend.py` |
| Tạo | `configs/gliner_zero_shot.yaml` |
| Tạo | `scripts/benchmark_gliner.py` |
| Tạo | `tests/test_gliner_extractor.py` |
| Tạo | `tests/test_gliner_offset_restoration.py` |

Tên file mới có thể điều chỉnh khi implement, nhưng phải giữ separation giữa model backend, extractor adapter và pipeline orchestration.

### NER-1 completion criteria

- GLiNER-only chạy hết development và calibration set.
- Offset mismatch bằng 0.
- Long-window overlap không tạo exact duplicate.
- Có extraction-only và end-to-end report.
- Có runtime/RAM/VRAM report.
- GLiNER disabled tái tạo V1 output.
- Có một config reproduction cố định, chưa cần là config tốt nhất.

## 8.7 Work package NER-2 — Controlled zero-shot benchmark

### Experiment axes

Chỉ thay một axis chính mỗi run.

#### Label schema

```text
A. descriptive English — baseline bắt buộc
B. descriptive Vietnamese
C. bilingual
D. short task labels
```

#### Chunking

```text
A. line/bullet units
B. section-aware token windows
C. token windows with overlap
```

#### Pass strategy

```text
A. full five-label pass — committed baseline
B. problem-focused pass: symptom + diagnosis — conditional
C. structured pass: drug + test + result — conditional
```

#### Threshold

- global sweep chỉ để khảo sát;
- cấu hình được chọn phải support per-type threshold;
- có density guard theo note/type;
- không tune bằng lockbox.

#### Checkpoint

- `urchade/gliner_multi-v2.1` là checkpoint reproduction bắt buộc;
- chỉ benchmark checkpoint khác nếu chạy offline, license phù hợp và interface/fine-tuning khả thi;
- không benchmark tràn lan trước khi checkpoint bắt buộc hoạt động ổn định.

### Metrics

```text
exact span precision/recall/F1
relaxed span precision/recall/F1
per-type precision/recall/F1
5×5 type confusion matrix
SYM→DX, DX→SYM, TEST→DX, RESULT→DX
boundary error rate
entities per note và per type
official-like end-to-end score
runtime, RAM, VRAM
```

### Selection rule

Chọn một zero-shot config chính theo thứ tự:

1. technical validity;
2. official-like end-to-end score trên development/calibration;
3. exact/per-type metrics và regression budget;
4. prediction density;
5. runtime/resource constraints.

Không chọn chỉ theo relaxed recall hoặc GLiNER confidence.

### NER-2 completion criteria

- Có experiment ledger đầy đủ.
- Có một zero-shot config chính được freeze.
- Focused pass chỉ được nhận nếu có incremental gain rõ ràng so với full pass.
- Không dùng lockbox để chọn labels/chunking/threshold.
- Có FP/FN/boundary/type error review cho từng entity type.

## 8.8 Work package NER-3 — V1 expert integration

### Vai trò của expert hiện có

| Expert | Vai trò trong V2 |
|---|---|
| Drug extractor | anchor drug name; mở rộng strength/unit/form/route/frequency có kiểm soát |
| Lab extractor | tách test name và result; không emit bare numeric result thiếu test context |
| Imaging extractor | phân biệt imaging test với finding |
| Problem extractor | precision evidence cho symptom/diagnosis heads |
| Dictionary extractor | exact/alias precision anchor |

### NER-3 tasks

1. Giữ interface `SpanCandidate` hiện tại.
2. Bổ sung adapter/provenance để mọi source có schema evidence nhất quán.
3. Không thay đổi output V1 khi GLiNER/fusion flags tắt.
4. Đo complementarity theo source:
   - entity chỉ V1 tìm thấy;
   - entity chỉ GLiNER tìm thấy;
   - exact agreement;
   - near-overlap agreement;
   - type conflict.
5. Tạo source-specific error report thay vì chỉ nhìn score tổng.

### Required comparisons

```text
A. V1_FROZEN
B. GLiNER-only
C. V1 + GLiNER naive union
D. GLiNER-centered simple fusion
```

Naive union là diagnostic baseline, không phải production target.

### NER-3 completion criteria

- V1 experts chạy cùng GLiNER qua pipeline chính.
- Có complementarity matrix theo source/type.
- GLiNER-only span không bị loại chỉ vì thiếu rule confirmation.
- Structural evidence mạnh có thể sửa/tránh boundary sai theo policy đã chốt.
- Không có duplicate exact do multi-source emission ở final NER output.

## 8.9 Work package NER-4 — Boundary cleanup, fusion và type resolution

### Minimal boundary cleanup bắt buộc

- trim whitespace/punctuation dư;
- trim negation và diagnosis cue khỏi entity span;
- xác nhận raw slice sau mỗi operation;
- split test/result khi cấu trúc chắc chắn;
- mở rộng drug formulation khi có drug anchor;
- không normalize/rewrite entity text trong output.

Ví dụ:

```text
"không đau ngực" → span NER: "đau ngực"
"nghi ngờ viêm phổi" → span NER: "viêm phổi"
"metoprolol 25 mg po bid" → full drug formulation nếu guideline yêu cầu
```

Advanced alternatives như modifier expansion/core span search là conditional và phải qua ablation riêng.

### Evidence cluster

Cluster hypotheses theo:

- exact span;
- high-overlap compatible span;
- shared lexical head;
- same structural unit;
- type compatibility.

Mỗi cluster lưu toàn bộ provenance, không chỉ source thắng.

### Deterministic fusion trước

Ưu tiên các feature có thể so sánh an toàn:

- exact agreement;
- agreement count;
- source reliability theo type;
- rank/pass evidence;
- structural support;
- calibrated threshold;
- boundary validity;
- optional cheap linkability probe.

Không cộng trực tiếp GLiNER confidence, BM25 score, fuzzy score và dense cosine khi chưa calibration.

### Type resolution

Resolver phải xử lý rõ:

- `TRIỆU_CHỨNG` ↔ `CHẨN_ĐOÁN`;
- `TÊN_XÉT_NGHIỆM` ↔ `KẾT_QUẢ_XÉT_NGHIỆM`;
- imaging finding ↔ diagnosis/result theo annotation contract;
- drug mention ↔ non-drug token;
- same-span multi-type conflict.

`type_resolution.source_priority.ner: -1` trong config hiện tại là policy của hạ tầng NER V1, không được giữ nguyên một cách vô thức cho GLiNER backbone. Policy V2 phải được thiết kế và benchmark riêng.

### Linkability probe boundary

ICD/RxNorm exact/fuzzy evidence có thể hỗ trợ phân biệt type, nhưng trong Phase này:

- không serialize candidate;
- không emit entity mới;
- không override evidence span mạnh chỉ vì retrieve được code;
- phải có ablation on/off.

### Learned fusion/type resolver

Chỉ thử logistic regression/gradient boosting hoặc model khác khi:

- deterministic fusion đã freeze;
- có đủ labeled examples;
- feature leakage đã audit;
- model vượt deterministic baseline trên primary score;
- provenance/debuggability vẫn đáp ứng.

### NER-4 completion criteria

- Fusion tốt hơn naive union trên primary metric.
- Exact duplicate giảm về 0 theo output policy.
- SYM/DX confusion giảm hoặc không tăng ngoài regression budget khi recall tăng.
- Exact boundary F1 không giảm ngoài regression budget.
- Drug/lab precision không regression ngoài budget đã freeze trước experiment.
- Mọi decision giữ source-level provenance.

## 8.10 Work package NER-5 — Data readiness

Fine-tuning không bắt đầu trước khi zero-shot pipeline và data validator hoạt động.

### Source priority

#### Tier 1 — Verified human/competition development data

- dùng cho guideline alignment, development và calibration;
- không đưa lockbox vào train;
- theo dõi concept/surface/template leakage.

#### Tier 2 — Task-aligned ontology data

Nguồn:

- ICD-10 names/aliases;
- RxNorm concepts/aliases;
- symptom lexicon;
- lab/test dictionaries;
- imaging patterns.

Sinh label by construction:

```text
sample concept
→ generate note with entity markers
→ remove markers
→ compute exact offsets
→ validate type/span/code
```

#### Tier 3 — Competition-style noise

Inject có kiểm soát:

- bỏ dấu;
- mixed Vietnamese/English;
- dính chữ hoặc fused heading;
- malformed bullet/punctuation;
- repeated token;
- abbreviation;
- drug typo;
- decimal comma/point;
- missing whitespace.

Mỗi transformation phải lưu provenance và offsets mới.

#### Tier 4 — External medical NER data

ViMedNER/VietBioNER chỉ được dùng sau khi audit:

- license/usage permission;
- provenance và version;
- annotation guideline;
- token-to-character conversion;
- label semantics;
- partial annotation;
- domain mismatch;
- duplicate/leakage.

Không map label gộp như `Symptom_and_Disease` trực tiếp sang cả symptom hoặc diagnosis. External data là auxiliary/source-domain data, không phải competition gold.

#### Tier 5 — Weak/silver data

Chỉ sử dụng nếu task-aligned data chưa đủ và có đồng thuận giữa các nguồn thực sự độc lập. Không coi hai lần chạy cùng một model là hai annotators độc lập.

### Data validator

Mọi dataset phải pass:

- exact text/span match;
- valid target/source label;
- valid overlap policy;
- no marker leakage;
- no empty/whitespace-only span;
- duplicate và near-duplicate checks;
- concept/type consistency;
- train/dev/lockbox leakage checks;
- source/license manifest;
- transformation provenance.

### Dataset artifacts

```text
data/processed/ner_v2/                         # cần tạo
├── source_inventory.json
├── label_mapping.csv
├── conversion_errors.jsonl
├── task_aligned_train.jsonl
├── noisy_train.jsonl
├── development.jsonl
├── calibration.jsonl
└── manifest.json
```

### NER-5 completion criteria

- 100% accepted samples pass offset validator.
- Manifest có source/license/version/hash/seed/counts.
- Không có lockbox leakage.
- Label mapping được review; ambiguous labels không bị ép map.
- Có distribution report theo source, type, noise và concept family.

## 8.11 Work package NER-6 — Conditional GLiNER fine-tuning

### Entry gate

Chỉ bắt đầu khi:

- zero-shot config đã freeze;
- NER-0 đến NER-5 pass;
- oracle/residual error cho thấy extraction còn đủ ceiling;
- data quality gate pass;
- compute/storage budget được xác nhận.

### Curriculum tối thiểu

```text
Stage 1: clean task-aligned synthetic
Stage 2: competition-style noisy synthetic
Stage 3: verified human development data
Stage 4: mined hard negatives nếu residual errors yêu cầu
```

External source-domain data có thể là pretraining/auxiliary channel sau audit, không mặc định nằm trên critical path.

### Training safeguards

- fixed seed và pinned dependencies;
- replay/mix data giữa stages;
- source-aware sampling;
- checkpoint evaluation sau từng stage;
- theo dõi per-type regression;
- early stopping theo development/calibration metric;
- không chọn checkpoint theo training loss;
- không train/tune trên lockbox;
- lưu model card, config, dataset hash và environment.

### Required ablations

```text
zero-shot
fine-tune clean task-aligned
+ competition noise
+ verified human data
+ hard negatives, nếu có
```

### Promotion rule

Fine-tuned GLiNER chỉ được nhận nếu:

- vượt zero-shot trên primary metric;
- không vi phạm per-type regression budget;
- prediction density vẫn kiểm soát;
- model load offline;
- runtime/resource phù hợp;
- kết quả tái tạo được từ clean environment.

Nếu không đạt, giữ zero-shot GLiNER; không cố promote checkpoint cuối.

## 8.12 Work package NER-7 — Handoff và phase exit

### Frozen artifacts

- best accepted NER config;
- model/checkpoint manifest;
- threshold profile;
- split/dataset manifests;
- extraction predictions;
- metrics/error report;
- annotation guideline version;
- NER → Assertion handoff schema;
- rollback config về `V1_FROZEN`.

### Exit gates

#### Technical validity

- 100% output parseable;
- zero raw offset mismatch;
- zero invalid entity type;
- zero unintended exact duplicate;
- deterministic two-run output cho NER stage;
- offline model load và clean rebuild pass.

#### Quality

- zero-shot baseline đã được reproduce và benchmark;
- selected combined system tăng official-like primary score so với `V1_FROZEN`;
- fusion tốt hơn naive union;
- prediction density được kiểm soát;
- drug/lab precision không regression ngoài budget đã chốt;
- SYM/DX confusion giảm hoặc không tăng ngoài budget;
- conditional fine-tuned model chỉ được dùng nếu vượt zero-shot.

#### Handoff

- spans/types được freeze;
- Assertion Detection không cần gọi lại NER;
- entity provenance đủ để debug assertion errors;
- phase review có quyết định `accept`, `iterate` hoặc `fallback V1`.

Nếu gate không đạt, nhánh V2 không được promote. `V1_FROZEN` tiếp tục là production baseline; GLiNER vẫn là backbone trong nhánh V2, không bị biến thành optional extractor để hợp thức hóa release.

## 8.13 Immediate execution backlog

Đây là thứ tự công việc ngay sau tài liệu này.

| Priority | ID | Task | Dependency | Output |
|---|---|---|---|---|
| P0 | NER-0.1 | Freeze V1 config, predictions, metrics, artifacts và hashes | Không | `v1_frozen` bundle |
| P0 | NER-0.2 | Chốt boundary/type annotation contract | NER-0.1 | Guideline V2 |
| P0 | NER-0.3 | Freeze coverage-aware splits và lockbox policy | NER-0.2 | Split manifest |
| P0 | NER-0.4 | Sanity-check scorer + oracle span/type | NER-0.2 | Measurement report |
| P0 | NER-1.1 | Thiết kế GLiNER adapter qua `BaseExtractor`/`SpanCandidate` | NER-0.2 | Interface decision |
| P0 | NER-1.2 | Port GLiNER full-pass baseline | NER-1.1 | Reproduction config |
| P0 | NER-1.3 | Implement token-window overlap + raw offset restoration | NER-1.2 | Offset-safe inference |
| P0 | NER-1.4 | Add unit/integration tests và prediction cache | NER-1.3 | Test/cache artifacts |
| P0 | NER-2.1 | Run descriptive-English baseline at reference threshold | NER-1.4 | GLiNER-only baseline |
| P0 | NER-2.2 | Benchmark labels, chunking và per-type thresholds | NER-2.1 | Selected zero-shot config |
| P1 | NER-3.1 | Measure V1/GLiNER complementarity và naive union | NER-2.2 | Source error matrix |
| P1 | NER-4.1 | Implement minimal boundary cleanup | NER-3.1 | Boundary module |
| P1 | NER-4.2 | Implement deterministic evidence fusion/type policy | NER-4.1 | Hybrid NER config |
| P1 | NER-4.3 | Run required A/B/C/D ablations | NER-4.2 | Acceptance report |
| P1 | NER-5.1 | Build task-aligned generator + data validator | NER-2.2 | Versioned train data |
| P1 | NER-5.2 | Audit external NER datasets | NER-0.2 | Audit/mapping report |
| P2 | NER-6.1 | Decide fine-tune go/no-go from oracle/residual errors | NER-4.3, NER-5.1 | Decision record |
| P2 | NER-6.2 | Fine-tune and ablate nếu go | NER-6.1 | Candidate checkpoint |
| P0 | NER-7.1 | Freeze accepted NER artifacts và handoff | Accepted config | Phase exit bundle |

## 8.14 Milestone plan

Timeline được quản lý theo dependency thay vì ngày cứng vì chưa có team capacity/compute SLA.

### Milestone M1 — Measurement ready

- V1 frozen;
- scorer/guideline/split ready;
- oracle report complete.

### Milestone M2 — Training Session NER reproduced

- full-pass GLiNER chạy offline;
- raw offsets an toàn;
- baseline config và report được freeze.

### Milestone M3 — Zero-shot selected

- controlled label/chunk/threshold benchmark hoàn tất;
- một config zero-shot được chọn.

### Milestone M4 — Hybrid NER candidate

- V1 experts tích hợp;
- boundary cleanup + deterministic fusion hoàn tất;
- required ablations hoàn tất.

### Milestone M5 — Data/fine-tuning decision

- task-aligned data pass quality gate;
- có go/no-go cho fine-tuning;
- nếu go, checkpoint phải qua promotion rule.

### Milestone M6 — Medical NER frozen

- exit gates pass;
- spans/types/provenance contract freeze;
- bàn giao cho Assertion Detection.

---

# 9. Phase 2 – Assertion Detection

## 9.1 Mục tiêu

Gán đúng clinical context cho entity đã freeze từ Medical NER, tập trung vào:

```text
isNegated
isHistorical
isFamily
```

Phase này không được sửa span/type. Một assertion tốt trên entity sai không tạo ra clinical fact đúng; vì vậy entry gate bắt buộc là Medical NER đã pass.

## 9.2 Entry gate

- Medical NER exit bundle đã freeze.
- Annotation policy cho uncertain/history/family đã chốt.
- Evaluation set có coverage assertion đủ để kết luận.
- Có matched-entity evaluation mode để tách assertion error khỏi NER error.

Nếu gold không có đủ `isFamily` hoặc historical examples, phải tạo/review contrast set trước khi đánh giá classifier.

## 9.3 Baseline bắt buộc — ConText-style rules

Tái sử dụng `src/assertion/` và đối chiếu logic từ Training Session.

Nâng cấp rule baseline theo thứ tự:

1. cue inventory cho negation/history/family;
2. pre/post cue direction;
3. clause/sentence scope;
4. termination cues;
5. coordinated/list scope;
6. section and blank-line structure;
7. family experiencer guard;
8. family reporter guard;
9. conflict resolution và confidence/provenance.

Section chỉ là prior/scope evidence, không là hard-coded truth. Entity trong `Tiền sử` không tự động historical nếu local context cho thấy hiện tại; ngược lại long-range structure phải được dùng khi local window chưa đủ.

## 9.4 Internal status model

Có thể biểu diễn nội bộ phong phú hơn:

```text
certainty:   confirmed | suspected | differential | screening_target | ruled_out
temporality: current | historical | resolved | future
experiencer: patient | family | other
```

Official serialization vẫn chỉ tạo ba flags hợp lệ. Internal status không được làm thay đổi output contract nếu chưa có mapping guideline rõ ràng.

## 9.5 Assertion-targeted data

Tạo minimal contrast sets by construction:

```text
Bệnh nhân khó thở.
Bệnh nhân không khó thở.
Bệnh nhân từng bị khó thở.
Mẹ bệnh nhân bị khó thở.
Vợ nhận thấy bệnh nhân khó thở.
Không sốt, ho hoặc khó thở.
Tiền sử tăng huyết áp; hiện huyết áp ổn định.
```

Mỗi set phải kiểm tra cue, target scope, section, experiencer, temporality và expected serialized assertions.

## 9.6 Conditional wider-context classifier

Chỉ triển khai nếu oracle assertion và residual error cho thấy rules là bottleneck.

Input đề xuất:

```text
section heading
previous line/clause
current clause with mention markers
next line/clause
```

Chạy ưu tiên cho:

- rule conflict;
- implicit/long-range context;
- family ambiguity;
- historical/current ambiguity;
- suspected/screening/ruled-out ambiguity nếu ảnh hưởng serialized flags.

Classifier phải so với rule-only và hybrid rule+classifier. Không chạy classifier trên mọi entity nếu selective routing đạt chất lượng tương đương với latency thấp hơn.

LLM assertion là deferred mặc định. Chỉ xem xét nếu local/offline, hợp lệ giới hạn model và thắng transparent rule/classifier baseline về primary score, latency và determinism.

## 9.7 Metrics

```text
per-label precision/recall/F1
exact assertion-set accuracy
scope error rate
family experiencer vs reporter errors
historical/current errors
official-like end-to-end score
runtime per entity/note
```

Phải báo cáo hai view:

1. assertion trên gold/matched entities để đo module;
2. end-to-end trên predicted entities để đo tác động thật.

## 9.8 Deliverables

```text
configs/assertion_v2.yaml                       # cần tạo hoặc tách từ default
data/processed/assertion_v2/contrast_sets.jsonl # cần tạo
outputs/reports/v2_assertion/
```

Code ưu tiên sửa/mở rộng các module hiện có:

```text
src/assertion/assertion_detector.py
src/assertion/context_rules.py
src/assertion/negation.py
src/assertion/historical.py
src/assertion/family.py
tests/test_assertion.py
```

Classifier files chỉ tạo sau go/no-go.

## 9.9 Exit gates

- NER text/start/end/type byte-for-byte không đổi qua assertion stage.
- List/coordinated negation scope pass regression tests.
- Family reporter không bị gán `isFamily`.
- Historical không dựa cứng duy nhất vào section.
- Per-label và exact-set metrics được báo cáo trên đủ coverage.
- Rule V2 không regression primary score so với rule V1.
- Classifier chỉ được nhận nếu tăng official-like primary score và qua latency/determinism gate.
- Assertions/provenance được freeze để bàn giao cho Entity Linking.

---

# 10. Phase 3 – Entity Linking

## 10.1 Mục tiêu

Ánh xạ:

```text
CHẨN_ĐOÁN → ICD-10
THUỐC     → RxNorm
```

Entity Linking chiếm trọng số lớn nhất trong công thức điểm tham chiếu của Training Session (`0.4 · J_candidates`). Đây là đòn bẩy lớn, nhưng chỉ tối ưu sau khi upstream entity contract ổn định để tránh nhầm lỗi extraction với lỗi candidate.

## 10.2 Entry gate

- Medical NER spans/types đã freeze.
- Assertion output contract đã freeze.
- ICD-10/RxNorm terminology snapshots có version/hash.
- Gold candidate convention đã chốt.
- Có candidate evaluation tách biệt khỏi end-to-end matching.

## 10.3 Candidate pool — committed hybrid retrieval

Các retrieval channels:

```text
exact/manual alias
BM25
character n-gram TF-IDF
SapBERT semantic embedding + FAISS
```

Dense semantic retrieval là committed core theo Training Session. Sparse/exact channels của V1 được giữ để tăng precision và coverage tiếng Việt/noisy strings.

Candidate phải aggregate theo canonical code:

```text
ICD-10 code
RxNorm RXCUI
```

không theo alias row. Một code có nhiều alias phải merge evidence trước ranking.

## 10.4 Dense index build

### ICD-10

Index các representation đã audit:

- Vietnamese disease name;
- English name nếu license/snapshot cho phép;
- approved aliases/abbreviations;
- qualifier-bearing names;
- leaf/category metadata.

### RxNorm

Index các TTY phù hợp với annotation convention:

- IN/PIN/MIN nếu ingredient-level được chấp nhận;
- SCD/SBD và related clinical-drug forms khi formulation cần strength/form/brand;
- suppress filtered rows;
- ingredient, strength, dose form và brand metadata.

### Manifest bắt buộc

```text
encoder name/hash
pooling/normalization
embedding dimension/dtype
terminology snapshot hash
row ordering hash
index type/parameters
build seed/environment
```

Index và metadata mismatch phải fail fast.

## 10.5 Query normalization và structure constraints

### Diagnosis

- giữ disease head và clinically meaningful qualifiers;
- xử lý abbreviation/diacritics;
- dùng acuity, laterality, subtype và anatomy khi có;
- tránh ưu tiên bare parent category khi mention đủ cụ thể.

### Drug

- strip route/frequency khỏi retrieval query khi không quyết định RxCUI;
- giữ ingredient, strength, unit, dose form và brand khi annotation convention cần;
- parse fused/typo forms có kiểm soát;
- không chọn combination product nếu mention chỉ có một ingredient;
- filter/penalize wrong TTY/strength/form/product.

## 10.6 Candidate pool audit trước reranking

Đo riêng:

```text
Recall@1
Recall@5
Recall@20
top-1 accuracy
candidate-set Jaccard
no-candidate rate
coverage theo ICD/RxNorm/type/surface category
```

Error buckets:

- code không có trong KB snapshot;
- alias/query normalization thiếu;
- correct code không vào top-k;
- correct code vào pool nhưng rank sai;
- annotation granularity mismatch;
- upstream wrong span/type;
- invalid/ambiguous gold convention.

Không train reranker trước khi biết correct code có nằm trong candidate pool đủ thường xuyên hay không.

## 10.7 Candidate fusion và deterministic ranking

Baseline ranker dùng:

- exact/manual alias evidence;
- per-channel rank;
- Reciprocal Rank Fusion hoặc calibrated rank features;
- source agreement;
- ICD qualifier compatibility;
- RxNorm ingredient/strength/form/brand compatibility;
- parent/category penalty;
- section/context evidence nếu có ích.

Không cộng raw BM25, TF-IDF cosine và dense cosine trực tiếp khi chưa calibration.

## 10.8 Conditional reranker

Entry gate:

- candidate Recall@20 đủ cao theo threshold đã freeze;
- error analysis cho thấy ranking, không phải retrieval coverage, là bottleneck;
- có hard-negative training/evaluation data;
- deterministic ranker đã freeze.

Lựa chọn:

1. cross-encoder riêng cho ICD và RxNorm;
2. local LLM ≤9B làm verifier/reranker.

Hard negatives:

```text
ICD: sibling code, wrong acuity/laterality/subtype/anatomy,
     unspecified vs specified, category vs leaf

RxNorm: same ingredient but wrong strength/form/brand/product,
        ingredient vs combination, SCD vs SBD mismatch
```

Reranker contract:

- chỉ chọn code trong retrieved pool;
- không sinh code mới;
- output phải validate against terminology snapshot;
- có abstain/no-candidate policy;
- fallback deterministic và failure phải được log rõ trong development;
- final mode không silent fallback nếu reranker là required artifact.

## 10.9 Candidate count calibration

Default là top-1. Top-2 chỉ được emit khi expected candidate Jaccard trên calibration tăng.

Không trả top-k lớn để “tăng cơ hội trúng”, vì extra wrong codes làm giảm Jaccard.

Phải calibrate riêng ICD và RxNorm, có thể theo ambiguity bucket nếu coverage đủ.

## 10.10 Deliverables

```text
configs/dense_linking.yaml                      # cần tạo
data/processed/dense_indices/                   # cần tạo
outputs/reports/v2_linking/
```

Code sẽ mở rộng các module hiện có:

```text
src/linking/icd10_linker.py
src/linking/rxnorm_linker.py
src/linking/sparse_retriever.py
src/linking/candidate_selector.py
src/linking/drug_parser.py
src/linking/rerank_lite.py
```

Module mới dự kiến:

```text
src/linking/dense_retriever.py
src/linking/candidate_aggregator.py
scripts/build_dense_indices.py
scripts/audit_candidate_pool.py
tests/test_dense_retriever.py
tests/test_candidate_aggregator.py
```

Reranker modules chỉ tạo sau go/no-go.

## 10.11 Exit gates

- NER span/type và assertions không đổi qua linking stage.
- Dense index load offline; manifest và index hashes khớp.
- Candidate pool Recall@1/5/20 và no-candidate rate được đo trước rerank.
- Candidate aggregation theo code, không theo alias row.
- Structured deterministic ranker có baseline report.
- Learned reranker chỉ được nhận nếu tăng top-1/candidate Jaccard và primary score.
- Không có code ngoài terminology/candidate pool.
- Candidate count được calibrate theo Jaccard.
- Runtime/memory phù hợp offline inference budget.
- Final linking artifacts được freeze và clean rebuild pass.

---

# 11. Cross-phase integration và final release

Phần này là release workstream sau ba capability, không phải phase thứ tư.

## 11.1 Final pipeline contract

```python
def process_text(raw_text, config):
    views, chunks = preprocess_and_parse(raw_text, config)

    # Phase 1: only span and type
    entities = run_medical_ner(raw_text, views, chunks, config)

    # Phase 2: preserve span/type
    entities = apply_assertions(entities, raw_text, chunks, config)

    # Phase 3: preserve span/type/assertions
    entities = link_diagnoses_and_drugs(entities, raw_text, config)

    entities = finalize_compatible_overlaps(entities, raw_text, config)
    records = format_output(entities)
    validate_output(raw_text, records)
    return records
```

Implementation thực tế tiếp tục dùng `ClinicalIEPipeline` trong `src/pipeline.py`; pseudocode chỉ thể hiện ownership và invariants giữa ba phase.

## 11.2 Required end-to-end ablations

| ID | System | Mục đích |
|---|---|---|
| A | `V1_FROZEN` | Production baseline |
| B1 | GLiNER extraction only | Đo backbone span/type |
| B2 | Training baseline: GLiNER + ConText + SapBERT/FAISS | Tái tạo foundational kernel |
| B3 | B2 + constrained reranker | Training improved tier nếu resource cho phép |
| C | V1 + GLiNER naive union | Đo complementarity/FP cost |
| D | GLiNER-centered deterministic fusion | Minimal V2 hybrid |
| E | D + accepted Medical NER tuning | Đo NER improvements |
| F | E + Assertion V2 | Đo assertion improvements |
| G | F + hybrid dense linking | Đo foundational linking upgrade |
| H | G + accepted reranker | Full accepted system |

Không bắt buộc H tồn tại. Final system chỉ gồm modules đã qua acceptance gate.

## 11.3 Final validator

Validator phải kiểm tra:

- JSON parseable và top-level list;
- đúng file count/folder structure;
- đúng required/optional fields;
- valid entity types/assertions;
- candidates chỉ ở diagnosis/drug;
- `raw[start:end] == text`;
- không duplicate exact ngoài annotation policy;
- không invalid candidate code;
- không missing/extra output file;
- không NaN/Inf/null trái schema.

## 11.4 Final mode

```yaml
mode: final
continue_on_error: false
allow_fallback: false
deterministic: true
require_all_models: true
fail_on_validation_error: true
```

Packaging phải refuse nếu:

- thiếu file hoặc model/index;
- có fatal error;
- có fallback file;
- offset mismatch;
- invalid JSON/schema;
- config/model/data/terminology/index hash mismatch;
- hai deterministic runs tạo output khác nhau.

## 11.5 Release gate

V2 chỉ được promote khi:

1. cả ba phase đã pass exit gate;
2. official-like primary score tăng so với `V1_FROZEN`;
3. scorer fidelity đã sanity-check;
4. không regression nghiêm trọng ở entity type/assertion/linking bucket đã chốt;
5. clean offline rebuild thành công;
6. final run deterministic;
7. validation/package fail-fast pass;
8. artifacts, licenses và hashes đầy đủ.

Nếu không đạt, release `V1_FROZEN` và giữ V2 ở nhánh experiment.

---

# 12. Metrics và decision framework

## 12.1 Primary metric

```text
official-like end-to-end score
```

Theo Training Session, công thức tham chiếu:

```text
final = 0.3·(1 − WER) + 0.3·J_assertion + 0.4·J_candidates
```

Implementation local phải được đối chiếu với organizer behavior; không giả định approximate scorer là tuyệt đối đúng.

## 12.2 Diagnostic metrics

### Medical NER

- exact/relaxed span P/R/F1;
- per-type P/R/F1;
- boundary errors;
- type confusion;
- entities/note/type;
- source complementarity.

### Assertion Detection

- per-label P/R/F1;
- exact assertion-set accuracy;
- scope/reporter/temporality errors.

### Entity Linking

- Recall@1/5/20;
- top-1 accuracy;
- candidate-set Jaccard;
- no-candidate rate;
- correct-in-pool-but-wrong-rank rate.

### Operational

- runtime/note;
- peak RAM/VRAM;
- artifact size;
- deterministic repeatability;
- validation failure count.

## 12.3 Acceptance thresholds

Không bịa số tuyệt đối khi chưa có reproducible baseline. Trước mỗi experiment family phải freeze:

- primary minimum gain hoặc non-inferiority rule;
- per-type regression budget;
- prediction density range;
- latency/memory ceiling;
- candidate Recall@20 gate trước reranking;
- confidence interval hoặc repeatability rule nếu sample nhỏ.

Threshold không được sửa sau khi xem lockbox result.

---

# 13. Testing strategy

## 13.1 Test pyramid

### Unit tests

- raw-offset mapping;
- GLiNER local/global offset conversion;
- window overlap dedupe;
- boundary trimming/expansion;
- evidence cluster/fusion;
- type conflict resolution;
- assertion cue/scope/termination;
- dense index/query behavior;
- candidate aggregation/ranking;
- schema validation.

### Integration tests

- GLiNER extractor qua `ClinicalIEPipeline`;
- V2 flags off reproduce V1;
- Medical NER handoff preserves offsets;
- Assertion preserves spans/types;
- Linking preserves spans/types/assertions;
- final formatter and validator.

### Regression tests

- fused headings;
- no-diacritics/mixed language;
- list negation;
- family reporter;
- historical/current conflict;
- full drug formulation;
- lab test/result pair;
- imaging test/finding;
- ICD parent/leaf;
- RxNorm strength/form/brand;
- duplicate/overlap cases.

### Reproducibility tests

- clean install/offline load;
- pinned artifact hashes;
- same seed/config two-run byte equality;
- missing required model/index fails fast.

## 13.2 Commands hiện có cần tiếp tục dùng

```bash
python scripts/run_inference.py --config configs/default.yaml --input-dir <input> --output-dir <output>
python scripts/run_evaluate.py --gold-dir <gold> --pred-dir <pred> --report-dir <report>
python scripts/run_validate.py --input-dir <input> --pred-dir <pred> --report-dir <report>
python -m pytest -q
```

CLI flags phải được xác nhận với script thực tế khi implement; ví dụ trên thể hiện intended workflow, không thay thế `--help` của từng script.

---

# 14. Data governance

## 14.1 Dataset manifest

Mỗi version lưu:

```text
source
license/usage note
download/version date
conversion/generator version
annotation guideline version
entity counts
unique surface/concepts
label/assertion/code distribution
noise distribution
quality rejection rate
duplicate/leakage report
hashes
seed
```

## 14.2 Confidence tiers

```text
GOLD_VERIFIED
TASK_ALIGNED_BY_CONSTRUCTION
AUGMENTED_HIGH
SILVER_HIGH
REVIEW
REJECT
```

Không hardcode sample weight trước khi có experiment. Ưu tiên curriculum và source-aware sampling.

## 14.3 Security/privacy

- không gửi clinical note ra external API;
- không log raw PHI ngoài policy cho phép;
- development artifacts phải theo access control của dataset;
- model/data artifact provenance phải traceable;
- synthetic/public data không được trộn với private data mà mất source tag.

---

# 15. Risk register

| Risk | Phase/impact | Mitigation | Trigger/decision |
|---|---|---|---|
| GLiNER over-emission | Medical NER | per-type calibration, density guard, fusion, FP review | primary score giảm hoặc entities/note tăng bất thường |
| GLiNER miss domain-specific spans | Medical NER | V1 experts, task-aligned data, conditional fine-tune | oracle span ceiling cao nhưng recall thấp |
| Chunk/window làm hỏng offset | Medical NER | raw window anchors, offset tests, reject mismatch | bất kỳ raw-slice mismatch nào |
| Naive union tăng FP/duplicate | Medical NER | required fusion ablation | union kém best single system |
| SYM/DX confusion | Medical NER | focused diagnostics, type resolver, optional probe | confusion bucket chiếm residual lớn |
| External labels lệch task | Medical NER/data | license/label/partial-annotation audit | ambiguous mapping hoặc false negatives |
| Synthetic overfit | Medical NER/data | noisy adaptation, verified data, template-family split | dev gain nhưng calibration regression |
| Assertion section-hardcoding | Assertion | section as prior, local scope and contrast tests | history/family precision thấp |
| Family reporter false positive | Assertion | explicit reporter guard, targeted set | reporter examples gán `isFamily` |
| Correct code không vào pool | Linking | KB/alias expansion, sparse+dense audit | low Recall@20/no-candidate cao |
| Reranker học trên pool yếu | Linking | Recall@20 entry gate | ranking experiment trước retrieval pass |
| Reranker hallucinate code | Linking | constrained pool + validator | code ngoài pool/KB |
| Candidate top-k làm giảm Jaccard | Linking | top-1 default, count calibration | top-2 expected Jaccard không tăng |
| Scorer mismatch | Toàn hệ thống | gold-vs-gold, edge-case tests, organizer comparison | local gain không lặp trên external signal |
| Gold/lockbox leakage | Toàn hệ thống | manifests, sealed split, milestone-only access | duplicate/concept family overlap |
| Leaderboard overfit | Toàn hệ thống | submission budget, one-change ledger | nhiều submissions không có hypothesis |
| V2 phá V1 | Toàn hệ thống | feature flags, regression snapshot, rollback | flags-off diff khác V1 |
| Silent fallback/empty files | Release | fail-fast final mode | missing model/crash/validation error |
| Clean rebuild fail | Release | pinned dependencies, local artifact cache, hashes | environment mới không reproduce |

---

# 16. Ownership và review cadence

Nếu team có nhiều người, ownership nên tách theo workstream:

| Role/workstream | Trách nhiệm |
|---|---|
| Evaluation owner | scorer, split, oracle, run ledger, lockbox |
| Medical NER owner | GLiNER adapter, benchmark, fusion, type resolution |
| Data owner | guideline, generators, dataset audit/manifest |
| Assertion owner | ConText rules, scope tests, conditional classifier |
| Linking owner | KB/index, retrieval audit, ranking/reranker |
| Release owner | validation, reproducibility, packaging, hashes |

Review cadence:

- daily: technical blockers, failed tests, artifact status;
- per experiment family: hypothesis, one-change diff, metrics, keep/reject;
- per milestone: acceptance gate review;
- lockbox: chỉ tại milestone được duyệt;
- release: independent validation checklist.

Nếu chỉ có một implementer, vẫn phải giữ separation bằng run ledger và milestone review để tránh vừa tune vừa tự thay acceptance rule.

---

# 17. Definition of Success

Implementation Plan V2.2 được xem là hoàn thành khi:

1. Chương trình triển khai đúng ba capability nối tiếp: Medical NER, Assertion Detection, Entity Linking.
2. `V1_FROZEN` tái tạo được và luôn có rollback path.
3. Annotation contract, scorer và coverage-aware split được version hóa.
4. Training Session GLiNER baseline được reproduce trong pipeline chính với zero offset mismatch.
5. GLiNER-centered hybrid NER vượt V1 và naive union trên primary metric.
6. Medical NER output span/type/provenance được freeze trước Assertion Detection.
7. Assertion rules xử lý đúng list scope, family reporter và historical context; learned classifier chỉ được giữ nếu tăng primary score.
8. SapBERT/FAISS dense retrieval được triển khai cùng exact/sparse channels và audit Recall@K.
9. Reranker, nếu có, chỉ chọn từ candidate pool và tăng top-1/Jaccard.
10. Không module downstream nào làm thay đổi trái phép contract upstream.
11. Final system chỉ chứa modules đã pass gate.
12. Output đúng schema/offset, deterministic, offline, clean-rebuildable và package fail-fast.
13. V2 tăng official-like end-to-end score so với `V1_FROZEN` mà không vi phạm regression/resource budgets đã freeze.

---

# 18. Final checklist

## Foundations

- [ ] `V1_FROZEN` bundle đầy đủ.
- [ ] Annotation guideline V2 đã chốt.
- [ ] Scorer gold-vs-gold pass.
- [ ] Coverage-aware split và lockbox policy đã freeze.
- [ ] Oracle span/type/assertion/candidate report đã chạy ở milestone phù hợp.

## Medical NER — active focus

- [ ] GLiNER full-pass baseline chạy trong repo chính.
- [ ] Descriptive-English reproduction config được freeze.
- [ ] Token windows map đúng raw offset và dedupe overlap.
- [ ] Per-type threshold được calibration.
- [ ] V1/GLiNER complementarity report hoàn tất.
- [ ] Naive union và deterministic fusion đã so sánh.
- [ ] Boundary/type policy pass regression tests.
- [ ] External data đã audit trước khi dùng.
- [ ] Fine-tune có go/no-go và promotion evidence.
- [ ] NER handoff contract đã freeze.

## Assertion Detection

- [ ] ConText rule baseline đã reproduce/strengthen.
- [ ] List scope, termination và section structure tests pass.
- [ ] Family reporter guard pass.
- [ ] Historical không section-hardcoded.
- [ ] Classifier, nếu có, tăng primary score.
- [ ] Assertion handoff contract đã freeze.

## Entity Linking

- [ ] ICD/RxNorm terminology snapshots có hash.
- [ ] SapBERT/FAISS indices load offline.
- [ ] Exact/sparse/dense pool aggregate theo code.
- [ ] Recall@1/5/20 và no-candidate rate đã audit.
- [ ] Structured deterministic ranking được benchmark.
- [ ] Reranker, nếu có, qua retrieval/ranking gates.
- [ ] Candidate count được calibrate theo Jaccard.
- [ ] Không code ngoài pool/terminology.

## Release

- [ ] V2 flags off tái tạo V1.
- [ ] All accepted modules pass tests.
- [ ] Zero offset/schema validation errors.
- [ ] Hai final runs byte-identical.
- [ ] Clean offline rebuild thành công.
- [ ] Config/model/data/terminology/index hashes đầy đủ.
- [ ] Packaging fail-fast pass.
- [ ] Primary release gate vượt `V1_FROZEN`.

---

# 19. Kết luận

Thứ tự thực thi được chốt:

```text
Medical NER
→ Assertion Detection
→ Entity Linking
```

Trọng tâm ngay lập tức là Medical NER:

```text
freeze V1 + contract + scorer
→ reproduce GLiNER zero-shot từ Training Session
→ benchmark labels/chunking/threshold
→ integrate V1 precision experts
→ boundary cleanup + deterministic fusion + type resolution
→ build task-aligned data
→ fine-tune chỉ khi qua go/no-go
→ freeze spans/types/provenance
```

Sau khi Medical NER pass exit gate, Assertion Detection mới nâng cấp context reasoning trên entity đã freeze. Entity Linking triển khai cuối cùng với exact/sparse + SapBERT/FAISS làm candidate pool, sau đó chỉ thêm learned reranker khi retrieval recall đã đủ và ranking được chứng minh là bottleneck.

Đây là lộ trình ngắn nhất để vừa bám kiến trúc xương sống của Training Session, vừa tận dụng code V1 đã hoạt động, đồng thời bảo đảm mỗi thay đổi đều đo được, rollback được và không phá raw-offset/output contract.
