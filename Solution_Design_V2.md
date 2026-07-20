# Solution Design V2.2
## GLiNER-centered Hybrid Pipeline cho trích xuất và chuẩn hóa khái niệm y khoa

**Bài toán:** Viettel AI Competition – Clinical Text Entity Extraction & Normalization  
**Ngày cập nhật:** 2026-07-20  
**Trạng thái:** Kiến trúc mục tiêu cho vòng nâng cấp tiếp theo  
**Định dạng nộp:** `output/{id}.json`

---

# 1. Mục đích tài liệu

Tài liệu này mô tả **kiến trúc khái niệm** của hệ thống V2. Nó trả lời các câu hỏi:

1. GLiNER giữ vai trò gì trong hệ thống?
2. Các rule và module V1 được giữ lại để làm gì?
3. Span, type, assertion và candidate code được quyết định như thế nào?
4. Thành phần nào là bắt buộc, thành phần nào chỉ triển khai khi có bằng chứng?
5. Hệ thống được đánh giá và kiểm soát rủi ro ra sao?

Tài liệu này **không phải kế hoạch chỉnh sửa repo theo từng file**. Implementation Plan sẽ chuyển kiến trúc dưới đây thành các phase, task, test và acceptance gate cụ thể.

---

# 2. Nguồn thiết kế và thứ tự ưu tiên

V2 được xây dựng từ ba nguồn:

## 2.1 Training Session – kiến trúc lõi ưu tiên

Training Session cung cấp baseline chuẩn:

```text
GLiNER zero-shot span NER
→ ConText-style assertion rules
→ SapBERT + FAISS semantic linking
→ validation
```

Improved tier bổ sung local LLM reranking trên candidate pool. LLM chỉ được chọn code đã retrieve, không được tự sinh code.

Quyết định nền tảng của V2:

> **GLiNER là backbone phát hiện semantic span của hệ thống.**

Quyết định này được giữ cố định trong V2, không coi GLiNER là một extractor tùy chọn ngang hàng hoàn toàn với các rule.

## 2.2 Solution Design V1 – lớp mở rộng thực dụng

V1 bổ sung các thành phần cần thiết để baseline hoạt động tốt trên dữ liệu thực tế:

- raw-preserving preprocessing và offset mapping;
- section parsing;
- drug parser;
- lab/test-result pairing;
- imaging rules;
- phân biệt triệu chứng và chẩn đoán;
- sparse retrieval và alias dictionaries;
- RxNorm slot constraints;
- merge overlap;
- strict JSON/offset validation;
- deterministic inference và packaging.

Các thành phần này được giữ lại dưới vai trò **precision experts, structural parsers và safety guards**.

## 2.3 Ba lớp của kiến trúc V2

Từ “lõi” trong tài liệu được dùng theo hai phạm vi khác nhau. Để tránh nhầm lẫn, V2 thống nhất ba lớp sau:

| Lớp | Vai trò | Thành phần chính |
|---|---|---|
| **Foundational kernel từ Training Session** | Hạt nhân kiến trúc, xác định vai trò chính của pipeline | GLiNER full pass, ConText, semantic dense retrieval, validation |
| **V1 production guardrail layer** | Bổ sung precision, structure, raw-offset safety và khả năng vận hành | text views, section parser, drug/lab/imaging experts, exact/sparse aliases, constraints, overlap cleanup, packaging |
| **Conditional learned upgrades** | Chỉ được đưa vào production khi qua acceptance gate | focused GLiNER passes, fine-tuning, learned fusion, assertion classifier, cross-encoder hoặc LLM reranker |

Trong mọi configuration mang tên V2, GLiNER vẫn là semantic backbone. Nếu V2 không vượt release gate, V2 không được promote; V1 tiếp tục là production baseline. Điều này không biến GLiNER thành một extractor tùy chọn bên trong V2.

## 2.4 Thực nghiệm hiện tại – căn cứ để hiệu chỉnh

Điểm hoặc benchmark được báo cáo từ các môi trường khác chỉ là tham chiếu. Mọi con số phải được tái tạo bằng:

- cùng input;
- cùng terminology snapshot;
- cùng scorer;
- cùng output convention;
- cùng điều kiện phần cứng và model.

Training repo đi kèm ghi nhận:

- baseline GLiNER + ConText + SapBERT/FAISS: host khoảng `21.8`;
- improved tier có constrained local-LLM reranker: host khoảng `24.5–26`.

Nếu có kết quả khoảng `27` từ buổi training hoặc leaderboard khác, kết quả đó được xem là **reported reference cần tái tạo**, không phải acceptance criterion mặc định.

### Evidence references

| Claim | Nguồn | Config/scorer | Trạng thái |
|---|---|---|---|
| Baseline dùng GLiNER + ConText + SapBERT/FAISS | `training-repo/README.md`, `training-repo/configs/baseline.yaml` | `training-repo/score.py`, baseline config | Source-inspected, chưa tái tạo trong V2 |
| Baseline host khoảng `21.8` | `training-repo/README.md` | Không có run artifact trong tài liệu này | Reported-only |
| Improved host khoảng `24.5–26` | `training-repo/README.md`, `training-repo/configs/improved.yaml` | Baseline + constrained Qwen3-8B reranker | Reported-only |
| GLiNER threshold tham chiếu `0.35` | `training-repo/configs/baseline.yaml` | `urchade/gliner_multi-v2.1` | Initial reference, phải calibration lại |

Khi triển khai, mỗi kết quả tái tạo phải bổ sung commit/config/model/terminology hashes, dataset split, scorer version và run ID.

---

# 3. Bài toán và output contract

## 3.1 Năm loại entity

```text
TRIỆU_CHỨNG
TÊN_XÉT_NGHIỆM
KẾT_QUẢ_XÉT_NGHIỆM
CHẨN_ĐOÁN
THUỐC
```

## 3.2 Assertions

Chỉ áp dụng cho `TRIỆU_CHỨNG`, `CHẨN_ĐOÁN`, `THUỐC`:

```text
isNegated
isHistorical
isFamily
```

## 3.3 Candidate normalization

