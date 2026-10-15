# Task: Build and verify Stage A of the Medical Transcription Verification System

You are working inside the git repo `EHRComplianceMonitor_Cloud_Project_2026`. Build everything described below, test it by actually running it, and leave the repo in a state where the human can deploy it to AWS Lambda with a few console clicks. Integration with the teammate's half (Stage B, dashboard) is NOT part of this task.

---

## 0. Working rules

- Work autonomously and in order (milestones M1 to M7). Ask a question only if you are truly blocked. Otherwise decide, and record the assumption in `docs/STAGE_A_REPORT.md`.
- Use the current branch `feature/girinath`. Make small commits with Conventional Commit messages (`feat:`, `fix:`, `docs:`, `test:`, `chore:`), one or more per milestone. Commit locally only. Do not push, and do not touch `main` or `develop`.
- Never put secrets in the repo.
- Do NOT modify or delete anything that already exists in `dataset/processed/`, `dataset/scripts/`, or `dataset/raw/`. Those scripts are the reproducible history of the project. New code goes in the new locations named below.
- Python 3.12+ syntax. The human is on Windows/PowerShell: use `pathlib`, never write bash-only scripts.
- Anything that will run inside AWS Lambda must use only the standard library plus `boto3` (already in the Lambda runtime): `urllib.request`, `json`, `re`, `math`, `difflib`, `collections`, `zipfile`. Do not use third-party runtime dependencies in Lambda code. Dev-only dependency allowed: `pytest`.
- Every number you report must come from a command you actually ran. If a command cannot run in your environment (for example no internet), say so and give the human the exact command instead of inventing results.

---

## 1. Context

**Project:** an AI system that verifies AI-generated medical transcripts before they are written to the EHR. It catches transcription errors (wrong drug, wrong dose, fabricated diagnosis or procedure).

**Architecture (important framing):** this is a Retrieval-Augmented Generation (RAG) pipeline.
- **Stage A = retrieval and cheap checks.** Extract medical entities, look them up in standardized databases (RxNorm for drugs, ICD-10-CM for diagnoses), and return the retrieved facts.
- **Stage B = generation (built by the teammate, out of scope).** An LLM judges clinical context using the facts Stage A retrieved, pasted into its prompt.

So Stage A's output must carry the retrieved facts in a form Stage B can use directly (`evidence_text`, see M3). Stage A can only answer "does this entity exist?", never "is it correct for this patient?". That is Stage B's job. Never label an entity "correct".

**Dataset:** MTSamples, 270 cleaned transcripts across 6 specialties.

**Existing files (read them first):**
- `dataset/processed/mtsamples_clean.json`: records `{transcript_id, medical_specialty, sample_name, description, transcription}`.
- `dataset/processed/mtsamples_with_errors.json`: same plus `ground_truth_label {is_error, error_type, injected_entity, correct_value}`. About 90 of 270 records have one injected error: `wrong_dosage` (~18), `fabricated_diagnosis` (~38), `fabricated_procedure` (~23), `wrong_medication` (~11).
- `dataset/scripts/extract_entities.py`: current extraction (regex plus hardcoded drug list).
- `dataset/scripts/verify_rxnorm.py`: RxNorm exact match with approximate-match fallback.
- `dataset/scripts/verify_icd10.py`: matches against a tiny curated list (~38 codes).
- `INTERFACE_CONTRACT.md` (look in `docs/`; if absent, search the repo): the JSON contract shared with the teammate.

**Known problems you must handle (verify each yourself):**
1. `KNOWN_DRUGS` in `extract_entities.py` is built from the same list used to inject errors. That is circular and inflates results. Replace it with a lexicon derived from RxNorm itself (M1).
2. The dosage regex treats any number plus unit as a drug dose. "Estimated blood loss is 300mL" becomes a "dosage". Doses must be tied to a nearby medication; other quantities are `measurement` entities.
3. Diagnosis extraction only finds text after headers such as `DIAGNOSIS:`.
4. No procedure extraction exists.
5. The ICD-10 list is far too small, and `SequenceMatcher` over free text is a weak retriever.
6. How errors were injected (important for honest evaluation):
   - `wrong_dosage` multiplies or divides the FIRST number-plus-unit in the text, which may not be a drug dose.
   - `fabricated_diagnosis` inserts "Additional diagnosis noted: X." after the first sentence.
   - `fabricated_procedure` appends "Patient also underwent X during this visit."
   - `wrong_medication` swaps in an ISMP look-alike drug, which is always a real drug.

---

## 2. Inputs the human has provided

- CMS ICD-10-CM order file(s) extracted into `dataset/raw/icd10/` (expect `icd10cm_order_*.txt`; glob for it). If missing, do not stop: finish the other milestones and mark ICD-10 work as blocked in the report.
- Internet on this machine for the public RxNorm and RxClass APIs (no key needed). Be polite: at most about 10 requests per second, with retry and exponential backoff, and cache every response to disk.

---

