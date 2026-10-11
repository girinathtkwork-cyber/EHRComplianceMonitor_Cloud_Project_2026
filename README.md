# EHR Transcription Safety Gate

A demonstrable clinical transcription review prototype. It compares an AI-generated transcript against a trusted source before a record is finalised, returning evidence-backed safety findings for dosage changes and unsupported medications, diagnoses, or procedures.

## Run the working demo

```powershell
npm.cmd run check
npm.cmd start
```

Open `http://localhost:3000`, select **Load MTSamples demo case**, and the dashboard loads a labelled evaluation case from `dataset/processed/mtsamples_with_errors.json`. The result shows the injected error, its severity, source evidence, detected entities, and RxNorm provenance.

No packages need to be installed for this local prototype. The server uses Node's built-in modules.

## What is implemented

- Interactive browser dashboard for paste-and-review workflow.
- Provenance comparison between a candidate transcript and a trusted source.
- Critical detection of dosage changes between source and candidate.
- High-severity detection of medication, diagnosis, and procedure claims present only in the candidate.
- Runtime medication terminology loading from the existing MTSamples/RxNorm verification output rather than an embedded drug list.
- Configurable review rules in `src/backend/config/review-rules.json`.
- Live evaluation endpoint and dashboard table that calculate recall from all labelled MTSamples error-injection records at request time.
- MTSamples error-injection pipeline and labelled evaluation dataset already present in `dataset/`.
- Deterministic Stage B audit pass that consumes Stage A evidence and produces final clinician-review findings.
- AWS Lambda package path for S3 ingestion, Stage A verification, Stage B audit, and S3 result output.

## Run the full Stage A + Stage B pipeline

```powershell
python scripts/run_end_to_end.py --input dataset/processed/sample_20_for_aws_test.json --output results/end_to_end_output.json
```

Build and locally test the Lambda package:

```powershell
python scripts/build_lambda.py
python scripts/invoke_local.py
```

## Safety boundary

This is a **review gate**, not a medical diagnosis, prescribing, or autonomous EHR-writing system. A clinical reviewer must confirm every finding. The strongest signals require a trusted source transcript; terminology lookup alone cannot prove that a valid medicine or diagnosis is correct in a particular patient record.

## Project structure

```text
dataset/             MTSamples cleaning, error injection, entity extraction, RxNorm verification
src/backend/         Node review engine, configurable rules, local API server
src/frontend/        Showable review dashboard
src/aws/lambda/      S3-triggered AWS ingestion starter
architecture/        Deployment and data-flow documentation
docs/                Methodology, evaluation, and compliance documentation
```

See `docs/STAGE_B_AND_AWS_HANDOFF.md` for the final pipeline and AWS handoff notes.
See `docs/AWS_DEPLOYMENT_README.md` for the step-by-step AWS Console deployment guide.

## Completion log

Last verified on 11 October 2026.

- `npm.cmd run check` passed for the local Node backend.
- `python -m pytest -q` passed: 50 tests.
- `python scripts/run_end_to_end.py --input dataset/processed/sample_20_for_aws_test.json --output results/end_to_end_output.json` passed: 20 records, 40 Stage B findings.
- `python scripts/build_lambda.py` produced `build/stage_a_lambda.zip` successfully.
- `python scripts/invoke_local.py` passed the local Lambda parity check against the CLI pipeline.
- Refreshed outputs were written to `results/end_to_end_output.json` and `output/stage_b/sample_20_for_aws_test.audited.json`.

Stage B LLM/RAG update on 11 October 2026:

- Added `stage_a_only`, `llm_plain`, and `llm_rag` Stage B modes with deterministic fallback.
- `python -m pytest -q` passed: 57 tests.
- `python scripts/run_end_to_end.py --mode llm_rag --input dataset/processed/sample_20_for_aws_test.json --dry-run` passed: 20 records, zero API calls.
- `python scripts/run_ablation.py --dry-run` passed for all 270 records and wrote `results/ablation_metrics.*`, `results/ablation_predictions.jsonl`, and `results/split.json`.
- `python scripts/build_lambda.py` and `python scripts/invoke_local.py` passed after adding the LLM/RAG files.
- Real Gemini evaluation still requires `GEMINI_API_KEY`; no live LLM metrics were invented.

Remaining manual work before a live AWS demo:

- Deploy the built Lambda zip to AWS and connect it to the real S3 bucket trigger.
- Confirm DynamoDB, SNS, and Cognito resources in the AWS account if the final demo requires managed cloud persistence, alerts, and login.
- Keep the system as a reviewer-assist prototype; it should not auto-write clinical records.

