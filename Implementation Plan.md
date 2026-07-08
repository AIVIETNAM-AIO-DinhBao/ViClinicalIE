# Implementation Plan: Hệ thống trích xuất, chuẩn hóa và validate khái niệm y khoa

**Dự án:** Viettel AI Race - Clinical Text Entity Extraction & Normalization  
**Tài liệu tham chiếu:** `Solution Design.md`  
**Phiên bản:** v1.0  
**Ngày:** 2026-07-09  
**Người viết:** ChatGPT  

---

## 1. Mục tiêu tài liệu

Tài liệu này chuyển hóa `Solution Design.md` thành một kế hoạch cài đặt chi tiết, có thể dùng để triển khai repo, phân công task, kiểm thử từng module và chuẩn bị submission cuối cùng.

Pipeline tổng quát được giữ nguyên theo Solution Design:

```text
Raw clinical text
→ preprocessing + offset mapping
→ section detection
→ span extraction
→ assertion detection
→ ICD/RxNorm candidate generation
→ reranking
→ merge overlap
→ JSON + offset validation
```

Các bổ sung mới trong kế hoạch này:

1. Có **20 file golden dataset được annotate thủ công** để validate trước khi submit.
2. Có **Streamlit validation UI** để xem file, prediction, gold annotation và highlight span.
3. Có sẵn nguồn chuẩn hóa:
   - `icd10_byt.csv` cho ICD-10.
   - `RXNCONSO.rff` cho RxNorm.
4. Kế hoạch cần phục vụ cả:
   - inference cho 100 file public/private test;
   - local evaluation trên golden dataset;
   - error analysis và threshold calibration;
   - đóng gói source code để BTC có thể rebuild.

---

## 2. Phạm vi triển khai

### 2.1 In scope

Hệ thống cần triển khai các chức năng sau:

- Đọc input `.txt` từ folder `input/`.
- Sinh output `.json` đúng format đề bài.
- Đảm bảo `raw_text[start:end] == text` cho mọi span.
- Nhận diện 5 entity types:
  - `TRIỆU_CHỨNG`
  - `TÊN_XÉT_NGHIỆM`
  - `KẾT_QUẢ_XÉT_NGHIỆM`
  - `CHẨN_ĐOÁN`
  - `THUỐC`
- Gán assertion cho `TRIỆU_CHỨNG`, `CHẨN_ĐOÁN`, `THUỐC`:
  - `isNegated`
  - `isFamily`
  - `isHistorical`
- Link candidate:
  - `CHẨN_ĐOÁN` → ICD-10 từ `icd10_byt.csv`.
  - `THUỐC` → RxNorm từ `RXNCONSO.rff`.
- Rerank candidates và chọn danh sách cuối.
- Merge overlap và deduplicate.
- Validate output JSON.
- Evaluate trên golden dataset 20 file.
- Streamlit UI để xem/highlight annotation và prediction.
- Một lệnh chạy inference toàn bộ test.
- Một lệnh tạo `output.zip` đúng cấu trúc.

### 2.2 Out of scope cho bản v1

Các hạng mục sau có thể để phase sau nếu thiếu thời gian:

- Full LLM agent tự động sinh output end-to-end.
- Sử dụng API ngoài trong inference.
- Training biomedical LLM lớn.
- UI annotation đầy đủ như Label Studio. Streamlit chỉ cần phục vụ validate/error analysis đơn giản.
- Mapping LOINC cho xét nghiệm. Đề không yêu cầu candidate cho xét nghiệm.
- SNOMED CT. Đề chỉ yêu cầu ICD-10 và RxNorm.

---

## 3. Nguyên tắc triển khai

### 3.1 Không phá offset

Đây là nguyên tắc quan trọng nhất.

- Mọi thao tác normalize chỉ dùng cho matching/search.
- Không dùng text đã normalize để xuất `position`.
- Mọi `FinalEntity` phải lấy `text` bằng slicing từ raw input.
- Mọi module extract span phải trả về `start`, `end` theo raw text.

Bất biến bắt buộc:

```python
assert raw_text[entity.start:entity.end] == entity.text
```

### 3.2 Section-aware nhưng không section-hardcoded

Section chỉ là feature/prior, không phải quyết định cuối cùng.

Ví dụ:

- `Thuốc trước khi nhập viện` làm tăng xác suất `isHistorical` cho thuốc.
- Nhưng `aspirin 325mg x 1` trong cấp cứu không nên gán historical chỉ vì nằm gần diễn biến trước nhập viện.
- `Các sự kiện trước khi nhập viện` có thể chứa triệu chứng active, thuốc cấp cứu, kết quả xét nghiệm, imaging finding.

Vì vậy assertion cần được quyết định ở **mention-level** bằng cue + scope + event status.

### 3.3 Linking không thay thế extraction

ICD/RxNorm linking chỉ chạy sau khi có span và type tương đối chắc.

- NER/rule/dictionary sinh span.
- Type resolver quyết định `CHẨN_ĐOÁN` hay `TRIỆU_CHỨNG`.
- ICD linker chỉ link `CHẨN_ĐOÁN`.
- RxNorm linker chỉ link `THUỐC`.

Tuy nhiên score từ linker có thể được dùng ngược lại như feature cho type resolver, ví dụ ICD-linkability cao hỗ trợ `CHẨN_ĐOÁN`.

### 3.4 Ưu tiên precision ở candidate mapping

Candidate score chiếm trọng số cao nhất. Không nên trả top-k bừa.

Policy mặc định:

- RxNorm: top-1 nếu confident; top-2 nếu có ambiguity hợp lý.
- ICD-10: top-1 hoặc top-2; chỉ trả nhiều hơn nếu evidence mạnh.
- Không sinh code ngoài index.
- Reranker chỉ chọn trong candidate pool.

### 3.5 Có thể triển khai tăng dần

Bản đầu tiên phải end-to-end valid dù model chưa mạnh.

Mục tiêu thứ tự:

1. Output hợp lệ 100%.
2. Rule baseline có precision ổn.
3. Golden evaluation chạy được.
4. Error analysis/UI chạy được.
5. NER/retrieval/reranking nâng chất lượng.

---

## 4. Kiến trúc repo đề xuất

```text
medical-ie-solution/
├── README.md
├── requirements.txt
├── pyproject.toml                  # optional
├── configs/
│   ├── default.yaml
│   ├── thresholds.yaml
│   ├── paths.yaml
│   ├── section_patterns.yaml
│   ├── assertion_rules.yaml
│   ├── entity_rules.yaml
│   └── ui.yaml
├── data/
│   ├── raw/
│   │   ├── input/                  # 1.txt ... 100.txt
│   │   └── all.txt                 # optional combined file
│   ├── terminologies/
│   │   ├── icd10_byt.csv
│   │   └── RXNCONSO.rff
│   ├── processed/
│   │   ├── icd10_index.parquet
│   │   ├── rxnorm_index.parquet
│   │   ├── alias_tables/
│   │   └── vector_indices/
│   ├── dictionaries/
│   │   ├── symptoms_vi.csv
│   │   ├── diagnosis_aliases.csv
│   │   ├── drug_aliases.csv
│   │   ├── lab_tests.csv
│   │   ├── abbreviations.csv
│   │   └── stop_procedures.csv
│   ├── golden/
│   │   ├── input/
│   │   │   ├── 1.txt
│   │   │   └── ...                 # 20 selected files
│   │   ├── gold/
│   │   │   ├── 1.json
│   │   │   └── ...
│   │   ├── split.json              # list selected IDs
│   │   └── notes.md                # annotation guidelines/issues
│   └── synthetic/
│       ├── train.jsonl
│       └── ner_bio/
├── models/
│   ├── ner/
│   ├── dense_linker/
│   ├── reranker/
│   └── llm_verifier/               # optional, self-host <= 9B
├── src/
│   ├── __init__.py
│   ├── data_types.py
│   ├── io_utils.py
│   ├── preprocess/
│   │   ├── normalizer.py
│   │   ├── offset_mapper.py
│   │   └── chunker.py
│   ├── section/
│   │   └── section_detector.py
│   ├── extractors/
│   │   ├── base.py
│   │   ├── drug_extractor.py
│   │   ├── lab_extractor.py
│   │   ├── problem_extractor.py
│   │   ├── dictionary_extractor.py
│   │   ├── ner_extractor.py
│   │   └── imaging_extractor.py
│   ├── type_resolution/
│   │   ├── features.py
│   │   └── resolver.py
│   ├── assertion/
│   │   ├── context_rules.py
│   │   ├── negation.py
│   │   ├── historical.py
│   │   ├── family.py
│   │   └── assertion_detector.py
│   ├── linking/
│   │   ├── icd10_index.py
│   │   ├── rxnorm_index.py
│   │   ├── sparse_retriever.py
│   │   ├── dense_retriever.py
│   │   ├── reranker.py
│   │   ├── icd10_linker.py
│   │   └── rxnorm_linker.py
│   ├── postprocess/
│   │   ├── merge.py
│   │   ├── calibration.py
│   │   └── output_formatter.py
│   ├── validation/
│   │   ├── schema_validator.py
│   │   ├── offset_validator.py
│   │   ├── evaluator.py
│   │   └── diff.py
│   ├── pipeline.py
│   └── logging_utils.py
├── scripts/
│   ├── build_icd10_index.py
│   ├── build_rxnorm_index.py
│   ├── build_all_indices.py
│   ├── run_inference.py
│   ├── run_validate.py
│   ├── run_eval_golden.py
│   ├── run_error_analysis.py
│   ├── make_submission_zip.py
│   ├── generate_synthetic.py
│   └── train_ner.py
├── streamlit_app/
│   ├── app.py
│   ├── components.py
│   └── utils.py
├── outputs/
│   ├── predictions/
│   ├── reports/
│   └── submission/
└── tests/
    ├── test_offset_mapper.py
    ├── test_section_detector.py
    ├── test_drug_extractor.py
    ├── test_lab_extractor.py
    ├── test_assertion.py
    ├── test_linking.py
    ├── test_merge.py
    └── test_validator.py
```

---

## 5. Data contracts

### 5.1 Raw input

Input public/private:

