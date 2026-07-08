# Solution Design: Hệ thống trích xuất, chuẩn hóa và suy luận ngữ cảnh khái niệm y khoa

**Bài toán:** Viettel AI Race - Clinical Text Entity Extraction & Normalization  
**Phiên bản tài liệu:** v1.0  
**Ngày:** 2026-07-08  
**Định dạng nộp:** `output/{id}.json` cho 100 file đầu vào  

---

## 1. Executive Summary

Bài toán yêu cầu xây dựng hệ thống xử lý văn bản y khoa tự do để phát hiện các khái niệm y tế, phân loại chúng, xác định assertion theo ngữ cảnh và chuẩn hóa một số loại khái niệm sang chuẩn y khoa. Các loại entity chính gồm:

- `TRIỆU_CHỨNG`
- `TÊN_XÉT_NGHIỆM`
- `KẾT_QUẢ_XÉT_NGHIỆM`
- `CHẨN_ĐOÁN`
- `THUỐC`

Trong đó:

- `CHẨN_ĐOÁN` cần được map sang ICD-10.
- `THUỐC` cần được map sang RxNorm.
- `CHẨN_ĐOÁN`, `THUỐC`, `TRIỆU_CHỨNG` cần xác định các assertion gồm `isNegated`, `isFamily`, `isHistorical` nếu có.
- Mọi entity cần trả về đúng `text` và `position` theo offset ký tự trên raw input.

Kết luận thiết kế: **không nên phụ thuộc vào một model duy nhất**. Lời giải nên là một hệ thống **hybrid, nhiều tầng, module hóa**, kết hợp rule, dictionary, NER encoder, retrieval, reranking và post-processing có kiểm soát.

Pipeline tổng quát được đề xuất:

```text
Raw clinical text
→ preprocessing + offset mapping
→ section detection
→ span extraction
→ type resolution
→ assertion detection
→ ICD/RxNorm candidate generation
→ reranking
→ merge overlap
→ JSON + offset validation
```

Triết lý chính:

1. **Rule/dictionary trước để đạt precision và offset tốt.**
2. **NER model để bắt span tự nhiên khó viết rule, đặc biệt `TRIỆU_CHỨNG` và `CHẨN_ĐOÁN`.**
3. **Retrieval + reranking cho ICD/RxNorm, không dùng LLM sinh mã trực tiếp.**
4. **Section-aware nhưng không section-hardcoded.** Section chỉ là prior; assertion phải quyết định ở mention-level.
5. **LLM local ≤9B nếu dùng thì dùng làm verifier/reranker, không làm generator chính.**
6. **Validation bắt buộc.** Offset sai hoặc JSON sai có thể làm mất điểm dù model đúng về mặt ngữ nghĩa.

---

## 2. Problem Understanding

### 2.1 Input

Input là văn bản y khoa tự do, có thể là:

- Ghi chú bác sĩ.
- Bệnh sử.
- Giấy ra viện.
- Kết quả xét nghiệm.
- Chẩn đoán hình ảnh.
- Hồ sơ sức khỏe điện tử.

Văn bản trong `all.txt` cho thấy dữ liệu có format gần giống clinical note, nhưng không hoàn toàn sạch. Các file thường có section như:

- `Tiền sử bệnh`
- `Tiền sử bệnh hiện tại` / `Bệnh sử hiện tại`
- `Triệu chứng hiện tại`
- `Các sự kiện trước khi nhập viện`
- `Đánh giá tại bệnh viện`
- `Kết quả xét nghiệm`
- `Kết quả chẩn đoán hình ảnh`
- `Các thủ thuật đã thực hiện`
- `Các phát hiện chẩn đoán khác`

Tuy nhiên các heading không đồng nhất, có lỗi chính tả, lặp cụm, dính chữ và pha Anh - Việt.

### 2.2 Output

Mỗi file output là một list JSON object:

```json
{
  "text": "metoprolol 25mg po bid",
  "type": "THUỐC",
  "candidates": ["...RxCUI..."],
  "assertions": ["isHistorical"],
  "position": [start, end]
}
```

Quy ước:

- `position[0]` là vị trí bắt đầu, inclusive.
- `position[1]` là vị trí kết thúc, exclusive.
- `raw_text[position[0]:position[1]] == text` phải luôn đúng.
- `candidates` chỉ cần cho `CHẨN_ĐOÁN` và `THUỐC`.
- `assertions` chỉ áp dụng cho `CHẨN_ĐOÁN`, `THUỐC`, `TRIỆU_CHỨNG`.

### 2.3 Metric implication

Metric gồm:

- `text_score`: dựa trên Word Error Rate của trường `text`.
- `assertions_score`: Jaccard similarity trên assertion.
- `candidates_score`: Jaccard similarity trên candidate mapping, có trọng số cao nhất.

Final score:

```text
final_score = 0.3 * text_score + 0.3 * assertions_score + 0.4 * candidates_score
```

Hệ quả:

1. Candidate mapping rất quan trọng vì chiếm 40%.
2. Sai type bị phạt nặng, vì một mention đúng text nhưng sai type bị tính như vừa thiếu gold vừa sinh thêm prediction sai.
3. Không nên trả quá nhiều ICD/RxNorm candidates nếu không chắc, vì Jaccard bị giảm khi union lớn.
4. Offset và span boundary phải chính xác, đặc biệt với các cụm dài như thuốc + liều + route + frequency.

---

## 3. Research Background & Design Rationale

Các hướng nghiên cứu liên quan ủng hộ solution hybrid:

| Nghiên cứu / hệ thống | Ý nghĩa với solution |
|---|---|
| SNOMED CT Entity Linking Challenge | Các solution top dùng dictionary, section-aware matching, encoder và post-processing; không chỉ dựa vào LLM. |
| KIRI solution | Ủng hộ dictionary + section-aware matching. |
| SNOBERT solution | Ủng hộ biomedical encoder + embedding matching. |
| BioSyn 2020 | Ủng hộ candidate generation bằng sparse + dense retrieval. |
| SapBERT 2021 | Ủng hộ semantic embedding cho biomedical synonym/entity linking. |
| ClinLinker 2024 | Ủng hộ pipeline retrieval + cross-encoder reranking. |
| ViHealthBERT / ViMedNER | Ủng hộ sử dụng backbone NER tiếng Việt/y khoa. |
| ConText 2009 | Ủng hộ rule-based assertion detection cho negation, temporality, experiencer. |
| ICD-10 multilingual linking 2025 | Ủng hộ dictionary trước, LLM chỉ xử lý case mơ hồ. |

Tham khảo chính:

- SNOMED CT Entity Linking Challenge: https://www.snomed.org/entity-linking-challenge
- Winning solutions repository: https://github.com/drivendataorg/snomed-ct-entity-linking
- BioSyn: https://aclanthology.org/2020.acl-main.335.pdf
- SapBERT: https://aclanthology.org/2021.naacl-main.334/
- ClinLinker: https://arxiv.org/abs/2404.06367
- ViHealthBERT: https://aclanthology.org/2022.lrec-1.35/
- ViMedNER: https://publications.eai.eu/index.php/inis/article/view/5221
- ConText: https://pubmed.ncbi.nlm.nih.gov/19435614/
- RxNorm overview: https://www.nlm.nih.gov/research/umls/rxnorm/overview.html

