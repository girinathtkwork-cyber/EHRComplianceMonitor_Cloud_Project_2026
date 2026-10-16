"""Authenticated asynchronous job API backed by SQS and DynamoDB."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO

try:
    import boto3
    from boto3.dynamodb.conditions import Attr, Key
except ImportError:  # Local test/runtime without AWS SDK.
    boto3 = None
    Attr = Key = None

from backend.web_api import _normalize_record, normalize_upload_file

RETENTION_SECONDS = 86_400
MAX_QUEUE_PAYLOAD_BYTES = 900_000


def _resources():
    if boto3 is None:
        raise RuntimeError("AWS SDK is unavailable.")
    table = _table()
    sqs = boto3.client("sqs")
    queue_url = os.environ.get("JOBS_QUEUE_URL")
    if not queue_url:
        queue_url = sqs.get_queue_url(QueueName=os.environ.get("JOBS_QUEUE_NAME", "ehr-compliance-jobs"))["QueueUrl"]
    return table, sqs, queue_url


def _table():
    if boto3 is None:
        raise RuntimeError("AWS SDK is unavailable.")
    return boto3.resource("dynamodb").Table(os.environ.get("JOBS_TABLE", "ehr-compliance-jobs"))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _request_owner(event):
    context = event.get("requestContext") or {}
    claims = (context.get("authorizer") or {}).get("jwt", {}).get("claims") or {}
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise PermissionError("Authentication is required.")
    return subject, claims


def _response(status, body, origin=None):
    allowed = {item.strip() for item in os.environ.get("ALLOWED_ORIGINS", "").split(",") if item.strip()}
    headers = {"Content-Type": "application/json", "Cache-Control": "no-store", "Vary": "Origin"}
    if origin and origin in allowed:
        headers["Access-Control-Allow-Origin"] = origin
    return {"statusCode": status, "headers": headers, "body": json.dumps(_json_safe(body), separators=(",", ":")) if body != "" else ""}


def _json_safe(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _dynamodb_safe(value):
    """Convert JSON numbers to DynamoDB-supported numeric types before writes."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _dynamodb_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_dynamodb_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_dynamodb_safe(item) for item in value]
    return value


def _client_record(item):
    """Expose consistent camelCase job fields to the dashboard API."""
    output = dict(item)
    for source, target in (("job_id", "jobId"), ("submitted_at", "submittedAt")):
        if source in output:
            output[target] = output.pop(source)
    return output


def _iso_epoch(value):
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (ValueError, AttributeError):
        raise ValueError("Date filters must use ISO 8601 timestamps.")


def submit_job(owner, payload):
    record = _normalize_record(payload)
    body = json.dumps({"jobId": record["transcript_id"], "transcript": record["transcription"], "sourceTranscript": record["source_transcript"]}, separators=(",", ":"))
    if len(body.encode("utf-8")) > MAX_QUEUE_PAYLOAD_BYTES:
        raise ValueError("The combined transcript and source must be smaller than 900 KB for asynchronous processing.")
    table, sqs, queue_url = _resources()
    now = _now()
    job_id = str(uuid.uuid4())
    record["transcript_id"] = job_id
    submitted_at = _now()
    content_hash = hashlib.sha256((record["transcription"] + "\0" + record["source_transcript"]).encode("utf-8")).hexdigest()
    item = {
        "job_id": job_id,
        "owner_sub": owner,
        "submitted_at": submitted_at,
        "expires_at": int(time.time()) + RETENTION_SECONDS,
        "status": "QUEUED",
        "reviewStatus": "PENDING",
        "cacheKey": content_hash,
        "pipelineVersion": "stage-a-2.0.0/stage-b-2.0.0",
        "findingCount": 0,
        "findingDistribution": {"critical": 0, "high": 0, "medium": 0, "low": 0},
        "cacheHit": False,
        "auditHistory": [{"at": now, "action": "JOB_SUBMITTED", "actor": "authenticated user"}],
    }
    table.put_item(Item=item, ConditionExpression=Attr("job_id").not_exists())
    try:
        sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps({"jobId": job_id, "transcript": record["transcription"], "sourceTranscript": record["source_transcript"]}, separators=(",", ":")))
    except Exception as exc:
        table.update_item(Key={"job_id": job_id}, UpdateExpression="SET #s=:failed, errorCategory=:category, completedAt=:now", ConditionExpression=Attr("owner_sub").eq(owner), ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":failed": "FAILED", ":category": "queue_submit_failed", ":now": _now()})
        raise RuntimeError("The job could not be queued.") from exc
    return {"jobId": job_id, "status": "QUEUED", "cacheHit": False, "storage": "aws_sqs_dynamodb"}