```text
input/
├── 1.txt
├── 2.txt
└── ...
```

Mỗi file là raw UTF-8 text. Không giả định có format chuẩn tuyệt đối.

### 5.2 Gold annotation format

Golden dataset nên dùng đúng format submission để evaluator dùng chung.

```json
[
  {
    "text": "metoprolol 25mg po bid",
    "type": "THUỐC",
    "candidates": ["..."],
    "assertions": ["isHistorical"],
    "position": [45, 67]
  }
]
```

Quy ước annotation:

- `position` là `[start, end)` theo raw file.
- `text` phải đúng bằng slice raw.
- `candidates` chỉ có ở `THUỐC` và `CHẨN_ĐOÁN`.
- `assertions` chỉ có ở `THUỐC`, `CHẨN_ĐOÁN`, `TRIỆU_CHỨNG`; với type khác có thể bỏ hoặc để `[]`, nhưng formatter cuối nên thống nhất.
- Gold annotation nên được validate bằng cùng validator trước khi dùng làm benchmark.

### 5.3 Internal span candidate

```python
@dataclass
class SpanCandidate:
    text: str
    start: int
    end: int
    raw_type: str | None
    source: str
    score: float
    section: str | None
    subsection: str | None
    context_left: str
    context_right: str
    features: dict[str, Any]
```

### 5.4 Final entity

```python
@dataclass
class FinalEntity:
    text: str
    start: int
    end: int
    type: str
    assertions: list[str]
    candidates: list[str]
    confidence: float
    provenance: dict[str, Any]
```

### 5.5 Mapping candidate

```python
@dataclass
class MappingCandidate:
    code: str
    name: str
    terminology: Literal["ICD10", "RXNORM"]
    lexical_score: float
    dense_score: float
    rerank_score: float
    final_score: float
    metadata: dict[str, Any]
```

---

## 6. Phase 0 - Thiết lập nền tảng project

### 6.1 Mục tiêu

Tạo skeleton repo, cấu hình đường dẫn, cài dependencies, chuẩn hóa cách chạy script.

### 6.2 Task chi tiết

#### Task 0.1 - Tạo repo skeleton

Tạo các folder như ở mục 4. Tối thiểu cần có:

```text
src/
scripts/
configs/
data/
outputs/
tests/
streamlit_app/
```

#### Task 0.2 - Cài dependencies

`requirements.txt` bản đầu:

```text
pandas
numpy
regex
rapidfuzz
scikit-learn
scipy
pyyaml
tqdm
pydantic
orjson
pytest
streamlit
intervaltree
rank-bm25
sentence-transformers
transformers
torch
```

Nếu chưa dùng dense/NER ở phase đầu, có thể để optional:

```text
faiss-cpu
accelerate
peft
bitsandbytes
```

#### Task 0.3 - Config paths

`configs/paths.yaml`:

```yaml
raw_input_dir: data/raw/input
icd10_csv: data/terminologies/icd10_byt.csv
rxnorm_rff: data/terminologies/RXNCONSO.rff
processed_dir: data/processed
golden_input_dir: data/golden/input
golden_gold_dir: data/golden/gold
prediction_dir: outputs/predictions
report_dir: outputs/reports
submission_dir: outputs/submission
```

#### Task 0.4 - Logging chuẩn

Mỗi run inference nên sinh:

```text
outputs/reports/run_<timestamp>/
├── config.yaml
├── summary.json
├── errors.jsonl
├── span_mismatch.jsonl
├── no_candidate.jsonl
├── low_confidence.jsonl
└── per_file_stats.csv
```

### 6.3 Definition of Done

- [ ] Repo chạy được `python -m pytest`.
- [ ] Có script đọc config.
- [ ] Có logging cơ bản.
- [ ] Có folder output/report.

---

## 7. Phase 1 - Build terminology indices

Phase này rất quan trọng vì bạn đã có sẵn `icd10_byt.csv` và `RXNCONSO.rff`. Cần biến chúng thành index dùng cho matching/linking.

---

### 7.1 ICD-10 index từ `icd10_byt.csv`

#### 7.1.1 Mục tiêu

Tạo bảng ICD normalized, alias-expanded, phục vụ exact/fuzzy/BM25/dense retrieval.

#### 7.1.2 Input giả định

`icd10_byt.csv` có thể có các cột như:

```text
code,name,chapter,block,...
```

Nhưng không nên hardcode cứng tên cột ngay. Script cần inspect columns và cho phép mapping qua config.

`configs/default.yaml`:

```yaml
icd10:
  code_col: code
  name_col: name
  extra_text_cols: []
```

Nếu file thực tế có tên cột khác, sửa config thay vì sửa code.

#### 7.1.3 Processing steps

1. Đọc CSV bằng pandas.
2. Chuẩn hóa code:
   - strip space;
   - uppercase;
   - giữ dấu chấm nếu có, ví dụ `K21.9`.
3. Chuẩn hóa name:
   - strip;
   - lowercase bản search;
   - Unicode NFC;
   - tạo bản bỏ dấu.
4. Sinh alias cơ bản:
   - name gốc;
   - name lowercase;
   - name bỏ dấu;
   - name bỏ dấu câu phụ;
   - name bỏ từ phụ như `không đặc hiệu`, `khác`, nếu dùng cho fuzzy phụ;
   - abbreviation/synonym từ `diagnosis_aliases.csv` nếu có.
5. Tạo fields:

```text
code
canonical_name
alias
alias_norm
alias_no_diacritics
source
metadata
```

6. Lưu:
   - `data/processed/icd10_index.parquet`
   - `data/processed/icd10_aliases.parquet`

#### 7.1.4 Diagnosis alias table cần bổ sung thủ công

`data/dictionaries/diagnosis_aliases.csv`:

```csv
alias,canonical_hint,code_hint,notes
GERD,bệnh trào ngược dạ dày thực quản,K21.9,
COPD,bệnh phổi tắc nghẽn mạn tính,J44.9,
UTI,nhiễm khuẩn đường tiết niệu,N39.0,
CAD,bệnh động mạch vành,I25.1,
MI,nhồi máu cơ tim,I21,
DVT,huyết khối tĩnh mạch sâu,I82,
PE,thuyên tắc phổi,I26,
```

#### 7.1.5 Unit tests

- `tăng huyết áp` retrieve nhóm ICD hypertension.
- `bệnh trào ngược dạ dày thực quản` retrieve K21.
- `COPD` retrieve bệnh phổi tắc nghẽn mạn tính.
- `đái tháo đường típ 2` retrieve E11.
- `xơ gan do rượu` retrieve alcohol-related cirrhosis nếu có alias.

---

### 7.2 RxNorm index từ `RXNCONSO.rff`

#### 7.2.1 Mục tiêu

Tạo bảng RxNorm normalized, hỗ trợ brand/generic/ingredient/strength matching.

#### 7.2.2 RXNCONSO format

`RXNCONSO.RRF/RFF` thường là pipe-delimited, không header. Cần parser robust.

Cột quan trọng thường gặp trong RxNorm RRF:

```text
RXCUI | LAT | TS | LUI | STT | SUI | ISPREF | RXAUI | SAUI | SCUI | SDUI | SAB | TTY | CODE | STR | SRL | SUPPRESS | CVF
```

Script nên đọc bằng:

```python
pd.read_csv(path, sep="|", header=None, dtype=str, keep_default_na=False)
```

Nếu dòng có trailing pipe, pandas có thể tạo cột rỗng cuối; cần xử lý.

#### 7.2.3 Processing steps

1. Đọc `RXNCONSO.rff`.
2. Gán tên cột theo RxNorm RRF.
3. Filter:
   - `LAT == 'ENG'` nếu dữ liệu chủ yếu English.
   - `SAB == 'RXNORM'` ưu tiên.
   - `SUPPRESS != 'Y'` nếu có.
4. Giữ các TTY quan trọng:
   - `IN` ingredient
   - `PIN` precise ingredient
   - `BN` brand name
   - `SCD` semantic clinical drug
   - `SBD` semantic branded drug
   - `GPCK`, `BPCK` pack
   - `MIN`, `DF`, `SCDC`, tùy file
5. Tạo alias rows:

```text
rxcui
tty
str
str_norm
ingredient_guess
strength_guess
unit_guess
form_guess
is_brand
source
```

6. Parse strength từ `STR` bằng regex:

```text
250 MG
25 MG Oral Tablet
0.4 MG/ML
1 GM
750 MG/150ML
```

7. Tạo brand/generic override table:

`data/dictionaries/drug_aliases.csv`:

```csv
alias,generic_hint,rxcui_hint,notes
tylenol,acetaminophen,,
lasix,furosemide,,
bactrim,sulfamethoxazole trimethoprim,,
cotrimoxazol,sulfamethoxazole trimethoprim,,
gleevec,imatinib,,
eliquis,apixaban,,
coumadin,warfarin,,
z-pack,azithromycin,,
prograf,tacrolimus,,
cellcept,mycophenolate,,
```

8. Lưu:
   - `data/processed/rxnorm_index.parquet`
   - `data/processed/rxnorm_aliases.parquet`

#### 7.2.4 Unit tests

- `metoprolol 25mg po bid` retrieve metoprolol 25 mg candidate.
- `aspirin 325mg x 1` retrieve aspirin 325 mg candidate.
- `tylenol` retrieve acetaminophen.
- `lasix` retrieve furosemide.
- `bactrim` retrieve sulfamethoxazole/trimethoprim.
- `gleevec` retrieve imatinib.

---

### 7.3 Sparse retrieval indices

#### 7.3.1 BM25 index

Tạo BM25 cho:

- ICD aliases.
- RxNorm aliases.

Tokenization:

- lowercase;
- Unicode normalize;
- optional no-diacritics version;
- split punctuation;
- preserve medical abbreviations.

#### 7.3.2 Char n-gram TF-IDF

Tạo char n-gram index cho typo/dính chữ.

Recommended:

```python
TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), lowercase=True)
```

Dùng cho:

- `doxycyclinebactrim`
- `vancozosynbactrim`
- `cảm giáckhó`
- thiếu dấu/typo nhẹ.

#### 7.3.3 Output