---

## 4. Quan sát dữ liệu từ `all.txt`

### 4.1 Đặc điểm tổng quan

`all.txt` gồm 100 bản ghi. Dữ liệu có nhiều nhóm thông tin:

- Bệnh lý mạn tính.
- Thuốc trước nhập viện.
- Lý do nhập viện.
- Triệu chứng hiện tại.
- Diễn biến trước nhập viện.
- Kết quả xét nghiệm.
- Kết quả chẩn đoán hình ảnh.
- Thủ thuật/điều trị.
- Phát hiện chẩn đoán khác.

Dữ liệu không hoàn toàn chuẩn hóa. Có các hiện tượng:

1. **Pha Anh - Việt:** `lower abdominal pain`, `fever`, `nausea`, `diarrhea`, `cbc`, `ct`, `mri`, `ecg`, `po`, `iv`, `bid`, `prn`.
2. **Dính chữ / lỗi gõ:** `cảm giáckhó`, `atenololtrong`, `doxycyclinebactrim`, `bình thườngbình thường`.
3. **Entity lặp lại nhiều lần:** cùng một triệu chứng có thể xuất hiện ở `Lý do nhập viện`, `Triệu chứng hiện tại`, `Đặc điểm triệu chứng`, `Đánh giá`.
4. **Phủ định dày đặc:** `Không`, `không có`, `không ghi nhận`, `không thấy`, `phủ nhận`, `âm tính`, `bình thường`.
5. **Section không đủ để quyết định assertion:** `Các sự kiện trước khi nhập viện` có thể chứa triệu chứng active, thuốc dùng tại nhà, thuốc cấp cứu, kết quả xét nghiệm, chẩn đoán hình ảnh.
6. **Lab/result nhiều format:** `troponin 0.01`, `creatinine 2.0 -> 3.2`, `kali là 6.3`, `bạch cầu 26.7`, `INR 1.7`, `âm tính`, `dương tính`, `bình thường`.
7. **Diagnosis nằm rải rác:** trong bệnh mạn tính, lý do nhập viện, imaging finding, phát hiện chẩn đoán khác.

### 4.2 Case study: `1.txt`

`1.txt` minh họa vì sao section chỉ nên là prior:

- `Thuốc trước khi nhập viện`: `metoprolol 25mg po bid`, `doxycycline`, `atenolol (uống hôm nay)`.
- `Triệu chứng hiện tại`: `đánh trống ngực`, `khó thở`, `cảm giác thắt chặt ngực`, `mệt mỏi`.
- `Các diễn biến trước khi nhập viện`: có cả `Bắt đầu dùng metoprolol`, `Ở nhà bệnh nhân đã sử dụng atenolol`, `aspirin 325mg x 1`, `chụp x-quang ngực`, `phân tích nước tiểu`, `ecg bình thường`.
- `Đánh giá tại bệnh viện`: lại có xét nghiệm, chẩn đoán hình ảnh, điện tâm đồ, monitor holter.

Do đó:

- `Thuốc trước khi nhập viện` thường historical.
- `aspirin 325mg x 1` trong cấp cứu không nên mặc định historical.
- Triệu chứng trước nhập viện nhưng là lý do vào viện vẫn là active/current, không nên tự động historical.
- Test/imaging trong diễn biến trước nhập viện có thể là thông tin chẩn đoán mới, không phải tiền sử.

### 4.3 Edge cases cần xử lý

| Nhóm | Ví dụ | Rủi ro |
|---|---|---|
| Dính chữ | `atenololtrong`, `doxycyclinebactrim`, `cảm giáckhó` | Sai span, miss drug/symptom |
| Pha Anh - Việt | `lower abdominal pain`, `fever`, `diarrhea` | Dictionary tiếng Việt miss |
| Viết tắt | `ct`, `mri`, `ecg`, `ekg`, `ERCP`, `FNA`, `PICC` | Sai type test/procedure |
| Lab số | `kali 6.3`, `troponin 0.01` | Không tách test/result |
| Lab định tính | `âm tính`, `dương tính`, `bình thường` | Không biết là result hay negation |
| Family cue | `vợ`, `con trai`, `cháu gái`, `mẹ` | Dễ gán nhầm `isFamily` cho người cung cấp thông tin |
| Negation scope | `Không sốt, ớn lạnh, nôn, táo bón, ho` | Cần lan phủ định qua list |
| Historical ambiguity | `trước nhập viện`, `gần đây`, `đã từng` | Không phải mọi mention đều historical |
| Procedure noise | `đặt stent`, `nội soi`, `truyền dịch` | Không phải tất cả thủ thuật đều là output entity |

---

## 5. High-level Architecture

```text
+---------------------+
| Raw clinical text   |
+----------+----------+
           |
           v
+------------------------------+
| Preprocessing + offset mapper|
+----------+-------------------+
           |
           v
+------------------------------+
| Section detection             |
+----------+-------------------+
           |
           v
+------------------------------+
| Span extraction               |
| - rule/pattern                |
| - dictionary                  |
| - NER model                   |
| - optional LLM verifier       |
+----------+-------------------+
           |
           v
+------------------------------+
| Type resolution               |
| SYM vs DX vs DRUG vs TEST     |
+----------+-------------------+
           |
           v
+------------------------------+
| Assertion detection           |
| negated / family / historical |
+----------+-------------------+
           |
           v
+------------------------------+
| Candidate generation          |
| DX -> ICD-10                  |
| DRUG -> RxNorm                |
+----------+-------------------+
           |
           v
+------------------------------+
| Reranking                     |
| lexical + dense + cross-enc   |
+----------+-------------------+
           |
           v
+------------------------------+
| Merge overlap + calibration   |
+----------+-------------------+
           |
           v
+------------------------------+
| JSON + offset validation      |
+------------------------------+
```

---

## 6. Data Structures

### 6.1 Chunk object

```python
@dataclass
class Chunk:
    text: str
    start: int
    end: int
    section: str
    subsection: str | None
    line_id: int
    bullet_level: int | None
```

### 6.2 SpanCandidate object

```python
@dataclass
class SpanCandidate:
    text: str
    start: int
    end: int
    raw_type: str | None
    source: str
    score: float
    section: str
    context_left: str
    context_right: str
    features: dict
```

`source` có thể là:

- `drug_rule`
- `lab_rule`
- `dictionary`
- `ner`
- `llm_verifier`
- `imaging_rule`

### 6.3 FinalEntity object

```python
@dataclass
class FinalEntity:
    text: str
    start: int
    end: int
    type: Literal[
        "TRIỆU_CHỨNG",
        "TÊN_XÉT_NGHIỆM",
        "KẾT_QUẢ_XÉT_NGHIỆM",
        "CHẨN_ĐOÁN",
        "THUỐC",
    ]
    assertions: list[str]
    candidates: list[str] | None
    confidence: float
    provenance: dict
```

### 6.4 Candidate mapping object

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
    metadata: dict