## 3. Milestones

### M1. Reference data builders
Create `src/backend/stage_a/reference/` and `scripts/build_reference_data.py`.

a. **RxNorm lexicon.** Fetch ingredient (IN), precise ingredient (PIN) and brand name (BN) concepts using RxNorm's "all concepts by term type" endpoint. FIRST check the endpoint name and parameters against the current official docs at https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html. Do not rely on memory. Save a compact `dataset/reference/rxnorm_lexicon.json`: lowercase name to `{rxcui, tty}`.

b. **ICD-10-CM codes.** Inspect the order file's format first (it is fixed-width: order number, code, header/billable flag, short description, long description). Parse it into `dataset/reference/icd10cm_codes.json`: `{code: {short, long, billable}}`.

c. **ICD-10 retrieval index.** Build an inverted index over normalized long descriptions (lowercase, tokenize, strip plurals, drop stopwords) with IDF weights. Implement `search(phrase, k=3)` returning `[{code, description, score}]` with score in 0 to 1, stdlib only. Store the index as compact JSON.

d. Print the file sizes. If the Lambda bundle (code plus reference data plus cache) would exceed about 45 MB zipped, document the alternative (load reference files from S3 at cold start) in the report, and still make the local build work.

### M2. Extraction v2 (`src/backend/stage_a/extract.py`)
- **Medications:** scan tokens and 1 to 3 word n-grams against the RxNorm lexicon, recording character offsets. Many ordinary words collide with lexicon entries. Build a stoplist by looking at the most frequent matches over the 270 transcripts, review them, and document the stoplist and how you chose it.
- **Doses:** regex for number plus unit (mg, mcg, g, mL, units, IU, mEq, %). Associate a dose with a medication only if the medication appears in the same clause within about 8 tokens. Everything else becomes `entity_type: "measurement"`.
- **Diagnoses:** header-based. Include the existing headers plus ASSESSMENT, IMPRESSION, FINAL DIAGNOSIS, DIAGNOSES. Split numbered lists and comma lists sensibly and dedupe.
- **Procedures:** from PROCEDURE, PROCEDURES, OPERATION, PROCEDURE PERFORMED headers. Extract text only; do not verify.
- **ANTI-LEAK RULE:** do not write any pattern that mirrors the injection templates ("Additional diagnosis noted", "Patient also underwent"), and do not special-case any injected value. The evaluation must be honest.
- Write at least 12 unit tests, including the blood-loss example (measurement, not dose).

### M3. Verification and retrieval (`verify.py`, `rxnorm_client.py`)
**RxNorm client** (`urllib`, cached to `dataset/cache/rxnorm_cache.json`): exact match first, then the approximate-match fallback. Then, after verifying endpoints in the docs, also fetch:
- canonical name, term type and ingredient (resolve brand names to ingredient first);
- therapeutic indications from RxClass (MED-RT `may_treat` and `may_prevent`). Coverage is incomplete; when nothing is returned, record `indications_status: "unknown"`, never "none";
- known product strengths for the ingredient, for dose plausibility.

**Per-medication result:** `exists`, `match_type`, `rxcui`, `canonical_name`, `ingredient`, `indications`, `known_strengths`.
- Dose check: if a dose is associated with the medication and `known_strengths` is non-empty but the dose value and unit is not among them, set flag `dose_not_a_known_strength` with `severity_hint: "soft"`. Document this as a weak signal (strengths describe products, not daily doses) and report how often it fires on CLEAN records.

**Per-diagnosis result:** top-3 ICD-10 candidates from the M1 index. Flag `no_confident_icd10_match` when the top score is below a threshold. Choose the threshold using only clean (non-injected) records and write down the procedure. Do not tune on injected records.

**Routing per entity:**
- `invalid`: failed the existence check (not in RxNorm, or no confident ICD match);
- `verified_needs_context_check`: exists, so it must go to Stage B.

**`evidence_text` per entity:** a compact plain-text block of the retrieved facts, ready to paste into an LLM prompt. Name the source of each fact (RxNorm, MED-RT, ICD-10-CM plus fiscal year). This is the RAG augmentation payload.

### M4. Pipeline and contract (`pipeline.py`, `contract.py`)
- `run_stage_a(record) -> dict` conforming to `INTERFACE_CONTRACT.md`. Keep every existing field. Only make ADDITIVE changes. Add:
  - `entities[]`: `{entity_type, text, span, routing, retrieval_context{..., evidence_text}}`
  - `stage_a_summary`: `{n_entities, n_invalid, n_forwarded_to_stage_b}`
  - `stage_a_version`
- Document every addition in `docs/CONTRACT_CHANGES.md` (the human forwards this to the teammate). Add a hand-written `validate_contract(output)` (no `jsonschema` dependency) and a JSON Schema file for documentation.
- Per-record try/except: a failure writes an error entry and processing continues.
- Provide `scripts/run_stage_a.py` (wrapper that fixes `sys.path`, so no PYTHONPATH tricks on Windows) with `--input` and `--output`. Output must be deterministic and ordered.
- Provide `scripts/warm_caches.py` (runs Stage A over the full dataset once to fill the RxNorm cache) and `scripts/make_sample.py` (writes `dataset/processed/sample_20_for_aws_test.json`: 20 records, mixed clean and injected, fixed seed).

