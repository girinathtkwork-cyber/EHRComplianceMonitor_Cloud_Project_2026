# Stage A: Deterministic Retrieval and Verification Engine

`src/backend/stage_a` implements the deterministic extraction, verification, and RAG retrieval pipeline of the EHR Compliance Monitor system.

---

## 1. Modules Overview

- `extract.py`: Entity extraction engine (v2).
  - Medication identification via an n-gram (1-3 token) exact lexicon scan using RxNorm concepts (TTY: IN, PIN, BN).
  - Stoplist filtering for high-frequency English collisions (`air`, `water`, `ice`, `salt`, `oxygen`, etc.).
  - Contextual dosage extraction: associations within an 8-token sliding window distinguish pharmaceutical dosages from surgical measurements (e.g. `blood loss 300mL`).
  - Section-aware diagnosis and procedure extraction spanning clinical headers (`PREOPERATIVE DIAGNOSES`, `ASSESSMENT`, `IMPRESSION`, `OPERATION`, etc.).
- `rxnorm_client.py`: RxNorm and RxClass (MED-RT) API client.
  - Standard library `urllib.request` implementation with persistent JSON disk cache (`dataset/cache/rxnorm_cache.json`).
  - Rate limiting (0.1s delay) and exponential backoff.
  - Queries exact/approximate match, concept properties, active ingredients, MED-RT indications (`may_treat`), and clinical drug forms (`known_strengths`).
- `icd10_searcher.py`: TF-IDF inverted index search engine over CMS ICD-10-CM 2027 code descriptions.
  - Cosine similarity matching yielding normalized scores in `[0.0, 1.0]`.
- `verify.py`: Per-entity verification orchestrator.
  - Verifies medications against RxNorm and checks dosage plausibility against known strengths.
  - Verifies diagnoses against ICD-10-CM with calibrated confidence thresholding (0.25).
  - Formulates structured, source-attributed `evidence_text` blocks for downstream RAG prompting.
- `contract.py`: Hand-written contract validator enforcing schema compliance, valid enums, non-empty spans, and ISO 8601 timestamps without third-party dependencies.
- `pipeline.py`: Main pipeline coordinator providing `run_stage_a(record)` and `run_stage_a_batch(records)`.

---

## 2. Quickstart & CLI Usage

### Build Reference Assets (First Time Only)
```bash
python scripts/build_reference_data.py
```
Outputs `rxnorm_lexicon.json`, `icd10cm_codes.json`, and `icd10cm_index.json` under `dataset/reference/` (35.26 MB total).

### Pre-warm RxNorm Cache
```bash
python scripts/warm_caches.py
```
Runs a pre-warming pass over the dataset to populate `dataset/cache/rxnorm_cache.json` so subsequent executions run with 100% cache hits and zero network latency.

### Run Pipeline
```bash
python scripts/run_stage_a.py --input dataset/processed/mtsamples_with_errors.json --output results/stage_a_output.json
```

### Run Evaluation
```bash
python src/backend/evaluation/evaluate_stage_a.py
```
Generates `results/stage_a_metrics.json` and formatted report `results/stage_a_metrics.md`.

### Run Unit Tests
```bash
python -m pytest tests/
```

### Build & Test AWS Lambda
```bash
python scripts/build_lambda.py
python scripts/invoke_local.py
```