```

---

## 7. Module 1: Preprocessing + Offset Mapping

### 7.1 Mục tiêu

- Chuẩn hóa đủ để matching tốt hơn.
- Không phá offset raw text.
- Cho phép chuyển span từ normalized text về raw text.

### 7.2 Representation

Nên giữ ít nhất 3 bản text:

```text
raw_text       : bản gốc, dùng để lấy position và text cuối
norm_text      : lowercase, chuẩn hóa Unicode, khoảng trắng, dấu câu nhẹ
search_text    : bản phục vụ fuzzy search, có thể bỏ dấu, normalize abbreviation
char_map       : map index norm/search về index raw
```

### 7.3 Các bước xử lý

1. Unicode normalize về NFC.
2. Normalize line endings.
3. Tách dòng, bullet, heading.
4. Tạo chunk theo line/section nhưng giữ `start/end` raw.
5. Tạo normalized copy cho matching:
   - lowercase.
   - chuẩn hóa khoảng trắng liên tiếp.
   - chuẩn hóa `xquang`, `x-quang`, `x quang`.
   - chuẩn hóa `ct`, `chụp cắt lớp vi tính`.
   - chuẩn hóa `ecg`, `ekg`, `điện tâm đồ`.
6. Tạo char map.

### 7.4 Không nên làm

- Không replace trực tiếp raw text rồi tính offset trên text mới.
- Không xóa dấu câu trong raw.
- Không tách từ bằng word tokenizer rồi bỏ qua char span.

### 7.5 Output của module

```python
PreprocessOutput(
    raw_text=raw_text,
    chunks=[...],
    norm_text=norm_text,
    search_text=search_text,
    char_map=char_map,
)
```

---

## 8. Module 2: Section Detection

### 8.1 Vai trò

Section detection không quyết định trực tiếp output, nhưng tạo prior cho:

- entity type;
- assertion;
- extraction strategy;
- conflict resolution.

### 8.2 Section labels đề xuất

```text
PAST_HISTORY
PAST_MEDICAL_HISTORY
PRE_ADMISSION_MEDICATION
CURRENT_ILLNESS
ADMISSION_REASON
CURRENT_SYMPTOM
SYMPTOM_CHARACTERISTIC
PRE_ADMISSION_EVENT
HOSPITAL_ASSESSMENT
PHYSICAL_EXAM
LAB_RESULT
IMAGING_RESULT
PROCEDURE
TREATMENT
DIAGNOSIS_FINDING
UNKNOWN
```

### 8.3 Rule nhận diện heading

Ví dụ regex:

```python
SECTION_PATTERNS = {
    "PRE_ADMISSION_MEDICATION": [
        r"thuốc trước khi nhập viện",
        r"thuốc đang dùng trước khi nhập viện",
        r"thuốc dùng trước khi nhập viện",
    ],
    "CURRENT_ILLNESS": [
        r"tiền sử bệnh hiện tại",
        r"bệnh sử hiện tại",
        r"lịch sử bệnh hiện tại",
    ],
    "CURRENT_SYMPTOM": [
        r"triệu chứng hiện tại",
        r"triệu chứng khi nhập viện",
        r"tình trạng lúc vào",
    ],
    "LAB_RESULT": [
        r"kết quả xét nghiệm",
        r"kết quả phòng thí nghiệm",
        r"laboratory",
    ],
    "IMAGING_RESULT": [
        r"kết quả chẩn đoán hình ảnh",
        r"kết quả chụp",
        r"chẩn đoán hình ảnh",
    ],
    "DIAGNOSIS_FINDING": [
        r"các phát hiện chẩn đoán khác",
        r"chẩn đoán",
        r"phát hiện chẩn đoán",
    ],
}
```

### 8.4 Quan trọng: section là prior, không phải hard rule

Ví dụ:

- `PRE_ADMISSION_EVENT` có thể chứa triệu chứng active, thuốc cấp cứu, imaging result.
- `PAST_HISTORY` có thể chứa bệnh mạn tính đang active.
- `TREATMENT` có thể chứa thuốc mới dùng tại viện, không historical.

Do đó module assertion/type resolution phải dùng thêm context quanh mention.

---

## 9. Module 3: Span Extraction

Span extraction nên là union của nhiều extractor:

```text
Span candidates =
    rule/pattern candidates
  ∪ dictionary candidates
  ∪ NER candidates
  ∪ optional LLM verified candidates