```text
CHẨN_ĐOÁN → ICD-10
THUỐC     → RxNorm RXCUI
```

## 3.4 Output example

```json
[
  {
    "text": "metoprolol 25mg po bid",
    "type": "THUỐC",
    "assertions": ["isHistorical"],
    "candidates": ["..."],
    "position": [45, 67]
  },
  {
    "text": "khó thở",
    "type": "TRIỆU_CHỨNG",
    "assertions": ["isNegated"],
    "position": [92, 99]
  }
]
```

Output contract V2 chốt như sau:

- top-level luôn là JSON list;
- mọi entity luôn có `text`, `type`, `assertions`, `position`;
- `candidates` bắt buộc với `CHẨN_ĐOÁN` và `THUỐC`, kể cả khi giá trị là `[]`;
- `candidates` không xuất hiện ở ba type còn lại;
- các trường debug như `confidence` và `provenance` chỉ tồn tại nội bộ, không serialize vào submission.

## 3.5 Bất biến quan trọng nhất

Mọi entity cuối phải thỏa:

```python
raw_text[start:end] == entity["text"]
```

Offset sử dụng quy ước `[start, end)`:

- `start` inclusive;
- `end` exclusive.

Normalization chỉ phục vụ matching, retrieval và feature extraction. Không được lấy offset trực tiếp từ text đã bị thay đổi.

---

# 4. Thiết kế theo metric

Điểm cuối:

```text
final_score
= 0.3 × text_score
+ 0.3 × assertions_score
+ 0.4 × candidates_score
```

Trong đó:

- `text_score` dựa trên WER;
- assertion dùng Jaccard;
- candidate dùng weighted Jaccard.

## 4.1 Hệ quả kiến trúc

### Span và type có ảnh hưởng dây chuyền

Nếu miss entity hoặc sai type, hệ thống có thể đồng thời mất:

- text score;
- assertion score;
- candidate score.

GLiNER vì vậy là backbone có ảnh hưởng xuyên pipeline.

### Candidate mapping có trọng số lớn nhất

Candidate chiếm 40%, nhưng chỉ được tính có ý nghĩa khi span và type đã đủ đúng. Linking không thay thế extraction; extraction cũng không làm linking trở nên không quan trọng.

### Precision quan trọng ngang recall

Không được:

- giữ mọi prediction của GLiNER;
- union mọi nguồn một cách ngây thơ;
- trả nhiều ICD/RxNorm code để “phòng hờ”;
- giữ fuzzy match không có bằng chứng hỗ trợ.

### Primary và diagnostic metrics

Metric ra quyết định chính:

```text
official-like end-to-end score
```

Các metric sau dùng để chẩn đoán, không thay thế metric chính:

- exact/relaxed span F1;
- per-type precision/recall/F1;
- type confusion;
- assertion per-label F1;
- candidate Recall@K và top-1 accuracy;
- boundary error;
- entities per note;
- runtime và memory.

---

# 5. Nguyên tắc thiết kế

## 5.1 GLiNER-centered, không GLiNER-only

GLiNER là nguồn proposal semantic chính cho cả năm type, đặc biệt:

- triệu chứng;
- chẩn đoán;
- paraphrase;
- phrase tự nhiên dài;
- mixed Vietnamese/English;
- cách diễn đạt chưa có trong dictionary.

Nhưng GLiNER không thay thế:

- drug formulation parser;
- lab-result parser;
- ICD/RxNorm knowledge base;
- ConText rules;
- raw-offset validation;
- structured candidate constraints.

## 5.2 Rules-as-experts

Rule được sử dụng ở nơi cấu trúc rõ và có precision cao:

- thuốc + hàm lượng + route + frequency;
- tên xét nghiệm + giá trị;
- đơn vị;
- danh sách phủ định;
- heading và section;
- fused drug strings;
- procedure/test distinction;
- abbreviation có nghĩa rõ.

Rule có thể độc lập đề xuất entity structured với bằng chứng rất mạnh. Tuy nhiên, **prediction GLiNER không bắt buộc phải có rule xác nhận**; nếu không, GLiNER không thể tăng recall ngoài V1.

## 5.3 Evidence fusion thay cho naive union

Mỗi prediction được xem là một hypothesis có thể nhận bằng chứng từ nhiều nguồn:

```text
GLiNER confidence
rule structure
dictionary match
section/context
ICD/RxNorm linkability
boundary quality
agreement giữa các pass
```

Fusion phải phụ thuộc vào entity type. Không tồn tại một global priority đúng cho mọi trường hợp.

## 5.4 Section là prior

Section hỗ trợ:

- chunking;
- assertion scope;
- type resolution;
- context features.

Section không tự quyết định assertion. Ví dụ, triệu chứng xuất hiện trước nhập viện vẫn có thể là triệu chứng hiện tại; thuốc được dùng ở cấp cứu không phải historical chỉ vì nằm gần phần diễn biến trước nhập viện.

## 5.5 Retrieval trước, reranking sau

Không model nào được tự sinh ICD/RxNorm code ngoài terminology index.

```text
retrieve candidate pool
→ aggregate theo code
→ apply structured constraints
→ rerank
→ calibrate candidate set
```

## 5.6 Mọi nâng cấp phải có thể tắt

Các module mới cần feature flag. Khi tắt module V2, hệ thống phải tái tạo baseline đã freeze.

---

# 6. Kiến trúc tổng thể

```text
Raw clinical note
        │
        ▼
1. Immutable text views
   raw / normalized / search / no-diacritics
   + offset maps
        │
        ▼
2. Clinical structure parser
   section / line / bullet / sentence / clause
   + token windows with overlap
        │
        ▼
3. Parallel hypothesis generation
   ├── GLiNER semantic backbone
   │   full five-type pass + optional focused passes
   ├── V1 specialized experts
   │   drug / lab / imaging / abbreviation / typo
   └── knowledge evidence
       dictionaries / aliases / cheap ICD-RxNorm probes
                       │
                       ▼
4. Minimal boundary cleanup + evidence fusion
                       │
                       ▼
5. Final span and type resolution
                       │
                       ▼
6. Overlap compatibility selection
   chọn final mention set trước khi chạy downstream đắt tiền
                       │
                       ▼
7. ConText-style assertion reasoning
   + optional wider-context classifier
                       │
                       ▼
8. Hybrid entity linking
   exact + sparse + SapBERT/semantic dense retrieval
                       │
                       ▼
9. Structured filtering + optional reranker
                       │
                       ▼
10. Final cleanup + serialization + validation
                       │
                       ▼
                output/{id}.json
```

