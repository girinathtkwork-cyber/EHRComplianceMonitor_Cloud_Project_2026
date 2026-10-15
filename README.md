# EHR Transcription Safety Gate

Clinical-transcription review prototype. It extracts entities, checks terminology references, and returns evidence-backed review signals before a clinician finalises an EHR record. It is not a diagnostic system and never writes to an EHR.

**Status checked 13 October 2026:** the local dashboard and Python verification pipeline are connected and tested. The project owner reports that the existing Lambda and S3 event configuration below were verified in the AWS Console. This workspace cannot independently inspect or compare the deployed ZIP, IAM policies, or API resources because AWS CLI credentials and console access are unavailable. API Gateway shows no API in the owner's selected `ap-south-1` console scope; no endpoint is configured for the dashboard.

## Current and target data flow

```mermaid
flowchart LR
  subgraph local["Verified local workflow"]
    B1[Browser dashboard] -->|POST /api/analyse or /api/upload| N[Node local web server]
    N --> P[Python web API adapter]
    P --> A[Stage A: entity extraction and RxNorm/ICD retrieval]
    A --> R[Stage B: deterministic audit or Gemini RAG]
    R -->|Structured findings; no transcript echoed by direct API| B1
    N -->|Local upload job store only| B1
  end

  subgraph aws["AWS flow supported by code; account wiring unverified"]
    B2[Hosted dashboard] -->|POST /api/analyse| G[API Gateway]
    G -->|Synchronous Lambda proxy| L[Existing Python Lambda handler]
    L --> A2[Stage A + packaged references / RxNorm]
    A2 --> R2[Stage B + optional server-side Gemini]
    R2 -->|JSON review result| G
    G --> B2
    B2 -->|POST /api/upload: JSON or text file| G
    G -->|PutObject input/<job-id>.json| S3[(Existing private S3 bucket)]
    S3 -->|input/*.json event| L
    L -->|audited result + job status| S3
    B2 -->|GET /api/jobs/{job-id}| G
    G -->|GetObject result/status| S3
  end
```

### Component responsibilities

- **Dashboard** ([src/frontend/](</D:/VIT/3_rd_yr/5th_sem/Cloud Aws/EHRComplianceMonitor_Cloud_Project_2026/src/frontend/>)): processes only text entered by the user or a JSON/TXT file they select; it does not preload bundled demo transcripts. It displays progress, findings, severity, evidence, Stage A routing, source-comparison excerpts, and Stage B fallback status. `config.js` centralises the API base URL.
- **Local web server** ([server.js](</D:/VIT/3_rd_yr/5th_sem/Cloud Aws/EHRComplianceMonitor_Cloud_Project_2026/src/backend/server.js>)): serves the existing static UI on loopback only, proxies requests to the same Python pipeline, and keeps local upload results in memory. A local upload is **not** sent to AWS or S3.
- **API adapter** ([web_api.py](</D:/VIT/3_rd_yr/5th_sem/Cloud Aws/EHRComplianceMonitor_Cloud_Project_2026/src/backend/web_api.py>)): validates transcript input, invokes Stage A and Stage B, and returns a stable result without echoing transcript text in direct API responses.
- **Stage A** ([stage_a/](</D:/VIT/3_rd_yr/5th_sem/Cloud Aws/EHRComplianceMonitor_Cloud_Project_2026/src/backend/stage_a/>)): extracts clinical entities and retrieves RxNorm/ICD-10-CM evidence. Reference files are packaged with the Lambda; RxNorm lookup can call the public RxNorm API if a local cache does not satisfy it.
- **Stage B** ([stage_b/](</D:/VIT/3_rd_yr/5th_sem/Cloud Aws/EHRComplianceMonitor_Cloud_Project_2026/src/backend/stage_b/>)): when configured, sends the candidate transcript and extracted entities to Gemini in `llm_plain` or `llm_rag` mode. `llm_rag` includes retrieved RxNorm/ICD-10-CM evidence in the prompt (retrieval-augmented prompting, not vector search). If a trusted source transcript is provided, Gemini also compares both texts for material omissions, additions, and contradictions. Returned excerpts are validated against their respective inputs before display. Deterministic-only and fallback runs explicitly report source comparison as not performed. Existing deterministic fallback is surfaced as `stage_b.llm_status = "fallback"`.
- **Lambda handler** ([stage_a_handler.py](</D:/VIT/3_rd_yr/5th_sem/Cloud Aws/EHRComplianceMonitor_Cloud_Project_2026/src/aws/lambda/stage_a_handler.py>)): the owner reports an existing `ehr-compliance-stage-b` Lambda in `ap-south-1`, Python 3.12, handler `stage_a_handler.lambda_handler`. Source supports S3 events and API Gateway routes. Deployed package/version and environment remain uninspected. The separate local [medical-transcript-processor.js](</D:/VIT/3_rd_yr/5th_sem/Cloud Aws/EHRComplianceMonitor_Cloud_Project_2026/src/aws/lambda/medical-transcript-processor.js>) is a legacy handler that throws instead of processing; its deployed function role/configuration is not known.
- **Deployment package** ([build_lambda.py](</D:/VIT/3_rd_yr/5th_sem/Cloud Aws/EHRComplianceMonitor_Cloud_Project_2026/scripts/build_lambda.py>)): builds `build/stage_a_lambda.zip` with the Lambda handler, backend modules, prompts, and reference data. Application imports use Python standard library plus runtime-provided `boto3`; deployed SDK support for conditional S3 writes must be checked before deployment.
- **S3**: the owner reports bucket `ehr-compliance-monitor-yogiri-2026` with an all-object-create notification filtered to `input/` and `.json`, targeting `ehr-compliance-stage-b`. Source writes uploads under `input/` and results/status under `output/stage_b/`. Bucket security settings and deployed role permissions remain unverified. S3 is the job store; no DynamoDB or queue is introduced.
- **Duplicate and failed events**: S3 processing uses conditional lock-object writes in `output/stage_b/`, with a lease longer than Lambda's maximum runtime, ETag-conditional reclaim of failed/expired locks, terminal status updates, and result/status checks to suppress completed duplicates. Polling trusts the persisted status before returning any result. The isolated test stubs conditional S3 writes; verify the deployed SDK supports them and test against the bucket before production. S3 event delivery remains at-least-once.
- **Gemini**: called only from server-side Python. The key must not be placed in `src/frontend/`, `config.js`, or any browser bundle.