```

Sau đó mới type resolution và merge.

---

## 10. Drug Extraction (`THUỐC`)

### 10.1 Mục tiêu

Phát hiện span thuốc đầy đủ nhất có thể, bao gồm:

- tên thuốc;
- liều;
- đơn vị;
- route;
- frequency;
- PRN/once/day;
- dạng phối hợp nếu có.

Ví dụ:

```text
metoprolol 25mg po bid
aspirin 325mg x 1
prednisone 40 mg/ngày
vancomycin 1 gram
albuterolipratropium nebulizer
```

### 10.2 Nguồn phát hiện

1. RxNorm dictionary.
2. Brand/generic synonym table.
3. Medication section.
4. Medication context triggers:
   - `dùng`
   - `được cho`
   - `điều trị bằng`
   - `bắt đầu`
   - `ngừng`
   - `đã ngừng`
   - `đang dùng`
   - `uống`
   - `tiêm tĩnh mạch`
5. Regex drug pattern.

### 10.3 Drug regex sketch

```python
DRUG_PATTERN = r"""
(?P<drug>[A-Za-z][A-Za-z0-9\-]+(?:\s+[A-Za-z][A-Za-z0-9\-]+){0,4})
(?:\s*(?P<strength>\d+(?:[.,]\d+)?(?:-\d+(?:[.,]\d+)?)?)\s*
(?P<unit>mg|mcg|g|gram|ml|mg/ml|%)
)?
(?:\s*(?P<route>po|iv|im|sc|uống|tiêm|truyền))?
(?:\s*(?P<freq>daily|bid|tid|qid|q\d+h|prn|qam|qhs|x\s*1|ngày|lần/ngày))?
"""
```

### 10.4 Drug normalization

```text
raw mention
→ lowercase
→ normalize spacing
→ split dose/unit: 25mg -> 25 mg
→ normalize unit: gram -> g, MG -> mg
→ normalize route: po -> oral, iv -> intravenous
→ normalize brand/generic
→ parse ingredient/strength/form/frequency
```

### 10.5 Brand/generic table cần có

```text
tylenol → acetaminophen
lasix → furosemide
bactrim / cotrimoxazol → sulfamethoxazole + trimethoprim
gleevec → imatinib
eliquis → apixaban
coumadin → warfarin
z-pack → azithromycin
prograf → tacrolimus
cellcept → mycophenolate
seroquel → quetiapine
vicodin → hydrocodone + acetaminophen
mucinex d → guaifenesin + pseudoephedrine
advil → ibuprofen
```

### 10.6 Handling dính chữ / typo

Cần có fuzzy splitter dựa trên dictionary:

```text
doxycyclinebactrim → doxycycline + bactrim
vancozosynbactrim → vancomycin? + zosyn + bactrim
albuterolipratropium → albuterol + ipratropium
atenololtrong → atenolol + trong
```

Chiến lược:

1. Nếu token dài không có trong dictionary, thử longest-prefix match với drug dictionary.
2. Nếu tách được hai drug names liên tiếp, sinh hai spans nếu có thể align offset.
3. Nếu không align chắc, giữ span gốc nhưng map ingredient qua fuzzy.

---

## 11. Lab/Test Extraction (`TÊN_XÉT_NGHIỆM`, `KẾT_QUẢ_XÉT_NGHIỆM`)

### 11.1 Mục tiêu

Tách test name và result thành hai entity riêng nếu format cho phép.

Ví dụ:

```text
troponin 0.01
creatinine 5.7
kali là 6.3
INR dưới ngưỡng điều trị 1.7
bạch cầu 26.7
tổng phân tích nước tiểu bình thường
cấy máu âm tính
```

### 11.2 Test dictionary

Cần dictionary gồm:

```text
WBC / bạch cầu
RBC / hồng cầu
HGB / hemoglobin / huyết sắc tố
HCT / hematocrit
PLT / tiểu cầu
Na / natri
K / kali
Cl / chloride
HCO3 / bicarbonate
BUN / ure
Cr / creatinine
AST / aspartate aminotransferase
ALT / alanine aminotransferase
ALP / phosphatase kiềm
bilirubin toàn phần / tbili
INR
PT / aPTT
troponin
BNP
lactate
glucose / đường huyết
HbA1c
cấy máu
cấy nước tiểu
tổng phân tích nước tiểu / UA
soi tươi ký sinh trùng
```

### 11.3 Result patterns

```python
VALUE_PATTERN = r"""
(?P<value>
    âm\s+tính|dương\s+tính|bình\s+thường|tăng|giảm|cao|thấp|
    \d+(?:[.,]\d+)?(?:\s*->\s*\d+(?:[.,]\d+)?)?|
    \d+\s*[-x×]\s*\d+(?:\s*[-x×]\s*\d+)?
)
(?P<unit>\s*(mg/dl|mmol/l|g/l|%|u/l|ng/ml|mmhg))?
"""
```

### 11.4 Pairing logic

Các pattern chính:

```text
<test> là <result>
<test>: <result>
<test> <result>
<result> <test>
<test> tăng/giảm/cao/thấp
<test> âm tính/dương tính/bình thường
```

Output:

```json
[
  {
    "text": "troponin",
    "type": "TÊN_XÉT_NGHIỆM",
    "assertions": [],
    "position": [a, b]
  },
  {
    "text": "0.01",
    "type": "KẾT_QUẢ_XÉT_NGHIỆM",
    "assertions": [],
    "position": [c, d]
  }
]
```

### 11.5 Imaging as test

Một số imaging/procedure có thể được xem là `TÊN_XÉT_NGHIỆM` nếu đóng vai trò cận lâm sàng có kết quả:

- `chụp x-quang ngực`
- `chụp ct sọ não`
- `mri vùng chậu`
- `siêu âm gan mật`
- `điện tâm đồ`
- `monitor holter`
- `FNA`
- `ERCP` nếu dùng như thăm dò/chẩn đoán

Không nên tự động extract mọi thủ thuật điều trị như `đặt stent`, `truyền dịch`, `phẫu thuật`, trừ khi annotation thực tế yêu cầu.

---

## 12. Problem Mention Extraction: `TRIỆU_CHỨNG` và `CHẨN_ĐOÁN`

### 12.1 Tư duy thiết kế

Không nên tách ngay thành hai extractor hoàn toàn độc lập. Nên có một tầng chung:

```text
clinical problem mention detection
→ type resolution: TRIỆU_CHỨNG vs CHẨN_ĐOÁN
```

Lý do:

- Một số phrase gần biên giới: `tăng kali máu`, `thiếu máu`, `suy hô hấp`, `hạ huyết áp`.
- Nhiều diagnosis xuất hiện dưới dạng finding trong imaging/lab.
- Nhiều symptom có thể có ICD nhưng trong schema vẫn nên là `TRIỆU_CHỨNG` nếu là triệu chứng bệnh nhân than phiền.

### 12.2 Sources

1. Symptom dictionary.
2. Diagnosis dictionary từ ICD-10 alias.
3. NER model.
4. Disease-head pattern.
5. Symptom-head pattern.
6. ICD-linkability score.

### 12.3 Symptom-head patterns

Ưu tiên `TRIỆU_CHỨNG` nếu mention có head:

```text
đau
khó thở
ho
sốt
buồn nôn
nôn
tiêu chảy
táo bón
chóng mặt
mệt mỏi
yếu
ngất
phù
ngứa
chảy máu
đờm
khàn tiếng
khó nuốt
khò khè
vã mồ hôi
ớn lạnh
đau buốt khi đi tiểu
```

Span nên lấy cụm đầy đủ:

- `đau bụng vùng hạ sườn phải`, không chỉ `đau bụng` nếu modifier nằm sát.
- `khó thở khi gắng sức`, không chỉ `khó thở` nếu phrase rõ.
- `ho mạn tính có đờm vàng loãng`, nếu annotation thích phrase dài; có thể tune sau.

### 12.4 Disease-head patterns

Ưu tiên `CHẨN_ĐOÁN` nếu mention có head:

```text
viêm
ung thư
u ác
u tuyến
suy
xơ gan
rung nhĩ
nhồi máu
thuyên tắc
xuất huyết
hẹp
tắc
bóc tách
gãy
áp xe
nhiễm khuẩn
nhiễm trùng
bệnh ...
hội chứng ...
loét
tràn dịch
phình động mạch
```

Ví dụ:

- `viêm túi mật cấp`
- `bóc tách động mạch chủ Stanford loại B`
- `rung nhĩ kèm đáp ứng thất nhanh`
- `ung thư phổi không tế bào nhỏ`
- `xơ gan do rượu`
- `hội chứng não gan`

### 12.5 NER model bắt buộc dùng cho cả diagnosis và symptom

NER không chỉ dùng cho triệu chứng. NER nên sinh span cho cả:

```text
B-SYM, I-SYM
B-DX, I-DX
B-DRUG, I-DRUG
B-TEST, I-TEST
B-RESULT, I-RESULT
O
```

Các backbone có thể thử:

1. `ViHealthBERT` cho tiếng Việt y tế.
2. `XLM-R` hoặc `mDeBERTa` cho dữ liệu code-switch Anh - Việt.
3. PhoBERT baseline nếu cần nhẹ.
4. Ensemble encoder nếu tài nguyên cho phép.

NER output không phải quyết định cuối. Type resolver có thể sửa label nếu:

- ICD linker score rất cao.
- Drug dictionary match rất chắc.
- Lab pattern match rõ.
- Section/context trái với NER label.

---

## 13. Module 4: Type Resolution

### 13.1 Mục tiêu

Chọn type cuối cho mỗi span candidate, đặc biệt khi các extractor bất đồng.

### 13.2 Feature set

```text
span text
normalized span
local sentence
left/right context ±100 chars
section label
source extractors
NER logits
symptom dictionary score
diagnosis dictionary score
drug dictionary score
lab pattern score
ICD top retrieval score
RxNorm top retrieval score
negation/family/historical cues
```

### 13.3 Decision policy

Thứ tự ưu tiên high precision:

1. Nếu match drug dictionary + medication pattern → `THUỐC`.
2. Nếu match lab/test pattern rõ → `TÊN_XÉT_NGHIỆM` hoặc `KẾT_QUẢ_XÉT_NGHIỆM`.
3. Nếu disease-head + ICD score cao → `CHẨN_ĐOÁN`.
4. Nếu symptom-head + symptom score cao → `TRIỆU_CHỨNG`.
5. Nếu NER high confidence và không có rule override → theo NER.
6. Nếu LLM verifier được bật → dùng cho case mơ hồ.

### 13.4 Ví dụ phân biệt

| Mention | Context | Type |
|---|---|---|
| `đau ngực` | `Lý do nhập viện`, bệnh nhân than phiền | `TRIỆU_CHỨNG` |
| `rung nhĩ` | `Phát hiện rung nhĩ...` | `CHẨN_ĐOÁN` |
| `troponin` | `troponin 0.01` | `TÊN_XÉT_NGHIỆM` |
| `0.01` | `troponin 0.01` | `KẾT_QUẢ_XÉT_NGHIỆM` |
| `tăng kali máu` | `Lý do nhập viện: xét nghiệm bất thường - tăng kali máu` | thường `CHẨN_ĐOÁN` |
| `sốt` | `Không sốt` | `TRIỆU_CHỨNG` + `isNegated` |
| `aspirin 325mg x 1` | `Được chỉ định điều trị` | `THUỐC`, không mặc định historical |

---

## 14. Module 5: Assertion Detection

### 14.1 Scope

Assertions chỉ áp dụng cho:

- `CHẨN_ĐOÁN`
- `THUỐC`
- `TRIỆU_CHỨNG`

Không cần assertion cho lab/test/result, trừ khi format output chấp nhận nhưng nên để `[]`.

### 14.2 Triết lý

Dùng ConText-style rule:

```text
assertion = lexical cues + scope + section prior + event status
```

Không dùng section hard rule.

### 14.3 `isNegated`

Cue list:

```text
không
không có
không ghi nhận
không thấy
không phát hiện
chưa phát hiện
phủ nhận
âm tính
bình thường
không có gì đáng chú ý
```

Scope:

- Từ cue đến dấu câu/list boundary.
- Lan qua list được ngăn bằng dấu phẩy nếu vẫn trong cùng phrase phủ định.
- Dừng tại contrast cue: `nhưng`, `tuy nhiên`, `song`, `mặc dù`.

Ví dụ:

```text
Không buồn nôn, hay nôn, đổ mồ hôi
```

→ `buồn nôn`, `nôn`, `đổ mồ hôi` đều `isNegated`.

```text
Không có sốt, ớn lạnh, nôn, táo bón, ho, tiểu khó
```

→ tất cả trong list là `isNegated`.

```text
chụp x-quang ngực không ghi nhận gì bất thường
```

→ không gán `isNegated` cho `chụp x-quang ngực`. Nếu có diagnosis/finding cụ thể bị phủ định thì mới gán.

### 14.4 `isHistorical`

Cue list:

```text
tiền sử
trước đây
đã từng
mạn tính
trước khi nhập viện
thuốc trước khi nhập viện
đang dùng tại nhà
đã sử dụng
đã ngừng
ngừng uống
vừa ngừng
lần nhập viện trước
cách đây vài năm
```

Nhưng cần phân biệt:

| Context | Có nên historical? |
|---|---|
| `Thuốc trước khi nhập viện` | thường có |
| `Các bệnh lý mạn tính` | thường có, nhưng tune theo annotation |
| `đã từng nhập viện vì...` | có |
| `bắt đầu dùng aspirin tại cấp cứu` | không |
| `xuất hiện đau ngực 3 ngày trước và vẫn còn` | không, vì đang là diễn biến hiện tại |
| `triệu chứng cách đây vài năm, đã hết` | có |

Cần một classifier/rule event-aware:

```python
historical_score = (
    section_prior
    + temporal_cue_score
    + status_cue_score
    - active_current_cue_score
    - emergency_treatment_cue_score
)
```

Active/current cues:

```text
hiện tại
khi nhập viện
lúc vào viện
vẫn còn
tiếp tục
ngày hôm nay
sáng nay
đến khoa cấp cứu
được chỉ định điều trị
được cho
```

### 14.5 `isFamily`

Cue list:

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
thành viên trong gia đình
```

