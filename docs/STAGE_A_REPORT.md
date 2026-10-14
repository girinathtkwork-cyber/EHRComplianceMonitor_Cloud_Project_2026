# Stage A Verification and Retrieval: Engineering Report

**Project:** EHR Compliance Monitor (Cloud Architecture Project 2026)  
**Author:** T K Girinath (feature/girinath)  
**Pipeline Version:** `2.0.0`  
**Date:** October 2026  

---

## 1. System Architecture: The Two-Stage RAG Paradigm

Medical record compliance auditing requires both strict terminology verification and contextual clinical reasoning. Running large language models (LLMs) over full clinical transcripts without grounding leads to hallucinated standards, high API token costs, and high latency.

The EHR Compliance Monitor solves this with an asymmetric **Two-Stage Architecture**:

```
Raw Clinical Transcription
            │
            ▼
┌────────────────────────────────────────────────────────┐
│               STAGE A: RETRIEVAL & VERIFICATION        │
│  - Lexicon-based n-gram entity extraction              │
│  - Proximity-aware dosage association (8 tokens)       │
│  - RxNorm & RxClass API lookups (with disk cache)      │
│  - ICD-10-CM TF-IDF Cosine Search                      │
│                                                        │
│  Routes:                                               │
│    • "invalid" (fails RxNorm existence check)          │
│    • "unresolved" (low-confidence ICD-10 match)        │
│    • "verified_needs_context_check" (+ evidence_text)  │
└────────────────────────────────────────────────────────┘
            │
            ▼
┌────────────────────────────────────────────────────────┐
│               STAGE B: CONTEXTUAL LLM AUDIT            │
│  - Receives Stage A output conforming to contract      │
│  - Prompts LLM using retrieved RAG evidence_text:      │
│      • Indications from MED-RT                         │
│      • Canonical ingredients                           │
│      • Known product strengths                         │
│      • Top-3 candidate ICD-10 matches for unresolved   │
│  - Assesses contextual clinical appropriateness        │
└────────────────────────────────────────────────────────┘
```

Stage A operates as a deterministic extraction and RAG grounding retriever. Entities that definitively do not exist in RxNorm are routed as `invalid`. Diagnoses with low-confidence ICD-10 matches are classified as `unresolved` and forwarded to Stage B along with their top-3 candidate matches so that Stage B's LLM can evaluate clinical context without premature rejection.

---

## 2. Reference Data Sources & Licensing

1. **RxNorm & RxClass (MED-RT):**
   - **Provider:** U.S. National Library of Medicine (NLM), National Institutes of Health.
   - **Scope:** 23,514 active concepts (TTY: `IN`, `PIN`, `BN`) in `rxnorm_lexicon.json` (1.72 MB).
   - **Access & License:** The RxNorm and RxClass REST APIs are publicly accessible services provided by the NLM. No proprietary commercial code systems (such as SNOMED CT) are distributed.
   - **Indication Relationships:** RxClass queries use the MED-RT provider with `may_treat` and `may_prevent` relationship attributes.

2. **ICD-10-CM (2027 Fiscal Year Release):**
   - **Provider:** Centers for Medicare & Medicaid Services (CMS) / National Center for Health Statistics (NCHS).
   - **Source file:** `dataset/raw/Code Descriptions/icd10cm_order_2027.txt` (14.7 MB, 98,404 entries).
   - **Artifacts:**
     - `icd10cm_codes.json`: 98,403 parsed codes (18.57 MB).
     - `icd10cm_index.json`: TF-IDF inverted index over 74,879 billable codes with 7,099 unique terms (14.97 MB).
   - **License:** Public domain official U.S. Government work.

---

## 3. Engineering Decisions & Calibrations

### 3.1 Lexicon Collision & Stoplist Formulation
Scanning 23,514 RxNorm concepts against raw clinical notes produces collisions on common English words that coincide with historical reagents, elements, or abbreviations (e.g., `air`, `water`, `ice`, `salt`, `sugar`, `lead`, `gold`, `tin`, `corn`, `rice`, `oxygen`, `normal`).
To eliminate these false detections, `MEDICATION_STOPLIST` (75+ terms) was established by analyzing all 270 transcripts and pruning high-frequency non-clinical collisions.

