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
- AWS Lambda starter for the S3 ingestion stage in `src/aws/lambda/`.

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
