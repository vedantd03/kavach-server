# Kavach evaluation

Corpus: 29 files, 339 labels (290 sensitive, 49 hard negatives). Policy 2026-09-26.1, model `gemini-3.5-flash-lite`. Synthetic data only.

| Metric | Rules only (baseline) | Pipeline (rules + context + LLM) |
| --- | --- | --- |
| Precision | 0.850 | 0.993 |
| Recall | 1.000 | 1.000 |
| F1 | 0.919 | 0.997 |
| False positives | 51 | 2 |
| Hard-negative rejection | 0.0 | 1.0 |
| Under-labelling rate (files) | 0.103 | 0.0 |
| Exact file tier | 18/29 | 25/29 |
| p50 s/file | 0.004 | 0.134 |

Triage hours saved per 1,000 files: **56.3** (49 false alerts avoided; 2 analyst minutes per false alert; scaled linearly from 29 files).

Pipeline decided_by: {'rules': 280, 'server': 12}. LLM calls: {'classify': 2} (+28 cache hits), OCR calls: 3, key rotations: 0, keys used: [0, 1].

## Per type (pipeline)

| Type | P | R | F1 | TP | FP | FN |
| --- | --- | --- | --- | --- | --- | --- |
| AADHAAR | 1.000 | 1.000 | 1.000 | 71 | 0 | 0 |
| BANK_ACCOUNT | 1.000 | 1.000 | 1.000 | 7 | 0 | 0 |
| EMAIL | 0.965 | 1.000 | 0.982 | 55 | 2 | 0 |
| GSTIN | 1.000 | 1.000 | 1.000 | 6 | 0 | 0 |
| MOBILE_IN | 1.000 | 1.000 | 1.000 | 86 | 0 | 0 |
| PAN_BUSINESS | 1.000 | 1.000 | 1.000 | 1 | 0 | 0 |
| PAN_INDIVIDUAL | 1.000 | 1.000 | 1.000 | 46 | 0 | 0 |
| SECRET | 1.000 | 1.000 | 1.000 | 2 | 0 | 0 |
| UPI_ID | 1.000 | 1.000 | 1.000 | 16 | 0 | 0 |

## Tier confusion (pipeline; rows = expected, cols = predicted)

| expected \ predicted | restricted | confidential | internal | public |
| --- | --- | --- | --- | --- |
| restricted | 7 | 0 | 0 | 0 |
| confidential | 4 | 3 | 0 | 0 |
| internal | 0 | 0 | 7 | 0 |
| public | 0 | 0 | 0 | 8 |