### 3.2 Distance-Aware Dosage vs. Surgical Measurement Disambiguation
A frequent failure mode in medical NLP is misclassifying non-pharmaceutical surgical measurements (e.g., `"Estimated blood loss was 300mL"`, `"SpO2 98%"`) as medication doses.
Stage A enforces an 8-token proximity window between numeric-unit pairs and detected medication mentions. Quantities lacking medication association are tagged as `measurement` rather than `dosage`, preventing spurious dose plausibility flags on blood loss and vitals.

### 3.3 Real ICD-10 Similarity-Score Distribution on Clean Records
The ICD-10 TF-IDF search computes cosine similarity in `[0.0, 1.0]`. To calibrate the confidence threshold objectively without tuning on injected errors, all **723 diagnosis mentions on clean records** were analyzed:

- **Total Mentions on Clean Records:** 723
- **Score Range:** Min = `0.0000`, Max = `1.0000`
- **Percentiles:**
  - $p_{10}$: `0.2508`
  - $p_{25}$: `0.3322`
  - $p_{50}$: `0.4572` (Median)
  - $p_{75}$: `0.5911`
  - $p_{90}$: `0.7590`
  - $p_{95}$: `0.8664`

#### Score Distribution Bins (Clean Records):
| Similarity Interval | Count | Percentage | Cumulative % |
|---|---|---|---|
| `[0.0, 0.1)` | 34 | 4.7% | 4.7% |
| `[0.1, 0.2)` | 5 | 0.7% | 5.4% |
| `[0.2, 0.3)` | 84 | 11.6% | 17.0% |
| `[0.3, 0.4)` | 162 | 22.4% | 39.4% |
| `[0.4, 0.5)` | 133 | 18.4% | 57.8% |
| `[0.5, 0.6)` | 129 | 17.8% | 75.7% |
| `[0.6, 0.7)` | 74 | 10.2% | 85.9% |
| `[0.7, 0.8)` | 48 | 6.6% | 92.5% |
| `[0.8, 0.9)` | 32 | 4.4% | 97.0% |
| `[0.9, 1.0]` | 22 | 3.0% | 100.0% |

#### Threshold Selection:
A confidence threshold of **`0.25`** corresponds to the 10th percentile ($p_{10} = 0.2508$). Exactly **71 of 723 mentions (9.8%)** fall below 0.25 on clean records. An audit of these 71 mentions reveals that they primarily consist of section headers bleeding into narrative text (e.g., `"AND PLAN:"`, `"4x4s"`, operative procedure details recorded under diagnostic headers). Routing these below-threshold cases to `unresolved` (with top-3 candidates forwarded to Stage B) rather than `invalid` eliminates false rejections on valid clinical variants while alerting Stage B to inspect the context.

---

## 4. Evaluation Results (Real Experimental Output)

Evaluated across all 270 transcripts (`dataset/processed/mtsamples_with_errors.json`), comprising 180 clean records and 90 records with injected compliance errors.

### 4.1 Headline Detection Recall & Clean-Record False Positive Rate

The headline detection metric evaluates **localized detection** (the specific injected entity itself is flagged):

| Metric | Value | 95% Wilson Confidence Interval |
|---|---|---|
| **Localized Detection Recall (Strict: Invalid)** | **0.0000** (0 / 90) | `[0.0000, 0.0409]` |
| **Localized Detection Recall (Soft: Unresolved / Flags)** | **0.2333** (21 / 90) | `[0.1580, 0.3305]` |
| **Clean-Record False Positive Rate (Strict: Invalid)** | **0.0000** (0 / 180) | `[0.0000, 0.0209]` |
| **Clean-Record False Positive Rate (Soft: Unresolved / Flags)** | **0.4444** (80 / 180) | `[0.3738, 0.5174]` |

*Analysis of Localized Recall:*
- In strict mode (`invalid` reserved for non-existent entities), recall is `0.0000` because none of the injected errors introduce synthetic drug names that fail RxNorm. Medication swaps (`wrong_medication`) replace legitimate drugs with other legitimate drugs, dosages are numerical deviations, and fabricated diagnoses/procedures use valid medical vocabulary.
- Consequently, the strict clean-record false positive rate is **`0.0000`** (zero clean transcripts are incorrectly flagged as invalid).
- In soft mode (including low-confidence `unresolved` ICD matches and `dose_not_a_known_strength`), localized recall is **`0.2333`** (21 of 90 injected errors flagged directly). Clean-record FPR is **`0.4444`** due to legitimate diagnostic phrasing variations falling below the $0.25$ similarity threshold.

