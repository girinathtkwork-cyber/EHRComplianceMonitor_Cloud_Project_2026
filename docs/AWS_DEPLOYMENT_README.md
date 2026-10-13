# AWS deployment and verification notes

This document describes how to deploy the repository Lambda package to the existing resources. AWS CLI/account access is unavailable in this workspace. The project owner reports Console verification of Lambda `ehr-compliance-stage-b` (Python 3.12, `stage_a_handler.lambda_handler`, `ap-south-1`) and S3 bucket `ehr-compliance-monitor-yogiri-2026` with an object-create notification filtered to `input/` + `.json`, targeting that Lambda. The owner reports no API visible in the selected API Gateway `ap-south-1` Console scope. These facts have not been independently queried here.

**No AWS deployment, API creation, IAM change, or live AWS test was performed.** Do not create a duplicate Lambda, bucket, role, or S3 notification. Compare deployed Lambda code with the package described here before uploading it.

## Lambda package

- Handler: `stage_a_handler.lambda_handler`
- Runtime: owner-reported Python 3.12
- Build command from repository root:

  ```powershell
  python scripts/build_lambda.py
  ```

- Artifact: `build/stage_a_lambda.zip`; latest observed build was 3.41 MB compressed (under Lambda's 50 MB direct-console ZIP-upload limit).
- Package contents: root handler, `backend` Stage A/Stage B modules, prompt text files, and packaged RxNorm/ICD reference data.
- Dependencies: Python standard library plus `boto3` for S3. The package builder intentionally does not bundle boto3/botocore; Lambda's runtime provides its SDK. No Lambda requirements file is present.
- The bundled application source imports no third-party package other than `boto3`. Other repository dependencies such as `pandas`, `requests`, and `pytest` are not imported by the Lambda package.
- The S3 lock implementation requires an SDK model that accepts conditional `PutObject` parameters `IfNoneMatch="*"` and `IfMatch=<ETag>`. Verify this with the deployed runtime SDK before enabling the update. The isolated test uses a conditional-write S3 stub, not AWS.

### Exact Windows PowerShell package and deployment commands

Run from the repository root in PowerShell. The local build uses this repository's Python 3.12 virtual environment. It creates/overwrites only the ignored `build/stage_a_lambda.zip` artifact.

```powershell
& ".\.venv-1\Scripts\python.exe" .\scripts\build_lambda.py
if ($LASTEXITCODE -ne 0) { throw "Lambda ZIP build failed." }

& ".\.venv-1\Scripts\python.exe" -c "import zipfile; z=zipfile.ZipFile(r'build\stage_a_lambda.zip'); assert z.testzip() is None, 'Corrupt ZIP member'; assert 'stage_a_handler.py' in z.namelist(), 'Handler is not at ZIP root'; print('ZIP_VALID; HANDLER_AT_ROOT')"
if ($LASTEXITCODE -ne 0) { throw "Lambda ZIP validation failed." }

$LocalCodeSha256 = & ".\.venv-1\Scripts\python.exe" -c "import base64,hashlib,pathlib; print(base64.b64encode(hashlib.sha256(pathlib.Path(r'build\stage_a_lambda.zip').read_bytes()).digest()).decode())"
$LocalCodeSha256
```

AWS CLI is not installed in the workspace used to prepare this package. Install AWS CLI v2 from an elevated or ordinary PowerShell as permitted by the workstation policy, then open a new PowerShell:

```powershell
winget install --exact --id Amazon.AWSCLI
aws --version
```

Use the organization's AWS IAM Identity Center (SSO) profile, not root credentials or long-lived access keys. The SSO start URL and SSO region must come from the AWS administrator; they are intentionally not guessed here.

```powershell
aws configure sso --profile ehr-project
aws sso login --profile ehr-project
aws sts get-caller-identity --profile ehr-project --no-cli-pager
```

Before continuing, confirm the returned account is the intended project account. Then, still from the repository root, inspect the existing Lambda configuration without printing environment-variable values and inspect the current bucket notification:

```powershell
aws lambda get-function-configuration --function-name ehr-compliance-stage-b --region ap-south-1 --profile ehr-project --query "{FunctionName:FunctionName,Runtime:Runtime,Handler:Handler,Role:Role,Timeout:Timeout,LastUpdateStatus:LastUpdateStatus}" --output json --no-cli-pager
aws lambda get-function-configuration --function-name ehr-compliance-stage-b --region ap-south-1 --profile ehr-project --query "keys(Environment.Variables)" --output json --no-cli-pager
aws s3api get-bucket-notification-configuration --bucket ehr-compliance-monitor-yogiri-2026 --region ap-south-1 --profile ehr-project --query "LambdaFunctionConfigurations[].{Id:Id,LambdaFunctionArn:LambdaFunctionArn,Events:Events,Filter:Filter}" --output json --no-cli-pager
```

Stop if the function is not Python 3.12 with handler `stage_a_handler.lambda_handler`, the account is wrong, or the notification does not show the expected Lambda target and `input/` + `.json` filter. Compare the deployed package/source with this ZIP before replacing code. `update-function-code` changes Lambda code only; do not use `update-function-configuration` for this deployment. This preserves the current handler setting, runtime, role, environment variables, timeout, and S3 notification.

Download the currently deployed ZIP to a temporary local file without printing its temporary signed download URL, then compare package entries and hashes:

```powershell
$DeployedZip = Join-Path $env:TEMP ("ehr-compliance-stage-b-" + [guid]::NewGuid().ToString("N") + ".zip")
$CodeUrl = aws lambda get-function --function-name ehr-compliance-stage-b --region ap-south-1 --profile ehr-project --query Code.Location --output text --no-cli-pager
if ($LASTEXITCODE -ne 0 -or -not $CodeUrl) { throw "Could not obtain the deployed package download URL." }
Invoke-WebRequest -UseBasicParsing -Uri $CodeUrl -OutFile $DeployedZip
if (-not (Test-Path $DeployedZip)) { throw "Deployed package download failed." }

& ".\.venv-1\Scripts\python.exe" -c "import hashlib,zipfile; a=zipfile.ZipFile(r'build\stage_a_lambda.zip'); b=zipfile.ZipFile(r'$DeployedZip'); ah={n:hashlib.sha256(a.read(n)).hexdigest() for n in a.namelist()}; bh={n:hashlib.sha256(b.read(n)).hexdigest() for n in b.namelist()}; print('ONLY_DEPLOYED='+repr(sorted(bh.keys()-ah.keys()))); print('ONLY_LOCAL='+repr(sorted(ah.keys()-bh.keys()))); print('CONTENT_DIFFS='+repr(sorted(n for n in ah.keys()&bh.keys() if ah[n]!=bh[n])))"
if ($LASTEXITCODE -ne 0) { throw "Package comparison failed." }
```

Review the entry differences and confirm the new local package contains the intended changes before proceeding. The downloaded ZIP may contain proprietary application code; keep it local and do not share it. After comparison, remove only that specifically named temporary package if no longer needed:

```powershell
Remove-Item -LiteralPath $DeployedZip
```

After approval to deploy the compared package, run:

```powershell
aws lambda update-function-code --function-name ehr-compliance-stage-b --zip-file fileb://build/stage_a_lambda.zip --region ap-south-1 --profile ehr-project --no-cli-pager
if ($LASTEXITCODE -ne 0) { throw "Lambda code update failed." }
aws lambda wait function-updated-v2 --function-name ehr-compliance-stage-b --region ap-south-1 --profile ehr-project
if ($LASTEXITCODE -ne 0) { throw "Lambda update did not reach a successful state." }

$DeployedCodeSha256 = aws lambda get-function-configuration --function-name ehr-compliance-stage-b --region ap-south-1 --profile ehr-project --query CodeSha256 --output text --no-cli-pager
if ($LASTEXITCODE -ne 0) { throw "Could not read deployed Lambda configuration." }
if ($DeployedCodeSha256 -ne $LocalCodeSha256) { throw "Deployed package hash does not match the local ZIP." }
Write-Output "DEPLOYED_ZIP_HASH_MATCHES"
```

Verify the runtime, handler, environment-variable **names** (not values), and S3 notification remain unchanged:

```powershell
aws lambda get-function-configuration --function-name ehr-compliance-stage-b --region ap-south-1 --profile ehr-project --query "{FunctionName:FunctionName,Runtime:Runtime,Handler:Handler,Role:Role,Timeout:Timeout,LastUpdateStatus:LastUpdateStatus,CodeSha256:CodeSha256}" --output json --no-cli-pager
aws lambda get-function-configuration --function-name ehr-compliance-stage-b --region ap-south-1 --profile ehr-project --query "keys(Environment.Variables)" --output json --no-cli-pager
aws s3api get-bucket-notification-configuration --bucket ehr-compliance-monitor-yogiri-2026 --region ap-south-1 --profile ehr-project --query "LambdaFunctionConfigurations[].{Id:Id,LambdaFunctionArn:LambdaFunctionArn,Events:Events,Filter:Filter}" --output json --no-cli-pager
```

Do not publish real clinical data during a deployment smoke test. First run a Lambda console test with a synthetic `GET /health` API Gateway v2 event (with an API event `requestContext.http.method` of `GET` and `rawPath` of `/health`), then separately validate S3 processing with approved synthetic data. This verifies code deployment only; it does not create API Gateway or establish frontend connectivity.

### Required and optional environment variables

Set these under **Lambda → Functions → `ehr-compliance-stage-b` → Configuration → Environment variables**. Preserve existing entries; never share secret values.

| Variable | Requirement | Value/purpose |
|---|---|---|
| `TRANSCRIPTS_BUCKET` | Required for upload and polling | Existing bucket: `ehr-compliance-monitor-yogiri-2026` (owner-reported). |
| `ALLOWED_ORIGINS` | Required for browser access | Exact deployed HTTPS frontend origin, e.g. `https://<approved-frontend-host>`; no wildcard and no path. |
| `STAGE_B_MODE` | Optional; explicit setting recommended | `stage_a_only`, `llm_plain`, or `llm_rag`. |
| `GEMINI_API_KEY` | Only for live Gemini | Protected, server-side secret. Never in ZIP, source, or frontend. Secrets Manager lookup is not implemented. |
| `GEMINI_MODEL` | Optional for Gemini | Model name selected by the owner. |
| `GEMINI_RPM` | Optional for Gemini | Rate limit; code default is `15`. |

`EHR_API_BASE_URL` is a frontend build-time setting, not a Lambda variable. `AWS_REGION` is not required; boto3 obtains region from the Lambda runtime. AWS credentials must come from the existing Lambda execution role.

## 1. Inspect and deploy the existing Lambda

1. In AWS Console, select the project account and **Asia Pacific (Mumbai), `ap-south-1`**.
2. Open **Lambda → Functions → `ehr-compliance-stage-b`**. Confirm runtime `Python 3.12`, handler `stage_a_handler.lambda_handler`, execution role, timeout, memory, layers, environment-variable names, and triggers.
3. Download or otherwise inspect the deployed package using the existing approved process. Compare it with `build/stage_a_lambda.zip` before any upload. Verify the deployed handler:
   - dispatches HTTP API v2/REST proxy events separately from S3 `Records`;
   - handles `GET /health`, `POST /api/analyse`, `POST /api/upload`, and `GET /api/jobs/{jobId}`;
   - keeps the S3 input → processor → result/status workflow;
   - supports the conditional S3 lock SDK parameters in this package.
4. Build the ZIP using the command above. Only if comparison shows required code is missing, and the owner approves:
   - In Lambda **Code → Upload from → .zip file**, select `build/stage_a_lambda.zip`, then **Save**.
   - Under **Runtime settings → Edit**, retain Python 3.12 and set/confirm `stage_a_handler.lambda_handler`.
   - Preserve function name, execution role, existing S3 trigger, environment entries, and other working configuration. Do not replace any unknown deployed behavior blindly.
5. Under **Configuration → General configuration**, select memory/timeout based on measured synthetic run time. Lambda timeout maximum is 15 minutes; API Gateway HTTP API synchronous integration has a 30-second ceiling (see below).
6. Verify/set environment variable names and values from the table. In particular verify `TRANSCRIPTS_BUCKET` and set `ALLOWED_ORIGINS` to the exact frontend origin when known. Keep Gemini credentials protected and server-side.
7. Under **Configuration → Permissions**, inspect the existing role. Reuse it; do not create another. Required scoped object actions:
   - `s3:GetObject`, `s3:PutObject` on `arn:aws:s3:::ehr-compliance-monitor-yogiri-2026/input/*`
   - `s3:GetObject`, `s3:PutObject` on `arn:aws:s3:::ehr-compliance-monitor-yogiri-2026/output/stage_b/*`
   - Standard CloudWatch Logs delivery permissions.

   The current code reads exact keys and does not need `s3:ListBucket`. Verify the bucket permission allowing its existing notification to invoke this Lambda. Preserve the notification exactly as currently configured; do not add another.

## 2. Configure API Gateway only if missing

First search API Gateway in the confirmed project account and `ap-south-1`. If an API exists, reuse it. If it is confirmed absent:

1. Open **API Gateway → Create API → HTTP API → Build**.
2. Add a Lambda integration pointing to the existing `ehr-compliance-stage-b` in `ap-south-1`. Use Lambda proxy integration.
3. Add these routes, all connected to that integration:

   | Method | Route |
   |---|---|
   | `GET` | `/health` |
   | `POST` | `/api/analyse` |
   | `POST` | `/api/upload` |
   | `GET` | `/api/jobs/{jobId}` |

   The Lambda returns proxy responses with status, headers, and JSON body. Configure HTTP API CORS to handle browser preflight; add an explicit `OPTIONS` route only if API Gateway CORS is not handling it, avoiding conflicting CORS methods.
4. Under **CORS**, allow only the exact approved frontend HTTPS origin; methods `GET`, `POST`, `OPTIONS`; headers `Content-Type`, `Authorization`. Do not use `*`. Set Lambda `ALLOWED_ORIGINS` to the same exact origin.
5. Configure the project-approved authorizer before enabling transcript routes. The current UI and Lambda do not implement user authentication or token forwarding. **Do not expose clinical-data routes publicly; an API key is not a user-authentication substitute.** If no approved authorizer/auth flow exists yet, restrict routes and use synthetic data only.
6. Deploy a stage, note its real invoke URL, then verify **Lambda → Configuration → Permissions** includes API Gateway `lambda:InvokeFunction` permission scoped to this API/stage. Do not remove S3's existing permission or notification.
7. Check timeout compatibility. `POST /api/analyse` is synchronous and RxNorm/Gemini calls may exceed HTTP API's 30-second integration limit. Test with synthetic data and do not declare direct review operational if it times out; the safe remedy is a separately designed async flow or measured timeout/performance change, not an assumed API setting.

## 3. Build and deploy the frontend

Set the actual API Gateway invoke base URL in PowerShell, then build:

```powershell
$env:EHR_API_BASE_URL = "https://<actual-api-id>.execute-api.ap-south-1.amazonaws.com/<actual-stage>"
npm.cmd run build:frontend
```

Deploy `build/frontend/` using the already-approved frontend hosting process. No hosting deployment configuration is present in this repository. Verify generated `config.js` contains only the public API URL, never AWS or Gemini credentials. The frontend currently does not attach authorization tokens, so implementing the selected auth flow is a blocker before using protected routes.

## 4. Synthetic end-to-end verification

Only after Lambda code, API, CORS, permissions, and authorization are configured:

1. `GET <invoke-url>/health`: expect HTTP 200 and `s3_upload_configured: true`.
2. `POST <invoke-url>/api/analyse` with synthetic transcript/source strings: expect Stage A and Stage B response fields, no transcript echo, and response within gateway timeout.
3. Upload a synthetic one-record `.json` file in the dashboard: expect `POST /api/upload` to return HTTP 202 and a UUID job ID.
4. In S3 confirm input at `input/<jobId>.json`; verify the existing notification invokes `ehr-compliance-stage-b`. Do not create a duplicate notification.
5. Poll `GET <invoke-url>/api/jobs/<jobId>`: expect 202 while processing, then 200 completed/partial with the result, or a failed status on processing error.
6. Confirm result/status/lock objects under `output/stage_b/`. Check writing output does not invoke the input notification. Review CloudWatch logs for aggregate/error information only, never transcript text, request bodies, secrets, or identifying keys.
7. With synthetic data, repeat event delivery and verify conditional locking prevents concurrent duplicate execution. Check failed-job retry behavior and delete test artifacts according to approved retention.

This repository has local package/stub tests only. No deployed code comparison, AWS deployment, API Gateway creation, permission change, or real AWS end-to-end test has been performed in this workspace. Do not use clinical data until the live tests and authentication are verified.