```text
data/processed/vector_indices/
├── icd_bm25.pkl
├── icd_tfidf.pkl
├── icd_tfidf_matrix.npz
├── rx_bm25.pkl
├── rx_tfidf.pkl
└── rx_tfidf_matrix.npz
```

---

### 7.4 Definition of Done

- [ ] Build script chạy được một lệnh:

```bash
python scripts/build_all_indices.py --config configs/default.yaml
```

- [ ] Có `icd10_index.parquet`, `rxnorm_index.parquet`.
- [ ] Có unit tests cho các query phổ biến.
- [ ] Không crash nếu CSV/RFF có cột dư/trailing delimiter.

---

## 8. Phase 2 - Preprocessing + offset mapping

### 8.1 Mục tiêu

Sinh representation phục vụ matching nhưng giữ raw offset tuyệt đối.

### 8.2 Module cần cài

```text
src/preprocess/normalizer.py
src/preprocess/offset_mapper.py
src/preprocess/chunker.py
```

### 8.3 Normalization design

Tạo nhiều view:

```python
@dataclass
class TextViews:
    raw: str
    normalized: str
    search: str
    no_diacritics: str
    norm_to_raw: list[int]
    search_to_raw: list[int]
```

#### Raw view

- Không sửa.
- Dùng để lấy output span.

#### Normalized view

- Unicode NFC.
- Normalize một số whitespace nhưng vẫn có map về raw.
- Lowercase optional.

#### Search view

- Lowercase.
- Bỏ dấu tiếng Việt.
- Chuẩn hóa một số ký tự:
  - `xquang` ↔ `x-quang`
  - `ct` ↔ `chụp cắt lớp vi tính`
  - `ecg/ekg` ↔ `điện tâm đồ`
- Có thể không map 1-1 nếu expand abbreviation; vì vậy nếu expand thì dùng cho retrieval, không dùng trực tiếp để xác định offset.

### 8.4 Chunking

Chunk theo thứ tự ưu tiên:

1. Heading/section.
2. Line break.
3. Bullet.
4. Sentence punctuation.
5. Colon/semi-colon khi câu quá dài.

Object:

```python
@dataclass
class Chunk:
    text: str
    start: int
    end: int
    section: str | None
    subsection: str | None
    line_id: int
    bullet_level: int | None
```

### 8.5 Unit tests bắt buộc

- Text có nhiều spaces: offset vẫn đúng.
- Text có tiếng Việt dấu: offset vẫn đúng.
- Text có newline/bullet: chunk start/end đúng.
- Dính chữ không bị tự ý sửa raw.

### 8.6 Definition of Done

- [ ] Mọi chunk slice đúng raw.
- [ ] Có `TextViews` và map về raw.
- [ ] Có tests cho offset.

---

## 9. Phase 3 - Section detection

### 9.1 Mục tiêu

Gán section label cho từng chunk để hỗ trợ extraction/assertion/type resolution.

### 9.2 Section labels

```python
SECTION_LABELS = {
    "PAST_HISTORY",
    "PAST_MEDICAL_HISTORY",
    "PRE_ADMISSION_MEDICATION",
    "CURRENT_ILLNESS",
    "CURRENT_SYMPTOM",
    "SYMPTOM_CHARACTERISTIC",
    "PRE_ADMISSION_EVENT",
    "HOSPITAL_ASSESSMENT",
    "PHYSICAL_EXAM",
    "LAB_RESULT",
    "IMAGING_RESULT",
    "PROCEDURE",
    "TREATMENT",
    "DIAGNOSIS_FINDING",
    "UNKNOWN",
}
```

### 9.3 Pattern config

`configs/section_patterns.yaml`:

```yaml
PRE_ADMISSION_MEDICATION:
  - "thuốc trước khi nhập viện"
  - "thuốc dùng trước khi nhập viện"
  - "thuốc đang dùng trước"
  - "thuốc đã điều trị trước"
CURRENT_SYMPTOM:
  - "triệu chứng hiện tại"
  - "triệu chứng khi nhập viện"
  - "tình trạng lúc vào"
LAB_RESULT:
  - "kết quả xét nghiệm"
  - "kết quả phòng thí nghiệm"
  - "laboratory"
IMAGING_RESULT:
  - "kết quả chẩn đoán hình ảnh"
  - "kết quả chụp"
  - "cận lâm sàng"
PROCEDURE:
  - "thủ thuật"
  - "các thủ thuật đã thực hiện"
DIAGNOSIS_FINDING:
  - "chẩn đoán"
  - "phát hiện chẩn đoán"
  - "các phát hiện chẩn đoán khác"
```

### 9.4 Logic

1. Scan line by line.
2. Detect heading by regex:
   - starts with numbering: `1.`, `2.`, `3.`.
   - contains known heading phrase.
   - line length not too long.
3. Maintain current section until next heading.
4. Subheading detected by line prefix or colon.
5. Attach section to chunks.

### 9.5 Section confidence

Mỗi chunk nên có:

```python
section: str
section_confidence: float
section_source: str
```

Vì section có thể sai, downstream chỉ dùng như feature.

### 9.6 Definition of Done

- [ ] File 1 detect đúng các phần thuốc, triệu chứng, xét nghiệm, imaging.
- [ ] Không crash khi record không có heading.
- [ ] Section được log trong Streamlit UI.

---

## 10. Phase 4 - Span extraction baseline

Span extraction nên chạy nhiều extractor song song, rồi union candidates.

```text
Drug extractor
Lab/test extractor
Problem extractor: symptom + diagnosis
Dictionary extractor
NER extractor, optional phase sau
Imaging extractor
```

---

### 10.1 Drug extractor

#### 10.1.1 Mục tiêu

Extract `THUỐC` với span đầy đủ nhất có thể: tên + dose + route + frequency nếu cùng cụm.

#### 10.1.2 Sources

- RxNorm aliases.
- Drug aliases thủ công.
- Regex dose/route/frequency.
- Section `PRE_ADMISSION_MEDICATION`, `TREATMENT`, `PROCEDURE` nếu có drug administration.

#### 10.1.3 Regex components

```python
DRUG_NAME = r"[A-Za-z][A-Za-z0-9\-\/]+(?:\s+[A-Za-z0-9\-\/]+){0,4}"
DOSE = r"(?:\d+(?:[\.,]\d+)?\s*(?:mg|mcg|g|gram|ml|mg/ml|units?|đơn vị))"
ROUTE = r"(?:po|iv|im|sc|oral|uống|tiêm|truyền|nebs?|nebulizer)"
FREQ = r"(?:daily|bid|tid|qid|q\d+h|prn|qam|qhs|x\s*\d+|/ngày|lần/ngày)"
```

Pattern examples:

```text
metoprolol 25mg po bid
aspirin 325mg x 1
vancomycin 1 gram
prednisone 40 mg/ngày
albuterolipratropium nebs x2
```

#### 10.1.4 Drug mention expansion

Nếu rule bắt được tên thuốc nhưng chưa bắt dose/frequency, mở rộng span sang phải trong cùng line/chunk nếu gặp dose/route/frequency gần đó.

Ví dụ:

```text
metoprolol (reduced from 50mg to 25mg daily)
```

Có thể chọn span là `metoprolol` hoặc mở rộng tùy confidence. Với metric text, nếu annotation hay lấy full phrase thì cần calibration trên golden.

#### 10.1.5 Dính chữ / split

Cần fuzzy splitter:

```text
doxycyclinebactrim → doxycycline + bactrim
vancozosynbactrim → vancomycin/vanco + zosyn + bactrim
albuterolipratropium → albuterol + ipratropium
```

Approach:

1. Nếu substring match nhiều drug aliases liền nhau, tách thành nhiều spans.
2. Nếu candidate dài không có RxNorm match tốt, thử segment bằng dynamic programming trên drug alias trie.

#### 10.1.6 Output features

```python
features = {
    "drug_name": "metoprolol",
    "strength": "25mg",
    "route": "po",
    "frequency": "bid",
    "from_rxnorm_alias": True,
    "from_drug_alias": False,
}
```

#### 10.1.7 Tests

- `metoprolol 25mg po bid`
- `atenolol (uống hôm nay)`
- `aspirin 325mg x 1`
- `iv lasix 40 mg once`
- `albuterolipratropium nebs x2`
- `doxycyclinebactrim`

---

### 10.2 Lab/test extractor

#### 10.2.1 Mục tiêu

Extract pair `TÊN_XÉT_NGHIỆM` và `KẾT_QUẢ_XÉT_NGHIỆM`.

#### 10.2.2 Lab dictionary

`data/dictionaries/lab_tests.csv`:

```csv
alias,canonical,type
wbc,bạch cầu,lab
bạch cầu,bạch cầu,lab
troponin,troponin,lab
creatinine,creatinine,lab
cr,creatinine,lab
k,kali,lab
kali,kali,lab
inr,inr,lab
hct,hematocrit,lab
hemoglobin,hemoglobin,lab
bilirubin,bilirubin,lab
ast,aspartate aminotransferase,lab
alt,alanine aminotransferase,lab
ua,tổng phân tích nước tiểu,lab
cấy máu,cấy máu,microbiology
cấy nước tiểu,cấy nước tiểu,microbiology
```

#### 10.2.3 Result patterns

```text
<test> là <value>
<test>: <value>
<test> <value>
<test> tăng/giảm/tăng nhẹ/giảm xuống
<test> âm tính/dương tính/bình thường
<test> từ <v1> lên <v2>
```

Value regex:

```python
VALUE = r"(?:\d+(?:[\.,]\d+)?(?:\s*(?:mg/dl|mmol/l|g/l|%|G/L|mEq|mEq/L))?|âm tính|dương tính|bình thường|tăng|giảm|cao|thấp)"
```

#### 10.2.4 Pairing logic

Với một match `test` và `result`:

- Emit test span: `TÊN_XÉT_NGHIỆM`.
- Emit result span: `KẾT_QUẢ_XÉT_NGHIỆM`.
- Store relation nội bộ `test_result_pair_id`, dù output đề không yêu cầu relation field.

Ví dụ:

```text
troponin là 0.10
```

Output candidates:

```json
{"text": "troponin", "type": "TÊN_XÉT_NGHIỆM", "position": [...]}
{"text": "0.10", "type": "KẾT_QUẢ_XÉT_NGHIỆM", "position": [...]}
```

#### 10.2.5 Imaging as test

Các cụm imaging có thể là `TÊN_XÉT_NGHIỆM` nếu có kết quả đi kèm:

- `chụp x-quang ngực`
- `chụp ct sọ não`
- `mri`
- `siêu âm`
- `điện tâm đồ`
- `monitor holter`
- `xạ hình tưới máu cơ tim`

Kết quả imaging có thể là:

- `bình thường`
- `không có gì đáng chú ý`
- `tim to`
- `viêm phổi thùy dưới phải`
- `bóc tách động mạch chủ Stanford loại B`

Nhưng nếu kết quả là disease/finding rõ, cần cân nhắc emit thêm `CHẨN_ĐOÁN` cho finding.

#### 10.2.6 Tests

- `troponin 0.01`
- `kali là 6.3`
- `creatinine 2.0 -> 3.2`
- `tổng phân tích nước tiểu bình thường`
- `cấy máu âm tính`
- `chụp x-quang ngực không ghi nhận gì bất thường`

---

### 10.3 Problem extractor: `TRIỆU_CHỨNG` + `CHẨN_ĐOÁN`

#### 10.3.1 Mục tiêu

Sinh problem mentions trước, chưa cần quyết định type tuyệt đối. Sau đó Type Resolver phân biệt symptom vs diagnosis.

#### 10.3.2 Sources

- Symptom dictionary.
- Diagnosis alias dictionary.
- ICD alias dictionary.
- Rule disease-head/symptom-head.
- NER model ở phase sau.
- Imaging/finding patterns.

#### 10.3.3 Symptom-head patterns

```text
đau + body_part/modifier
khó + verb
ho / sốt / buồn nôn / nôn / chóng mặt / mệt mỏi
phù / sưng / chảy máu / tiêu chảy / táo bón
ngất / mất ý thức / rối loạn ý thức
nhìn mờ / song thị / ảo giác / lo âu / tự tử
```

Cần bắt cụm dài:

```text
đau bụng vùng hạ sườn phải
khó thở khi gắng sức
ho mạn tính có đờm vàng loãng
đau ngực trái dữ dội lan xuống cánh tay trái
```

#### 10.3.4 Disease-head patterns

```text
viêm ...
ung thư ...
suy ...
xơ gan ...
rung nhĩ ...
nhồi máu ...
thuyên tắc ...
xuất huyết ...
hẹp ...
tắc ...
bóc tách ...
gãy ...
áp xe ...
u ác ...
bệnh ...
```

#### 10.3.5 Span boundary rules

- Không lấy trigger negation vào span: `không sốt` → span `sốt`.
- Lấy modifier y khoa nếu giúp rõ nghĩa: `đau bụng vùng hạ sườn phải`.
- Không lấy quá dài qua dấu câu/list boundary.
- Với repeated word typo, có thể trim duplicate nếu annotation golden ủng hộ.

#### 10.3.6 Tests

- `đánh trống ngực`
- `cảm giác thắt chặt ngực vùng trước tim`
- `bệnh tim mạch do xơ vữa động mạch`
- `rung nhĩ kèm đáp ứng thất nhanh`
- `bóc tách động mạch chủ Stanford loại B`
- `viêm túi mật cấp`
- `lo ngại áp xe`

---

### 10.4 Dictionary extractor

Dùng Aho-Corasick hoặc trie/regex để match dictionary aliases.

Dictionaries:

```text
symptoms_vi.csv
diagnosis_aliases.csv
drug_aliases.csv
lab_tests.csv
abbreviations.csv
```

Output source: `dictionary`.

Cần có filters:

- Không match alias quá ngắn nếu không có context, ví dụ `k`, `cr`, `ct`.
- Với abbreviation, cần section/context phù hợp.
- Với body part đơn lẻ, không emit nếu không có symptom/disease head.

---

### 10.5 NER extractor, phase sau

Cài interface trước, model có thể dummy ở baseline.

```python
class NERExtractor:
    def extract(self, text: str, chunks: list[Chunk]) -> list[SpanCandidate]:
        ...
```

BIO labels:

```text
B-SYM, I-SYM
B-DX, I-DX
B-DRUG, I-DRUG
B-TEST, I-TEST
B-RESULT, I-RESULT
O
```

Trong baseline, có thể disable bằng config:

```yaml
extractors:
  ner:
    enabled: false
```

---

## 11. Phase 5 - Type resolution

### 11.1 Mục tiêu

Chuyển `SpanCandidate` thành type cuối cùng:

```text
TRIỆU_CHỨNG / CHẨN_ĐOÁN / THUỐC / TÊN_XÉT_NGHIỆM / KẾT_QUẢ_XÉT_NGHIỆM
```

### 11.2 Priority cơ bản

```text
Lab/result rule > Drug rule/dictionary > Diagnosis/Symptom resolver > NER fallback
```

Lý do:

- Lab và thuốc có pattern mạnh.
- Diagnosis/symptom dễ nhập nhằng.

### 11.3 Feature set

```python
features = {
    "source_scores": {...},
    "section": chunk.section,
    "has_symptom_head": bool,
    "has_disease_head": bool,
    "icd_linkability_score": float,
    "rxnorm_linkability_score": float,
    "lab_pattern_score": float,
    "drug_pattern_score": float,
    "ner_logits": {...},
    "left_context": str,
    "right_context": str,
}
```

### 11.4 Decision policy

1. Nếu span từ lab result pattern → lab/test type.
2. Nếu span match RxNorm/drug alias + drug context → `THUỐC`.
3. Nếu disease-head hoặc ICD-linkability cao → `CHẨN_ĐOÁN`.
4. Nếu symptom-head và không có disease-head mạnh → `TRIỆU_CHỨNG`.
5. Nếu NER confidence cao và không conflict → dùng NER label.
6. Nếu conflict, log vào `type_conflicts.jsonl`.

### 11.5 Ví dụ policy

| Mention | Context | Type |
|---|---|---|
| `khó thở` | triệu chứng hiện tại | `TRIỆU_CHỨNG` |
| `viêm phổi` | chụp x-quang gợi ý | `CHẨN_ĐOÁN` |
| `tăng kali máu` | lý do nhập viện | `CHẨN_ĐOÁN` |
| `kali 6.3` | kết quả xét nghiệm | test/result pair |
| `aspirin 325mg` | được chỉ định điều trị | `THUỐC` |

### 11.6 Definition of Done

- [ ] Có resolver deterministic.
- [ ] Có log conflict.
- [ ] Có test phân biệt symptom vs diagnosis.
- [ ] Không để ICD linker tự biến mọi symptom thành diagnosis.

---

## 12. Phase 6 - Assertion detection

### 12.1 Mục tiêu

Gán assertion cho mỗi `FinalEntity` thuộc:

```text
TRIỆU_CHỨNG, CHẨN_ĐOÁN, THUỐC
```

Assertions hợp lệ:

```text
isNegated
isFamily
isHistorical
```

### 12.2 Module structure

```text
src/assertion/
├── negation.py
├── historical.py
├── family.py
├── context_rules.py
└── assertion_detector.py
```

### 12.3 Negation detection

#### Cues

```yaml
negation_pre:
  - "không"
  - "không có"
  - "không thấy"
  - "không ghi nhận"
  - "chưa ghi nhận"
  - "chưa phát hiện"
  - "phủ nhận"
negation_post:
  - "âm tính"
  - "bình thường"
  - "không bất thường"
termination:
  - "nhưng"
  - "tuy nhiên"
  - "."
  - ";"
```

#### Scope examples

```text
Không sốt, ớn lạnh, nôn, táo bón, ho
```

Scope từ `Không` đến hết list hoặc termination.

```text
chụp x-quang ngực không phát hiện viêm phổi hoặc phù phổi
```

`viêm phổi`, `phù phổi` negated.

```text
tổng phân tích nước tiểu âm tính nitrite
```

Có thể là lab result `âm tính`, không nhất thiết gán negation cho test name.

### 12.4 Historical detection

#### Không hardcode section

Historical là mention-level.

Features:

- Section prior.
- Temporal cue.
- Medication status cue.
- Event status cue.
- Current illness override.

#### Cues tăng historical

```text
tiền sử
trước đây
đã từng
mạn tính
thuốc trước khi nhập viện
đang dùng tại nhà
đã ngừng
ngừng uống
cách nhập viện ...
lần nhập viện trước
```

#### Cues giảm historical

```text
hiện tại
lúc vào viện
khi nhập viện
được chỉ định điều trị
được cho dùng
kết quả tại bệnh viện
đánh giá tại bệnh viện
phát hiện mới
chẩn đoán sơ bộ
```

#### Drug-specific policy

- `Thuốc trước khi nhập viện` + drug → historical high confidence.
- `đang dùng X tại nhà` → historical/current home med; output thường `isHistorical`.
- `được cho X`, `bắt đầu X tại cấp cứu`, `điều trị tại bệnh viện` → không historical mặc định.
- `ngừng X cách nhập viện 5 ngày` → historical.

#### Symptom-specific policy

- `tiền sử đau lưng kéo dài từ lâu` → `đau lưng` historical.
- `triệu chứng hiện tại: đau lưng` → không historical.
- `từng bị đau bụng sau khi uống sữa` → historical.
- `lý do nhập viện: đau bụng` → không historical.

### 12.5 Family detection

High precision only.

Cues:

```text
bố
mẹ
anh
chị
em
con
vợ
chồng
người nhà
gia đình
nhiều thành viên trong gia đình
```

Không gán `isFamily` nếu family member chỉ là người kể/phát hiện:

```text
con trai phát hiện bệnh nhân nằm trên sàn
vợ nhận thấy bệnh nhân ảo giác
cháu gái hét lên
```

Chỉ gán khi entity thuộc family member:

```text
vợ có triệu chứng tương tự
mẹ mắc ung thư
nhiều thành viên trong gia đình có ho và chảy nước mũi
```

### 12.6 Assertion confidence

Mỗi assertion nên có score nội bộ:

```python
assertion_scores = {
    "isNegated": 0.0,
    "isHistorical": 0.0,
    "isFamily": 0.0,
}
```

