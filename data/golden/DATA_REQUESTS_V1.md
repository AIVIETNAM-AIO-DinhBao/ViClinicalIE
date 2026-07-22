# NER-2 Residual Data Requests V1

Generated only from the frozen selected NER-2 extraction error review. Preserve the shared V2 annotation contract and raw offsets.

## Shared gate

- Use `data/golden/ANNOTATION_GUIDELINE_V2.md` and `data/golden/ner_data_schema.json`.
- Keep clean/noisy variants grouped by original sample and concept family.
- Validate pilots with `python scripts/validate_ner_dataset.py --input <file.jsonl>`.
- Do not use lockbox examples or predictions for data generation.

## CHẨN_ĐOÁN

Residual counts: FP=16, FN=13, boundary=5, type=12.

Create reviewed minimal contrasts targeting the corresponding residual files under the selected error-review artifact. Include positive, hard-negative, boundary, type-confusion, mixed-language, and noisy-format variants.

## KẾT_QUẢ_XÉT_NGHIỆM

Residual counts: FP=1, FN=4, boundary=0, type=0.

Create reviewed minimal contrasts targeting the corresponding residual files under the selected error-review artifact. Include positive, hard-negative, boundary, type-confusion, mixed-language, and noisy-format variants.

## THUỐC

Residual counts: FP=11, FN=10, boundary=7, type=3.

Create reviewed minimal contrasts targeting the corresponding residual files under the selected error-review artifact. Include positive, hard-negative, boundary, type-confusion, mixed-language, and noisy-format variants.

## TRIỆU_CHỨNG

Residual counts: FP=60, FN=31, boundary=13, type=9.

Create reviewed minimal contrasts targeting the corresponding residual files under the selected error-review artifact. Include positive, hard-negative, boundary, type-confusion, mixed-language, and noisy-format variants.

## TÊN_XÉT_NGHIỆM

Residual counts: FP=0, FN=3, boundary=0, type=0.

Create reviewed minimal contrasts targeting the corresponding residual files under the selected error-review artifact. Include positive, hard-negative, boundary, type-confusion, mixed-language, and noisy-format variants.

## Owners

- Problem Data owner: `TRIỆU_CHỨNG`, `CHẨN_ĐOÁN`.
- Structured Data owner: `THUỐC`, `TÊN_XÉT_NGHIỆM`, `KẾT_QUẢ_XÉT_NGHIỆM`.