Nhưng không được gán chỉ vì có người nhà xuất hiện.

Không gán `isFamily` trong các câu:

```text
Con trai phát hiện bệnh nhân nằm trên sàn.
Vợ nhận thấy bệnh nhân lú lẫn.
Cháu gái hét lên.
```

Chỉ gán khi người nhà là experiencer của disease/symptom:

```text
Vợ có các triệu chứng tương tự.
Nhiều thành viên trong gia đình có ho và chảy nước mũi.
Mẹ bệnh nhân mắc ung thư vú.
Bố bệnh nhân bị đau bụng tương tự.
```

### 14.6 Assertion classifier optional

Có thể train một classifier nhẹ cho từng mention:

Input:

```text
[CLS] section [SEP] left context [MENTION] mention [/MENTION] right context [SEP]
```

Output multi-label:

```text
isNegated, isHistorical, isFamily
```

Rule vẫn là baseline và fallback. Classifier dùng để xử lý case mơ hồ, không thay thế rule hoàn toàn.

---

## 15. Module 6: ICD-10 Candidate Generation cho `CHẨN_ĐOÁN`

### 15.1 Mục tiêu

Với mỗi final entity type `CHẨN_ĐOÁN`, trả về list ICD-10 code phù hợp nhất.

### 15.2 ICD index

Mỗi ICD concept nên được index thành nhiều alias:

```python
ICDConcept(
    code="K21.9",
    official_name_en="Gastro-esophageal reflux disease without esophagitis",
    official_name_vi="Bệnh trào ngược dạ dày thực quản không có viêm thực quản",
    synonyms=[...],
    abbreviations=["GERD"],
    chapter="Diseases of the digestive system",
    parent_code="K21",
    normalized_aliases=[...],
)
```

Nguồn alias:

1. ICD-10 official descriptions.
2. Vietnamese translations nếu có.
3. Synonym tự xây từ dữ liệu/synthetic.
4. Common abbreviations: COPD, GERD, CKD, CAD, DVT, PE, MI, CHF, UTI.
5. Disease dictionary từ all.txt và synthetic data.

### 15.3 Normalization cho diagnosis mention

```text
lowercase
normalize Unicode
remove extra spaces
optional remove accents
standardize abbreviations
standardize anatomy terms
standardize acuity/severity
```

Ví dụ:

```text
bệnh trào ngược dạ dày- thực quản không có viêm thực quản
→ trao nguoc da day thuc quan khong co viem thuc quan
→ GERD without esophagitis
```

### 15.4 Candidate generation tiers

```text
Tier 1: exact normalized alias match
Tier 2: rule/synonym expansion match
Tier 3: BM25 over ICD aliases
Tier 4: char n-gram TF-IDF
Tier 5: dense retrieval using biomedical/multilingual embeddings
Tier 6: optional LLM/verifier for ambiguous cases
```