Threshold ban đầu:

```yaml
assertion_thresholds:
  isNegated: 0.55
  isHistorical: 0.60
  isFamily: 0.80
```

Family threshold nên cao để tránh false positive.

### 12.7 Definition of Done

- [ ] Negation có scope qua list.
- [ ] Historical event-aware.
- [ ] Family high precision.
- [ ] Assertions không gán cho lab/test result.
- [ ] Có test với `1.txt` case aspirin vs thuốc trước nhập viện.

---

## 13. Phase 7 - ICD-10 candidate generation

### 13.1 Mục tiêu

Với mỗi entity `CHẨN_ĐOÁN`, trả danh sách ICD candidate codes.

### 13.2 Input

```python
FinalEntity(type="CHẨN_ĐOÁN", text="viêm túi mật cấp", context=...)
```

### 13.3 Normalization

```python
normalize_diagnosis(text):
    - lowercase
    - remove extra spaces
    - normalize punctuation
    - normalize common variants
    - no-diacritics variant
    - expand abbreviations when safe
```

Examples:

```text
GERD → trào ngược dạ dày thực quản
COPD → bệnh phổi tắc nghẽn mạn tính
UTI → nhiễm khuẩn đường tiết niệu
MI → nhồi máu cơ tim
```

### 13.4 Candidate generation tiers

#### Tier 1 - Exact/alias match

- Search `alias_norm == mention_norm`.
- Search no-diacritics exact.
- Search abbreviation alias.

#### Tier 2 - Lexical fuzzy

- BM25 top 20.
- Char TF-IDF top 20.
- RapidFuzz token sort ratio top 20.

#### Tier 3 - Dense retrieval

Phase sau, dùng sentence-transformers hoặc SapBERT-like embedding.

- Encode mention + short context.
- Retrieve top 20 from ICD alias embeddings.

#### Tier 4 - Context/rule boosting

Boost nếu slot match:

- `cấp` vs acute.
- `mạn` vs chronic.
- `do rượu` vs alcoholic.
- `típ 2` vs type 2.
- anatomy match: phổi, gan, thận, đại tràng, tuyến tiền liệt.
- laterality/location if present.

### 13.5 Candidate scoring

Initial formula:

```python
score = (
    0.40 * exact_or_alias_score +
    0.25 * bm25_score +
    0.20 * char_tfidf_score +
    0.10 * slot_match_score +
    0.05 * section_context_score
)
```

Nếu dense available:

```python
score = (
    0.25 * lexical_score +
    0.25 * dense_score +
    0.25 * slot_match_score +
    0.25 * rerank_score
)
```

### 13.6 Candidate count policy

```yaml
icd10:
  max_candidates: 3
  min_score_top1: 0.55
  include_second_if_within: 0.05
  min_score_additional: 0.70
```

Suggested logic:

1. Always keep top-1 if score >= threshold.
2. Add top-2 only if:
   - score close to top-1, or
   - candidate is sibling/variant likely accepted, or
   - golden shows multi-code annotation for that concept.
3. Do not return >3 unless absolutely necessary.

### 13.7 Logging

Log all diagnosis with:

```json
{
  "text": "viêm túi mật cấp",
  "context": "...",
  "top_candidates": [...],
  "chosen": [...],
  "scores": {...}
}
```

### 13.8 Tests

- `tăng huyết áp`
- `đái tháo đường típ 2`
- `bệnh phổi tắc nghẽn mạn tính`
- `viêm túi mật cấp`
- `xơ gan do rượu`
- `rung nhĩ kèm đáp ứng thất nhanh`
- `bóc tách động mạch chủ Stanford loại B`
- `ung thư đại tràng`

---

## 14. Phase 8 - RxNorm candidate generation

### 14.1 Mục tiêu

Với mỗi entity `THUỐC`, trả RxCUI candidates.

### 14.2 Drug parser

Parse mention:

```python
@dataclass
class ParsedDrug:
    raw: str
    ingredient_or_brand: str | None
    normalized_name: str | None
    strength_value: float | None
    strength_unit: str | None
    route: str | None
    frequency: str | None
    dose_form: str | None
    is_combination: bool
```

Examples:

```text
metoprolol 25mg po bid
→ name=metoprolol, strength=25 mg, route=po, freq=bid

vancomycin 1 gram
→ name=vancomycin, strength=1 gram

albuterolipratropium nebs x2
→ combination: albuterol + ipratropium, route/form=nebs
```

### 14.3 Candidate generation tiers

#### Tier 1 - Exact alias match

- exact normalized mention to RxNorm `STR`.
- exact drug name if mention only has ingredient/brand.

#### Tier 2 - Brand/generic alias

- `tylenol` → acetaminophen.
- `lasix` → furosemide.
- `coumadin` → warfarin.

#### Tier 3 - Ingredient + strength

Filter candidates where:

- ingredient matches;
- strength value close;
- unit compatible.

#### Tier 4 - Fuzzy retrieval

- BM25.
- Char TF-IDF.
- RapidFuzz.

#### Tier 5 - Dense retrieval/reranker

Optional phase, useful for brand names and weird strings.

### 14.4 Constraint filtering

Hard filters:

- Candidate ingredient must match parsed ingredient or brand alias unless confidence low.
- If mention has strength, prefer candidates containing same strength.
- Do not choose totally unrelated drug due to fuzzy typo.

Soft boosts:

- route/form match.
- TTY priority:
  - SCD/SBD if mention includes strength/form.
  - IN/BN if mention only drug name.

### 14.5 Candidate count policy

```yaml
rxnorm:
  max_candidates: 2
  min_score_top1: 0.60
  include_second_if_within: 0.04
```

Policy:

- Drug with exact strength → top-1.
- Brand/generic ambiguity → top-1 after alias table.
- Combination drug → may output multiple RxCUIs only if annotation/golden expects; otherwise prefer combination RxCUI if exists.

### 14.6 Tests

- `metoprolol 25mg po bid`
- `aspirin 325mg x 1`
- `atenolol`
- `omeprazole`
- `vancomycin 1 gram`
- `levofloxacin 750mg iv`
- `tylenol`
- `lasix`
- `coumadin`
- `suboxone`

---

## 15. Phase 9 - Reranking

### 15.1 Mục tiêu

Sắp xếp và lọc ICD/RxNorm candidates để tăng candidate Jaccard.

### 15.2 Baseline reranker

Bản đầu dùng formula deterministic:

```python
final_score = (
    w_lexical * lexical_score +
    w_dense * dense_score +
    w_slot * slot_match_score +
    w_context * context_score +
    w_source * source_score
)
```

Weights nằm trong `configs/thresholds.yaml`.

### 15.3 Cross-encoder reranker, phase sau

Input pair:

```text
Mention: <entity text>
Context: <left/right sentence>
Candidate code: <code>
Candidate name: <canonical name>
Question: Is this the correct mapping?
```

Output score 0-1.

Training data:

- Positive từ golden dataset.
- Synthetic positives từ dictionary exact match.
- Hard negatives từ top lexical candidates cùng nhóm.

### 15.4 LLM verifier, optional

Nếu dùng local LLM ≤9B:

- Không cho LLM sinh mã tự do.
- Prompt chỉ yêu cầu chọn trong candidate pool.
- Output phải là JSON đơn giản: candidate codes + confidence.
- Nếu output không parse được, fallback deterministic reranker.

### 15.5 Definition of Done

- [ ] Reranker không chọn code ngoài pool.
- [ ] Candidate score được log.
- [ ] Threshold tune được trên golden dataset.

---

## 16. Phase 10 - Merge overlap & post-processing

### 16.1 Mục tiêu

Hợp nhất spans từ nhiều extractor, loại duplicate, xử lý overlap/type conflict.

### 16.2 Priority source

```text
lab_rule > drug_rule > exact_dictionary > ner > fuzzy_dictionary > llm_verifier
```

### 16.3 Merge rules

#### Same type overlap

- Nếu IoU char > 0.8: giữ span score cao hơn.
- Nếu một span chứa span khác:
  - symptom/diagnosis: thường giữ span dài hơn nếu không quá dài.
  - drug: giữ span có dose/frequency nếu cùng drug.
  - lab: giữ test/result riêng, không merge test với value.

#### Different type overlap

- `THUỐC` vs `TRIỆU_CHỨNG`: drug priority nếu match RxNorm.
- `TÊN_XÉT_NGHIỆM` vs `CHẨN_ĐOÁN`: nếu cụm là imaging test thì giữ test; nếu finding disease trong result thì giữ thêm diagnosis ở span riêng.
- `CHẨN_ĐOÁN` vs `TRIỆU_CHỨNG`: dùng type resolver; nếu vẫn conflict, chọn type có confidence cao, log conflict.

#### Negation trigger trim

Nếu span bắt cả cue:

```text
không sốt
```

Trim thành:

```text
sốt
```

và assertion `isNegated`.

### 16.4 Duplicate policy

- Same `(start, end, type)` → keep one.
- Same text/type different position → keep both.
- Same code candidate across duplicates okay.

### 16.5 Definition of Done

- [ ] Không có duplicate exact.
- [ ] Không có span invalid.
- [ ] Conflict log đầy đủ.

---

## 17. Phase 11 - JSON formatter & validation

### 17.1 Output formatter

Final JSON object:

```python
obj = {
    "text": entity.text,
    "type": entity.type,
    "assertions": entity.assertions,
    "position": [entity.start, entity.end],
}
if entity.type in {"CHẨN_ĐOÁN", "THUỐC"}:
    obj["candidates"] = entity.candidates
```

Nên thống nhất:

- `assertions` luôn có, kể cả `[]`, cho 3 type clinical problem/drug.
- Với lab/test, có thể để không có `assertions` hoặc để `[]`; nên xem ví dụ đề. Để an toàn và đồng nhất, có thể luôn output `assertions: []` cho mọi type, nhưng nếu format strict không yêu cầu thì config hóa.
- `candidates` chỉ output cho `CHẨN_ĐOÁN`, `THUỐC`.

### 17.2 Schema validator

Check:

