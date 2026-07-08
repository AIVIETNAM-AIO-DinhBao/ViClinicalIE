# ViClinicalIE

Repo này chứa pipeline rule-based/deterministic cho bài toán trích xuất thông tin lâm sàng tiếng Việt. Baseline hiện tại đọc raw note từ `input/`, parse section/line, extract span candidates, gán assertion, resolve overlap, xuất JSON submission V0, và có web Streamlit nhỏ để verify output.

## Cấu trúc repo

- `ABOUT.md`: mô tả bài toán, schema output, metric và ví dụ từ đề.
- `solution_DESIGN.md`: thiết kế hệ thống, schema trung gian, rule, rủi ro và hướng cải thiện.
- `implementation_PLAN.md`: kế hoạch triển khai theo ngày.
- `all.txt`: bản gộp 100 hồ sơ input để đọc/tra cứu nhanh.
- `input/`: raw clinical notes, đặt tên `{file_id}.txt`.
- `configs/`: cấu hình phục vụ parser/pipeline nếu cần mở rộng.
- `data_resources/`: dictionary seed dùng cho rule extraction.
- `src/`: mã nguồn chính của pipeline.
  - `io_utils.py`: load input và helper I/O cơ bản.
  - `normalization.py`, `offset_mapper.py`: normalize text và recover raw offset.
  - `section_parser.py`: parse section, subsection, line.
  - `rule_extractors.py`: extract span V0 cho lab, thuốc, chẩn đoán, triệu chứng.
  - `assertion.py`: rule V0 cho `isNegated`, `isHistorical`, `isFamily`.
  - `merge.py`: dedupe và resolve overlap.
  - `output_writer.py`, `validator.py`: ghi JSON submission và validate artifact.
- `scripts/`: script chạy pipeline và build artifacts.
- `tests/`: test nhẹ, chạy trực tiếp bằng Python.
- `analysis/`: artifacts phân tích sinh ra từ pipeline.
- `outputs/`: output JSON/zip sinh ra để nộp hoặc review.
- `reports/`: báo cáo validation/error analysis sinh ra từ pipeline.
- `verify_app/`: web Streamlit để xem raw text và output JSON cạnh nhau.

## Chạy pipeline

Build span candidates ngày 09:

```powershell
python scripts\build_span_candidates.py
```

Build full V0 output ngày 10:

```powershell
python scripts\build_v0_outputs.py
```

Artifacts V0 chính:

- `analysis/span_candidates_v0.jsonl`
- `analysis/span_candidates_v0_asserted.jsonl`
- `analysis/span_candidates_v0_merged.jsonl`
- `outputs/v0/output/{file_id}.json`
- `outputs/v0/output.zip`
- `reports/validation_v0.md`

Các artifacts này là generated files. `.gitignore` đã cấu hình để bỏ qua các file sinh mới; nếu artifact nào đã được Git track từ trước thì cần bỏ track riêng bằng `git rm --cached` khi muốn.

## Build silver test set (offline eval)

Script `scripts/build_silver_test.py` gọi 1 LLM endpoint theo chuẩn OpenAI-compatible cho mỗi file trong `input/` và ghi ra silver JSON theo đúng schema submission (`text`, `position`, `type`, `assertions`, `candidates`). Chỉ dùng stdlib, không cần cài thêm dependency.

Chạy nhanh với API key + base URL truyền trực tiếp:

```powershell
python scripts\build_silver_test.py `
    --base-url https://api.openai.com/v1 `
    --api-key sk-... `
    --model gpt-4o-mini
```

Hoặc dùng env var:

```powershell
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:OPENAI_API_KEY  = "sk-..."
$env:OPENAI_MODEL    = "gpt-4o-mini"
python scripts\build_silver_test.py
```

Các cờ hữu ích:

- `--input-dir input` (mặc định).
- `--output-dir silver_test\output` (mặc định).
- `--limit 5` để smoke-test trên 5 file đầu.
- `--only 1,2,7` để chạy 1 số file cụ thể.
- `--concurrency 4` số request song song.
- `--overwrite` để ép sinh lại các file JSON đã tồn tại.

Artifacts:

- `silver_test/output/{file_id}.json`: silver labels từng file.
- `silver_test/silver_manifest.json`: log số entity, số span đã fix offset, số span bị drop, và các file lỗi.

Script tự sửa lại `position` nếu LLM trả offset sai (miễn là chuỗi `text` xuất hiện trong input), và drop các span không tìm thấy trong input. Với `TÊN_XÉT_NGHIỆM` / `KẾT_QUẢ_XÉT_NGHIỆM`, `assertions` và `candidates` luôn bị bỏ để đúng schema.

## Chạy test

```powershell
python tests\test_offset.py
python tests\test_section_parser.py
python tests\test_rule_extractors.py
python tests\test_assertion_merge_output.py
```

## Chạy web verify output

Cài dependency cho app:

```powershell
python -m pip install -r verify_app\requirements.txt
```

Chạy Streamlit:

```powershell
python -m streamlit run verify_app\app.py
```

Mở URL local mà Streamlit in ra, thường là:

```text
http://localhost:8501
```

Trong app có thể chọn:

- `input_dir`, mặc định là `input/`.
- `output_dir`, mặc định auto-detect `outputs/v0/output/`.
- `file_id` cần review.
- filter theo entity type/assertion.

App sẽ highlight entity trực tiếp trên raw text, hiển thị JSON/table bên cạnh, và báo lỗi schema, offset mismatch hoặc overlap nếu có.
