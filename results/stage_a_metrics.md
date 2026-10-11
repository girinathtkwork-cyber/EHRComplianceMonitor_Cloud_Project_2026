# Stage A Evaluation Metrics (Updated Routing)

## Headline Detection & Clean FPR

| Metric | Value | 95% Wilson CI |
|---|---|---|
| **Localized Detection Recall (Strict)** | 0.0000 | [0.0000, 0.0409] |
| **Localized Detection Recall (Soft / Unresolved)** | 0.2333 | [0.1580, 0.3305] |
| **Clean-Record False Positive Rate (Strict)** | 0.0000 | [0.0000, 0.0209] |
| **Clean-Record False Positive Rate (Soft / Unresolved)** | 0.4444 | [0.3738, 0.5174] |

## Record-Level Detection

| Mode | TP | FP | FN | TN | Precision | Recall | F1 | FPR |
|---|---|---|---|---|---|---|---|---|
| Strict | 0 | 0 | 90 | 180 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Soft | 51 | 80 | 39 | 100 | 0.3893 | 0.5667 | 0.4615 | 0.4444 |

## Per Error Type & Funnel Delivery

| Error Type | Injected (N) | Localized Strict | Localized Soft | Funnel Coverage | Funnel Delivery Rate |
|---|---|---|---|---|---|
| `wrong_dosage` | 18 | 0 (0.0%) | 12 (66.7%) | **18** | **100.0%** |
| `fabricated_diagnosis` | 38 | 0 (0.0%) | 3 (7.9%) | **34** | **89.5%** |
| `fabricated_procedure` | 23 | 0 (0.0%) | 6 (26.1%) | **20** | **87.0%** |
| `wrong_medication` | 11 | 0 (0.0%) | 0 (0.0%) | **10** | **90.9%** |

## Forwarding Throughput & Cost Proxy

- **Transcripts Forwarded to Stage B:** 258 / 270 (95.56%)
- **Transcripts with Zero Forwarded Entities:** 12 (4.44%)
- **Entities Forwarded to Stage B:** 3625 / 3625 (100.00%)
- **Entities Classified Invalid (RxNorm Absence):** 0
- **Entities Classified Unresolved (Low-Confidence ICD-10):** 122

## Wrong Dosage Audit: Drug-Linked vs. Measurement Split

Total wrong dosage injections: 18
- **Drug-associated dosages:** 11 / 18 (61.1%)
- **Non-drug surgical measurements:** 7 / 18 (38.9%)
- **Missed during extraction:** 0 / 18 (0.0%)

### Detail of Drug-Associated Injections:
- `mt_00072`: injected `750mg` (was `75mg`) → linked to drug `Brevital` (flags: dose_not_a_known_strength)
- `mt_00135`: injected `30mg` (was `3mg`) → linked to drug `fentanyl` (flags: dose_not_a_known_strength)
- `mt_00139`: injected `30mg` (was `300mg`) → linked to drug `Lithium` (flags: dose_not_a_known_strength)
- `mt_00153`: injected `550mg` (was `55mg`) → linked to drug `Glucose` (flags: dose_not_a_known_strength)
- `mt_00182`: injected `32mg` (was `325mg`) → linked to drug `hydrocodone` (flags: dose_not_a_known_strength)
- `mt_00184`: injected `12mg` (was `125mg`) → linked to drug `Digoxin` (flags: dose_not_a_known_strength)
- `mt_00188`: injected `40mg` (was `400mg`) → linked to drug `Dilantin` (flags: dose_not_a_known_strength)
- `mt_00203`: injected `50mg` (was `500mg`) → linked to drug `cephalexin` (flags: none)
- `mt_00205`: injected `87mg` (was `875mg`) → linked to drug `Augmentin` (flags: dose_not_a_known_strength)
- `mt_00224`: injected `50mg` (was `500mg`) → linked to drug `vitamin D` (flags: none)
- `mt_00269`: injected `17mcg` (was `175mcg`) → linked to drug `fentanyl` (flags: dose_not_a_known_strength)

### Detail of Non-Drug Measurements:
- `mt_00004`: injected `30mL` (was `300mL`) → categorized as `measurement`
- `mt_00045`: injected `100mL` (was `10mL`) → categorized as `measurement`
- `mt_00068`: injected `10mL` (was `100mL`) → categorized as `measurement`
- `mt_00076`: injected `50mg` (was `5mg`) → categorized as `measurement`
- `mt_00097`: injected `100mL` (was `10mL`) → categorized as `measurement`
- `mt_00136`: injected `32mg` (was `325mg`) → categorized as `measurement`
- `mt_00244`: injected `50mL` (was `5mL`) → categorized as `measurement`

## Audit of Missed Injections (Not in Funnel)

Total missed injections: 8 / 90 (Funnel coverage: 82 / 90 = 91.1%)

| Transcript ID | Error Type | Injected Entity | Extraction Reason |
|---|---|---|---|
| `mt_00014` | `fabricated_diagnosis` | `Chronic Kidney Disease, Stage 3` | `not_extracted` |
| `mt_00029` | `fabricated_diagnosis` | `Chronic Kidney Disease, Stage 3` | `not_extracted` |
| `mt_00053` | `wrong_medication` | `Navane` | `not_extracted` |
| `mt_00091` | `fabricated_diagnosis` | `Chronic Kidney Disease, Stage 3` | `not_extracted` |
| `mt_00143` | `fabricated_procedure` | `upper endoscopy` | `not_extracted` |
| `mt_00209` | `fabricated_procedure` | `diagnostic laparoscopy` | `not_extracted` |
| `mt_00210` | `fabricated_diagnosis` | `Chronic Kidney Disease, Stage 3` | `not_extracted` |
| `mt_00216` | `fabricated_procedure` | `cardiac catheterization` | `not_extracted` |