```python
VALID_TYPES = {
    "TRIỆU_CHỨNG",
    "TÊN_XÉT_NGHIỆM",
    "KẾT_QUẢ_XÉT_NGHIỆM",
    "CHẨN_ĐOÁN",
    "THUỐC",
}
VALID_ASSERTIONS = {"isNegated", "isFamily", "isHistorical"}
```

Validation:

- JSON là list.
- Mỗi item có `text`, `type`, `position`.
- `position` là list 2 int.
- `0 <= start < end <= len(raw_text)`.
- `raw_text[start:end] == text`.
- Type hợp lệ.
- Assertion hợp lệ.
- Candidate string list.
- Candidate chỉ ở diagnosis/drug.

### 17.3 File-level validator

- Có đủ file `1.json` ... `100.json`.
- Không file nào empty do crash.
- JSON parse được.
- Không có NaN/None.
- UTF-8 encoding.

### 17.4 Validation CLI

```bash
python scripts/run_validate.py \
  --input_dir data/raw/input \
  --pred_dir outputs/predictions/run_001 \
  --report_dir outputs/reports/run_001_validation
```

### 17.5 Definition of Done

- [ ] Validator pass 100% trên prediction.
- [ ] Validator pass 100% trên golden gold files.
- [ ] Có report lỗi rõ ràng.

---

## 18. Phase 12 - Golden dataset evaluation

Bạn đã chọn 20 file và annotate thủ công. Đây là tài sản rất quan trọng để calibrate threshold và debug.

### 18.1 Folder structure

```text
data/golden/
├── input/
│   ├── <id>.txt
│   └── ... 20 files
├── gold/
│   ├── <id>.json
│   └── ... 20 files
├── split.json
└── notes.md
```

`split.json`:

```json
{
  "golden_ids": [1, 4, 7, 12, 17, 23, 28, 32, 40, 41, 50, 54, 58, 63, 70, 75, 84, 88, 96, 100]
}
```

Danh sách trên chỉ là ví dụ. Dùng đúng 20 file bạn đã chọn.

### 18.2 Gold validator

Trước khi dùng golden để đo model, phải validate gold:

```bash
python scripts/run_validate.py \
  --input_dir data/golden/input \
  --pred_dir data/golden/gold \
  --report_dir outputs/reports/golden_gold_validation
```

Nếu gold có offset mismatch thì sửa gold trước, không sửa evaluator.

### 18.3 Evaluator design

Cần một evaluator mô phỏng càng sát metric đề càng tốt.

Do metric chính thức có chi tiết matching có thể khác implementation nội bộ, evaluator local nên hỗ trợ nhiều view:

1. Exact span/type metric.
2. Text WER-like metric.
3. Assertion Jaccard.
4. Candidate Jaccard.
5. Final weighted score approximation.
6. Error tables by type.

### 18.4 Matching strategy local

Để phân tích lỗi, nên có 2 chế độ:

#### Strict mode

Match khi:

```text
start == gold_start AND end == gold_end AND type == gold_type
```

Dùng để debug offset/type.

#### Relaxed mode

Match khi:

```text
same type AND char overlap IoU >= threshold
```

Dùng để xem model bắt gần đúng nhưng sai boundary.

Threshold đề xuất:

```yaml
match:
  char_iou: 0.5
```

### 18.5 Metrics report

Sinh report:

```text
outputs/reports/golden_eval/run_<timestamp>/
├── summary.json
├── per_file_metrics.csv
├── per_type_metrics.csv
├── false_positive.jsonl
├── false_negative.jsonl
├── type_errors.jsonl
├── assertion_errors.jsonl
├── candidate_errors.jsonl
├── boundary_errors.jsonl
└── html_report.html optional
```

Summary fields:

```json
{
  "num_files": 20,
  "num_gold_entities": 1234,
  "num_pred_entities": 1180,
  "strict_precision": 0.0,
  "strict_recall": 0.0,
  "strict_f1": 0.0,
  "relaxed_precision": 0.0,
  "relaxed_recall": 0.0,
  "relaxed_f1": 0.0,
  "assertion_jaccard": 0.0,
  "candidate_jaccard": 0.0,
  "approx_final_score": 0.0
}
```

### 18.6 Golden-driven calibration

Dùng golden để tune:

- dictionary thresholds;
- ICD/RxNorm candidate thresholds;
- merge overlap settings;
- assertion thresholds;
- span expansion/trimming;
- symptom vs diagnosis resolver.

Không nên tune quá mức vào 20 file. Mọi rule thêm vào nên general hóa.

### 18.7 Error buckets cần review hằng ngày

1. Offset mismatch.
2. Missing drug.
3. Missing lab result.
4. Symptom predicted as diagnosis.
5. Diagnosis predicted as symptom.
6. Missing ICD candidate.
7. Wrong RxNorm due to brand/generic/dose.
8. False `isHistorical` do section.
9. False `isFamily` do người nhà chỉ là người kể.
10. False negation due to scope quá rộng.

### 18.8 Definition of Done

- [ ] Gold 20 files pass validation.
- [ ] Prediction trên 20 files pass validation.
- [ ] Evaluator sinh đầy đủ reports.
- [ ] Có baseline score đầu tiên.
- [ ] Có error analysis loop.

---

## 19. Phase 13 - Streamlit validation UI

### 19.1 Mục tiêu

Tạo giao diện đơn giản để:

- Chọn file `.txt`.
- Hiển thị raw text.
- Highlight prediction spans.
- Highlight gold spans nếu có.
- Xem bảng entity list.
- So sánh prediction vs gold.
- Lọc theo type/assertion/source/confidence.
- Click entity để nhảy đến span.

Không cần annotation editor đầy đủ ở bản đầu.

### 19.2 Folder

```text
streamlit_app/
├── app.py
├── components.py
└── utils.py
```

Run:

```bash
streamlit run streamlit_app/app.py -- \
  --input_dir data/golden/input \
  --gold_dir data/golden/gold \
  --pred_dir outputs/predictions/run_001
```

Hoặc dùng config:

```bash
streamlit run streamlit_app/app.py
```

### 19.3 UI layout

#### Sidebar

- Select dataset:
  - `golden`
  - `public test`
  - custom folder
- Select file ID.
- Toggle layers:
  - show gold
  - show prediction
  - show overlap/conflict
- Filter type:
  - all
  - symptom
  - diagnosis
  - drug
  - test
  - result
- Filter assertion:
  - negated
  - historical
  - family
- Confidence threshold slider.

#### Main panel

Tabs:

1. **Text view**
   - Raw text with highlights.
   - Color by entity type.
   - Gold as underline/border, prediction as background.
   - Tooltip: type, assertions, candidates, confidence, source.

2. **Entities table**
   - Prediction table.
   - Gold table.
   - Columns:
     - text
     - type
     - position
     - assertions
     - candidates
     - confidence
     - source/provenance

3. **Diff view**
   - True positives.
   - False positives.
   - False negatives.
   - Type errors.
   - Boundary errors.
   - Candidate errors.
   - Assertion errors.

4. **Debug view**
   - Chunk/section list.
   - Span candidates before merge, if available.
   - Candidate linker top-k.

### 19.4 Highlight rendering

Do HTML overlap phức tạp, nên tạo interval renderer.

Approach:

1. Collect spans selected for display.
2. Sort by start/end.
3. Resolve visual overlaps:
   - Nếu overlap gold/pred, dùng nested border hoặc label prefix.
   - Nếu too complex, show separate views:
     - Gold-only view.
     - Pred-only view.
     - Combined simplified view.
4. Escape HTML raw text.
5. Insert `<mark>` tags.

Color map:

```python
TYPE_COLORS = {
    "TRIỆU_CHỨNG": "#fff3b0",
    "CHẨN_ĐOÁN": "#ffd6a5",
    "THUỐC": "#caffbf",
    "TÊN_XÉT_NGHIỆM": "#9bf6ff",
    "KẾT_QUẢ_XÉT_NGHIỆM": "#bdb2ff",
}
```

Nếu không muốn hardcode màu trong matplotlib thì không liên quan; Streamlit HTML có thể dùng màu để UI dễ đọc.

### 19.5 Entity table actions

Bản đơn giản:

- Sort by start.
- Click/select row không nhất thiết scroll được ngay.
- Có column `context` lấy ±80 ký tự quanh span.

Bản nâng cấp:

- Khi chọn row, show context expanded.
- Copy entity JSON.
- Export current diff CSV.

### 19.6 Integration với evaluator

Streamlit nên đọc report nếu có:

```text
outputs/reports/golden_eval/latest/*.jsonl
```

Nếu chưa có report thì tự tính diff on the fly cho file đang xem.

### 19.7 Definition of Done

- [ ] Chọn được file trong golden dataset.
- [ ] Highlight được prediction spans.
- [ ] Highlight được gold spans.
- [ ] Xem được bảng entities.
- [ ] Xem được diff FP/FN/type/candidate/assertion.
- [ ] Không crash khi spans overlap.
- [ ] Không sửa file gold/pred ở bản đầu.

---

## 20. Phase 14 - NER training

### 20.1 Mục tiêu

Tăng recall cho `TRIỆU_CHỨNG` và `CHẨN_ĐOÁN`, đồng thời hỗ trợ `THUỐC`, `TÊN_XÉT_NGHIỆM`, `KẾT_QUẢ_XÉT_NGHIỆM` nếu đủ data.

### 20.2 Training data sources

1. Golden 20 files.
2. Synthetic templates.
3. Weak labels từ rule baseline trên `all.txt` hoặc dữ liệu tương tự.
4. Public Vietnamese medical NER resources nếu được phép dùng.

### 20.3 Synthetic generation

Generate theo template:

```text
Bệnh nhân nhập viện vì {symptom}.
Bệnh nhân có tiền sử {diagnosis}.
Không ghi nhận {symptom}.
Được chẩn đoán {diagnosis}.
Thuốc trước nhập viện: {drug} {dose} {route} {freq}.
Kết quả xét nghiệm: {test} là {value}.
```

Noise injection:

- lowercase/uppercase random.
- thiếu dấu.
- dính chữ.
- lặp từ.
- mixed English/Vietnamese.
- abbreviation.

### 20.4 BIO conversion

Convert JSON spans to token BIO.

