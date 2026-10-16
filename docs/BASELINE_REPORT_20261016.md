# EHR Compliance Monitor baseline — 16 October 2026

> Historical pre-deployment snapshot. For the current AWS state, see [AWS deployment report](AWS_DEPLOYMENT_REPORT_20261016.md).

## Architecture found

- The browser uses a framework-free HTML/CSS/JavaScript dashboard in `src/frontend/`.
- Local mode is a Node HTTP server bound to `127.0.0.1:3000`, which invokes a Python JSON adapter as a child process.
- The Python adapter runs deterministic Stage A extraction and terminology retrieval, then Stage B in deterministic, Gemini-only, or Gemini-plus-retrieval mode. Stage B validates Gemini output against its hand-written schema and uses a visibly labeled deterministic fallback on failure.
- The AWS path is an existing public API Gateway HTTP API → Python 3.12 Lambda (`ehr-compliance-stage-b`) → private S3 input/output objects. S3 `input/*.json` object creation invokes the same Lambda. S3 conditional lock objects suppress duplicate event work. There is no separate worker process.
- The existing API contract is documented in `docs/INTERFACE_CONTRACT.md`; the front-end response adapter omits the raw transcript from direct analysis responses.

## Baseline status

- Git working tree was clean before this task.
- Existing tests: **100 passed** (`STAGE_B_MODE=stage_a_only`, `python -m pytest -q`).
- JavaScript checks: **passed** (`npm.cmd run check`, plus `node --check src/frontend/config.js`).
- Deterministic 20-record end-to-end run: **20/20 completed**, no Stage A or Stage B failures or fallbacks.
- Live API `GET /health`: **200**, reports Stage A `2.0.0`, supported Stage B modes, and S3 upload configured. This proves the endpoint responds, not that every cloud dependency or Gemini request works.
- Gemini live call was not made.

## Incomplete or material risks

- Direct browser analysis runs synchronously. Local file jobs are kept in an in-memory `Map`; there is no durable local history, dashboard metrics API, reviewer workflow, or completeness review.
- Existing API Gateway routes have `AuthorizationType=NONE`. This makes the API publicly callable. Do not send real patient data to this prototype.
- The Lambda role's exact effective access and deployed code parity have not yet been audited. The Lambda has a 900-second timeout while API Gateway integrations have a 30-second timeout; a direct analysis request can exceed the HTTP integration limit.
- The S3 bucket has public access blocked and AES256 server-side encryption, but has no lifecycle policy and versioning is not enabled. Retention and deletion behavior need an owner decision.
- S3 job objects are not an indexed history store. There is no DynamoDB table, SQS queue, EC2 instance, or CloudWatch dashboard in `ap-south-1` for this application. The Lambda log group exists without a retention period.
- There is a second project-related log processor Lambda, and a legacy `medical-transcript-processor` Lambda. Neither is needed by the inspected request path.
- No deployment/IaC definition in the repository currently manages the API, Lambda, S3, IAM, or frontend hosting resources.
- Stage A's ICD index intentionally returns candidates rather than confirmed diagnosis codes. Its diagnosis header extraction can still include too much text for some unstructured same-line sections; extraction signals remain review aids.

## Environment configuration names

The application reads `PORT`, `PYTHON_EXECUTABLE`, `PIPELINE_TIMEOUT_MS`, `STAGE_B_MODE`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_RPM`, `PERSIST_STAGE_B_PROMPTS`, `TRANSCRIPTS_BUCKET`, `ALLOWED_ORIGINS`, and `EHR_API_BASE_URL` as documented in `.env.example` and `README.md`. Secret values were not read into this report. The Lambda currently has environment keys for Stage B mode, Gemini key/model, and transcript bucket; values were not queried.

## Incremental implementation plan

1. Preserve Stage A/Stage B and add a conservative completeness review that marks absence as `not_mentioned` unless supported context makes a field potentially expected.
2. Add a durable, local-demo job/history/review API that stores result metadata only (not transcript text), with append-only review audit entries and no invented reviewer identity.
3. Connect the existing frontend to that API and expose overview, submission, history, details, review, and health states. Keep direct `/api/analyse` compatibility.
4. Add tests for new behavior and rerun the full offline suite.
5. Prepare, but do not apply, AWS infrastructure changes until the public unauthenticated endpoint and clinical-data retention/authentication controls are resolved. Proposed cloud scope: private DynamoDB table with queryable job history, SQS main/DLQ, least-privilege Lambda processing, CloudWatch retention, and an API authorization plan.

## AWS inventory (read-only, `ap-south-1`)

- Account identity is configured and CLI calls succeeded. Account identifiers are intentionally omitted from this report.
- Existing API: `ehr-compliance-api`, HTTP protocol, `$default` stage; routes for health, analysis, upload, and job status point to `ehr-compliance-stage-b`. All inspected routes use no authorization.
- Existing processing Lambda: `ehr-compliance-stage-b`, Python 3.12, `stage_a_handler.lambda_handler`, 900-second timeout, 1024 MB, no VPC config. Its environment variable names were inspected without revealing values.
- Existing bucket `ehr-compliance-monitor-yogiri-2026`: `ap-south-1`, all four public-access-block settings enabled, AES256 server-side encryption, S3 input-prefix notification to the Lambda. No lifecycle policy was configured. Versioning status is not enabled (empty status).
- No EC2 instances, SQS queues, or DynamoDB tables were found in the configured region. No CloudWatch dashboard was found. The Lambda log group exists with no retention configured.
- No AWS changes were made during baseline inspection.

## Deployment decision

The existing backend is Lambda-based and already has a live API. Adding EC2 would duplicate compute without a repository requirement, so the current Lambda path should be retained. SQS and DynamoDB are absent and would materially expand the public clinical-data path. The implementation will be prepared locally first; cloud creation or mutation should follow only after a concrete review of authentication, retention, IAM, and existing deployed-code parity.