### M5. AWS Lambda packaging
- `src/aws/lambda/stage_a_handler.py`, with entry point `lambda_handler(event, context)`:
  - triggered by an S3 object-created event; reads the JSON (array of records, or a single record) from the key;
  - ignores keys that do not start with `input/` or do not end in `.json`;
  - URL-decodes the key correctly (`+` and `%xx`);
  - writes results to `output/stage_a/<basename>.stage_a.json` in the SAME bucket. NEVER write under `input/`, because that would trigger itself in a loop;
  - logs structured summary lines to stdout for CloudWatch (record count, entity count, cache hit rate, elapsed time);
  - if `context.get_remaining_time_in_millis()` gets low, write partial output with `"truncated": true` instead of timing out.
- `scripts/build_lambda.py`: builds `build/stage_a_lambda.zip` with Python's `zipfile` (forward-slash paths, handler at zip root, package, reference JSONs, warmed cache). Print the size and fail if it exceeds 49 MB.
- `scripts/invoke_local.py`: builds a fake S3 event, swaps in a stub S3 client that reads and writes local files, runs the real handler, and checks the output matches the CLI output for the same input (parity test).
- Do not write scripts that need AWS credentials. The human does the AWS console steps.
- Add `build/` to `.gitignore`.

### M6. Evaluation (`src/backend/evaluation/evaluate_stage_a.py`)
Write `results/stage_a_metrics.json` and `results/stage_a_metrics.md`. Include:

1. **Record-level detection by Stage A alone** (positive = at least one `invalid` entity; also report a variant that includes soft flags): TP/FP/FN/TN, precision, recall, F1, false-positive rate on clean records, with 95% Wilson confidence intervals (the sample sizes are small).
2. **Per error type** (all four): n, Stage-A-detected recall, and **funnel coverage** = the injected entity was extracted AND either flagged or forwarded for context check. Funnel coverage is the key number for the two-stage design: how many errors even reach Stage B. Localization matters: the flag or entity must overlap the ground-truth `injected_entity`, not merely be any flag in the record.
3. **Audit table:** split `wrong_dosage` cases into drug-associated vs non-drug measurements using the M2 association logic. List misses with a reason code (`not_extracted`, `extracted_but_passed`, ...).
4. **Cost proxy:** entities forwarded to Stage B vs total entities; share of transcripts with zero forwarded entities.
5. Optional `--stage-b-results <path>`: accepts the teammate's Stage B output in contract format and computes end-to-end metrics with the same code, so integration later is a one-command re-run. Skip silently if not given.

**Expectation (do NOT tune to change it):** Stage A alone should have low detection recall on `wrong_medication` (every swap is a real drug) and on the fabricated types. That is an honest finding to report. Do not change the dataset, extraction rules, or thresholds to improve these numbers.

### M7. Tests and docs
- `pytest`: extraction cases; ICD search sanity (for example "type 2 diabetes mellitus" returns an E11.x code in the top 3, "chronic otitis media with effusion" returns the H65/H66 family); RxNorm client against recorded fixtures (no network in tests); contract validation on 10 sample outputs; the handler parity test.
- `docs/STAGE_A_REPORT.md`: architecture (RAG framing), data sources and licenses (RxNorm and RxClass from NLM; ICD-10-CM from CMS), the metrics tables copied from real runs, limitations and future work (regex-based extraction, lexicon collisions, soft dose signal, MED-RT coverage gaps, reference-data size), exact commands to reproduce everything, and a list of what remains for the human.
- `src/backend/stage_a/README.md`: short usage guide.

---

## 4. Definition of done (run each; paste real outputs into the report)

1. `pytest` passes.
2. `python scripts/build_reference_data.py` and `python scripts/warm_caches.py` succeed.
3. `python scripts/run_stage_a.py --input dataset/processed/mtsamples_with_errors.json --output results/stage_a_output.json` processes all 270 records in under about 5 minutes with a warm cache, with zero contract-validation errors.
4. `python src/backend/evaluation/evaluate_stage_a.py` writes both metrics files.
5. `python scripts/build_lambda.py` produces a zip under 49 MB, and `python scripts/invoke_local.py` passes the parity check.
6. `docs/STAGE_A_REPORT.md` and `docs/CONTRACT_CHANGES.md` exist and are accurate.

---

## 5. Out of scope. Do not do these

- No LLM or paid API calls of any kind.
- No Stage B, dashboard, DynamoDB, SNS, Cognito, or API Gateway.
- Do not touch anything under `src/frontend/` or the teammate's code or branch.
- Do not fabricate or estimate metrics, file sizes, or runtimes.