Cần tokenizer offset mapping:

- Dùng tokenizer của model.
- Align char spans to token spans.
- Nếu span không align chính xác do tokenizer, label token overlap.

### 20.5 Model options

- ViHealthBERT nếu môi trường hỗ trợ.
- XLM-R/mDeBERTa multilingual nếu mixed English/Vietnamese nhiều.
- PhoBERT có thể thử nhưng text y khoa pha English nên cần so sánh.

### 20.6 Evaluation

NER riêng đo:

- span exact F1;
- relaxed F1;
- per-type F1;
- boundary error rate.

Nhưng final quyết định bằng end-to-end golden score, không chỉ NER F1.

### 20.7 Definition of Done

- [ ] Train script chạy được.
- [ ] Model load được trong pipeline.
- [ ] NER improves recall trên golden without too much FP.
- [ ] Có config bật/tắt NER.

---

## 21. Phase 15 - Dense retrieval + reranker training

### 21.1 Mục tiêu

Tăng chất lượng candidate mapping ICD/RxNorm.

### 21.2 Dense index

Build embeddings cho:

- ICD aliases.
- RxNorm aliases.

Model:

- sentence-transformers multilingual.
- SapBERT-like nếu có available local.
- Có thể fine-tune contrastive với alias pairs.

### 21.3 Training pairs

ICD:

- Positive: `(mention, correct ICD name/code)` từ golden.
- Weak positive: alias exact dictionary.
- Hard negative: top lexical candidates sai.

RxNorm:

- Positive: `(drug mention, correct RxNorm STR/RXCUI)` từ golden.
- Weak positive: ingredient/brand exact.
- Hard negative: same ingredient wrong strength, same strength wrong ingredient.

### 21.4 Cross-encoder reranker

Training input:

```json
{
  "mention": "bệnh trào ngược dạ dày thực quản",
  "context": "...",
  "candidate_code": "K21.9",
  "candidate_name": "Gastro-esophageal reflux disease without oesophagitis",
  "label": 1
}
```

### 21.5 Calibration

Tune thresholds on golden:

- `min_score_top1`.
- `include_second_if_within`.
- max candidates.
- score weight formula.

### 21.6 Definition of Done

- [ ] Dense retrieval top-k improves recall candidate pool.
- [ ] Reranker improves candidate top-1/top-k on golden.
- [ ] No candidate hallucination.

---

## 22. Phase 16 - End-to-end inference CLI

### 22.1 Script

```bash
python scripts/run_inference.py \
  --input_dir data/raw/input \
  --output_dir outputs/predictions/run_001 \
  --config configs/default.yaml
```

### 22.2 Pipeline pseudocode

```python
def process_file(path):
    raw = read_text(path)
    views = build_text_views(raw)
    chunks = chunk_text(raw, views)
    chunks = detect_sections(chunks)

    span_candidates = []
    for extractor in extractors:
        span_candidates.extend(extractor.extract(raw, views, chunks))

    typed = type_resolver.resolve(span_candidates, raw, chunks)
    asserted = assertion_detector.apply(typed, raw, chunks)

    for ent in asserted:
        if ent.type == "CHẨN_ĐOÁN":
            ent.candidates = icd10_linker.link(ent, raw)
        elif ent.type == "THUỐC":
            ent.candidates = rxnorm_linker.link(ent, raw)
        else:
            ent.candidates = []

    merged = merge_entities(asserted, raw)
    formatted = format_output(merged)
    validate_output(raw, formatted)
    return formatted
```

### 22.3 Batch behavior

- Nếu một file crash, log error nhưng không dừng toàn run nếu config `continue_on_error=true`.
- Với file crash, output `[]` hoặc retry minimal baseline; tốt nhất không để thiếu file.
- Cuối run tạo summary.

### 22.4 Determinism

Set seed:

```python
random.seed(seed)
np.random.seed(seed)
torch.manual_seed(seed)
```

Sort output by `position[0]`, then `position[1]`, then type priority.

### 22.5 Definition of Done

- [ ] Chạy đủ 100 files.
- [ ] Output deterministic.
- [ ] Validate pass.
- [ ] Có report per-file.

---

## 23. Phase 17 - Submission packaging

### 23.1 Make zip

Script:

```bash
python scripts/make_submission_zip.py \
  --pred_dir outputs/predictions/run_001 \
  --zip_path outputs/submission/output.zip
```

Cấu trúc zip:

```text
output.zip
└── output/
    ├── 1.json
    ├── 2.json
    └── ...
```

### 23.2 Pre-submit checklist

- [ ] `output.zip` giải nén đúng có folder `output/`.
- [ ] Đủ 100 JSON.
- [ ] JSON parse được.
- [ ] Không có span mismatch.
- [ ] Không có candidates ở type không yêu cầu.
- [ ] Không thiếu candidates ở `CHẨN_ĐOÁN`/`THUỐC` nếu policy yêu cầu.
- [ ] Không dùng API ngoài.
- [ ] Model self-host nếu dùng LLM ≤9B.

### 23.3 Source packaging cho BTC

Cần chuẩn bị:

```text
source_submission/
├── src/
├── scripts/
├── configs/
├── data/
│   ├── terminologies/
│   ├── dictionaries/
│   └── processed/ optional
├── models/
├── requirements.txt
├── README.md
└── run.sh
```

`README.md` phải có:

```bash
pip install -r requirements.txt
python scripts/build_all_indices.py --config configs/default.yaml
python scripts/run_inference.py --input_dir test/input --output_dir output --config configs/default.yaml
python scripts/make_submission_zip.py --pred_dir output --zip_path output.zip
```

---

## 24. Development milestones

### Milestone 1 - Valid baseline

**Mục tiêu:** end-to-end chạy được, output hợp lệ.

Deliverables:

- Repo skeleton.
- Build ICD/RxNorm indices.
- Preprocess/section/chunk.
- Rule drug/lab/problem extractors.
- Basic assertion.
- Basic sparse linking.
- Merge/validator.
- Inference CLI.

Exit criteria:

- Chạy được 100 files.
- Validate pass.
- Streamlit chưa bắt buộc.

### Milestone 2 - Golden evaluation loop

**Mục tiêu:** đo và debug được trên 20 gold files.

Deliverables:

- Golden data folder.
- Gold validator.
- Evaluator.
- Error reports.
- Baseline score.

Exit criteria:

- Có per-type metrics.
- Có FP/FN/candidate/assertion error files.

### Milestone 3 - Streamlit UI

**Mục tiêu:** review prediction dễ hơn.

Deliverables:

- Streamlit app.
- Highlight spans.
- Prediction/gold diff.
- Entity tables.

Exit criteria:

- Review được từng file golden.
- Không crash với overlap.

### Milestone 4 - Improve extraction

**Mục tiêu:** tăng recall và giảm sai type.

Deliverables:

- Expanded dictionaries.
- Better span boundary.
- Type resolver tuned.
- Optional NER model.

Exit criteria:

- Golden relaxed F1 tăng.
- Type errors giảm.

### Milestone 5 - Improve linking

**Mục tiêu:** tăng candidate score.

Deliverables:

- Better ICD aliases.
- Better RxNorm parser.
- Dense retrieval optional.
- Reranker optional.
- Threshold calibration.

Exit criteria:

- Candidate Jaccard tăng trên golden.
- False candidates giảm.

### Milestone 6 - Final packaging

**Mục tiêu:** sẵn sàng submit và rebuild.

Deliverables:

- output.zip.
- source package.
- README.
- deterministic config.
- validation reports.

Exit criteria:

- Một lệnh tái tạo output.
- Không phụ thuộc đường dẫn local hardcoded.

---

## 25. Testing plan

### 25.1 Unit tests

| Module | Test |
|---|---|
| Offset mapper | raw slice đúng sau normalize/chunk |
| Section detector | detect headings phổ biến |
| Drug extractor | dose/route/freq/brand/typo |
| Lab extractor | test-value pair số và định tính |
| Problem extractor | symptom/disease head |
| Type resolver | symptom vs diagnosis |
| Assertion | negation scope, historical, family |
| ICD linker | common diagnosis mapping |
| RxNorm linker | brand/generic/strength |
| Merge | overlap/dedup/trim |
| Validator | invalid JSON/span/type/candidate |

### 25.2 Integration tests

- Process một file ngắn synthetic.
- Process `1.txt` hoặc representative golden file.
- Run full golden folder.
- Run full 100 input folder.

### 25.3 Regression tests

Sau mỗi lần tune rule, chạy:

```bash
pytest
python scripts/run_eval_golden.py --config configs/default.yaml
```

So sánh với previous run:

- final score không giảm quá threshold.
- candidate score không giảm.
- offset errors = 0.

### 25.4 Manual QA qua Streamlit

Review các file có nhiều lỗi nhất theo report:

1. File có nhiều false negative diagnosis.
2. File có nhiều wrong RxNorm.
3. File có nhiều assertion mismatch.
4. File có nhiều overlap conflicts.

---

## 26. Configuration strategy

Tất cả threshold/rule nên nằm trong config để tune nhanh.

`configs/thresholds.yaml`:

```yaml
span:
  min_dictionary_score: 0.80
  min_fuzzy_score: 0.86
  max_problem_span_length: 120

merge:
  same_type_iou: 0.80
  contained_span_keep_longer: true

assertion:
  isNegated: 0.55
  isHistorical: 0.60
  isFamily: 0.80

icd10:
  max_candidates: 3
  min_score_top1: 0.55
  include_second_if_within: 0.05
  min_score_additional: 0.70

rxnorm:
  max_candidates: 2
  min_score_top1: 0.60
  include_second_if_within: 0.04
```

---

## 27. Error analysis workflow

Daily workflow đề xuất:

```text
1. Run inference on golden
2. Run evaluator
3. Open Streamlit
4. Review top error buckets
5. Add/tune one group of rules
6. Run regression
7. Commit config/code changes
```

Không nên cùng lúc sửa quá nhiều module vì khó biết improvement đến từ đâu.

### 27.1 Error bucket actions

