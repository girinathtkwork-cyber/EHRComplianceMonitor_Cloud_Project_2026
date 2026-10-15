# AWS CLI deployment report — 16 October 2026

## Outcome

Deployed the local-only demo configuration in `ap-south-1` using AWS CLI. The selected callback/logout origin is `http://localhost:3000/`. Retention is one day for job rows, SQS messages, the existing project bucket input/output prefixes, and the project processor/worker Lambda log groups. No hosted frontend hostname was configured.

## Resources and access controls

- Existing HTTP API `ehr-compliance-api` and `$default` stage now use a Cognito JWT authorizer. Transcript, upload, job, overview, and review routes require a JWT with the `openid` scope. Only `GET /health` is public. CORS permits only localhost port 3000.
- Cognito pool `ehr-compliance-demo` uses email sign-in and verification, a 12-character password minimum with mixed character classes, optional TOTP MFA, deletion protection, and self sign-up. Its public client has no secret and permits authorization-code OAuth for `openid email`; managed login is active. A synthetic test account was disabled after a browser smoke attempt.
- Encrypted SQS main queue and DLQ use 20-second long polling, 90-minute visibility timeout, one-day retention, and redrive after four receives. A reserved-concurrency-2 worker consumes one message at a time and invokes the existing processor Lambda synchronously, retaining its configured Stage B behavior.
- Encrypted on-demand DynamoDB uses an owner/submission-time index and one-day TTL. The application applies expiry checks itself. Rows contain owner/job/status, timestamps, result/review metadata, and a content hash; transcript text is excluded.
- The worker has a separate role limited to queue consumption, invoking the existing processor, and its own log group. The existing processor role gained only queue submission and scoped table actions. Its existing S3 policy, environment, runtime, and S3 notification path were preserved.
- Existing bucket lifecycle now expires objects under `input/` and `output/stage_b/` after one day. This applies to older objects subsequently covered by those prefixes too. Other bucket prefixes were not modified.
- Processor and worker log groups retain events for one day. Dashboard `ehr-compliance-demo` charts Lambda errors/invocations/duration and visible messages for both queues. No alarm actions were configured.

## Verification

- Python suite: 117 passed. Node syntax/config checks and local queue tests passed. Lambda package built, and package isolation/regression checks passed.
- Deployed Lambda code update completed successfully; existing runtime, handler, timeout, memory, role, environment, and S3 trigger remained intact. Worker event source mapping is active.
- `GET /health` returned HTTP 200. Protected `GET /api/jobs` without authorization and with an invalid bearer token returned HTTP 401.
- Cognito login completed in the user's normal browser and the dashboard displayed “Signed in.” Their first job reached DynamoDB/SQS, proving the authenticated submission path.
- The callback failure was traced to local static-file routing treating `/?code=...&state=...` as a filename. The server now serves the dashboard from the URL path while preserving OAuth query parameters; the user confirmed the successful redirect by sharing the signed-in dashboard.
- The first job exposed DynamoDB rejecting Python floats and an unaliased reserved word in the update expression. The worker now converts floating-point values to `Decimal`, aliases `result`, removes stale error categories after success, and returns camelCase job identifiers/timestamps for the UI. Code update completed successfully.
- The user's follow-up screenshots showed HTTP 500s on overview/history/system health. Lambda's resource policy allowed only original API routes, so newly added read routes had no API Gateway invoke permission. Added narrowly scoped Lambda permissions for overview, history, async submission, and review. API Gateway now returns HTTP 200 for `/health`; unauthenticated requests to the protected overview/history endpoints return 401 as expected.
- A synthetic worker round trip through the deployed worker/processor completed, persisted a result, and returned `REVIEW_REQUIRED` with one finding. The transcript was not persisted. A direct invocation of the overview handler with the signed-in user's subject returned HTTP 200 with expected camelCase fields. Reload the browser and re-submit the failed job to confirm the full browser-to-API-to-worker path after these fixes.
- No real patient data was used. The original failed job remains as a one-day-retention record; it can be retried from the dashboard. The synthetic worker verification record is also visible to that signed-in user until retention expires.

## Demo use and limits

Open `http://localhost:3000` and sign up a demo reviewer through Cognito with an email address you control; the temporary synthetic account is disabled. The API accepts only the local origin. The local web demo and cloud async job path are distinct; use the cloud-enabled local configuration, not a stale static frontend build. Check the `ehr-compliance-demo` CloudWatch dashboard while using the demo.

This is a student demonstration, not a production EHR or HIPAA compliance certification. Do not submit identifiable patient data. S3 lifecycle, DynamoDB TTL, and log retention are configured for one day, but removal is asynchronous and backups or other services are not covered. CloudWatch dashboard metrics and AWS request/model usage may incur charges; remaining free-tier eligibility was not inspected. No dollar cost or HIPAA compliance claim is made.

The pasted deployment prompt was cut off in the S3 section (`Least-priv…`), so requirements beyond the provided text could not be applied.