### 15.5 Dense retrieval

Embedding options:

- SapBERT-style biomedical entity embedding.
- Multilingual sentence embedding if Vietnamese aliases are available.
- Custom contrastive model trained with ICD alias pairs.

Training pairs:

```text
positive: mention ↔ correct ICD alias
negative: mention ↔ sibling ICD codes / hard negatives from BM25
```

### 15.6 Reranker input

```text
Mention: "bóc tách động mạch chủ Stanford loại B"
Context: "... không thấy hình ảnh thuyên tắc mạch phổi. Phát hiện tổn thương bóc tách động mạch chủ Stanford loại B ..."
Candidate ICD: I71.x - Aortic aneurysm and dissection
Question: Is this candidate the best ICD mapping?
```

### 15.7 Candidate count policy

Do Jaccard phạt false positives, candidate count nên calibrated:

```python
if top1_score >= 0.90 and top1_margin >= 0.15:
    return [top1]
elif top1_score >= 0.75 and top2_score >= 0.72 and same_parent:
    return [top1, top2]
else:
    return [top1]  # hoặc [] nếu quá thấp, tùy validation
```

Không nên trả top-5 mặc định.

---

## 16. Module 7: RxNorm Candidate Generation cho `THUỐC`

### 16.1 Mục tiêu

Với mỗi final entity type `THUỐC`, trả về list RxNorm code phù hợp nhất.

### 16.2 RxNorm index

Mỗi RxNorm concept nên có:

```python
RxConcept(
    rxcui="...",
    name="metoprolol 25 MG Oral Tablet",
    tty="SCD",
    ingredient="metoprolol",
    brand=None,
    strength_value=25,
    strength_unit="mg",
    dose_form="oral tablet",
    route="oral",
    aliases=[...],
)
```

Cần phân biệt các level:

- Ingredient-level.
- Clinical drug with strength/form.
- Brand name.
- Branded drug.
- Multi-ingredient combination.

### 16.3 Drug parser

Parser tách mention thành slot:

```python
DrugSlots(
    raw="metoprolol 25mg po bid",
    ingredient="metoprolol",
    brand=None,
    strength_value=25,
    strength_unit="mg",
    dose_form=None,
    route="oral",
    frequency="bid",
    is_combination=False,
)
```

### 16.4 Matching tiers

```text
Tier 1: exact RxNorm alias/name
Tier 2: brand/generic normalization
Tier 3: ingredient + strength
Tier 4: ingredient + form
Tier 5: fuzzy lexical matching
Tier 6: dense retrieval
Tier 7: reranking with constraints
```

### 16.5 Constraint filtering

Ví dụ:

```python
if parsed.ingredient:
    keep candidates with same ingredient/brand synonym
if parsed.strength_value:
    boost same strength
if parsed.route == "oral":
    boost oral forms
if parsed.is_combination:
    require all ingredients if possible
```

### 16.6 Output candidate policy

- Nếu match exact clinical drug: return exact RxCUI.
- Nếu chỉ biết ingredient: return ingredient RxCUI nếu annotation cho phép; nếu không, return best clinical drug only khi strength/form có trong mention.
- Nếu brand ambiguous: map brand to ingredient/clinical drug by context.
- Nếu combination drug split được: có thể output combination RxCUI hoặc ingredient-level tùy RxNorm index và annotation trend.

---

## 17. Module 8: Reranking

### 17.1 Vì sao cần reranking

Candidate generation có recall cao nhưng dễ nhiều false positives. Reranker giúp:

- dùng context;
- phân biệt disease cùng họ;
- phân biệt drug cùng ingredient khác dose/form;
- giảm số candidates sai;
- cải thiện Jaccard candidate score.

### 17.2 Reranker types

1. **Rule-based scorer**
   - exact match boost;
   - slot match boost;
   - section/context boost;
   - parent/child penalty.

2. **Cross-encoder**
   - Input pair: mention + context + candidate name.
   - Output relevance score.

3. **Local LLM verifier**
   - Chỉ dùng cho top-k mơ hồ.
   - Không sinh candidate mới.
   - Không sinh offset.

### 17.3 Reranker formula

```python
final_score = (
    0.35 * lexical_score
  + 0.25 * dense_score
  + 0.25 * cross_encoder_score
  + 0.10 * slot_match_score
  + 0.05 * context_score
)
```

Weights cần tune trên validation synthetic/weak-label set.

---

## 18. Module 9: Merge Overlap

### 18.1 Vấn đề

Nhiều extractor sinh span overlap:

- `đau bụng` vs `đau bụng vùng hạ sườn phải`
- `khó thở` vs `khó thở khi gắng sức`
- `metoprolol` vs `metoprolol 25mg po bid`
- `creatinine` vs `creatinine 5.7`

### 18.2 Priority source

```text
exact rule span > drug/lab pattern > dictionary > NER > LLM proposed
```

### 18.3 Merge rules

1. Same type, high overlap:
   - chọn span dài hơn nếu span dài là phrase hợp lệ.
   - chọn confidence cao hơn nếu span dài chứa noise.

2. Drug:
   - ưu tiên span gồm drug + dose + route/frequency.
   - không lấy chỉ ingredient nếu dose nằm sát sau.

3. Lab:
   - tách test name và result.
   - không merge `troponin 0.01` thành một entity nếu output cần hai entity.

4. Symptom:
   - lấy modifier clinically meaningful: location, trigger, duration nếu sát.
   - không lấy negation trigger vào text.

5. Diagnosis:
   - lấy disease phrase đầy đủ gồm anatomy/acuity/etiology nếu sát.
   - tránh lấy cả câu dài có `cho thấy`, `gợi ý`, `lo ngại`.

6. Different type overlap:
   - Drug vs symptom: thường giữ drug nếu dictionary exact.
   - Test vs diagnosis: có thể giữ cả nếu spans khác nhau.
   - Symptom vs diagnosis: dùng type resolver.

### 18.4 Duplicate policy

- Same `text`, `type`, `position`: remove duplicate.
- Same `text`, `type`, different `position`: keep, vì task là mention-level.
- Same concept, different mention: keep nếu có offset khác.

---

## 19. Module 10: JSON + Offset Validation

### 19.1 Validator bắt buộc

```python
def validate_entity(entity, raw_text):
    start, end = entity["position"]
    assert isinstance(start, int) and isinstance(end, int)
    assert 0 <= start < end <= len(raw_text)
    assert raw_text[start:end] == entity["text"]
    assert entity["type"] in VALID_TYPES
    assert set(entity.get("assertions", [])) <= VALID_ASSERTIONS

    if entity["type"] in {"CHẨN_ĐOÁN", "THUỐC"}:
        assert "candidates" in entity
        assert isinstance(entity["candidates"], list)
    else:
        # candidates nên absent hoặc []
        pass
```

### 19.2 File-level validation

- Có đủ 100 JSON files.
- Tên file đúng `1.json` ... `100.json`.
- Mỗi file là list.
- Không có NaN/null sai schema.
- Không có duplicate exact.
- Sort theo `position[0]` để dễ debug.