def submit_upload(owner, payload):
    record = normalize_upload_file(payload.get("fileName"), payload.get("content"))
    return submit_job(owner, {"transcript": record["transcription"], "sourceTranscript": record.get("source_transcript", ""), "forceRerun": payload.get("forceRerun", False)})


def get_job(owner, job_id):
    table = _table()
    result = table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
    if not result or result.get("owner_sub") != owner or int(result.get("expires_at", 0)) <= int(time.time()):
        return None
    result.pop("owner_sub", None)
    result.pop("expires_at", None)
    return _client_record(result)


def _query_all(table, owner, status=None, review_status=None, start=None, end=None, search=None):
    condition = Key("owner_sub").eq(owner)
    if start and end:
        condition &= Key("submitted_at").between(start, end)
    elif start:
        condition &= Key("submitted_at").gte(start)
    elif end:
        condition &= Key("submitted_at").lte(end)
    filters = Attr("expires_at").gt(int(time.time()))
    if status:
        filters &= Attr("status").eq(status)
    if review_status:
        filters &= Attr("reviewStatus").eq(review_status)
    if search:
        filters &= Attr("job_id").contains(search)
    query = {"IndexName": "ByOwnerSubmittedAt", "KeyConditionExpression": condition, "FilterExpression": filters, "ScanIndexForward": False}
    items = []
    while True:
        response = table.query(**query)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        query["ExclusiveStartKey"] = last_key
    return items


def list_jobs(owner, query):
    table = _table()
    status = query.get("status")
    review = query.get("reviewStatus")
    start = query.get("from")
    end = query.get("to")
    if start:
        _iso_epoch(start)
    if end:
        _iso_epoch(end)
    try:
        cursor = max(0, int(query.get("cursor", "0")))
        limit = max(1, min(100, int(query.get("limit", "20"))))
    except ValueError as exc:
        raise ValueError("Page cursor and size must be integers.") from exc
    items = _query_all(table, owner, status, review, start, end, query.get("search") or None)
    summaries = []
    for item in items:
        item.pop("owner_sub", None)
        item.pop("expires_at", None)
        summaries.append(_client_record(item))
    page = summaries[cursor:cursor + limit]
    return {"items": page, "nextCursor": cursor + limit if cursor + limit < len(summaries) else None, "total": len(summaries)}


def overview(owner):
    table = _table()
    items = _query_all(table, owner)
    counts = {"total": len(items), "completed": 0, "active": 0, "failed": 0, "reviewRequired": 0, "cacheHits": 0}
    distribution = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    durations = []
    for item in items:
        status = item.get("status")
        counts["completed"] += status in {"COMPLETED", "REVIEW_REQUIRED"}
        counts["active"] += status in {"QUEUED", "PROCESSING"}
        counts["failed"] += status == "FAILED"
        counts["reviewRequired"] += item.get("reviewStatus") != "REVIEWED" and (status == "REVIEW_REQUIRED" or item.get("reviewStatus") == "ESCALATED")
        counts["cacheHits"] += bool(item.get("cacheHit"))
        if isinstance(item.get("processingDurationMs"), (int, float)):
            durations.append(item["processingDurationMs"])
        for level, count in item.get("findingDistribution", {}).items():
            if level in distribution:
                distribution[level] += int(count)
    return {"counts": counts, "averageProcessingDurationMs": round(sum(durations) / len(durations)) if durations else None, "cacheHitRate": counts["cacheHits"] / len(items) if items else None, "findingDistribution": distribution, "recentActivity": [_client_record({key: value for key, value in item.items() if key not in {"owner_sub", "expires_at"}}) for item in items[:6]], "metricsDefinition": "Counts and finding totals cover unexpired jobs for this signed-in user (maximum one-day retention)."}