---

# 7. Data contracts nội bộ

Data contract dưới đây mô tả thông tin cần có. Implementation có thể mở rộng nhưng không được làm mất các trường cốt lõi.

## 7.1 TextViews

```python
@dataclass
class TextViews:
    raw: str
    normalized: str
    search: str
    no_diacritics: str
    norm_to_raw: list[int]
    search_to_raw: list[int]
    no_diacritics_to_raw: list[int]
```

## 7.2 StructuralUnit

```python
@dataclass
class StructuralUnit:
    text: str
    start: int
    end: int
    section: str | None
    line_id: int | None
    sentence_id: int | None
    clause_id: int | None
    bullet_level: int | None
```

Mọi unit phải thỏa:

```python
raw_text[unit.start:unit.end] == unit.text
```

## 7.3 EntityHypothesis

```python
@dataclass
class EntityHypothesis:
    start: int
    end: int
    text: str
    proposed_type: str

    source: str
    source_pass: str | None
    source_scores: dict[str, float]
    provenance: list[dict]

    section: str | None
    sentence_id: int | None
    clause_id: int | None

    boundary_score: float
    agreement_count: int
    icd_probe_score: float
    rxnorm_probe_score: float
    lab_structure_score: float
    drug_structure_score: float

    features: dict
```

## 7.4 FinalEntity

```python
@dataclass
class FinalEntity:
    start: int
    end: int
    text: str
    type: str
    assertions: list[str]
    candidates: list[str]  # serialize chỉ cho CHẨN_ĐOÁN/THUỐC
    confidence: float
    provenance: dict
```

## 7.5 MappingCandidate

```python
@dataclass
class MappingCandidate:
    code: str
    name: str
    terminology: str
    lexical_score: float
    char_score: float
    dense_score: float
    structure_score: float
    rerank_score: float | None
    final_score: float
    metadata: dict
```

`rerank_score` là `None` khi learned reranker chưa được bật. Raw scores khác scale không được cộng trực tiếp nếu chưa calibration. Trong giai đoạn đầu nên dùng rank, agreement, source reliability hoặc Reciprocal Rank Fusion.

---

# 8. Module 1 – Text views và structure parser

## 8.1 Mục tiêu

- giữ raw text bất biến;
- hỗ trợ accent-insensitive matching;
- cung cấp context có cấu trúc;
- tạo input phù hợp giới hạn token của GLiNER;
- map mọi prediction về raw offset.

## 8.2 Text views

```text
raw             dùng cho output và validation
normalized      Unicode/whitespace/lowercase có map
search          phục vụ retrieval và alias expansion
no_diacritics   phục vụ tiếng Việt bỏ dấu
```

Nếu search view thực hiện expansion không 1-1 như `ecg → điện tâm đồ`, expansion chỉ dùng cho retrieval; không dùng trực tiếp để sinh offset.

## 8.3 Structural hierarchy

```text
document
→ section
→ line/bullet
→ sentence
→ clause
→ token window
```

## 8.4 Chunking cho GLiNER

Checkpoint tham chiếu `urchade/gliner_multi-v2.1` có giới hạn token, vì vậy phải:

- ưu tiên boundary tự nhiên;
- dùng token windows khi unit quá dài;
- có overlap giữa windows;
- deduplicate prediction vùng overlap;
- giữ `window_start` raw để restore offset.

Không chia character ngẫu nhiên nếu còn boundary cấu trúc phù hợp.

## 8.5 Inline/fused headings

Parser phải xử lý:

```text
2. Tiền sử bệnh hiện tạiBệnh nhân nhập viện vì...
```

Heading và content có thể nằm cùng dòng, thiếu dấu hai chấm hoặc bị dính chữ.

---

# 9. Module 2 – GLiNER semantic backbone

## 9.1 Vai trò

GLiNER là nguồn semantic proposal chính. Nó trả về:

- raw-aligned span;
- proposed type;
- confidence;
- pass/label provenance.

GLiNER không trực tiếp gán assertion hoặc ICD/RxNorm code.

## 9.2 Baseline pass bắt buộc

Trước tiên phải tái tạo cấu hình gần Training Session:

```text
model: urchade/gliner_multi-v2.1
mode: zero-shot
labels: descriptive labels
threshold: calibrated, tham chiếu ban đầu 0.35
```

Label map tham chiếu:

```text
symptom                          → TRIỆU_CHỨNG
disease or diagnosis             → CHẨN_ĐOÁN
medication or drug               → THUỐC
medical test or lab name         → TÊN_XÉT_NGHIỆM
test result or measurement value → KẾT_QUẢ_XÉT_NGHIỆM
```

Threshold `0.35` chỉ là điểm khởi đầu từ training baseline, không phải giá trị cuối.

## 9.3 Focused passes – nâng cấp có kiểm soát

Sau khi full pass hoạt động ổn định, có thể thử:

### Problem pass

```text
TRIỆU_CHỨNG
CHẨN_ĐOÁN
```

Mục tiêu: tăng recall và giảm cạnh tranh label ở cặp khó nhất.

### Structured pass

```text
THUỐC
TÊN_XÉT_NGHIỆM
KẾT_QUẢ_XÉT_NGHIỆM
```

Mục tiêu: chạy trên line/bullet ngắn để cải thiện structured spans.

## 9.4 Label experiments

Cần benchmark có kiểm soát:

- descriptive English;
- descriptive Vietnamese;
- bilingual;
- short labels.

Không mặc định label dài hoặc bilingual luôn tốt hơn. Mỗi thay đổi label schema là một experiment riêng.

## 9.5 Precision guard

GLiNER-only hypothesis có thể trở thành entity nếu:

- vượt threshold theo type;
- raw offset hợp lệ;
- span không chứa heading/cue/noise rõ;
- không xung đột với structural evidence mạnh hơn;
- qua length/density guard.

Không yêu cầu rule confirmation cho mọi GLiNER span.

---

# 10. Module 3 – Specialized experts từ V1

## 10.1 Drug expert

Drug name phải được anchor bởi ít nhất một nguồn đáng tin cậy:

- GLiNER drug proposal;
- RxNorm alias;
- brand/generic dictionary;
- high-confidence drug lexicon.

Sau đó parser mở rộng có kiểm soát:

```text
name → strength → unit → form → route → frequency → PRN/once
```

Ví dụ:

```text
metoprolol succinate xl 50 mg po daily
```

Drug expert chịu trách nhiệm chính cho boundary formulation. Frequency được giữ trong text nếu annotation convention yêu cầu nhưng không quyết định RxCUI.

Fused strings như:

```text
doxycyclinebactrim
albuterolipratropium
```

được xử lý bằng alias trie hoặc segmentation. Chỉ emit span có thể align chính xác với raw text.

## 10.2 Lab/test-result expert

Các pattern chính:

```text
<test> là <value>
<test>: <value>
<test> <value>
<test> tăng/giảm
<test> âm tính/dương tính/bình thường
<test> từ <v1> lên <v2>
```

Parser tạo relation nội bộ:

```python
TestResultPair(test_span, result_span, relation_score)
```

Không emit bare numeric result nếu không có test context đủ mạnh.

## 10.3 Imaging expert

Phân biệt:

```text
test name          chụp x-quang ngực
result/finding     tim to
diagnosis finding viêm phổi thùy dưới phải
```

Policy result/diagnosis phải theo annotation contract, không suy đoán tùy từng case.

## 10.4 Symptom/diagnosis precision rules

Rules theo head word là supporting evidence, không phải bộ ngôn ngữ hoàn chỉnh.

Symptom heads ví dụ:

```text
đau, khó thở, ho, sốt, buồn nôn, chóng mặt, mệt mỏi, phù
```

Disease heads ví dụ:

```text
viêm, ung thư, suy, xơ gan, rung nhĩ, nhồi máu, thuyên tắc,
bóc tách, áp xe, hội chứng
```

GLiNER chịu trách nhiệm generalization; rules cung cấp precision anchor.

---

# 11. Module 4 – Boundary cleanup, refinement và evidence fusion

## 11.1 Minimal boundary cleanup – core

Mọi hypothesis phải qua các thao tác deterministic an toàn:

- trim whitespace và punctuation dư;
- trim negation/diagnosis cue khỏi span;
- xác nhận raw slice;
- split test/result khi có cấu trúc chắc;
- mở rộng drug formulation bằng parser có anchor.

Đây là một phần của V2 production architecture, không phải learned upgrade.

## 11.2 Advanced boundary alternatives – conditional

Tạo alternative boundary theo type:

```text
original span
trim cue
trim punctuation
expand drug formulation
expand clinically meaningful modifier
core problem span
split test/result
```

Ví dụ:

```text
không đau ngực                    → đau ngực
gợi ý viêm phổi thùy dưới phải   → viêm phổi thùy dưới phải
troponin là 0.10                  → troponin + 0.10
```

Boundary score có thể dùng:

- GLiNER support;
- exact dictionary alignment;
- head inclusion;
- punctuation/cue penalty;
- meaningful modifier;
- span length;
- annotation convention.

Chỉ bật toàn bộ alternative generation/scoring nếu minimal cleanup chưa đủ và ablation cho thấy có lợi.

## 11.3 Exact fusion

Cùng `(start, end, type)`:

- merge provenance;
- giữ score từng nguồn;
- tăng agreement count;
- chỉ tạo một output entity.

## 11.4 Near-overlap fusion

Các span overlap cao được đưa vào cùng cluster. Resolver chọn boundary cuối thay vì union tất cả.

## 11.5 Type-specific authority

| Entity group | Nguồn chính | Nguồn bổ sung |
|---|---|---|
| Symptom/diagnosis | GLiNER semantic proposal | head rules, dictionary, ICD probe, context |
| Drug | GLiNER discovery + drug anchor | drug parser quyết định formulation boundary |
| Test/result | GLiNER discovery | lab pairing có authority cao cho cấu trúc |
| Imaging | GLiNER + imaging expert | context và annotation policy |

## 11.6 Giai đoạn đầu không cần learned fusion

Fusion ban đầu nên deterministic và giải thích được:

1. merge exact agreement;
2. cluster near-overlap;
3. áp dụng type-specific boundary policy;
4. giữ GLiNER-only span nếu qua precision guard;
5. giữ structured rule-only span nếu evidence rất mạnh;
6. resolver xử lý conflict.

Learned fusion chỉ được thêm nếu deterministic fusion đạt trần rõ ràng.

---

# 12. Module 5 – Type resolution

## 12.1 Mục tiêu

Chọn đúng một type cuối cho mỗi mention, đặc biệt:

```text
TRIỆU_CHỨNG ↔ CHẨN_ĐOÁN
```

## 12.2 Features

```text
GLiNER type scores từ các pass
rule/dictionary evidence
section/clause context
boundary score
agreement count
ICD probe
RxNorm probe
lab pair score
drug parse validity
```

## 12.3 Linkability probe

Probe là feature nhẹ, không phải final linking:

```text
ICD exact/fuzzy top score
RxNorm exact/fuzzy top score
```

ICD linkability cao không được tự động đổi mọi symptom thành diagnosis. Nó chỉ là một bằng chứng trong resolver.

## 12.4 Implementation policy

Thứ tự ưu tiên:

1. deterministic weighted resolver;
2. logistic regression/gradient boosting khi có đủ calibration data;
3. small transformer classifier chỉ khi phương án đơn giản không đủ.

---

# 13. Module 6 – Assertion reasoning

## 13.1 Baseline bắt buộc: ConText-style rules

Assertions được quyết định sau khi span/type đã ổn định.

```text
assertion = cue + scope + structure prior + event override
```

## 13.2 Negation

Cues ví dụ:

