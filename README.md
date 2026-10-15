# EHR Compliance Monitor

A research prototype for reviewing AI-generated clinical documentation against extracted entities, terminology references, and an optional trusted-source transcript. It returns review signals for a qualified human; it is not a clinical decision support device, does not diagnose or recommend treatment, and does not write to an EHR.

> **Data and use restriction:** Use synthetic data only. The current AWS environment is a short-retention classroom demo, not a production clinical service. Do not submit identifiable or real patient information. No HIPAA, GDPR, or other regulatory compliance certification is claimed.

## Project documentation

- [High-level design (HLD)](docs/HLD.md) — system boundaries, components, flows, security, lifecycle, resilience, and production-readiness gaps.
- [Low-level design (LLD)](docs/LLD.md) — routes, validation, job persistence/state, FHIR conversion, module responsibilities, configuration, and failure behavior.
- [AWS deployment report, 16 October 2026](docs/AWS_DEPLOYMENT_REPORT_20261016.md) — account-side demo configuration and checks performed.
- [AWS deployment procedures](docs/AWS_DEPLOYMENT_README.md) — operational deployment guide for the existing demo resources.
- [Interface contract](docs/INTERFACE_CONTRACT.md) — detailed pipeline request/result contract.
- [Stage A report](docs/STAGE_A_REPORT.md) and [Stage B report](docs/STAGE_B_LLM_REPORT.md) — pipeline design and evaluation context.

## Current deployment snapshot

As of 16 October 2026, the AWS demo runs in `ap-south-1`. The local dashboard at `http://localhost:3000` authenticates with Cognito authorization code + PKCE and calls API Gateway. API Gateway protects job, upload, history, overview, review, and analysis routes with a JWT authorizer; `GET /health` is public. API submission uses encrypted SQS, a Lambda worker, the processor Lambda, and encrypted DynamoDB job metadata. Job data, queue messages, project S3 input/output prefixes, and processor/worker Lambda logs have one-day retention settings. Expiration and deletion are asynchronous; this does not guarantee immediate erasure or cover every AWS subsystem.

The deployed environment is intentionally loopback-only: Cognito callback/logout and API CORS permit `http://localhost:3000/`; there is no hosted frontend origin. Configuration and verification detail are recorded in the deployment report. The system has not had a production readiness, clinical safety, penetration, disaster recovery, or regulatory assessment.

## Capabilities

- Two-stage pipeline: deterministic clinical entity extraction and terminology retrieval (Stage A), then deterministic review or optional Gemini-based contextual review (Stage B).
- Local demonstration mode with a Node dashboard/API, Python pipeline process, and single-process JSON job history.
- Authenticated AWS demo mode with asynchronous jobs, owner-scoped history/review, queue retries, and short retention.
- Trusted-source comparison when an optional source transcript is supplied and the configured LLM review completes successfully.
- Browser-side Synthea FHIR R4 chart-context importer. One single-patient JSON Bundle is summarized locally; the FHIR file itself is not posted to the API.
- Synthetic importer evaluation corpus and edge-case tests. These assess parsing behavior only, not clinical accuracy.

## Local setup

### Prerequisites

- Node.js 18 or later and npm.
- Python 3.10 or later (the Lambda deployment uses Python 3.12).
- The checked-in local pipeline primarily uses the Python standard library; AWS runtime paths use `boto3` supplied by Lambda. The repository currently has no `requirements.txt` or `pyproject.toml` dependency manifest.
- Optional: a server-side Gemini API key for LLM review. Without it, set Stage B to deterministic-only mode.

### Install and run

```powershell
npm install
python -m venv .venv
\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
npm start
```

Open `http://localhost:3000`. Configure `.env` for either local mode or the authenticated cloud demo as described below. Never commit `.env` or put model/AWS credentials in frontend configuration. The app’s Python code uses standard-library modules locally; install additional packages only if a selected optional integration requires them.

### Local vs cloud demo configuration

- **Local:** leave `EHR_API_BASE_URL` blank and Cognito values blank. The dashboard uses the local Node service and local JSON job store under `.ehr-local-data/`. This mode is single-process and not suitable for shared or multi-instance use.
- **AWS demo:** set the API base URL and public Cognito domain/client ID plus localhost callback/logout values in `.env`. The Cognito app client is public and uses PKCE; do not add a client secret. Sign up with a demo account and submit synthetic content only. The exact account configuration is in the deployment report.
- `GEMINI_API_KEY` is server-side only. The browser receives the public API base URL and Cognito settings, never the Gemini key or AWS credentials.

## Using the dashboard

1. Open **New verification** and provide the candidate AI transcription by pasting text, uploading `.txt`/`.json`, or importing a FHIR chart.
2. Optionally provide a trusted source. The source is compared only when an LLM Stage B review succeeds; it is not itself checked for clinical correctness.
3. Run verification and inspect review signals, severity, evidence, source discrepancies, and completeness context.
4. Record a reviewer action in the job detail. Review actions are audit events; they do not alter an EHR.
5. Use History and Overview to inspect the signed-in user's unexpired cloud records or local demo records.

