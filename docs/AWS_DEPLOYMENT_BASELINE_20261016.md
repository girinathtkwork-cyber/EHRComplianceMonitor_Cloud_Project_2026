# AWS deployment baseline — 16 October 2026

## Scope and method

Read-only inventory via the configured AWS CLI in `ap-south-1`; no AWS resources were changed. The existing project documentation and source tree were read first. Account identifiers and secret values are omitted. The pasted execution prompt ends during the S3 section at `Least-priv…`; later requirements may be missing.

## Current AWS state

- Caller identity is the configured `yogesh-admin` IAM user; region is `ap-south-1`.
- API Gateway is HTTP API `ehr-compliance-api`, API ID `ra5qh05emk`, `$default` auto-deploy stage. Routes: `GET /health`, `POST /api/analyse`, `POST /api/upload`, and `GET /api/jobs/{jobId}`. All routes have `AuthorizationType=NONE`; there are no authorizers, custom API domains, or API custom mappings.
- All four routes use the existing `ehr-compliance-stage-b` Lambda proxy integration with a 30,000 ms integration timeout. Lambda is Python 3.12, handler `stage_a_handler.lambda_handler`, 900-second timeout, 1,024 MB, no VPC, and has environment keys for Stage B mode, Gemini key/model, and the transcripts bucket. Secret values were not queried. The function has an existing S3 input event notification; no SQS event source mapping exists.
- The Lambda role has AWS basic logging plus an inline project S3 policy scoped to the existing input/output prefixes. It has no observed SQS or DynamoDB permissions.
- Existing bucket `ehr-compliance-monitor-yogiri-2026` has all four public-access-block settings enabled, AES256 default encryption, and an `input/` + `.json` object-created notification targeting the existing Lambda. There is no lifecycle configuration. Other bucket names exist, but no public access or contents were inspected for them.
- No regional Cognito user pools, SQS queues, DynamoDB tables, or EC2 instances were found. Three Lambda log groups exist without retention. No CloudWatch dashboard or CloudFront distribution was returned. No frontend origin/host is recorded in `src/frontend/config.js` (it is blank); the local dashboard is loopback-only.
- Existing API health URL responds, but this only confirms health endpoint reachability; it does not verify authentication, Gemini, queue processing, or an authenticated workflow.

## Repository state and baseline checks

- Existing working-tree edits and untracked files from the prior implementation were present. They were preserved.
- Source confirms the existing cloud handler supports synchronous API Gateway requests and S3 object events. The new local job queue is JSON-file based and is not SQS. The frontend has no Cognito/OAuth/PKCE flow or bearer-token forwarding. There is no AWS infrastructure definition in the repository.
- `npm.cmd run check` and `node --check src/frontend/config.js`: passed.
- Full Python suite with repository `src` on `PYTHONPATH`: **111 passed**. Without `PYTHONPATH=src`, collection fails to import `backend`; this is a test invocation/environment issue, not a test failure.
- `git diff --check`: passed before this baseline report was added.

## Planned cloud change set — not applied

1. Add a browser-compatible Cognito authorization-code/PKCE flow only after the frontend origin is selected; keep the client secret disabled and tokens out of logs. Add an HTTP API JWT authorizer and require it on every job/transcript/review route, leaving only minimal health public.
2. Add a standard SQS queue and DLQ with encryption, long polling, and a redrive policy; add an async submission/status flow backed by a DynamoDB job table with queryable history, conditional transitions, review audit, and application-enforced retention plus TTL.
3. Use separate least-privilege API/worker roles where practical; preserve the existing Lambda and S3 notification path unless the implementation has been safely migrated and verified.
4. Set explicit CloudWatch retention, structured non-PHI logs, and queue/job error/backlog visibility.
5. Deploy API/backend changes through AWS CLI, then run synthetic-only unauthenticated, invalid-token, authenticated, queue/DLQ, and job-history checks. No frontend deployment target currently exists.

## Material blockers before live mutation

- The user confirmed a local-only demo origin (`http://localhost:3000`) and requested retention under a week for the demo tomorrow. The deployment uses one day for new DynamoDB jobs, SQS/DLQ messages, existing project input/output prefixes, and the Lambda API/worker log groups.
- The existing cloud handler and UI have no authentication or SQS/DynamoDB implementation; attaching an authorizer before deploying a tested token flow would break the existing UI. The API is currently public, so it should not receive real clinical data.
- The prompt's final section is truncated; additional deployment or data-handling constraints may be missing.

No AWS operation beyond read-only inspection was performed.