### Dashboard-to-AWS connection

The browser does not connect to S3 or Lambda directly. It sends HTTP requests to an API: in local development, same-origin `/api/...` requests go to the Node server on `127.0.0.1:3000`, which invokes the Python pipeline. In AWS, the hosted dashboard must be configured with the verified API Gateway base URL; API Gateway must route requests to the existing Lambda, and that Lambda uses its IAM execution role to access S3. The AWS account resources and endpoint are not verified in this workspace, so the dashboard is **not yet connected to deployed AWS services**.

The repository still contains datasets and synthetic test fixtures for offline evaluation and automated tests. They are not loaded into the dashboard or returned by the dashboard API.

## AWS inventory

No AWS CLI is installed here, and there is no CloudFormation/SAM/CDK/Terraform deployment definition or AWS resource manifest in the repository. The following are repository findings—not evidence of deployed account resources.

The project owner reports API Gateway displays **“Create an API to begin”** with `ap-south-1` selected. No API is visible in the console scope containing the reported Lambda and bucket. The account ID was not shared. AWS CLI is not installed and no credentials are available in this workspace, so this remains owner-reported rather than independently verified.

| Service | Repository evidence and purpose | Deployment status / dependencies | Website integration |
|---|---|---|---|
| IAM | Lambda uses its existing execution role through the AWS SDK default credential chain. | **Role name and policies unverified.** Owner must verify least-privilege S3 access and CloudWatch logging. Never use root credentials or static AWS keys. | Reuse the role; ensure scoped access to bucket prefixes and API Gateway permission to invoke the existing function. |
| Lambda | [stage_a_handler.py](</D:/VIT/3_rd_yr/5th_sem/Cloud Aws/EHRComplianceMonitor_Cloud_Project_2026/src/aws/lambda/stage_a_handler.py>) is the repository handler; package builder creates `build/stage_a_lambda.zip`. The local `medical-transcript-processor.js` throws a legacy/no-op error. | **Owner-reported Console configuration:** `ehr-compliance-stage-b`, Python 3.12, handler `stage_a_handler.lambda_handler`, `ap-south-1`. Deployed code/version, environment, role, timeout, and permissions remain uninspected. The deployed role of `medical-transcript-processor` is unknown. | Reuse `ehr-compliance-stage-b` and preserve its S3 trigger. Inspect deployed code before updating it. Do not route API Gateway to the legacy function. |
| API Gateway | Lambda source supports `GET /health`, `POST /api/analyse`, `POST /api/upload`, `GET /api/jobs/{jobId}`, and `OPTIONS`. | **Owner reports no API visible in selected `ap-south-1` console scope.** Account ID and other regions are unverified; no invoke URL or route configuration was provided. | After confirming project account/region, create an HTTP API only if no existing API exists; wire routes to existing Lambda and configure CORS, invoke permission, and approved authorization. |
| S3 | Lambda source reads `input/*.json`, writes `output/stage_b/*.audited.json` and `.status.json`; upload API requires `TRANSCRIPTS_BUCKET`. | **Owner-reported Console configuration:** bucket `ehr-compliance-monitor-yogiri-2026`; all object-create events filtered by `input/` + `.json` target `ehr-compliance-stage-b`. Bucket security and deployed function permissions are not independently verified. | Reuse the bucket and existing notification; do not create a duplicate. Verify encryption, retention, private access, and scoped role permissions. Output prefix is outside the input filter. |
| DynamoDB | No application code or table definition. | **Not present in repository.** Not needed for the current S3-backed job/result polling design. | None. |
| CloudWatch | Lambda emits aggregate counts and error types without transcript content. | **Log group, metrics, alarms, retention, and role permissions are unverified.** | Verify the existing Lambda log group and safe retention/alarms; do not log transcript bodies or object keys containing identifiers. |
| Secrets Manager / Parameter Store | No SDK call, secret ARN, parameter name, or integration is present. Gemini client reads `GEMINI_API_KEY` from server environment. | **Not configured in repository or verified in AWS.** | For this code path set the key only as a protected Lambda environment variable; a Secrets Manager integration would require explicit code and role permission and is not currently implemented. |
| Cognito | No auth client, pool, authorizer, or config. The frontend has no sign-in/token flow. | **Not present in repository; account status unverified.** | Configure the existing approved authorizer and its frontend credential flow before exposing transcript routes. Do not send real clinical data to an unauthenticated deployment. |
| ECS / EC2 / App Runner | No service/task/instance/deployment definitions. | **No repository evidence.** | Not used by the implemented flow. |
| Amplify / S3 website / CloudFront | Static HTML/CSS/JS only; no Amplify, bucket website, or distribution configuration. | **No hosting resource or frontend URL is recorded.** | Host the existing frontend with the account’s already-approved hosting service; set its API origin in [config.js](</D:/VIT/3_rd_yr/5th_sem/Cloud Aws/EHRComplianceMonitor_Cloud_Project_2026/src/frontend/config.js>) and add that exact origin to Lambda/API CORS. |
| Step Functions / SQS | No workflow or queue code/configuration. | **Not present.** S3 event invocation is the current asynchronous mechanism. | Not needed unless measured workload/timeout limits make the existing S3-triggered Lambda insufficient. |
| RDS | No SQL client, schema, or database config. | **Not present.** | Not used. |
| CloudFormation / SAM / CDK / Terraform | No templates or deployment state; `infrastructure/modules/` is empty. | **No IaC deployment method identified.** | Check with the AWS owner for the existing deployment method before introducing one. |
| Other external services | RxNorm public REST APIs provide terminology retrieval; Google Gemini API is optional Stage B. | Code dependencies are present; live network credentials/availability are not verified. | Lambda needs outbound access to these APIs. Gemini credentials remain server-side. |