### 4.2 Record-Level Confusion Matrix

| Mode | TP | FP | FN | TN | Precision | Recall | F1 | FPR |
|---|---|---|---|---|---|---|---|---|
| **Strict** | 0 | 0 | 90 | 180 | 0.0000 | 0.0000 | 0.0000 | **0.0000** |
| **Soft** | 51 | 80 | 39 | 100 | 0.3893 | 0.5667 | 0.4615 | **0.4444** |

### 4.3 Honest Restatement of Funnel Coverage & Forwarding Throughput

Because Stage A routes low-confidence diagnoses to `unresolved` rather than rejecting them as `invalid`, Stage A functions primarily as an **enriching pass-through retriever**, not an aggressive filter:

- **Total Entities Forwarded to Stage B:** **3,625 / 3,625 (100.00%)**
- **Transcripts Forwarded to Stage B:** **258 / 270 (95.56%)**
- **Transcripts with Zero Forwarded Entities:** **12 / 270 (4.44%)** (records with no extractable entities)

Given that 100% of extracted entities and 95.56% of transcripts reach Stage B, **funnel coverage is a measure of extraction completeness**, reflecting whether the injected entity was successfully identified from the transcript and delivered to Stage B with RAG grounding:

| Error Type | Injected ($N$) | Localized Strict | Localized Soft | Funnel Coverage | Funnel Delivery Rate |
|---|---|---|---|---|---|
| `wrong_dosage` | 18 | 0 (0.0%) | 12 (66.7%) | **18 / 18** | **100.0%** |
| `fabricated_diagnosis` | 38 | 0 (0.0%) | 3 (7.9%) | **34 / 38** | **89.5%** |
| `fabricated_procedure` | 23 | 0 (0.0%) | 6 (26.1%) | **20 / 23** | **87.0%** |
| `wrong_medication` | 11 | 0 (0.0%) | 0 (0.0%) | **10 / 11** | **90.9%** |
| **Total Injected** | **90** | **0 (0.0%)** | **21 (23.3%)** | **82 / 90** | **91.1%** |

**Overall Result:** **91.1% (82 of 90)** of all injected errors are captured in the extraction funnel and delivered to Stage B with grounded context.

### 4.4 Wrong Dosage Audit: Drug-Linked vs. Measurement Split

All 18 injected `wrong_dosage` cases were examined to determine whether they represent true pharmaceutical dosages or non-drug measurements:

- **Total Injected Wrong Dosage Records:** 18
- **Drug-Associated Dosages:** **11 / 18 (61.1%)**
- **Non-Drug Surgical Measurements:** **7 / 18 (38.9%)**
- **Misses During Extraction:** **0 / 18 (0.0%)** (100% extracted and forwarded)

#### Detail of Drug-Associated Dosage Injections (11 Cases):
| Transcript ID | Injected Entity | Original Value | Associated Drug | Plausibility Flag |
|---|---|---|---|---|
| `mt_00072` | `750mg` | `75mg` | `Brevital` | `dose_not_a_known_strength` |
| `mt_00135` | `30mg` | `3mg` | `fentanyl` | `dose_not_a_known_strength` |
| `mt_00139` | `30mg` | `300mg` | `Lithium` | `dose_not_a_known_strength` |
| `mt_00153` | `550mg` | `55mg` | `Glucose` | `dose_not_a_known_strength` |
| `mt_00182` | `32mg` | `325mg` | `hydrocodone` | `dose_not_a_known_strength` |
| `mt_00184` | `12mg` | `125mg` | `Digoxin` | `dose_not_a_known_strength` |
| `mt_00188` | `40mg` | `400mg` | `Dilantin` | `dose_not_a_known_strength` |
| `mt_00203` | `50mg` | `500mg` | `cephalexin` | None (matches oral suspension strength) |
| `mt_00205` | `87mg` | `875mg` | `Augmentin` | `dose_not_a_known_strength` |
| `mt_00224` | `50mg` | `500mg` | `vitamin D` | None (matches capsule strength) |
| `mt_00269` | `17mcg` | `175mcg` | `fentanyl` | `dose_not_a_known_strength` |

*Finding:* For drug-associated dosages, the `dose_not_a_known_strength` check detected **9 of 11 (81.8%)** dosage deviations based purely on known RxNorm product strengths.