### Transcript file input

`.txt` files are treated as candidate text. `.json` must be one object with `transcript` or `transcription` string and may include `sourceTranscript` or `source_transcript`. The backend limits each transcript field to 100,000 characters; cloud queue submissions are limited to 900 KB combined payload.

### Synthea FHIR R4 input

On **New verification**, choose one Synthea FHIR JSON Bundle and select **Import chart context**. The browser requires exactly one Patient and converts supported Patient, Condition, AllergyIntolerance, MedicationRequest/Statement/Administration, Observation, Encounter, Procedure, Immunization, and CarePlan resources into a readable summary in the trusted-source field. If the candidate is empty, it is initialized with that summary so it can be edited. This generated summary is not an original note; review it before use.

Limits: 10 MB input, 20,000 resources, and 100,000 summary characters. Malformed, empty, multi-patient, invalid Bundle type, cross-patient-reference, and unsupported-only bundles are rejected. Unsupported resource types in a usable bundle are skipped and counted. Bulk NDJSON is not supported. Get synthetic examples from the [Synthea downloads page](https://synthetichealth.github.io/synthea/) or generate records with [Synthea](https://github.com/synthetichealth/synthea); import one patient JSON file at a time.

## Development and verification

```powershell
npm run check
npm run test:local
npm run test:fhir
npm run build:frontend
```

The FHIR evaluation corpus is [dataset/evaluation/synthea_fhir_cases.json](dataset/evaluation/synthea_fhir_cases.json). `npm run test:fhir` checks acceptance/rejection labels, small and large Bundles, malformed JSON, BOM handling, patient-reference isolation, unsupported resources, missing/choice values, and size limits. This suite is not a model benchmark and does not establish clinical sensitivity, specificity, or safety. Python pipeline tests and evaluation scripts are documented in the Stage A/Stage B reports and their command-line help.

## Repository map

| Path | Responsibility |
|---|---|
| `src/frontend/` | Dashboard, browser auth client, and FHIR summary importer |
| `src/backend/server.js` | Local static server, local API adapter, local job submission |
| `src/backend/cloud_jobs.py` | Authenticated cloud API, DynamoDB job/history/review logic, SQS worker processing |
| `src/backend/web_api.py` | Shared transcript validation and Stage A/Stage B orchestration |
| `src/backend/stage_a/` | Extraction, verification, and terminology retrieval |
| `src/backend/stage_b/` | Deterministic/LLM review and response schema |
| `src/aws/lambda/` | API/processor and SQS worker Lambda handlers |
| `dataset/reference/` | Local terminology/reference indexes |
| `dataset/evaluation/` | Synthetic FHIR importer accept/reject corpus |
| `tests/` | Local job-store and FHIR importer tests |
| `docs/` | HLD, LLD, interface contract, deployment and evaluation reports |

## Data handling and security boundaries

- Cloud API ownership comes from the authenticated JWT `sub` claim. Job reads, history, overview, and review are scoped by owner in application code.
- Transcript and source text are sent through the browser, API, queue, and processor for analysis. The cloud DynamoDB item stores result/findings and metadata, not raw transcript text. Queue messages necessarily contain the text while processing. The legacy S3 upload path stores input and audited output objects; those prefixes have a one-day lifecycle rule in the demo and must remain private.
- Local mode writes job history/results to `.ehr-local-data/jobs.json`; use synthetic data and protect/remove this directory when finished.
- CloudWatch application logs are intended to contain event names, error types, counters, and durations, not transcript text. Do not enable prompt persistence for ordinary use.
- The FHIR parser runs in the user's browser and submits only the generated/edited text through existing APIs. Browser-side parsing is not a replacement for server-side authorization or a clinical FHIR conformance validator.
- External terminology lookups and Gemini calls may receive clinical text/terms in their respective pipeline paths. The current demo is not approved for real patient information.

## Limitations and production readiness

This is a research/class project. It has no validated clinical dataset, external clinical evaluation, formal threat model, independent security review, disaster recovery exercise, service-level objective, or production support process. Terminology matching and automated review can miss errors or raise false positives; a qualified reviewer must judge every signal. The system does not ensure a complete, correct, or safe clinical record.

Before any production use, the owners would need (at minimum) formal clinical safety and privacy review, a regulatory determination, validated intended-use and acceptance criteria, independent clinical evaluation, approved vendor/data-processing terms, threat modeling and penetration testing, production identity and reviewer controls, key/secret lifecycle, tested backup/restore and incident response, measurable retention/deletion guarantees, capacity/cost controls, and a change-controlled release process. This README does not assert those controls exist.