### Inventory classification

1. **Configured and reported Console-verified by the owner:** `ehr-compliance-stage-b` (Python 3.12, `stage_a_handler.lambda_handler`, `ap-south-1`) and bucket `ehr-compliance-monitor-yogiri-2026` with existing all-object-create notification filtered to `input/` + `.json` and targeting that function. This workspace has not independently read the account.
2. **Configured but not independently verified:** deployed Lambda package/version/environment/role policies, bucket security settings, exact runtime/role of `medical-transcript-processor`, and the API Gateway absence beyond the owner's selected console scope.
3. **Partially implemented:** API Gateway-compatible routes, S3 dashboard upload, S3 event processing, status/result polling, and CORS handling are in source; AWS resources and security settings are not wired or verified.
4. **Missing and necessary for cloud use:** API Gateway routes/integration/invoke permission and URL; deployed Lambda/source match; Lambda role permissions; deployed `TRANSCRIPTS_BUCKET`; frontend origin; authorization decision and matching frontend authentication if required. Verify bucket security.
5. **Optional future improvements:** DynamoDB (only if S3 status storage proves insufficient), SQS/Step Functions for larger/longer batches, CloudWatch alarms, Cognito if selected as the authorizer, and formal IaC after the existing deployment method is established.

**Do not create duplicate AWS resources based on this repository alone.** The AWS Console owner must first establish whether Girinath’s API, Lambda, bucket, role, hosting, or auth resources already exist. No resource identifier or URL is safe to claim from current evidence.