```text
không, không có, không thấy, không ghi nhận, chưa phát hiện, phủ nhận
```

Scope chạy theo clause/list và dừng tại:

- contrast cue;
- sentence boundary;
- section boundary;
- subject change rõ ràng.

```text
Không sốt, ớn lạnh, nôn, ho
```

→ tất cả problem mentions trong list nhận `isNegated`.

`bình thường` không phải generic negation cue:

```text
điện tâm đồ bình thường
```

→ `bình thường` chủ yếu là result của test.

## 13.3 Historical

Kết hợp:

- temporal cue;
- section prior;
- medication status;
- current-event override.

Không gán historical chỉ vì sự kiện xảy ra trước thời điểm nhập viện. Nếu triệu chứng vẫn là lý do nhập viện hoặc còn tiếp diễn, nó có thể là current.

## 13.4 Family experiencer

Phải phân biệt:

```text
Vợ nhận thấy bệnh nhân lú lẫn.  → reporter, không isFamily
Vợ có triệu chứng tương tự.     → experiencer, isFamily
```

## 13.5 Internal status tối thiểu

Giai đoạn core chỉ cần:

```text
negation:    affirmed / negated / unknown
temporality: current / historical / unknown
experiencer: patient / family / unknown
```

Các trạng thái rộng hơn như `suspected`, `differential`, `screening_target`, `resolved`, `future` là conditional upgrade khi error analysis chứng minh cần thiết.

## 13.6 Wider-context classifier – conditional

Chỉ thêm khi rules còn lỗi long-range đáng kể. Input có thể gồm:

```text
section
previous line
current clause with mention markers
next line
```

Classifier không được thay rule baseline nếu không tăng end-to-end score.

---

# 14. Module 7 – ICD-10 linking

## 14.1 Knowledge base

Mỗi concept cần:

```text
code
canonical Vietnamese/English name
aliases
no-diacritic aliases
abbreviations
parent/category/leaf metadata
qualifiers
source/version
```

## 14.2 Candidate generation channels

Semantic dense retrieval là thành phần của kiến trúc mục tiêu theo Training Session, không chỉ là một ý tưởng phụ:

```text
exact normalized alias
no-diacritic exact
abbreviation alias
BM25
char n-gram TF-IDF
multilingual SapBERT/semantic dense retrieval
```

Các channel được aggregate theo ICD code trước khi ranking, tránh một code thắng chỉ vì có nhiều alias rows.

### Dense index contract

Mỗi dense index phải lưu hoặc tham chiếu được:

```text
encoder name + revision/hash
pooling và normalization policy
embedding dimension và dtype
concept/alias row ordering
aggregation key = ICD code
terminology/alias snapshot hash
FAISS index type và build parameters
```

Query và corpus embeddings phải dùng cùng encoder/pooling/normalization. Index manifest phải được validate trước inference.

## 14.3 Qualifier-aware filtering

Không xóa các từ phân biệt code như:

```text
cấp / mạn
trái / phải
do rượu
có biến chứng / không biến chứng
không đặc hiệu
stage/type/subtype
```

Relaxed alias có thể giúp recall nhưng conflict qualifier phải bị phạt.

## 14.4 ICD granularity

Category hay leaf phải theo annotation contract. Training reference cho thấy gold thường ưu tiên specific leaf, nhưng convention này cần được kiểm tra trên gold hợp lệ và không hardcode mù quáng.

---

# 15. Module 8 – RxNorm linking

## 15.1 ParsedDrug

```python
@dataclass
class ParsedDrug:
    ingredient_or_brand: str | None
    strength_value: float | None
    strength_unit: str | None
    concentration: str | None
    route: str | None
    frequency: str | None
    dose_form: str | None
    is_combination: bool
```

## 15.2 Candidate generation

```text
exact RxNorm STR
ingredient/brand alias
ingredient + strength
BM25
char n-gram
multilingual biomedical dense retrieval
```

RxNorm dense index dùng cùng manifest contract, nhưng aggregation key là `RXCUI`.

## 15.3 Hard constraints

- ingredient phải compatible nếu parse chắc;
- strength/unit phải compatible;
- unrelated fuzzy drug bị loại;
- combination cần đủ ingredients khi convention yêu cầu;
- frequency không quyết định RxCUI.

## 15.4 TTY policy

```text
name only              → IN/BN tùy annotation convention
name + strength        → SCDC/SCD
name + strength + form → SCD/SBD
```

Training baseline tham chiếu ưu tiên strength-specific `SCD`, nhưng policy cuối phải được calibration theo gold contract.

---

# 16. Module 9 – Reranking và candidate policy

## 16.1 Candidate pool trước, reranker sau

Trước khi train reranker cần đạt candidate recall đủ cao. Reranker không thể chọn đúng code nếu code không nằm trong pool.

## 16.2 Structured reranking

Baseline reranking có thể dùng:

- exact alias boost;
- ingredient/strength match;
- ICD qualifier match;
- parent/category penalty;
- section/context features.

## 16.3 Learned reranker – conditional

Các lựa chọn:

1. cross-encoder riêng cho ICD và RxNorm;
2. local LLM verifier/reranker ≤9B.

LLM/cross-encoder chỉ được chọn từ candidate pool. Không sinh code mới.

Hard negatives:

```text
ICD: sibling code, wrong acuity/laterality/subtype
RxNorm: same ingredient, wrong strength/form/product
```

## 16.4 Candidate count

Default:

```text
top-1
```

Top-2 chỉ khi calibrated expected Jaccard tăng. Không trả top-k lớn mặc định.

---

# 17. Module 10 – Overlap selection và final validation

## 17.1 Overlap policy

| Conflict | Policy |
|---|---|
| Same span + same type | merge evidence |
| Nested drug | ưu tiên full valid formulation |
| Same span symptom/diagnosis | type resolver chọn một |
| Test và result ở spans riêng | giữ cả hai |
| Imaging test và finding ở spans riêng | giữ riêng |
| Same mention từ nhiều source | một output entity |

Giai đoạn đầu dùng cluster-wise deterministic selection. Maximum-weight interval selection chỉ là conditional upgrade.

## 17.2 Validator

