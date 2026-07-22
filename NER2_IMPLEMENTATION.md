# NER-2 Controlled Zero-shot Benchmark

NER-2 keeps `configs/gliner_zero_shot.yaml` as the immutable NER-1 reproduction
point. Experiment configs extend that baseline and change one declared axis.

## Safety and ordering

1. Run the real-model proposal equivalence gate on development.
2. Run label schemas on development and shortlist at most two.
3. Run window profiles from the shortlisted label configuration.
4. Run the global threshold survey, followed by generated per-type coordinate
   candidates.
5. Run focused passes only after label/window/threshold selection. P3 requires a
   recorded `keep` decision for P1 or P2.
6. Calibration requires an explicit shortlist. Lockbox is prohibited for
   selection and requires a frozen config, milestone, and confirmation flag.

## Commands

```powershell
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"

python scripts/check_gliner_proposal_equivalence.py `
  --config configs/gliner_zero_shot.yaml `
  --split development `
  --output outputs/reports/ner2_proposal_equivalence.json

python scripts/run_ner2_experiments.py --family labels --split development
python scripts/summarize_ner2_experiments.py --family labels

# Supply the reviewed winner/shortlist configuration for later families.
python scripts/run_ner2_experiments.py --family windows --parent-config <label-winner.yaml>
python scripts/run_ner2_experiments.py --family thresholds --parent-config <window-winner.yaml>

python scripts/generate_ner2_coordinate_search.py --base-config <global-threshold-winner.yaml>

python scripts/run_ner2_experiments.py --family passes --parent-config <threshold-winner.yaml>

python scripts/run_ner2_experiments.py --split calibration `
  --shortlist outputs/reports/ner2_experiment_summary/shortlist.json `
  --purpose calibration
```

Do not run `--split lockbox` during selection. The runner rejects that purpose
even when a milestone flag is supplied.

## Final verification

After a calibration run receives a reviewed `keep` decision, the summarizer may
freeze it. Then execute two offline runs and require byte-identical files:

```powershell
python scripts/verify_ner2_determinism.py `
  --run1 <predictions-run-1> --run2 <predictions-run-2> `
  --expected-count 4 `
  --output outputs/reports/v2_ner2_selected/determinism.json

python scripts/review_ner2_errors.py `
  --run-dir outputs/reports/v2_ner2_selected
```

`selected_zero_shot.yaml` is not created when no calibration configuration
passes the frozen policy. This avoids representing an incomplete benchmark as a
selected result.