## Dashboard workflows

- **Paste workflow:** browser `POST /api/analyse` → backend validates and runs Stage A + Stage B → structured result returned. This does not store a transcript in S3.
- The paste field accepts plain text or one JSON object with `transcript`/`transcription` and an optional `sourceTranscript`/`source_transcript`; invalid JSON and JSON arrays are rejected with a useful message.
- **File workflow:** select one `.txt` or `.json` file, then `POST /api/upload`. In local development it runs locally and reports that it did not use S3. In AWS the Lambda writes a normalised JSON object to the configured bucket’s `input/` prefix and returns a job ID. The S3 notification invokes the Lambda; the browser polls `GET /api/jobs/{jobId}` until the corresponding result/status object exists.
- **Supported JSON file:** one object with `transcription` or `transcript` string and optional `source_transcript` or `sourceTranscript` string. A `.txt` file is treated as the candidate transcript. Maximum upload size in the UI: 800 KB.
- **Source comparison:** a non-empty trusted source is compared only when an LLM Stage B request succeeds. The response includes `stage_b.source_comparison.status` and a `discrepancies` array with exact, validated source/candidate excerpts. `stage_b_summary.source_comparison` is true only when that comparison completed. The comparison is for textual fidelity, not clinical correctness; the trusted source itself may also contain errors. No source, deterministic-only mode, or LLM fallback means no comparison was completed.
- **Direct API request:**

  ```json
  {
    "transcript": "DIAGNOSIS: hypertension. MEDICATIONS: aspirin 81 mg daily.",
    "sourceTranscript": "DIAGNOSIS: hypertension. MEDICATIONS: aspirin 81 mg daily."
  }
  ```

- **Direct response (illustrative shape; findings depend on the actual input):**

  ```json
  {
    "verification_status": "completed",
    "stage_a_summary": {"n_entities": 2, "n_invalid": 0, "n_forwarded_to_stage_b": 2},
    "entities": [],
    "stage_b_status": "completed",
    "stage_b": {
      "mode": "stage_a_only",
      "llm_status": "skipped",
      "source_comparison": {"status": "not_provided", "discrepancies": []}
    },
    "stage_b_summary": {
      "n_findings": 0,
      "critical": 0,
      "high": 0,
      "medium": 0,
      "low": 0,
      "source_comparison": false
    },
    "stage_b_findings": []
  }
  ```

- **Upload response:** `202 {"jobId":"<uuid>","status":"processing","storage":"s3"}` in AWS; local mode reports `"storage":"local"`. Poll the job route for `processing`, `completed`, `partial`, or `failed`. The Lambda S3 notification must be configured for polling to complete.
- **Health:** `GET /health`. It reports code health, not account-side S3/API readiness.

## Environment and secrets

| Variable | Required? | Used by / purpose |
|---|---|---|
| `PORT` | Optional | Local Node web-server port; defaults to `3000`. |
| `PYTHON_EXECUTABLE` | Optional | Local Node-to-Python executable; defaults to `python` on Windows and `python3` elsewhere. |
| `PIPELINE_TIMEOUT_MS` | Optional | Local subprocess timeout; defaults to `240000`. |
| `STAGE_B_MODE` | Optional | `stage_a_only`, `llm_plain`, or `llm_rag`; defaults to `llm_rag` only when a key is present, otherwise `stage_a_only`. |
| `GEMINI_API_KEY` | Required only for live Gemini | Python backend/Lambda only. Do not add to frontend files, commit it, or put it in a browser build. If a local credential has been left in a plaintext `.env`, rotate it if it is still valid. |
| `GEMINI_MODEL` | Optional for Gemini | Model name; current default is `gemini-3.8-flash`. |
| `GEMINI_RPM` | Optional for Gemini | Client rate limit; defaults to `15`. |
| `PERSIST_STAGE_B_PROMPTS` | Optional debugging only | Defaults off. When set to `true`, full transcript-bearing prompts are written locally under `results/prompts/` or in Lambda `/tmp`; disable after debugging and manage those files under the applicable retention policy. |
| `TRANSCRIPTS_BUCKET` | Required for AWS dashboard file uploads and polling | Existing private S3 bucket name. Not needed for local workflow or direct-paste API calls. |
| `ALLOWED_ORIGINS` | Required for cross-origin browser access | Exact comma-separated frontend origins allowed by the Lambda CORS response. |
| `EHR_API_BASE_URL` | Required when the static frontend is hosted separately from its API | Build-time public API Gateway base URL; not a credential. Set it before `npm run build:frontend`. |

