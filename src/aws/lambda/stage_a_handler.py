"""AWS Lambda entry point for API Gateway requests and existing S3 events."""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.parse
import uuid
from contextlib import redirect_stdout
from io import StringIO
from pathlib import PurePosixPath

try:
    import boto3
except ImportError:
    boto3 = None

from backend.stage_a import rxnorm_client
from backend.stage_a.pipeline import run_stage_a
from backend.stage_b.stage_b import run_stage_b
from backend.web_api import normalize_upload_file, process_transcript

_S3_JOB_LOCK_LEASE_SECONDS = 960


def _create_s3_client():
    if boto3 is None:
        raise RuntimeError("boto3 is not available in this environment.")
    return boto3.client("s3")


def _cors_headers(origin: str | None) -> dict:
    allowed = {
        value.strip()
        for value in os.environ.get("ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    }
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Vary": "Origin",
    }
    if origin and origin in allowed:
        headers["Access-Control-Allow-Origin"] = origin
    return headers


def _api_response(status: int, body: dict | str, origin: str | None = None) -> dict:
    return {
        "statusCode": status,
        "headers": _cors_headers(origin),
        "body": body if isinstance(body, str) else json.dumps(body),
    }


def _http_request(event: dict) -> bool:
    return (
        isinstance(event.get("httpMethod"), str)
        or isinstance(event.get("requestContext"), dict)
        and isinstance(event["requestContext"].get("http"), dict)
    )


def _handle_http(event: dict) -> dict:
    request_context = event.get("requestContext") or {}
    method = event.get("httpMethod") or (request_context.get("http") or {}).get("method", "")
    path = event.get("path") or event.get("rawPath") or request_context.get("resourcePath") or ""
    headers = event.get("headers") or {}
    origin = next((value for key, value in headers.items() if key.lower() == "origin"), None)

    if method == "OPTIONS":
        return _api_response(204, "", origin)
    from backend.cloud_jobs import api_handler
    return api_handler(event)


def _upload_to_s3(payload: object, origin: str | None) -> dict:
    if not isinstance(payload, dict):
        return _api_response(400, {"error": "Request body must be a JSON object."}, origin)
    bucket = os.environ.get("TRANSCRIPTS_BUCKET")
    if not bucket:
        return _api_response(503, {"error": "S3 upload is not configured for this deployment."}, origin)
    record = normalize_upload_file(payload.get("fileName"), payload.get("content"))
    job_id = str(uuid.uuid4())
    record["transcript_id"] = job_id
    client = _create_s3_client()
    client.put_object(
        Bucket=bucket,
        Key=f"input/{job_id}.json",
        Body=json.dumps(record, separators=(",", ":")),
        ContentType="application/json",
    )
    return _api_response(202, {"jobId": job_id, "status": "processing", "storage": "s3"}, origin)


def _missing_s3_key(exc: Exception) -> bool:
    response = getattr(exc, "response", {})
    error = response.get("Error", {}) if isinstance(response, dict) else {}
    code = error.get("Code") if isinstance(error, dict) else None
    return code in {"NoSuchKey", "404", "NotFound"}


def _get_job(job_id: str, origin: str | None) -> dict:
    bucket = os.environ.get("TRANSCRIPTS_BUCKET")
    if not bucket:
        return _api_response(503, {"error": "S3 job storage is not configured for this deployment."}, origin)
    client = _create_s3_client()
    status_key = f"output/stage_b/{job_id}.status.json"
    try:
        status_response = client.get_object(Bucket=bucket, Key=status_key)
        job_status = json.loads(status_response["Body"].read().decode("utf-8"))
    except Exception as exc:
        if _missing_s3_key(exc):
            return _api_response(202, {"jobId": job_id, "status": "processing"}, origin)
        print(json.dumps({"event": "job_status_read_failed", "error_type": type(exc).__name__}))
        return _api_response(500, {"error": "Unable to retrieve job status."}, origin)
    if not isinstance(job_status, dict) or job_status.get("status") not in {
        "processing",
        "failed",
        "completed",
        "partial",
    }:
        return _api_response(500, {"error": "The stored job status is invalid."}, origin)
    status = job_status["status"]
    if status == "failed":
        return _api_response(200, {
            "jobId": job_id,
            "status": "failed",
            "error": "Transcript processing failed. Check the Lambda operational logs.",
        }, origin)
    if status == "processing":
        return _api_response(202, {"jobId": job_id, "status": "processing"}, origin)

    key = f"output/stage_b/{job_id}.audited.json"
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _missing_s3_key(exc):
            return _api_response(500, {"error": "The job is terminal but its verification result is missing."}, origin)
        print(json.dumps({"event": "job_result_read_failed", "error_type": type(exc).__name__}))
        return _api_response(500, {"error": "Unable to retrieve the verification result."}, origin)

    try:
        output = json.loads(response["Body"].read().decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return _api_response(500, {"error": "The stored verification result is invalid."}, origin)
    partial = status == "partial"
    records = output.get("records", []) if isinstance(output, dict) else output
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        return _api_response(500, {"error": "The stored verification result has an invalid shape."}, origin)
    if not records:
        return _api_response(500, {"error": "The stored verification result is empty."}, origin)
    if partial and not isinstance(output, dict):
        return _api_response(500, {"error": "The stored partial result is invalid."}, origin)
    from backend.web_api import format_result
    formatted_records = [format_result(record) for record in records]
    counts = {
        "expected_count": output.get("total_count", len(records)) if isinstance(output, dict) else len(records),
        "processed_count": output.get("processed_count", len(records)) if isinstance(output, dict) else len(records),
        "successful_count": output.get("successful_count", len(records)) if isinstance(output, dict) else len(records),
        "failed_count": output.get("failed_count", 0) if isinstance(output, dict) else 0,
        "fallback_count": output.get("fallback_count", 0) if isinstance(output, dict) else 0,
    }
    return _api_response(200, {
        "jobId": job_id,
        "status": "partial" if partial else "completed",
        # Keep the original single-record field for existing dashboard clients.
        "result": formatted_records[0],
        "results": formatted_records,
        **counts,
    }, origin)


def _write_job_status(s3_client, bucket: str, basename: str, status: str) -> None:
    s3_client.put_object(
        Bucket=bucket,
        Key=f"output/stage_b/{basename}.status.json",
        Body=json.dumps({"status": status}),
        ContentType="application/json",
    )


def _completed_output_exists(s3_client, bucket: str, basename: str) -> bool:
    try:
        response = s3_client.get_object(
            Bucket=bucket,
            Key=f"output/stage_b/{basename}.audited.json",
        )
        output = json.loads(response["Body"].read().decode("utf-8"))
        status_response = s3_client.get_object(
            Bucket=bucket,
            Key=f"output/stage_b/{basename}.status.json",
        )
        status = json.loads(status_response["Body"].read().decode("utf-8"))
        if not isinstance(status, dict):
            return False
        if status.get("status") == "completed":
            return isinstance(output, list) and bool(output) and all(
                isinstance(record, dict) for record in output
            )
        if status.get("status") == "partial":
            records = output.get("records") if isinstance(output, dict) else None
            return (
                isinstance(output, dict)
                and isinstance(records, list)
                and bool(records)
                and all(isinstance(record, dict) for record in records)
                and output.get("processed_count", len(records)) == len(records)
            )
        return False
    except Exception as exc:
        if _missing_s3_key(exc):
            return False
        raise


def _is_conditional_write_conflict(exc: Exception) -> bool:
    response = getattr(exc, "response", {})
    error = response.get("Error", {}) if isinstance(response, dict) else {}
    code = error.get("Code") if isinstance(error, dict) else None
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode") if isinstance(response, dict) else None
    return code in {"PreconditionFailed", "ConditionalRequestConflict", "412"} or status in {409, 412}


def _acquire_s3_job_lock(
    s3_client,
    bucket: str,
    basename: str,
    retry_missing: bool = True,
) -> tuple[bool, str | None]:
    if _completed_output_exists(s3_client, bucket, basename):
        return False, None
    lock_key = f"output/stage_b/{basename}.lock.json"
    now = int(time.time())
    attempt_id = str(uuid.uuid4())
    lock_body = json.dumps({
        "status": "processing",
        "attempt_id": attempt_id,
        "lease_expires_at": now + _S3_JOB_LOCK_LEASE_SECONDS,
    })
    try:
        response = s3_client.put_object(
            Bucket=bucket,
            Key=lock_key,
            Body=lock_body,
            ContentType="application/json",
            IfNoneMatch="*",
        )
        return True, response.get("ETag")
    except Exception as exc:
        if not _is_conditional_write_conflict(exc):
            raise

    try:
        existing = s3_client.get_object(Bucket=bucket, Key=lock_key)
    except Exception as exc:
        if _missing_s3_key(exc) and retry_missing:
            return _acquire_s3_job_lock(s3_client, bucket, basename, retry_missing=False)
        raise
    etag = existing.get("ETag")
    try:
        state = json.loads(existing["Body"].read().decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("The S3 job lock is invalid.")
    if not isinstance(state, dict):
        raise RuntimeError("The S3 job lock has an invalid shape.")

    if _completed_output_exists(s3_client, bucket, basename):
        return False, None
    if state.get("status") == "processing" and int(state.get("lease_expires_at", 0)) > now:
        return False, None
    if not etag:
        raise RuntimeError("The S3 job lock has no ETag for a safe conditional update.")

    try:
        response = s3_client.put_object(
            Bucket=bucket,
            Key=lock_key,
            Body=lock_body,
            ContentType="application/json",
            IfMatch=etag,
        )
        return True, response.get("ETag")
    except Exception as exc:
        if _is_conditional_write_conflict(exc):
            return False, None
        raise


def _set_s3_job_lock_status(s3_client, bucket: str, basename: str, etag: str | None, status: str) -> None:
    if not etag:
        raise RuntimeError("Cannot safely update an S3 job lock without its ETag.")
    lock_key = f"output/stage_b/{basename}.lock.json"
    try:
        existing = s3_client.get_object(Bucket=bucket, Key=lock_key)
        lock = json.loads(existing["Body"].read().decode("utf-8"))
        if not isinstance(lock, dict) or existing.get("ETag") != etag:
            return
        lock["status"] = status
        lock["lease_expires_at"] = 0
        s3_client.put_object(
            Bucket=bucket,
            Key=lock_key,
            Body=json.dumps(lock, separators=(",", ":")),
            ContentType="application/json",
            IfMatch=etag,
        )
    except Exception as exc:
        if _is_conditional_write_conflict(exc):
            return
        print(json.dumps({"event": "job_lock_status_write_failed", "error_type": type(exc).__name__}))


def _process_s3_object(s3_client, bucket: str, key: str, context) -> None:
    basename = PurePosixPath(key).name.removesuffix(".json")
    acquired, lock_etag = _acquire_s3_job_lock(s3_client, bucket, basename)
    if not acquired:
        print(json.dumps({"event": "duplicate_s3_event_skipped"}))
        return
    try:
        _run_s3_job(s3_client, bucket, key, context, lock_etag)
    except Exception:
        try:
            _write_job_status(s3_client, bucket, basename, "failed")
        except Exception as status_exc:
            print(json.dumps({"event": "job_status_write_failed", "error_type": type(status_exc).__name__}))
        _set_s3_job_lock_status(s3_client, bucket, basename, lock_etag, "failed")
        raise


def _run_s3_job(s3_client, bucket: str, key: str, context, lock_etag: str | None) -> None:
    basename = PurePosixPath(key).name.removesuffix(".json")
    _write_job_status(s3_client, bucket, basename, "processing")
    response = s3_client.get_object(Bucket=bucket, Key=key)
    input_data = json.loads(response["Body"].read().decode("utf-8"))
    if isinstance(input_data, dict):
        input_data = [input_data]
    if not isinstance(input_data, list):
        raise ValueError("S3 input must be a JSON object or array of records.")
    if not input_data:
        raise ValueError("S3 input must contain at least one record.")

    mode = os.environ.get(
        "STAGE_B_MODE",
        "llm_rag" if os.environ.get("GEMINI_API_KEY") else "stage_a_only",
    )
    rx_cache_before = rxnorm_client.get_cache()
    rx_cache_before_counts = (rx_cache_before._hits, rx_cache_before._misses)
    rx_metrics_before = rxnorm_client.metrics_snapshot()
    processed_records = []
    successful_count = 0
    failed_count = 0
    stage_a_duration_ms = 0
    stage_b_duration_ms = 0
    fallback_count = 0
    batch_started = time.monotonic()
    truncated = False
    for item in input_data:
        if hasattr(context, "get_remaining_time_in_millis") and context.get_remaining_time_in_millis() < 30_000:
            truncated = True
            break
        if not isinstance(item, dict):
            processed_records.append({
                "verification_status": "error",
                "stage_b_status": "error",
                "error": {"code": "invalid_record", "message": "Each input record must be a JSON object."},
            })
            failed_count += 1
            continue
        item.pop("ground_truth_label", None)
        if "transcription" not in item and isinstance(item.get("transcript"), str):
            item["transcription"] = item.pop("transcript")
        if "source_transcript" not in item and isinstance(item.get("sourceTranscript"), str):
            item["source_transcript"] = item.pop("sourceTranscript")
        item.pop("transcript", None)
        item.pop("sourceTranscript", None)
        with redirect_stdout(StringIO()):
            stage_a_started = time.monotonic()
            stage_a_record = run_stage_a(item)
            stage_a_duration_ms += int((time.monotonic() - stage_a_started) * 1000)
            if stage_a_record.get("verification_status") not in ("completed", "partial"):
                processed_records.append({
                    "transcript_id": stage_a_record.get("transcript_id", item.get("transcript_id")),
                    "verification_status": "error",
                    "stage_a_version": stage_a_record.get("stage_a_version"),
                    "stage_b_status": "error",
                    "error": {"code": "stage_a_failed", "message": "Stage A could not process this record."},
                })
                failed_count += 1
                continue
            stage_b_started = time.monotonic()
            audited_record = run_stage_b(stage_a_record, mode=mode)
            stage_b_duration_ms += int((time.monotonic() - stage_b_started) * 1000)
            processed_records.append(audited_record)
            if audited_record.get("stage_b_status") == "completed":
                successful_count += 1
            else:
                failed_count += 1
            if (audited_record.get("stage_b") or {}).get("llm_status") == "fallback":
                fallback_count += 1

    rxnorm_client.save_cache()
    rx_cache_after = rxnorm_client.get_cache()
    rx_metrics_after = rxnorm_client.metrics_snapshot()
    output_key = f"output/stage_b/{basename}.audited.json"
    partial = truncated or failed_count > 0 or fallback_count > 0
    final_status = "failed" if not processed_records else ("partial" if partial else "completed")
    output_data = processed_records
    if partial:
        output_data = {
            "records": processed_records,
            "truncated": truncated,
            "processed_count": len(processed_records),
            "total_count": len(input_data),
            "successful_count": successful_count,
            "failed_count": failed_count,
            "fallback_count": fallback_count,
        }
    s3_client.put_object(
        Bucket=bucket,
        Key=output_key,
        Body=json.dumps(output_data, separators=(",", ":")),
        ContentType="application/json",
    )
    _write_job_status(s3_client, bucket, basename, final_status)
    _set_s3_job_lock_status(
        s3_client, bucket, basename, lock_etag,
        final_status,
    )
    print(json.dumps({
        "metric": "transcript_batch_processed",
        "expected_count": len(input_data),
        "processed_count": len(processed_records),
        "successful_count": successful_count,
        "failed_count": failed_count,
        "fallback_count": fallback_count,
        "stage_b_failures": sum((record.get("stage_b") or {}).get("request_failures", 0) for record in processed_records),
        "gemini_request_count": sum((record.get("stage_b") or {}).get("request_count", 0) for record in processed_records),
        "gemini_latency_ms": sum((record.get("stage_b") or {}).get("latency_ms", 0) for record in processed_records),
        "terminology_request_count": rx_metrics_after["requests"] - rx_metrics_before["requests"],
        "terminology_request_failures": rx_metrics_after["failures"] - rx_metrics_before["failures"],
        "terminology_request_duration_ms": rx_metrics_after["duration_ms"] - rx_metrics_before["duration_ms"],
        "rxnorm_cache_hits": rx_cache_after._hits - rx_cache_before_counts[0],
        "rxnorm_cache_misses": rx_cache_after._misses - rx_cache_before_counts[1],
        "stage_a_duration_ms": stage_a_duration_ms,
        "stage_b_duration_ms": stage_b_duration_ms,
        "total_duration_ms": int((time.monotonic() - batch_started) * 1000),
        "truncated": truncated,
    }))


def _handle_s3(event: dict, context) -> dict:
    s3_client = _create_s3_client()
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        if not key.startswith("input/") or not key.endswith(".json"):
            continue
        _process_s3_object(s3_client, bucket, key, context)

    return {"statusCode": 200, "body": json.dumps({"status": "Processing complete"})}


def lambda_handler(event, context):
    if not isinstance(event, dict):
        return {"statusCode": 400, "body": json.dumps({"error": "Invalid event."})}
    if _http_request(event):
        return _handle_http(event)
    if isinstance(event.get("Records"), list):
        if event["Records"] and event["Records"][0].get("eventSource") == "aws:sqs":
            from backend.cloud_jobs import handle_sqs_event
            return handle_sqs_event(event)
        try:
            return _handle_s3(event, context)
        except Exception as exc:
            print(json.dumps({"event": "s3_processing_failed", "error_type": type(exc).__name__}))
            raise
    return {"statusCode": 400, "body": json.dumps({"error": "Unsupported event."})}
