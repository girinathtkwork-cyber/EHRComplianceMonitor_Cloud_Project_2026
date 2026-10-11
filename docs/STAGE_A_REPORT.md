# Stage A Verification and Retrieval: Engineering Report

**Project:** EHR Compliance Monitor (Cloud Architecture Project 2026)  
**Author:** T K Girinath (feature/girinath)  
**Pipeline Version:** `2.0.0`  
**Date:** October 2026  

---

## 1. System Architecture: The Two-Stage RAG Paradigm

Medical record auditing requires both strict medical terminology verification and nuanced clinical reasoning. Running large language models (LLMs) over full clinical transcripts without grounding leads to hallucinated standards, high API costs, and latency.

The EHR Compliance Monitor solves this with an asymmetric **Two-Stage Architecture**:

```
Raw Clinical Transcription
            │
            ▼
┌────────────────────────────────────────────────────────┐
│               STAGE A: RETRIEVAL & VERIFICATION        │
│  - Lexicon-based n-gram entity extraction              │
│  - Distance-aware dosage association                   │
│  - RxNorm & RxClass API lookups (with disk cache)      │
│  - ICD-10-CM TF-IDF Cosine Search                      │
│                                                        │
│  Routes:                                               │
│    • "invalid" (hallucinated / non-existent)           │
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
│      • ICD-10 top candidate matches                    │
│  - Assesses contextual clinical appropriateness        │
└────────────────────────────────────────────────────────┘
```

Stage A serves as the deterministic retrieval engine. By filtering ungrounded entities and providing structured `evidence_text` to Stage B, Stage A eliminates retrieval latency during LLM prompting and provides factual grounding.

---

## 2. Reference Data Sources & Licensing

1. **RxNorm & RxClass (MED-RT):**
   - **Provider:** U.S. National Library of Medicine (NLM), National Institutes of Health.
   - **Scope:** 23,514 active concepts (TTY: `IN`, `PIN`, `BN`) in `rxnorm_lexicon.json` (1.72 MB).
   - **Licensing:** RxNorm is free to use for research and clinical purposes under NLM terms. UMLS Metathesaurus license guidelines are respected. No proprietary code sets (e.g. SNOMED CT) are distributed.
   - **Indication Relationships:** RxClass queries use the MED-RT provider with `may_treat` and `may_prevent` relationship attributes.

2. **ICD-10-CM (2027 Fiscal Year Release):**
   - **Provider:** Centers for Medicare & Medicaid Services (CMS) / National Center for Health Statistics (NCHS).
   - **Source file:** `dataset/raw/Code Descriptions/icd10cm_order_2027.txt` (14.7 MB, 98,404 entries).
   - **Artifacts:**
     - `icd10cm_codes.json`: 98,403 parsed codes (18.57 MB).
     - `icd10cm_index.json`: TF-IDF inverted index over 74,879 billable codes with 7,099 unique terms (14.97 MB).
   - **Licensing:** Public domain official U.S. Government work.

---

## 3. Engineering Decisions & Calibrations

### 3.1 Lexicon Collision & Stoplist Formulation
A raw scan of 23,514 RxNorm concepts against clinical notes produces false positives on ordinary words that share names with trace substances, historical reagents, or abbreviations (e.g., `air`, `water`, `ice`, `salt`, `sugar`, `lead`, `gold`, `tin`, `corn`, `rice`, `oxygen`, `normal`).
To resolve this, `MEDICATION_STOPLIST` (75+ terms) was established by scanning all 270 clean transcripts and identifying high-frequency non-clinical collisions.

### 3.2 Distance-Aware Dosage vs. Surgical Measurement Disambiguation
A common defect in EHR parsers is misclassifying surgical measurements (e.g., `"Estimated blood loss was 300mL"`, `"SpO2 98%"`) as pharmaceutical doses.
Stage A enforces an 8-token proximity window between numeric-unit pairs and detected medication tokens. Measurements beyond 8 tokens or without drug linkage are labeled as `measurement` rather than `dosage`, eliminating false dose alerts on blood loss and vital signs.

### 3.3 ICD-10 Confidence Threshold Calibration
The ICD-10 TF-IDF search computes cosine similarity in `[0.0, 1.0]`. Diagnoses from non-injected (clean) records were evaluated to find the distribution of similarity scores. Legitimate clinical terms with slight typographical or syntactic variation (e.g., *"chronic otitis media with effusion"*) achieve scores between 0.40 and 0.85. Obvious non-medical terms or severe hallucinations drop below 0.20. A threshold of `0.25` was selected strictly on clean records to catch fabricated concepts without discarding valid clinical variants.

---

## 4. Evaluation Results (Real Experimental Output)

Evaluated over the complete 270-transcript dataset (`dataset/processed/mtsamples_with_errors.json`), comprising 180 clean transcripts and 90 records with injected compliance errors.

### 4.1 Record-Level Detection (Stage A Alone)

| Mode | TP | FP | FN | TN | Precision | Recall | F1 | FPR |
|---|---|---|---|---|---|---|---|---|
| **Strict** (Invalid only) | 31 | 47 | 59 | 133 | 0.3974 | 0.3444 | 0.3690 | 0.2611 |
| **Soft** (Invalid + flags) | 51 | 80 | 39 | 100 | 0.3893 | 0.5667 | 0.4615 | 0.4444 |

#### 95% Wilson Score Confidence Intervals
- **Strict Recall:** `[0.2545, 0.4472]`
- **Strict FPR:** `[0.2024, 0.3298]`
- **Soft Recall:** `[0.4636, 0.6642]`
- **Soft FPR:** `[0.3738, 0.5174]`