The root `.gitignore` ignores `.env` and `.env.*` while allowing `.env.example`; the local `.env` is ignored and untracked. The local Node server and frontend build load the root `.env` (existing shell variables take precedence). The server passes server-side settings such as `GEMINI_API_KEY` to the Python pipeline, but serves only the public `EHR_API_BASE_URL` value through `/config.js`; it never sends Gemini or AWS secrets to the browser. If the URL is empty, the dashboard calls the local Node API. If set, the dashboard calls that API Gateway URL directly. AWS SDK credentials come from the Lambda execution role; do not configure access keys in frontend or application files. The repository does not define an AWS region/account, and `AWS_REGION`, `VITE_API_BASE_URL`, Secrets Manager ARNs, and Cognito variables are not read by this code.

For an AWS-hosted static frontend, build with the **verified** API Gateway URL:

```powershell
$env:EHR_API_BASE_URL = "https://<verified-api-id>.execute-api.<verified-region>.amazonaws.com/<verified-stage>"
npm.cmd run build:frontend
```

Upload `build/frontend/` using the account's already-approved frontend hosting process. This build writes the URL into the generated public `config.js`; it must never contain credentials or secrets. With no variable set, the built UI uses same-origin `/api/...` paths.

## Local development

Requirements: Node.js and Python 3.10+ with the project’s pytest dependency available.

```powershell
$env:STAGE_B_MODE = "stage_a_only"
npm.cmd run check
python -m pytest -q --basetemp build\pytest-run
npm.cmd start
```

Open `http://127.0.0.1:3000`. Paste a transcript, or upload a `.txt`/`.json` file; local uploads are explicitly **not** sent to S3. To run live Gemini locally, set `GEMINI_API_KEY` in the launching PowerShell process and choose `STAGE_B_MODE=llm_rag`. No live Gemini call is required for the synthetic local tests.

The server reads `PORT`, `PYTHON_EXECUTABLE`, `PIPELINE_TIMEOUT_MS`, `STAGE_B_MODE`, and Gemini settings from the root `.env`. To point the local dashboard at AWS, set `EHR_API_BASE_URL` in `.env` to the verified API Gateway base URL and restart the Node server. The browser then calls AWS directly; Lambda must have its own environment settings and permissions. A local `.env` setting does not deploy Lambda, create API Gateway, or configure S3.

Test a synthetic direct request from PowerShell:

```powershell
$body = @{
  transcript = "DIAGNOSIS: hypertension. MEDICATIONS: aspirin 81 mg daily."
  sourceTranscript = "DIAGNOSIS: hypertension. MEDICATIONS: aspirin 81 mg daily."
} | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "http://127.0.0.1:3000/api/analyse" -Method Post -ContentType "application/json" -Body $body
```

Full offline pipeline and Lambda-package commands:

```powershell
python scripts/run_end_to_end.py --mode stage_a_only --input dataset/processed/sample_20_for_aws_test.json --output results/end_to_end_output.json
python scripts/build_lambda.py
python scripts/invoke_local.py
```

## AWS deployment using existing resources

AWS CLI and account access are not available in this workspace; the resource facts below were supplied by the project owner after Console inspection. **Reuse the reported Lambda and bucket.** Do not create a second bucket, function, S3 notification, or role.

The current deployment artifact is `build/stage_a_lambda.zip`; build it with `python scripts/build_lambda.py`. For the full Console steps, IAM policy scopes, environment variable names, HTTP API route/CORS/auth configuration, frontend build, and synthetic end-to-end checklist, see [AWS_DEPLOYMENT_README.md](</D:/VIT/3_rd_yr/5th_sem/Cloud Aws/EHRComplianceMonitor_Cloud_Project_2026/docs/AWS_DEPLOYMENT_README.md>). No deployed-package comparison or AWS deployment has been performed in this workspace.