| Error | Action |
|---|---|
| Missing drug | add alias, improve regex/splitter |
| Wrong RxNorm | tune parser strength/form, alias table |
| Missing diagnosis | add diagnosis alias/disease-head, NER data |
| Diagnosis as symptom | adjust type resolver disease-head/ICD-linkability |
| Symptom as diagnosis | blacklist symptom ICD override, lower ICD influence |
| Missing lab | add lab alias/result pattern |
| False negation | tighten scope termination |
| Miss negation | add cue/scope list |
| False historical | add current-event override |
| Miss historical | add temporal/status cue |
| False family | require family member as experiencer, not reporter |
| Boundary too short | span expansion rules |
| Boundary too long | trim modifiers/triggers |

---

## 28. Annotation guideline cho golden dataset

Để golden dataset hữu ích, cần thống nhất annotation.

### 28.1 Span boundary

- Không lấy dấu bullet/numbering.
- Không lấy negation trigger vào span.
- Lấy cụm y khoa đủ nghĩa.
- Với thuốc, nếu liều/route/frequency gắn liền trong cùng cụm, annotate full phrase nếu theo style đề.
- Với lab, tách test name và result value.

### 28.2 Type guideline

- `TRIỆU_CHỨNG`: biểu hiện bệnh nhân cảm nhận/ghi nhận, dấu hiệu lâm sàng dạng symptom/sign.
- `CHẨN_ĐOÁN`: bệnh lý, hội chứng, biến chứng, imaging finding có tính bệnh danh.
- `THUỐC`: tên thuốc/hoạt chất/brand cùng liều nếu có.
- `TÊN_XÉT_NGHIỆM`: tên xét nghiệm/cận lâm sàng.
- `KẾT_QUẢ_XÉT_NGHIỆM`: giá trị số, định tính hoặc mô tả kết quả.

### 28.3 Assertion guideline

- `isNegated`: concept bị phủ định trực tiếp trong ngữ cảnh.
- `isHistorical`: concept thuộc tiền sử, trước đây, thuốc trước nhập viện, bệnh mạn tính/đã từng, tùy mention.
- `isFamily`: concept thuộc người nhà, không phải người cung cấp thông tin.

### 28.4 Candidate guideline

- `CHẨN_ĐOÁN`: annotate ICD code tốt nhất từ `icd10_byt.csv`.
- `THUỐC`: annotate RxCUI từ `RXNCONSO.rff`.
- Nếu không chắc code, ghi vào `notes.md` để review, tránh gold nhiễu.

---

## 29. Performance & resource plan

### 29.1 Baseline runtime

Rule + sparse retrieval nên chạy nhanh cho 100 files.

Target:

- < 1 phút cho 100 files nếu không dùng heavy model.
- < 5 phút nếu dùng NER CPU/GPU nhẹ.
- < 15 phút nếu dùng dense/cross-encoder tùy hardware.

### 29.2 Caching

Cache:

- ICD/RxNorm indices.
- Embeddings.
- Per-file span candidates for debug.
- Linker top-k results if repeated.

### 29.3 Memory

RxNorm có thể lớn. Cần:

- filter relevant TTY/SAB;
- store parquet;
- lazy load columns cần dùng;
- optionally sqlite/duckdb for lookup.

---

## 30. Risk register

| Risk | Impact | Mitigation |
|---|---:|---|
| Offset mismatch | Rất cao | offset validator bắt buộc, raw-preserving design |
| Gold annotation sai offset | Cao | validate gold trước khi evaluate |
| Section hardcode gây sai historical | Cao | mention-level assertion, current-event overrides |
| RxNorm file quá lớn/chậm | Trung bình | filter TTY/SAB, parquet, cached indices |
| ICD alias thiếu | Cao | expand aliases từ golden error analysis |
| Drug brand/generic miss | Cao | drug_aliases.csv, fuzzy splitter |
| False positive diagnosis từ lab/imaging | Cao | lab priority, type resolver, procedure blacklist |
| LLM hallucinate | Cao | chỉ chọn trong candidate pool, optional |
| Overfit 20 golden files | Trung bình | rule generalization, holdout nội bộ nếu đủ |
| Streamlit overlap rendering lỗi | Thấp-Trung bình | separate gold/pred views, simplified combined view |
| BTC rebuild fail | Rất cao | README, deterministic scripts, no local path hardcode |

---

## 31. Final deliverables

### 31.1 Code deliverables

- `src/` modules đầy đủ.
- `scripts/` cho build/run/eval/zip.
- `streamlit_app/` cho validation UI.
- `tests/` unit/integration tests.
- `configs/` threshold/rule/path configs.

### 31.2 Data deliverables

- `icd10_byt.csv`.
- `RXNCONSO.rff`.
- dictionaries bổ sung.
- processed indices hoặc scripts để rebuild.
- golden dataset 20 files và gold annotations.

### 31.3 Model deliverables

Tùy phase:

- Baseline không cần model.
- NER model nếu train.
- Dense/reranker models nếu train.
- Local LLM verifier nếu dùng, đảm bảo ≤9B.

### 31.4 Report deliverables

- Golden evaluation report.
- Validation report.
- Error analysis report.
- Submission checklist.

---

## 32. Command reference

### Build indices

```bash
python scripts/build_all_indices.py --config configs/default.yaml
```

### Validate golden gold files

```bash
python scripts/run_validate.py \
  --input_dir data/golden/input \
  --pred_dir data/golden/gold \
  --report_dir outputs/reports/golden_gold_validation
```

### Run inference on golden

```bash
python scripts/run_inference.py \
  --input_dir data/golden/input \
  --output_dir outputs/predictions/golden_run \
  --config configs/default.yaml
```

### Evaluate golden

```bash
python scripts/run_eval_golden.py \
  --input_dir data/golden/input \
  --gold_dir data/golden/gold \
  --pred_dir outputs/predictions/golden_run \
  --report_dir outputs/reports/golden_eval/golden_run
```

### Open Streamlit UI

```bash
streamlit run streamlit_app/app.py -- \
  --input_dir data/golden/input \
  --gold_dir data/golden/gold \
  --pred_dir outputs/predictions/golden_run \
  --report_dir outputs/reports/golden_eval/golden_run
```

### Run inference on 100 files

```bash
python scripts/run_inference.py \
  --input_dir data/raw/input \
  --output_dir outputs/predictions/final_run \
  --config configs/default.yaml
```

### Validate final output

```bash
python scripts/run_validate.py \
  --input_dir data/raw/input \
  --pred_dir outputs/predictions/final_run \
  --report_dir outputs/reports/final_validation
```

### Make submission zip

```bash
python scripts/make_submission_zip.py \
  --pred_dir outputs/predictions/final_run \
  --zip_path outputs/submission/output.zip
```

---

## 33. Implementation checklist tổng hợp

### 33.1 Foundation

- [ ] Repo skeleton.
- [ ] Config loader.
- [ ] Logging.
- [ ] Data types.
- [ ] Read/write JSON utils.

### 33.2 Terminology

- [ ] Parse `icd10_byt.csv`.
- [ ] Parse `RXNCONSO.rff`.
- [ ] Build alias tables.
- [ ] Build sparse indices.
- [ ] Add manual aliases.

### 33.3 Pipeline modules

- [ ] Preprocessing.
- [ ] Offset mapping.
- [ ] Chunking.
- [ ] Section detection.
- [ ] Drug extractor.
- [ ] Lab/test extractor.
- [ ] Problem extractor.
- [ ] Type resolver.
- [ ] Assertion detector.
- [ ] ICD linker.
- [ ] RxNorm linker.
- [ ] Reranker.
- [ ] Merge.
- [ ] Formatter.
- [ ] Validator.

### 33.4 Evaluation

- [ ] Golden data layout.
- [ ] Gold validation.
- [ ] Local evaluator.
- [ ] Error reports.
- [ ] Threshold calibration.

### 33.5 UI

- [ ] Streamlit file selector.
- [ ] Raw text highlight.
- [ ] Gold/pred toggle.
- [ ] Entity table.
- [ ] Diff table.
- [ ] Debug view.

### 33.6 Models, optional

- [ ] Synthetic generator.
- [ ] NER training.
- [ ] Dense retrieval.
- [ ] Cross-encoder reranker.
- [ ] LLM verifier, optional.

### 33.7 Submission

- [ ] Inference 100 files.
- [ ] Validate output.
- [ ] Make zip.
- [ ] README rebuild.
- [ ] Freeze dependencies.
- [ ] Final report.

---

## 34. Recommended execution order

Nếu cần triển khai nhanh, thứ tự ưu tiên nên là:

```text
1. Build ICD/RxNorm indices
2. Preprocess + offset validator
3. Rule baseline extractors
4. Formatter + validator
5. Golden validator/evaluator
6. Streamlit UI
7. Tune rules/assertions/linking on golden
8. Add NER if baseline recall thấp
9. Add dense/reranker if candidate score thấp
10. Final packaging
```

Lý do:

- Không có terminology index thì không làm được candidate mapping.
- Không có offset validator thì mọi module sau đều có thể sinh output sai format.
- Không có golden evaluator/UI thì tune bằng cảm giác, dễ over/under extract.
- NER/reranker chỉ nên thêm sau khi baseline và error loop đã ổn.

---

## 35. Kết luận

Implementation Plan này bám sát Solution Design nhưng cụ thể hóa thành các phase có thể triển khai. Trọng tâm của hệ thống là:

```text
Rule-first + Retrieval-first + Encoder-assisted + Validator-enforced
```

Các điểm bắt buộc để solution có khả năng cạnh tranh:

1. **Offset tuyệt đối đúng.**
2. **Candidate ICD/RxNorm chính xác và không trả quá nhiều.**
3. **Type resolution tốt giữa `TRIỆU_CHỨNG` và `CHẨN_ĐOÁN`.**
4. **Assertion event-aware, không section-hardcoded.**
5. **Golden dataset 20 files được dùng như vòng feedback chính.**
6. **Streamlit UI giúp review lỗi nhanh, đặc biệt span boundary, assertion và linking.**
7. **Validation và packaging deterministic để tránh mất điểm vì lỗi kỹ thuật.**

Bản v1 nên ưu tiên end-to-end valid baseline + golden evaluation loop. Sau đó mới nâng cấp NER, dense retrieval và reranker để tối ưu điểm.