*Analysis:* Stage A is an existence and retrieval filter, not a clinical reasoning judge. As expected, strict detection alone achieves ~34.4% recall because errors like `wrong_medication` swap real drugs for other real drugs (which exist in RxNorm).

### 4.2 Per Error Type & Funnel Coverage

The primary measure of success for Stage A is **Funnel Coverage**: the proportion of injected errors that are successfully extracted and routed into Stage B with grounded reference context.

| Error Type | Injected ($N$) | Stage A Detected | Detection Recall | Funnel Coverage | Funnel Rate |
|---|---|---|---|---|---|
| `wrong_dosage` | 18 | 3 | 16.67% | **18** | **100.00%** |
| `fabricated_diagnosis` | 38 | 3 | 7.89% | **34** | **89.47%** |
| `fabricated_procedure` | 23 | 6 | 26.09% | **20** | **86.96%** |
| `wrong_medication` | 11 | 0 | 0.00% | **10** | **90.91%** |
| **Total Injected** | **90** | **12** | **13.33%** | **82** | **91.11%** |

**Key Finding:** **91.11% (82 of 90)** of all injected errors are successfully preserved and delivered to Stage B with full RAG context.
- For `wrong_dosage`, 100% of cases are passed into Stage B alongside known product strengths.
- For `wrong_medication`, 0% are flagged as invalid (as both original and swapped drugs are real RxNorm concepts), but 90.9% are extracted and forwarded with MED-RT indication data, allowing Stage B's LLM to identify the clinical mismatch.

### 4.3 Cost Proxy & Throughput

- **Total Extracted Entities:** 3,625 entities across 270 records.
- **Forwarded Entities:** 3,503 entities (96.63%).
- **Transcripts with Zero Forwarded Entities:** 12 (4.44%).
- **Processing Time:** **13.9 seconds** for all 270 records (warm cache).
- **Cache Hit Rate:** **100.0%** (1,232 cached RxNorm entries).

### 4.4 Audit Table of Misses
Only 8 records had injected entities that escaped Stage A extraction:
1. `mt_00014`, `mt_00029`, `mt_00091`, `mt_00210`: `"Chronic Kidney Disease, Stage 3"` — injected inside narrative paragraphs lacking a formal diagnostic header.
2. `mt_00053`: `"Navane"` — masked by narrative casing.
3. `mt_00143`, `mt_00209`, `mt_00216`: procedures (`upper endoscopy`, `diagnostic laparoscopy`, `cardiac catheterization`) embedded in non-standard body sections.

---

## 5. AWS Lambda Deployment Profile

- **Package:** `build/stage_a_lambda.zip`
- **Compressed Size:** **3.40 MB** (well below the 49.0 MB AWS limit).
- **Contents:**
  - `stage_a_handler.py` at root
  - `backend/stage_a/` Python package
  - `dataset/reference/rxnorm_lexicon.json` (1.72 MB)
  - `dataset/reference/icd10cm_codes.json` (18.57 MB)
  - `dataset/reference/icd10cm_index.json` (14.97 MB)
  - `dataset/cache/rxnorm_cache.json` (0.22 MB)
- **Local Parity Test:** `scripts/invoke_local.py` verified 100% equivalence between the Lambda handler execution and CLI pipeline execution across 20 records.

---

## 6. Limitations & Future Work

1. **Unheaded Diagnosis Extraction:** Currently diagnoses are extracted from standard sections (`ASSESSMENT`, `DIAGNOSIS`, `IMPRESSION`). Clinical entities mentioned exclusively in freeform history paragraphs are missed.
2. **Product Strength vs. Daily Dose Limits:** RxNorm SCDs supply product unit strengths (e.g., `metformin 500mg`), not maximum cumulative daily limits (e.g., `2000mg/day`). Full dosage validation relies on Stage B reasoning.
3. **MED-RT Indication Coverage:** While RxNorm covers all approved pharmaceuticals, MED-RT indications in RxClass are absent for certain older generic drugs (`indications_status: "unknown"`).

---

## 7. Commands to Reproduce

All commands run directly on Windows PowerShell:

```powershell
# 1. Run unit test suite (47 tests)
python -m pytest tests/

# 2. Build reference data (if missing)
python scripts/build_reference_data.py

# 3. Pre-warm RxNorm cache
python scripts/warm_caches.py

# 4. Run Stage A batch pipeline
python scripts/run_stage_a.py --input dataset/processed/mtsamples_with_errors.json --output results/stage_a_output.json

# 5. Run evaluation and metrics generator
python src/backend/evaluation/evaluate_stage_a.py

# 6. Build Lambda ZIP artifact
python scripts/build_lambda.py

# 7. Execute local Lambda parity test
python scripts/invoke_local.py
```

---

## 8. Remaining Steps for the Human Operator

The automated Stage A milestone is complete. The following infrastructure steps remain for the human operator in AWS Console:

1. **Deploy Lambda Function:**
   - Create an AWS Lambda function named `ehr-stage-a-processor` (Python 3.12 or 3.13 runtime, x86_64 or arm64).
   - Configure Lambda memory to **512 MB** and timeout to **60 seconds**.
   - Upload `build/stage_a_lambda.zip` as the function deployment package.
   - Set handler to: `stage_a_handler.lambda_handler`.

2. **Configure S3 Bucket Trigger:**
   - Attach an S3 notification trigger on `input/` with suffix `.json`.
   - Ensure the Lambda execution role has `s3:GetObject` on `input/*` and `s3:PutObject` on `output/stage_a/*`.

3. **Hand Off to Stage B Teammate:**
   - Share `docs/INTERFACE_CONTRACT.md` and `docs/CONTRACT_CHANGES.md`.
   - The teammate can test their prompt pipeline using `results/stage_a_output.json`.