1. Verify the AWS account owner and selected `ap-south-1` region. The owner reports no API visible there; check other project accounts/regions only if Girinath used them. Record the frontend host and deployment process.
2. In Lambda → `ehr-compliance-stage-b`, confirm Python 3.12 and `stage_a_handler.lambda_handler`, then inspect the deployed code/version, timeout, environment key names, and execution role. Do not change the deployed package until it has been compared with `python scripts/build_lambda.py`; preserve the existing function name, role, and trigger.
3. In the existing Lambda environment verify/set `TRANSCRIPTS_BUCKET=ehr-compliance-monitor-yogiri-2026`, `STAGE_B_MODE`, `ALLOWED_ORIGINS`, and—only for Gemini—`GEMINI_MODEL`, `GEMINI_RPM`, and a protected `GEMINI_API_KEY`. Never expose secret values. Secrets Manager lookup is not implemented by this source.
4. Preserve the owner-confirmed bucket notification on `ehr-compliance-monitor-yogiri-2026`: all object-create events filtered by prefix `input/` and suffix `.json`, destination `ehr-compliance-stage-b`. Verify public-access block, encryption, lifecycle/retention, and that output writes under `output/stage_b/` are outside the event filter. Do not create a duplicate notification.
5. Inspect the existing Lambda role and S3 invoke permission. Verify only scoped S3 object `GetObject`/`PutObject` on the input/output keys (plus CloudWatch log delivery) and the bucket's invoke permission. After creating the API, add API Gateway invoke permission for that API/stage. Do not grant broad bucket access or create duplicate roles.
6. In API Gateway (verify the account/region first), create an **HTTP API** only if the project API is genuinely missing. Add a Lambda integration to the existing `ehr-compliance-stage-b` and routes `GET /health`, `POST /api/analyse`, `POST /api/upload`, `GET /api/jobs/{jobId}`. Configure CORS for the exact deployed frontend origin, `GET,POST,OPTIONS`, and `Content-Type,Authorization`; set Lambda `ALLOWED_ORIGINS` to that same origin. Confirm API Gateway created Lambda invoke permission. Do not add an S3 notification; preserve the existing one. Before using clinical data, configure the approved authorizer and matching frontend token flow: the current handler and frontend do not implement authentication. Ensure gateway/Lambda timeout settings accommodate synchronous analysis; otherwise use an approved asynchronous architecture.
7. Deploy the existing frontend using its current approved host. Set `EHR_API_BASE_URL` to the verified API URL, run `npm run build:frontend`, deploy `build/frontend/`, and confirm the matching CORS allowlist.
8. Run the synthetic upload and polling validation below before any real data. Confirm CloudWatch logs contain only aggregate counts/error classes, never transcripts, object keys with identifiers, API keys, or request bodies.

No AWS configuration changes were made by this workspace session. The owner reports the bucket event is already wired correctly. API Gateway deployment, deployed handler code inspection, role permissions, `TRANSCRIPTS_BUCKET`, CORS, authorizer, Gemini invocation, and live frontend-to-AWS validation remain outstanding.

### Synthetic AWS upload validation

After the existing API Gateway, Lambda, S3 event, IAM permissions, authorizer, and frontend URL are verified and configured, save this synthetic JSON object as `synthetic-transcript.json` and upload it from the dashboard:

```json
{
  "transcription": "DIAGNOSIS: hypertension. MEDICATIONS: aspirin 81 mg daily.",
  "source_transcript": "DIAGNOSIS: hypertension. MEDICATIONS: aspirin 81 mg daily."
}
```

The dashboard should show an S3 job ID, poll until `completed`, and display Stage A entities plus Stage B findings. Verify the input object is under `input/`, the audited result is under `output/stage_b/`, and the status object is separate from the result. Delete synthetic artifacts according to the bucket retention policy after the test. A local upload instead reports that it was processed locally and not stored in S3.

## Validation and results