### 19.3 Logging

Validator nên xuất:

```text
span_mismatch.log
invalid_type.log
candidate_missing.log
duplicate.log
overlap_conflict.log
empty_output.log
```

---

## 20. Training & Data Augmentation

### 20.1 Vì sao cần synthetic data

Đề yêu cầu dùng giải pháp ngoài lời giải chính để tạo thêm dữ liệu huấn luyện. Không nên hard-code output 100 public test vì BTC có thể rebuild trên private test/source code.

### 20.2 Synthetic generation sources

1. ICD-10 dictionary.
2. RxNorm dictionary.
3. Symptom dictionary.
4. Lab/test templates.
5. Section templates mô phỏng clinical note.
6. Assertion templates.
7. Noise injection.

### 20.3 Synthetic templates

#### Diagnosis

```text
Bệnh lý mạn tính: {diagnosis}.
Chẩn đoán hình ảnh cho thấy {diagnosis}.
Các phát hiện chẩn đoán khác: lo ngại {diagnosis}.
Bệnh nhân có tiền sử {diagnosis}.
Không ghi nhận {diagnosis}.
```

#### Symptom

```text
Lý do nhập viện: {symptom}.
Triệu chứng hiện tại: {symptom}.
Không có {symptom}.
Bệnh nhân xuất hiện {symptom} từ {time}.
```

#### Drug

```text
Thuốc trước khi nhập viện: {drug} {dose}{unit} po bid.
Được cho {drug} {dose}{unit} iv.
Đã ngừng {drug} cách nhập viện {n} ngày.
```

#### Lab

```text
Kết quả xét nghiệm: {test} là {value}.
{test}: {value} {unit}.
{test} âm tính.
{test} dương tính.
```

#### Family

```text
Mẹ bệnh nhân có {diagnosis}.
Vợ có các triệu chứng tương tự như {symptom}.
Nhiều thành viên trong gia đình có {symptom}.
```

### 20.4 Noise injection

- Bỏ dấu tiếng Việt.
- Random capitalization.
- Dính chữ.
- Lặp từ.
- Pha Anh - Việt.
- Viết tắt.
- Sai chính tả nhẹ.
- Dấu câu thiếu.

Ví dụ:

```text
khó thở → kho tho
bình thường → bình thườngbình thường
metoprolol trong ngày → metoprololtrong ngày
chụp x-quang → chup xquang
```

### 20.5 Weak labeling từ rules

Rules high-precision có thể tạo pseudo labels:

- Drug labels từ RxNorm dictionary.
- Lab labels từ regex.
- Negation labels từ ConText rules.
- Diagnosis labels từ ICD exact/synonym match.

Dùng pseudo labels để fine-tune NER, nhưng cần kiểm soát noise.

---

## 21. Model Design

### 21.1 NER model

Recommended baseline:

```text
Backbone: ViHealthBERT, ViMedNER hoặc XLM-R
Task: token classification BIO
Labels: SYM, DX, DRUG, TEST, RESULT
Training: synthetic + weak labels + manual mini-dev
```

### 21.2 Entity linker embeddings

ICD/RxNorm retriever:

```text
Sparse: BM25 + char n-gram TF-IDF
Dense: SapBERT-style / multilingual sentence embedding
Reranker: cross-encoder
```

### 21.3 Local LLM role

Nếu dùng model self-host ≤9B:

Nên dùng cho:

- type verifier;
- ICD top-k reranking;
- ambiguous assertion review;
- JSON sanity explanation trong debug.

Không nên dùng cho:

- sinh offset;
- sinh toàn bộ JSON end-to-end;
- tự bịa ICD/RxNorm code ngoài candidate pool.

Prompt nên ràng buộc:

```text
You are given a mention, its context, and candidate codes.
Choose the best candidate IDs only from the provided list.
Do not invent codes.
Return JSON with selected_ids and confidence.
```

---

## 22. Scoring-driven Optimization

### 22.1 Ưu tiên theo metric

```text
Priority 1: candidate mapping quality
Priority 2: type correctness
Priority 3: span boundary correctness
Priority 4: assertion precision/recall
Priority 5: output format robustness
```

### 22.2 Candidate threshold calibration

Không nên luôn return nhiều candidates.

Gợi ý:

```python
if confidence_high:
    return [top1]
elif sibling_codes_close and annotation_often_multi_code:
    return [top1, top2]
else:
    return [top1]
```

### 22.3 Assertion threshold

- `isNegated`: high precision, vì gán sai phủ định rất hại.
- `isFamily`: very high precision, vì family cue dễ nhiễu.
- `isHistorical`: moderate recall, nhưng cần event-aware.

### 22.4 Span strategy

- Drug: lấy span dài gồm dose/frequency.
- Symptom: lấy clinical phrase đầy đủ nhưng không lấy cả câu.
- Diagnosis: lấy disease phrase đầy đủ nhưng không lấy trigger phrase.
- Test/result: tách riêng.

---

## 23. Inference Pseudocode

```python
def infer_one(raw_text: str, resources: Resources) -> list[dict]:
    pp = preprocess(raw_text)
    chunks = detect_sections(pp)

    span_candidates = []
    span_candidates += extract_drugs(chunks, resources.rxnorm_dict)
    span_candidates += extract_labs(chunks, resources.lab_dict)
    span_candidates += extract_tests_imaging(chunks, resources.test_dict)
    span_candidates += extract_problem_dictionary(chunks, resources.problem_dict)
    span_candidates += run_ner(chunks, resources.ner_model)

    span_candidates = align_and_repair_offsets(span_candidates, raw_text)
    span_candidates = remove_invalid_spans(span_candidates, raw_text)

    typed_entities = []
    for span in span_candidates:
        entity_type = resolve_type(span, resources)
        typed_entities.append(to_entity(span, entity_type))

    typed_entities = merge_overlaps(typed_entities)

    for ent in typed_entities:
        if ent.type in {"CHẨN_ĐOÁN", "THUỐC", "TRIỆU_CHỨNG"}:
            ent.assertions = detect_assertions(ent, raw_text, chunks)
        else:
            ent.assertions = []

        if ent.type == "CHẨN_ĐOÁN":
            cands = generate_icd_candidates(ent, raw_text, resources.icd_index)
            ent.candidates = rerank_icd(ent, cands, raw_text)
        elif ent.type == "THUỐC":
            cands = generate_rxnorm_candidates(ent, raw_text, resources.rxnorm_index)
            ent.candidates = rerank_rxnorm(ent, cands, raw_text)

    typed_entities = final_cleanup(typed_entities)
    validate_output(typed_entities, raw_text)

    return [entity_to_json(e) for e in typed_entities]
```

---

## 24. Repo Structure đề xuất