Validator phải kiểm tra:

- JSON parseable;
- top-level là list;
- type/assertion hợp lệ;
- field presence đúng schema;
- candidates chỉ ở diagnosis/drug;
- `raw[start:end] == text`;
- không thiếu file;
- không thừa file;
- không duplicate exact ngoài annotation policy.

## 17.3 Final mode

Final inference phải:

- fail fast;
- không silent fallback;
- không tự xuất `[]` khi model crash;
- deterministic;
- kiểm tra model/config/terminology hashes;
- chỉ package khi validation pass.

---

# 18. Chiến lược dữ liệu

## 18.1 Mục tiêu

Dữ liệu bổ sung dùng để:

- fine-tune GLiNER theo năm task labels;
- học boundary và noise thực tế;
- tạo assertion contrast sets;
- train linker/reranker;
- giảm domain gap.

## 18.2 Task-aligned ontology data – nguồn ưu tiên

Sinh label-by-construction từ:

- ICD-10;
- RxNorm;
- symptom lexicon;
- lab/test dictionaries;
- imaging patterns.

Quy trình:

```text
sample concept
→ generate note with entity markers
→ remove markers
→ compute exact offsets
→ validate type/code/span
```

## 18.3 Assertion-targeted contrast sets

```text
Bệnh nhân khó thở.
Bệnh nhân không khó thở.
Bệnh nhân từng bị khó thở.
Mẹ bệnh nhân bị khó thở.
Vợ nhận thấy bệnh nhân khó thở.
```

Mục tiêu là phân biệt cue, scope và experiencer, không chỉ tăng số lượng câu.

## 18.4 Competition-style noise

Inject có kiểm soát:

- bỏ dấu;
- mixed Vietnamese/English;
- dính chữ;
- repeated tokens;
- fused headings;
- malformed bullets;
- typo drug name;
- decimal comma/point;
- thiếu punctuation.

Mỗi transformation phải lưu provenance và offset mới.

## 18.5 External medical NER data

External datasets chỉ được sử dụng sau khi audit:

- license;
- provenance;
- annotation guideline;
- label semantics;
- partial annotation;
- domain mismatch.

`VietBioNER` phù hợp để học biomedical boundaries và diagnostic procedures, nhưng label `Symptom_and_Disease` không được map trực tiếp sang cả symptom hoặc diagnosis.

`ViMedNER` là candidate source, chỉ dùng sau khi xác minh license và label contract.

External data là **auxiliary/source-domain data**, không phải competition gold.

## 18.6 Weak/silver data – conditional

Chỉ nhận silver sample khi có đồng thuận cao giữa các nguồn độc lập. Không coi “model + bản sao của chính model” là hai nguồn độc lập.

LLM silver generation và cross-lingual projection được hoãn đến khi task-aligned synthetic chưa đủ.

## 18.7 Data quality gate

Mọi dataset phải kiểm tra:

- exact span offset;
- valid label;
- overlap policy;
- no marker leakage;
- duplicate/near-duplicate;
- semantic consistency;
- concept/code consistency;
- train/dev leakage;
- source/license manifest.

---

# 19. GLiNER fine-tuning strategy

Fine-tuning chỉ bắt đầu sau khi zero-shot pipeline đã được tái tạo và đánh giá.

## 19.1 Curriculum tối thiểu

```text
Stage 1: task-aligned clean synthetic
Stage 2: competition-style noisy synthetic
Stage 3: verified human development data
Stage 4: mined hard negatives nếu cần
```

External source data và silver data là optional channels, không nằm trên critical path.

## 19.2 Tránh catastrophic forgetting

Không mặc định train tuần tự rồi luôn chọn checkpoint cuối. Cần:

- replay/mix dữ liệu các stage;
- source-aware sampling;
- đánh giá checkpoint sau từng stage;
- giữ checkpoint tốt nhất theo primary metric;
- theo dõi per-type regression.

## 19.3 Calibration

Tune threshold theo:

- entity type;
- GLiNER pass;
- section;
- span length;
- rule support;
- agreement;
- prediction density.

---

# 20. Evaluation governance

## 20.1 Annotation contract phải được chốt trước

Cần quyết định rõ:

- uncertain diagnosis có emit không;
- screening target có phải diagnosis không;
- imaging finding là result, diagnosis hay cả hai;
- chronic disease có historical không;
- home medication đang dùng có historical không;
- drug class có emit không;
- ICD category hay leaf;
- RxNorm IN/SCD/SBD convention;
- result boundary;
- overlap policy;
- representation nội bộ của candidate và serialization contract đã chốt ở Section 3.4.

Các mục chưa chốt ở trên là **annotation-policy decisions**. Chúng phải được ghi thành versioned guideline trước khi tạo split hoặc train model. Riêng JSON field presence không còn là open decision: contract ở Section 3.4 là bắt buộc.

## 20.2 Development, calibration và lockbox

Không dùng cùng một sample để vừa train, tune và báo cáo.

Với dataset nhỏ, không áp dụng máy móc `12/4/4`. Split phải coverage-aware theo:

- năm entity types;
- negated/historical/family;
- linked entities có candidate;
- note length/noise;
- clinical sections.

Khuyến nghị:

- grouped cross-validation cho development;
- calibration set cố định để tune thresholds;
- sealed lockbox chỉ mở tại milestone;
- không ghi lockbox score cho từng run hàng ngày.

Nếu không có `isFamily` hoặc candidate coverage đủ trong gold, không được dùng tập đó để kết luận classifier/reranker đã tốt.

## 20.3 Oracle error attribution

Trước khi đầu tư vào model phức tạp, đo score ceiling của từng nhóm lỗi:

```text
Oracle span:      thay span bằng gold, giữ downstream hiện tại
Oracle type:      sửa type, giữ phần còn lại
Oracle assertion: dùng gold assertions trên matched entities
Oracle candidate: dùng gold candidates trên matched entities
```

Oracle experiments trả lời module nào còn nhiều điểm có thể phục hồi nhất.

## 20.4 Metrics

### Primary

```text
official-like end-to-end score
```

### Extraction diagnostics

