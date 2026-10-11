# Stage A Evaluation Metrics

## Record-Level Detection

| Mode | TP | FP | FN | TN | Precision | Recall | F1 | FPR |
|------|----|----|----|----|-----------|--------|-----|-----|
| Strict | 31 | 47 | 59 | 133 | 0.3974 | 0.3444 | 0.3690 | 0.2611 |
| Soft | 51 | 80 | 39 | 100 | 0.3893 | 0.5667 | 0.4615 | 0.4444 |

### 95% Wilson Confidence Intervals (Strict)

- **Recall**: [0.2545, 0.4472]
- **FPR**: [0.2024, 0.3298]

### 95% Wilson Confidence Intervals (Soft)

- **Recall**: [0.4636, 0.6642]
- **FPR**: [0.3738, 0.5174]

## Per Error Type

| Error Type | N | Stage-A Detected | Detection Recall | Funnel Coverage | Funnel Rate |
|------------|---|------------------|-----------------|-----------------|-------------|
| wrong_dosage | 18 | 3 | 0.1667 | 18 | 1.0000 |
| fabricated_diagnosis | 38 | 3 | 0.0789 | 34 | 0.8947 |
| fabricated_procedure | 23 | 6 | 0.2609 | 20 | 0.8696 |
| wrong_medication | 11 | 0 | 0.0000 | 10 | 0.9091 |

## Cost Proxy

- Entities forwarded to Stage B / Total: 3503 / 3625 (96.63%)
- Transcripts with zero forwarded entities: 12 (4.44%)

## Audit Table: Misses

Total misses (injected entity not in funnel): 8

| Transcript ID | Error Type | Injected Entity | Reason |
|---------------|------------|-----------------|--------|
| mt_00014 | fabricated_diagnosis | Chronic Kidney Disease, Stage 3 | not_extracted |
| mt_00029 | fabricated_diagnosis | Chronic Kidney Disease, Stage 3 | not_extracted |
| mt_00053 | wrong_medication | Navane | not_extracted |
| mt_00091 | fabricated_diagnosis | Chronic Kidney Disease, Stage 3 | not_extracted |
| mt_00143 | fabricated_procedure | upper endoscopy | not_extracted |
| mt_00209 | fabricated_procedure | diagnostic laparoscopy | not_extracted |
| mt_00210 | fabricated_diagnosis | Chronic Kidney Disease, Stage 3 | not_extracted |
| mt_00216 | fabricated_procedure | cardiac catheterization | not_extracted |

### Wrong Dosage Audit

- Drug-associated misses: 0
- Non-drug (measurement) misses: 0