```text
solution/
  README.md
  requirements.txt
  configs/
    thresholds.yaml
    section_patterns.yaml
    assertion_patterns.yaml
    extractor_weights.yaml
  data/
    icd10/
      icd10_codes.csv
      icd10_aliases_vi_en.csv
    rxnorm/
      rxnorm_concepts.csv
      rxnorm_aliases.csv
    dictionaries/
      symptoms_vi_en.csv
      labs.csv
      imaging_tests.csv
      drug_brand_generic.csv
      abbreviations.csv
    synthetic/
      train.jsonl
      dev.jsonl
  models/
    ner/
    reranker_icd/
    reranker_rxnorm/
  src/
    preprocess.py
    sectionizer.py
    schema.py
    extractors/
      drug_extractor.py
      lab_extractor.py
      test_extractor.py
      problem_extractor.py
      ner_extractor.py
    assertion/
      context_rules.py
      assertion_classifier.py
    linking/
      build_icd_index.py
      build_rxnorm_index.py
      sparse_retriever.py
      dense_retriever.py
      reranker.py
      icd_linker.py
      rxnorm_linker.py
    resolution/
      type_resolver.py
      merge.py
    validation/
      validate_json.py
      validate_offsets.py
    infer.py
  scripts/
    build_indices.sh
    generate_synthetic.py
    train_ner.sh
    train_reranker.sh
    run_inference.sh
    make_submission.sh
  tests/
    test_offsets.py
    test_drug_extractor.py
    test_lab_extractor.py
    test_assertion_scope.py
    test_linking.py
```

---

## 25. Implementation Plan

### Phase 1: Strong rule baseline

Mục tiêu: có pipeline end-to-end, output valid.

- Preprocessing + offset mapping.
- Section detection.
- Drug regex + drug dictionary.
- Lab/test regex.
- Symptom/diagnosis dictionary.
- Assertion ConText rules.
- ICD/RxNorm sparse retrieval.
- JSON validator.

### Phase 2: NER + weak/synthetic training

Mục tiêu: tăng recall span.

- Generate synthetic BIO data.
- Weak label all.txt-like data bằng rules.
- Train ViHealthBERT/XLM-R NER.
- Add type resolver using NER logits + rule scores.

### Phase 3: Dense retrieval + reranking

Mục tiêu: tăng candidate score.

- Build ICD/RxNorm aliases.
- Train/finetune dense bi-encoder.
- Train cross-encoder reranker with hard negatives.
- Calibrate candidate count threshold.

### Phase 4: Error analysis + calibration

Mục tiêu: giảm false positive và sai type.

- Analyze overlap conflicts.
- Tune thresholds by entity type.
- Tune assertion rules.
- Add case-specific abbreviation/synonym dictionary.

### Phase 5: Packaging

Mục tiêu: nộp được source code tái dựng.

- Freeze model weights.
- Freeze dictionaries.
- Write README install/run.
- Add deterministic seeds.
- Add one-command inference.
- Validate output zip.

---

## 26. Review Checklist sau khi viết/triển khai

### 26.1 Architecture review

- [x] Pipeline có đủ các bước: preprocessing, section, extraction, assertion, linking, reranking, merge, validation.
- [x] Không phụ thuộc vào một model duy nhất.
- [x] NER được dùng cho cả `CHẨN_ĐOÁN` và `TRIỆU_CHỨNG`.
- [x] ICD/RxNorm mapping được tách khỏi span extraction.
- [x] Section chỉ là prior, không phải hard rule.
- [x] LLM nếu dùng thì chỉ làm verifier/reranker.

### 26.2 Data/offset review

- [ ] `raw_text[start:end] == text` cho 100% output.
- [ ] Không normalize phá offset.
- [ ] Có test cho dính chữ và lỗi Unicode.
- [ ] Có log span mismatch.

### 26.3 Entity extraction review

- [ ] Drug extractor bắt được tên + dose + route + frequency.
- [ ] Lab extractor tách được test/result.
- [ ] Diagnosis extractor bắt được imaging findings.
- [ ] Symptom extractor bắt được phrase có modifier.
- [ ] Không extract quá nhiều procedure điều trị không thuộc schema.

### 26.4 Type resolution review

- [ ] Có cơ chế phân biệt symptom vs diagnosis.
- [ ] ICD-linkability score không override mọi symptom.
- [ ] Drug dictionary có priority cao.
- [ ] Lab pattern có priority cao.
- [ ] Sai type được log để phân tích.

### 26.5 Assertion review

- [ ] Negation có scope, không chỉ cue matching.
- [ ] `isHistorical` event-aware, không section-hardcoded.
- [ ] `isFamily` không gán nhầm cho người cung cấp thông tin.
- [ ] Assertion chỉ áp dụng cho `CHẨN_ĐOÁN`, `THUỐC`, `TRIỆU_CHỨNG`.

### 26.6 Linking review

- [ ] ICD index có alias tiếng Việt + tiếng Anh + abbreviation.
- [ ] RxNorm index có brand/generic/ingredient/strength.
- [ ] Candidate count được threshold, không top-k bừa.
- [ ] Reranker không sinh code ngoài candidate pool.
- [ ] Có hard negatives cho sibling ICD/drug gần nhau.

### 26.7 Submission review

- [ ] Đủ `1.json` ... `100.json`.
- [ ] Output zip đúng cấu trúc.
- [ ] README tái dựng rõ.
- [ ] Không dùng API ngoài nếu bị cấm.
- [ ] Model self-host ≤9B nếu dùng LLM/agent.

---

## 27. Key Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---:|---|
| Sai offset do normalize | Rất cao | Raw-preserving offset mapper + validator |
| Sai type symptom/diagnosis | Rất cao | Type resolver + ICD-linkability + NER logits |
| Candidate ICD/RxNorm false positive | Cao | Reranking + candidate threshold |
| Gán nhầm historical theo section | Cao | Mention-level event-aware assertion |
| Gán nhầm family | Trung bình-cao | Experiencer rules, high precision threshold |
| Miss drug do brand/typo | Cao | Brand-generic table + fuzzy splitter |
| Lab result bị gán diagnosis | Trung bình | Lab parser priority cao |
| Procedure noise | Trung bình | Procedure whitelist/blacklist |
| LLM hallucination | Cao | LLM chỉ chọn trong candidate pool |
| Private test khác public | Cao | Synthetic + dictionary generalization, không hard-code |

---

## 28. Final Recommendation

Solution nên được triển khai theo hướng:

```text
Rule-first + Retrieval-first + Encoder-assisted + Validator-enforced
```

Cụ thể:

1. **Preprocessing/offset** là nền móng, phải làm chắc.
2. **Section detection** giúp định hướng nhưng không quyết định cứng assertion.
3. **Span extraction** là hợp nhất của rule, dictionary và NER.
4. **Type resolution** là module riêng, đặc biệt cho `TRIỆU_CHỨNG` vs `CHẨN_ĐOÁN`.
5. **Assertion detection** dùng ConText-style rules, mention-level, event-aware.
6. **ICD/RxNorm linking** dùng sparse + dense retrieval, sau đó reranking.
7. **Merge overlap** cần entity-type-specific policy.
8. **JSON/offset validation** là bước bắt buộc trước khi tạo submission.

Thiết kế này phù hợp với đặc điểm dữ liệu quan sát được: clinical note dài/ngắn không đều, nhiều section, nhiều phủ định, nhiều thuốc/lab/imaging, nhiều lỗi format, nhiều pha Anh - Việt và candidate mapping có trọng số cao nhất trong metric.