```text
exact/relaxed P/R/F1
per-type F1
boundary error
entities per note
```

### Type diagnostics

```text
5×5 confusion matrix
SYM→DX / DX→SYM / TEST→DX / RESULT→DX
```

### Assertion diagnostics

```text
per-label P/R/F1
exact assertion-set accuracy
scope/reporter/temporality errors
```

### Linking diagnostics

```text
Recall@1/5/20
top-1 accuracy
candidate-set Jaccard
no-candidate rate
```

## 20.5 Scorer fidelity

Local scorer phải làm rõ:

- entity matching/order;
- duplicate handling;
- WER aggregation;
- empty assertion/candidate sets;
- candidate weighting;
- wrong-type penalty;
- per-document aggregation.

Không chọn kiến trúc dựa trên một approximate scorer chưa được sanity-check.

---

# 21. Baselines và ablation bắt buộc

## 21.1 Baseline definitions

| ID | System | Mục đích |
|---|---|---|
| A | `V1_FROZEN`: rule + sparse retrieval | Freeze output/config/terminology hiện tại |
| B1 | GLiNER extraction only | Đo chất lượng span/type của backbone |
| B2 | Training baseline: GLiNER + ConText + SapBERT/FAISS | Tái tạo kiến trúc Training Session |
| B3 | Training improved: B2 + constrained reranker | Tái tạo improved tier nếu tài nguyên cho phép |
| C | V1 + GLiNER naive union | Đo complementarity và FP cost |
| D | GLiNER-centered simple fusion | Hệ thống V2 tối thiểu |
| E | GLiNER multi-pass | Đo lợi ích focused passes |
| F | Fine-tuned GLiNER | Đo lợi ích dữ liệu task-aligned |
| G | Advanced boundary refiner | Đo gain ngoài minimal core cleanup |
| H | Assertion classifier | Chỉ khi assertion là bottleneck |
| I | Dense-channel ablation trong hybrid linker | Đo phần đóng góp của semantic dense channel đã thuộc core |
| J | Learned reranker | Chỉ khi candidate recall đủ cao |
| K | Full accepted system | Chỉ gồm module đã qua gate |

## 21.2 Causal experiment rule

Mỗi experiment phải có:

```text
parent config
hypothesis
one primary change
primary metric
diagnostic metrics
minimum useful effect
kill criterion
runtime/memory cost
prediction diff
```

Ví dụ:

```text
A → C: chỉ thêm GLiNER proposals bằng naive union
C → D: chỉ thay union bằng deterministic fusion
D → E: chỉ thêm focused GLiNER pass
D → G: chỉ thêm boundary refinement
```

---

# 22. V2 production architecture và conditional upgrades

## 22.1 Committed production architecture

Đây là foundational kernel của Training Session cộng production guardrail layer từ V1. Các thành phần bắt buộc gồm:

1. immutable raw text và offset mapping;
2. structure-aware chunking;
3. GLiNER semantic backbone;
4. drug/lab/imaging precision experts;
5. deterministic evidence fusion;
6. type resolution;
7. ConText-style assertion;
8. exact/sparse + semantic dense linking;
9. structured candidate filtering;
10. overlap cleanup và strict validation.

## 22.2 Conditional upgrades

| Upgrade | Chỉ thêm khi |
|---|---|
| Multi-pass GLiNER | focused pass tăng end-to-end score sau precision guard |
| Fine-tuned GLiNER | task-aligned data vượt zero-shot và không gây regression lớn |
| Advanced boundary refiner | minimal deterministic cleanup còn nhiều lỗi boundary có tác động tới primary score |
| Wider-context assertion classifier | residual errors chủ yếu là long-range/rule conflict |
| Cross-encoder reranker | gold code thường nằm trong pool nhưng rank sai |
| Local LLM reranker | tăng điểm đủ bù latency/memory/complexity |
| Learned fusion/type resolver | deterministic method đạt trần và có đủ labeled data |
| Advanced interval optimization | simple cluster selection còn overlap errors đáng kể |

## 22.3 Deferred mặc định

- full clinical-status ontology;
- large-scale LLM silver generation;
- cross-lingual projection;
- multi-checkpoint self-consistency;
- agent/LLM end-to-end JSON generation.

---

# 23. Risk register

| Risk | Impact | Mitigation |
|---|---:|---|
| GLiNER over-emission | Rất cao | per-type thresholds, density guard, fusion |
| GLiNER miss structured spans | Cao | drug/lab/imaging experts |
| Sai raw offset | Rất cao | raw map, offset restoration, validator |
| SYM/DX confusion | Rất cao | problem pass, context, type resolver |
| Naive union tăng FP | Rất cao | deterministic evidence fusion |
| Assertion scope sai | Cao | clause/list scope, termination cues |
| Family reporter thành experiencer | Cao | reporter guard, contrast data |
| Candidate không nằm trong pool | Cao | alias coverage, sparse+dense retrieval audit |
| Candidate đúng nhưng rank sai | Cao | structured/learned reranker |
| ICD qualifier bị mất | Cao | preserve qualifier và conflict penalty |
| RxNorm granularity sai | Cao | parsed slots + calibrated TTY policy |
| Synthetic quá sạch | Cao | competition-style noise + real-data validation |
| External label mismatch | Cao | license/label audit, no forced mapping |
| Gold/lockbox leakage | Rất cao | sealed split, manifests, milestone-only lockbox |
| Leaderboard overfit | Cao | submission budget, one-change ledger |
| Rebuild fail | Rất cao | pinned dependencies, hashes, clean offline test |
| Silent model fallback | Rất cao | fail-fast final mode |

---

# 24. Acceptance gates

## 24.1 Technical validity

- 100% JSON parseable;
- đúng file count và folder structure;
- zero offset mismatch;
- zero invalid type/assertion;
- candidates chỉ ở diagnosis/drug;
- deterministic two-run output;
- clean offline rebuild.

## 24.2 GLiNER backbone

- zero-shot baseline chạy được end-to-end;
- per-type threshold được calibration;
- prediction density được kiểm soát;
- combined system tăng primary score so với V1;
- drug/lab precision không regression vượt ngưỡng đã định.