#### Detail of Non-Drug Surgical Measurements (7 Cases):
| Transcript ID | Injected Entity | Original Value | Extracted Category | Reason |
|---|---|---|---|---|
| `mt_00004` | `30mL` | `300mL` | `measurement` | Surgical blood loss mention |
| `mt_00045` | `100mL` | `10mL` | `measurement` | Fluid volume mention |
| `mt_00068` | `10mL` | `100mL` | `measurement` | Blood loss / drainage volume |
| `mt_00076` | `50mg` | `5mg` | `measurement` | Freeform measurement in narrative |
| `mt_00097` | `100mL` | `10mL` | `measurement` | Surgical irrigation fluid |
| `mt_00136` | `32mg` | `325mg` | `measurement` | Disconnected numeric in narrative |
| `mt_00244` | `50mL` | `5mL` | `measurement` | Blood loss mention |

*Finding:* None of the surgical measurements were incorrectly associated with pharmaceuticals.

### 4.5 Audit of Missed Injections (8 Cases)
Only 8 injected entities failed to be extracted into the funnel:
- `mt_00014`, `mt_00029`, `mt_00091`, `mt_00210`: Injected diagnosis `"Chronic Kidney Disease, Stage 3"` placed in freeform history text outside standard diagnostic headers (`ASSESSMENT`, `DIAGNOSES`, `IMPRESSION`).
- `mt_00053`: Injected medication `"Navane"` in narrative sentence.
- `mt_00143`, `mt_00209`, `mt_00216`: Injected procedures (`upper endoscopy`, `diagnostic laparoscopy`, `cardiac catheterization`) embedded in non-standard body sections.

---

## 5. AWS Lambda Deployment Profile

- **Deployment Package:** `build/stage_a_lambda.zip`
- **Compressed Size:** **3.40 MB** (limit: 49.0 MB).
- **Filesystem & Cache Architecture:**
  - When executing in Lambda (`AWS_LAMBDA_FUNCTION_NAME` present), all cache writes automatically route to `/tmp/rxnorm_cache.json`.
  - The cache client seeds in-memory state from the packaged read-only `dataset/cache/rxnorm_cache.json`.
  - The cache client gracefully catches `OSError` and `PermissionError`, allowing zero-error execution in completely read-only filesystems.
- **Verification Tests:**
  - `tests/test_handler_zip.py` extracts `build/stage_a_lambda.zip` into an isolated temporary directory and executes `stage_a_handler.lambda_handler` with only the temporary directory on `sys.path` and as the current working directory. Status: **PASSED**.
  - `scripts/invoke_local.py` verified 100% parity between local Lambda invocation and CLI batch pipeline execution. Status: **PASSED**.

---

## 6. Commands to Reproduce

All commands run directly on Windows PowerShell:

```powershell
# 1. Audit invalid entities breakdown
python scripts/audit_invalid.py

# 2. Run full test suite (48 tests, including isolated zip test)
python -m pytest tests/

# 3. Build reference data (if missing)
python scripts/build_reference_data.py

# 4. Pre-warm RxNorm cache
python scripts/warm_caches.py

# 5. Run Stage A batch pipeline
python scripts/run_stage_a.py --input dataset/processed/mtsamples_with_errors.json --output results/stage_a_output.json

# 6. Run evaluation and metrics generator
python src/backend/evaluation/evaluate_stage_a.py

# 7. Package Lambda ZIP artifact
python scripts/build_lambda.py

# 8. Run local Lambda parity test
python scripts/invoke_local.py
```

---

## 7. Remaining Steps for the Human Operator

1. **Deploy Lambda Function in AWS Console:**
   - Upload `build/stage_a_lambda.zip` (3.40 MB) to an AWS Lambda function (`stage_a_handler.lambda_handler`).
   - Configure Lambda memory to **512 MB** and timeout to **60 seconds**.
2. **Configure S3 Trigger:**
   - Attach an S3 notification trigger on bucket prefix `input/` with suffix `.json`.
   - Ensure IAM role permits `s3:GetObject` on `input/*` and `s3:PutObject` on `output/stage_a/*`.
3. **Handoff to Stage B Teammate:**
   - Share [`docs/INTERFACE_CONTRACT.md`](INTERFACE_CONTRACT.md) and [`docs/CONTRACT_CHANGES.md`](CONTRACT_CHANGES.md).
   - Downstream prompt engineers can develop and evaluate Stage B prompts against [`results/stage_a_output.json`](../results/stage_a_output.json).