def review_job(owner, job_id, payload):
    table = _table()
    item = table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
    if not item or item.get("owner_sub") != owner or int(item.get("expires_at", 0)) <= int(time.time()):
        return None
    action = payload.get("action")
    finding_id = payload.get("findingId")
    reason = str(payload.get("reason", "")).strip()
    note = str(payload.get("note", "")).strip()
    if action not in {"mark_reviewed", "dismiss_finding", "escalate_finding"}:
        raise ValueError("Unsupported review action.")
    if action != "mark_reviewed" and not reason:
        raise ValueError("A reason is required for dismissing or escalating a finding.")
    if action == "mark_reviewed":
        item["reviewStatus"] = "REVIEWED"
        item["reviewedAt"] = _now()
    else:
        findings = (item.get("result") or {}).get("stage_b_findings", [])
        finding = next((entry for entry in findings if entry.get("id") == finding_id), None)
        if not finding:
            raise ValueError("The finding was not found in this job.")
        finding["reviewStatus"] = "DISMISSED" if action == "dismiss_finding" else "ESCALATED"
        finding["reviewReason"] = reason
        if note:
            finding["reviewNote"] = note
        item["result"]["stage_b_findings"] = findings
        if action == "escalate_finding":
            item["reviewStatus"] = "ESCALATED"
    audit = {"at": _now(), "action": action.upper(), "actor": "authenticated reviewer"}
    if finding_id:
        audit["findingId"] = finding_id
    if reason:
        audit["reason"] = reason
    if note:
        audit["note"] = note
    item.setdefault("auditHistory", []).append(audit)
    table.update_item(Key={"job_id": job_id}, UpdateExpression="SET #r=:result, reviewStatus=:review, reviewedAt=:reviewedAt, auditHistory=:audit", ConditionExpression=Attr("owner_sub").eq(owner) & Attr("expires_at").gt(int(time.time())), ExpressionAttributeNames={"#r": "result"}, ExpressionAttributeValues={":result": item.get("result"), ":review": item.get("reviewStatus", "PENDING"), ":reviewedAt": item.get("reviewedAt", ""), ":audit": item["auditHistory"]})
    item.pop("owner_sub", None)
    item.pop("expires_at", None)
    return _client_record(item)


def api_handler(event):
    context = event.get("requestContext") or {}
    method = (context.get("http") or {}).get("method") or event.get("httpMethod", "")
    path = event.get("rawPath") or event.get("path", "")
    headers = event.get("headers") or {}
    origin = next((value for key, value in headers.items() if key.lower() == "origin"), None)
    if method == "GET" and path.rstrip("/") == "/health":
        return _response(200, {"status": "ok"}, origin)
    try:
        owner, claims = _request_owner(event)
        query = event.get("queryStringParameters") or {}
        if method == "GET" and path.rstrip("/") == "/api/overview":
            return _response(200, overview(owner), origin)
        if method == "GET" and path.rstrip("/") == "/api/jobs":
            return _response(200, list_jobs(owner, query), origin)
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body, validate=True).decode("utf-8")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        if method == "POST" and path.rstrip("/") == "/api/jobs":
            return _response(202, submit_job(owner, payload), origin)
        if method == "POST" and path.rstrip("/") == "/api/upload":
            return _response(202, submit_upload(owner, payload), origin)
        import re
        review = re.fullmatch(r"/api/jobs/([0-9a-fA-F-]{36})/review", path.rstrip("/"))
        if method == "POST" and review:
            result = review_job(owner, review.group(1), payload)
            return _response(404, {"error": "Verification job was not found."}, origin) if result is None else _response(200, result, origin)
        match = re.fullmatch(r"/api/jobs/([0-9a-fA-F-]{36})", path.rstrip("/"))
        if method == "GET" and match:
            result = get_job(owner, match.group(1))
            if result is None:
                return _response(404, {"error": "Verification job was not found."}, origin)
            return _response(200, result, origin)
        if method == "POST" and path.rstrip("/") == "/api/analyse":
            from backend.web_api import process_transcript
            with redirect_stdout(StringIO()):
                result = process_transcript(payload)
            status_code = 422 if result.get("verification_status") == "error" else 200
            return _response(status_code, result, origin)
        return _response(404, {"error": "Not found."}, origin)
    except PermissionError as exc:
        return _response(401, {"error": str(exc)}, origin)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _response(400, {"error": str(exc)}, origin)
    except Exception as exc:
        print(json.dumps({"event": "cloud_job_api_failed", "error_type": type(exc).__name__}))
        return _response(500, {"error": "The cloud job service failed unexpectedly."}, origin)