Nếu gate này không đạt, không promote V2; `V1_FROZEN` tiếp tục là production baseline. GLiNER vẫn là backbone trong nhánh V2, không bị đổi thành optional extractor để hợp thức hóa release.

## 24.3 Fusion/type resolution

- fusion tốt hơn naive union;
- duplicate giảm;
- SYM/DX confusion giảm hoặc không tăng khi recall tăng;
- mọi quyết định có provenance để debug.

## 24.4 Assertions

- list negation scope đúng;
- family reporter guard đúng;
- historical không section-hardcoded;
- classifier chỉ được giữ nếu primary score tăng.

## 24.5 Linking

- candidate pool Recall@20 được đo trước reranker;
- semantic dense retrieval core load được offline, manifest/index hash hợp lệ và được đánh giá cùng sparse channels;
- nếu bật learned reranker, nó phải tăng top-1/candidate Jaccard;
- không sinh code ngoài terminology pool;
- candidate count được calibration theo Jaccard.

## 24.6 Data/training

- mọi sample pass offset validator;
- manifest có source/license/hash/seed;
- không lockbox leakage;
- fine-tuned model vượt zero-shot trên development/calibration;
- lockbox chỉ được xem tại milestone đã định.

---

# 25. Conceptual roadmap

## Phase 0 – Measurement and contracts

- freeze `V1_FROZEN`: config, terminology snapshot, predictions, metrics và hashes;
- chốt annotation contract;
- tái tạo official-like scorer;
- tạo coverage-aware evaluation split;
- chạy oracle error attribution.

## Phase 1 – Reproduce Training Session baseline

- GLiNER zero-shot;
- ConText assertions;
- SapBERT/FAISS semantic linking;
- strict validation;
- benchmark runtime/memory/score.

## Phase 2 – V1 experts + GLiNER

- integrate drug/lab/imaging experts;
- compare naive union và simple fusion;
- add minimal boundary cleanup và type resolution;
- freeze minimal GLiNER-centered hybrid.

## Phase 3 – Task-aligned data and fine-tuning

- ontology-first synthetic;
- assertion contrast sets;
- noise adaptation;
- fine-tune GLiNER nếu zero-shot baseline và data quality gates đã đạt;
- hard-negative continuation nếu cần.

## Phase 4 – Conditional assertion/linking upgrades

- assertion classifier nếu oracle/residual errors cho thấy cần;
- candidate pool audit;
- cross-encoder hoặc local-LLM reranker nếu rank là bottleneck.

## Phase 5 – Final hardening

- accepted-module-only full system;
- deterministic clean rebuild;
- final validation;
- package fail-fast.

---

# 26. Review checklist cho Agent và con người

## Architecture

- [ ] GLiNER được dùng như semantic backbone, không phải phần thêm tùy chọn sau cùng.
- [ ] Rule experts không chặn mọi GLiNER-only prediction.
- [ ] Quyền ưu tiên của structural rules được định nghĩa rõ theo type.
- [ ] Assertion và linking tách khỏi span proposal.
- [ ] Foundational kernel, V1 guardrail layer và conditional upgrades được phân biệt.

## Offset/data contract

- [ ] Mọi structural unit và entity map đúng raw text.
- [ ] Token-window overlap không tạo duplicate.
- [ ] Search expansion không được dùng trực tiếp làm raw offset.
- [ ] `no_diacritics_to_raw` được kiểm tra cùng các offset maps khác.
- [ ] Provenance được giữ qua fusion/type/linking.

## Metric/evaluation

- [ ] Primary gate dùng official-like score.
- [ ] Diagnostic metrics không bị dùng thay primary score.
- [ ] Scorer đã sanity-check gold-vs-gold.
- [ ] Có oracle attribution trước nâng cấp lớn.
- [ ] Lockbox không bị xem trong daily experiments.

## Linking

- [ ] Training baseline semantic retrieval đã được tái tạo.
- [ ] Dense index manifest khớp encoder, pooling, terminology snapshot và row ordering.
- [ ] Candidate aggregation theo code, không theo alias row.
- [ ] ICD qualifier và RxNorm slots được giữ.
- [ ] Reranker không hallucinate code.
- [ ] Candidate count tối ưu theo Jaccard.

## Training

- [ ] External dataset đã audit license và label semantics.
- [ ] Synthetic sample có exact offset by construction.
- [ ] Train/dev split không có template/concept near-duplicate.
- [ ] Có replay/mixing để tránh catastrophic forgetting.
- [ ] Model selection dựa trên end-to-end score, không chỉ training loss.

## Submission

- [ ] Final mode fail-fast.
- [ ] Không silent fallback.
- [ ] Config/model/terminology hashes đầy đủ.
- [ ] Hai lần chạy cho output byte-identical.
- [ ] Zip đúng cấu trúc và đủ file.

---

# 27. Kết luận

Kiến trúc V2 được định vị như sau:

```text
Training Session xác định hệ thống lõi:
GLiNER + ConText + semantic linking + validation.

V1 bổ sung các expert và guardrail:
drug/lab parsing, type resolution, sparse retrieval,
structured constraints, overlap cleanup và reproducibility.

V2 kết hợp hai hướng thành:
GLiNER-centered Hybrid Pipeline.
```

Vai trò của từng nhóm thành phần:

```text
GLiNER tìm semantic spans mà rule khó bao phủ.
Rules và parsers làm rõ structured spans.
Evidence fusion hợp nhất bằng chứng mà không làm mất recall của GLiNER.
Type resolver quyết định schema cuối.
ConText reasoning quyết định assertion.
Sparse + semantic retrieval tạo candidate pool.
Structured deterministic ranker chọn code trong core pipeline.
Learned reranker chỉ thay hoặc bổ sung bước này nếu qua acceptance gate.
Validator bảo đảm output hợp lệ và tái tạo được.
```

Đây là thiết kế mục tiêu. Implementation Plan phải triển khai theo thứ tự đo lường được, bắt đầu bằng việc tái tạo Training Session baseline, sau đó mới bổ sung các điểm mạnh của V1 và chỉ giữ các nâng cấp thực sự cải thiện end-to-end score.