- `python -m pytest -q --basetemp build\pytest-run-final2-20261013`: **64 passed**. Covers input/upload validation, Lambda API routes/CORS, simulated S3 upload → event → status/result polling, failed-job handling, Gemini-failure fallback, and isolated Lambda packaging.
- `npm.cmd run check`, `node --check src/frontend/app.js`, and `node --check src/frontend/config.js`: passed.
- `python scripts/invoke_local.py` with `STAGE_B_MODE=stage_a_only`: passed for 20 records; Lambda S3-stub and CLI Stage A outputs matched transcript IDs, entity counts, and routing.
- Local HTTP verification: `GET /health`, JSON `POST /api/analyse`, JSON `POST /api/upload` plus job polling all returned expected responses; malformed JSON upload returned HTTP 400. Local health explicitly reported `s3UploadConfigured=false`.
- Synthetic local direct workflow through Node → Python Stage A → Stage B → dashboard: passed. Observed `verification_status=completed`, 3 Stage A entities, 2 Stage B findings, and `llm_status=skipped` (deterministic mode); transcript text was not echoed by the direct response.
- Browser validation: pasted JSON returned one displayed Stage A/Stage B finding; uploaded `.txt` returned a displayed finding and explicitly reported local-only processing.
- S3-based asynchronous route tested with an in-memory S3 stub, including upload → event → pending/completed polling, simultaneous lock claim rejection, reclaiming failed locks, HTTP-event unknown-route rejection, repeated completed-event delivery, and ensuring a failed marker is not masked by an existing result. The deployed S3 event configuration is owner-reported; no live AWS invocation was exercised.
- Real Gemini/RAG network call: **not run**; no live secret was used.
- AWS account/endpoint/permissions, S3 dashboard upload in AWS, and deployment: **not verified**; AWS CLI is unavailable and no account resource inventory is in the repository.
- Browser UI was exercised locally for direct JSON and file upload. AWS browser-to-S3 workflow still needs the account-side wiring above.

Run full verification:

```powershell
npm.cmd run check
python -m pytest -q --basetemp build\pytest-run
python scripts/build_lambda.py
python scripts/invoke_local.py
```

## Troubleshooting

- **“Python verification runtime could not be started”**: install Python and set `PYTHON_EXECUTABLE` to its executable before `npm.cmd start`.
- **No result / upload timeout in AWS**: verify API Gateway routes, Lambda invoke permission, `TRANSCRIPTS_BUCKET`, S3 `input/` event filter, Lambda S3 role permissions, and CloudWatch error type. A `202 processing` response alone does not prove the S3 notification ran.
- **S3 upload returns not configured**: `TRANSCRIPTS_BUCKET` is absent in the deployed Lambda; set it to the already-verified bucket.
- **Browser CORS error**: configure the exact website origin in `ALLOWED_ORIGINS` and API Gateway CORS/preflight. Do not use `*` for clinical data.
- **Stage B says fallback**: inspect the non-PHI fallback reason shown in the UI, confirm `STAGE_B_MODE`, outbound network access, and server-side Gemini key. A fallback is not a Gemini result.
- **Gemini credential exposure**: rotate any credential previously stored in plaintext or shared; keep the replacement server-side.
- **Legacy Node Lambda handler**: [medical-transcript-processor.js](</D:/VIT/3_rd_yr/5th_sem/Cloud Aws/EHRComplianceMonitor_Cloud_Project_2026/src/aws/lambda/medical-transcript-processor.js>) is intentionally disabled because it was not connected to verification. The Python handler is the package/deployment path.

## Clinical-data safety

- Use synthetic data until the account-side IAM, private S3 settings, retention, encryption, network, audit, and authentication controls have been reviewed.
- No HIPAA compliance assessment, certification, or production approval is claimed.
- The API adapter avoids echoing transcripts in direct responses; S3 results necessarily retain the source/candidate record and must remain private with approved retention.
- Lambda logs report aggregate counts and error types only. Do not add transcript bodies, prompts, keys, object identifiers, or patient metadata to logs.
- The API Gateway authorizer and storage controls are not implemented/configured in this repository. This prototype must not be treated as production-compliant or as an autonomous clinical decision system.
- A clinician must review every finding. The software does not diagnose, prescribe, or automatically update an EHR.

## Phase 2 ML experiment

See [Phase 2 ML training and SageMaker preparation](docs/PHASE2_ML_TRAINING.md) for the synthetic-label task definition, grouped split, local metrics, reproducible commands, and gated SageMaker workflow. The model is not integrated into processing and has not been trained in AWS.