def worker_record(record):
    table = _table()
    message = json.loads(record["body"])
    job_id = str(uuid.UUID(message["jobId"]))
    item = table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
    if not item or int(item.get("expires_at", 0)) <= int(time.time()):
        return
    previous_status = item.get("status")
    previous_started = item.get("processingStartedAt")
    if previous_status == "PROCESSING":
        started_epoch = _iso_epoch(previous_started or "1970-01-01T00:00:00Z")
        if started_epoch > int(time.time()) - 1_200:
            return
    elif previous_status not in {"QUEUED", "FAILED"}:
        return
    started = time.monotonic()
    claim_condition = Attr("status").eq(previous_status) & Attr("expires_at").gt(int(time.time()))
    claim_condition &= Attr("processingStartedAt").eq(previous_started) if previous_started else Attr("processingStartedAt").not_exists()
    table.update_item(Key={"job_id": job_id}, UpdateExpression="SET #s=:processing, processingStartedAt=:now", ConditionExpression=claim_condition, ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":processing": "PROCESSING", ":now": _now()})
    try:
        from backend.web_api import process_transcript
        with redirect_stdout(StringIO()):
            result = process_transcript({"transcript": message["transcript"], "sourceTranscript": message.get("sourceTranscript", ""), "transcript_id": job_id})
        findings = result.get("stage_b_findings", [])
        distribution = {level: sum(str(finding.get("severity", "")).lower() == level for finding in findings) for level in ("critical", "high", "medium", "low")}
        complete = result.get("verification_status") != "error"
        needs_review = bool(findings) or bool((result.get("completeness_review") or {}).get("human_review_required"))
        status = "FAILED" if not complete else "REVIEW_REQUIRED" if needs_review else "COMPLETED"
        duration = int((time.monotonic() - started) * 1000)
        table.update_item(Key={"job_id": job_id}, UpdateExpression="SET #s=:status, #result=:result, findingCount=:count, findingDistribution=:distribution, completedAt=:completed, processingDurationMs=:duration, reviewStatus=:review REMOVE errorCategory", ConditionExpression=Attr("status").eq("PROCESSING"), ExpressionAttributeNames={"#s": "status", "#result": "result"}, ExpressionAttributeValues={":status": status, ":result": _dynamodb_safe(result), ":count": len(findings), ":distribution": distribution, ":completed": _now(), ":duration": duration, ":review": "PENDING"})
        metric_name = "JobsFailed" if status == "FAILED" else "JobsCompleted"
        print(json.dumps({"_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [{"Namespace": "EHR/ComplianceMonitor", "Dimensions": [["Component"]], "Metrics": [{"Name": metric_name, "Unit": "Count"}, {"Name": "ProcessingDurationMs", "Unit": "Milliseconds"}, {"Name": "FindingCount", "Unit": "Count"}]}]}, "Component": "worker", metric_name: 1, "ProcessingDurationMs": duration, "FindingCount": len(findings)}))
    except Exception as exc:
        table.update_item(Key={"job_id": job_id}, UpdateExpression="SET #s=:failed, errorCategory=:category, completedAt=:now, processingDurationMs=:duration", ConditionExpression=Attr("status").eq("PROCESSING"), ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":failed": "FAILED", ":category": type(exc).__name__, ":now": _now(), ":duration": int((time.monotonic() - started) * 1000)})
        print(json.dumps({"_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [{"Namespace": "EHR/ComplianceMonitor", "Dimensions": [["Component"]], "Metrics": [{"Name": "JobsFailed", "Unit": "Count"}]}]}, "Component": "worker", "JobsFailed": 1}))
        error_code = getattr(exc, "response", {}).get("Error", {}).get("Code") if hasattr(exc, "response") else None
        print(json.dumps({"event": "cloud_job_failed", "error_type": type(exc).__name__, "error_code": error_code}))


def handle_sqs_event(event):
    failures = []
    for record in event.get("Records", []):
        try:
            worker_record(record)
        except Exception as exc:
            failures.append({"itemIdentifier": record.get("messageId", "unknown")})
            print(json.dumps({"event": "cloud_job_worker_failed", "error_type": type(exc).__name__}))
    return {"batchItemFailures": failures}
