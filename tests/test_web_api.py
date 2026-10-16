import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "aws" / "lambda"))

from backend.web_api import process_transcript
from backend.web_api import normalize_upload_file
import stage_a_handler


class _Body:
    def __init__(self, value):
        self.value = value

    def read(self):
        return self.value


class _MemoryS3:
    class _PreconditionFailed(Exception):
        response = {
            "Error": {"Code": "PreconditionFailed"},
            "ResponseMetadata": {"HTTPStatusCode": 412},
        }

    def __init__(self):
        self.objects = {}

    @staticmethod
    def _etag(value):
        return '"' + hashlib.md5(value).hexdigest() + '"'

    def put_object(self, *, Bucket, Key, Body, ContentType, IfNoneMatch=None, IfMatch=None):
        object_key = (Bucket, Key)
        value = Body.encode("utf-8") if isinstance(Body, str) else Body
        existing = self.objects.get(object_key)
        if IfNoneMatch == "*" and existing is not None:
            raise self._PreconditionFailed()
        if IfMatch is not None and (existing is None or self._etag(existing) != IfMatch):
            raise self._PreconditionFailed()
        self.objects[object_key] = value
        return {"ETag": self._etag(value)}

    def get_object(self, *, Bucket, Key):
        try:
            value = self.objects[(Bucket, Key)]
        except KeyError as exc:
            exc.response = {"Error": {"Code": "NoSuchKey"}}
            raise
        return {"Body": _Body(value), "ETag": self._etag(value)}


def test_process_transcript_runs_both_stages_and_omits_transcript(monkeypatch):
    monkeypatch.setenv("STAGE_B_MODE", "stage_a_only")
    result = process_transcript({"transcript": "Patient reports feeling well.", "sourceTranscript": ""})

    assert result["verification_status"] == "completed"
    assert result["stage_b_status"] == "completed"
    assert result["stage_b"]["mode"] == "stage_a_only"
    assert "transcription" not in result
    assert "source_transcript" not in result


def test_process_transcript_validates_payload_and_size(monkeypatch):
    monkeypatch.setenv("STAGE_B_MODE", "stage_a_only")
    for payload in (None, {}, {"transcript": " "}, {"transcript": "x" * 100_001}):
        try:
            process_transcript(payload)
        except ValueError:
            continue
        raise AssertionError("Invalid request must be rejected.")


def test_json_upload_validation():
    record = normalize_upload_file(
        "synthetic.json",
        json.dumps({"transcription": "Patient reports feeling well.", "source_transcript": ""}),
    )
    assert record["transcription"] == "Patient reports feeling well."
    for name, content in (
        ("synthetic.json", "{"),
        ("synthetic.json", "[]"),
        ("synthetic.txt", ""),
        ("notes.csv", "not a transcript"),
    ):
        try:
            normalize_upload_file(name, content)
        except ValueError:
            continue
        raise AssertionError("Invalid upload must be rejected.")


def test_lambda_http_health_cors_and_preflight(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://ehr.example")
    health = stage_a_handler.lambda_handler({
        "httpMethod": "GET",
        "path": "/health",
        "headers": {"origin": "https://ehr.example"},
    }, None)
    assert health["statusCode"] == 200
    assert health["headers"]["Access-Control-Allow-Origin"] == "https://ehr.example"
    assert json.loads(health["body"])["status"] == "ok"

    preflight = stage_a_handler.lambda_handler({
        "requestContext": {"http": {"method": "OPTIONS"}},
        "rawPath": "/api/analyse",
        "headers": {"origin": "https://unapproved.example"},
    }, None)
    assert preflight["statusCode"] == 204
    assert "Access-Control-Allow-Origin" not in preflight["headers"]


def test_lambda_http_analysis_uses_pipeline_and_returns_validation_errors(monkeypatch):
    monkeypatch.setenv("STAGE_B_MODE", "stage_a_only")
    event = {
        "requestContext": {"http": {"method": "POST"}, "authorizer": {"jwt": {"claims": {"sub": "synthetic-user"}}}},
        "rawPath": "/api/analyse",
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"transcript": "Patient reports feeling well."}),
    }
    response = stage_a_handler.lambda_handler(event, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["verification_status"] == "completed"
    assert body["stage_b"]["mode"] == "stage_a_only"

    event["body"] = json.dumps({"transcript": ""})
    response = stage_a_handler.lambda_handler(event, None)
    assert response["statusCode"] == 400
    assert "transcript" in json.loads(response["body"])["error"].lower()


def test_cloud_job_routes_require_authorized_http_api_context(monkeypatch):
    unauthenticated = stage_a_handler.lambda_handler({
        "httpMethod": "POST", "path": "/api/upload",
        "body": json.dumps({"fileName": "synthetic.txt", "content": "Synthetic note."}),
    }, None)
    assert unauthenticated["statusCode"] == 401

    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")
    health = stage_a_handler.lambda_handler({"httpMethod": "GET", "path": "/health"}, None)
    assert json.loads(health["body"]) == {"status": "ok"}


class _RemainingTime:
    def __init__(self, values):
        self.values = iter(values)

    def get_remaining_time_in_millis(self):
        return next(self.values)


def _mock_s3_pipeline(monkeypatch):
    def run_stage_a(record):
        if record.get("transcription") == "stage-a-failure":
            return {"transcript_id": record.get("transcript_id"), "verification_status": "error", "stage_a_version": "test"}
        return {**record, "verification_status": "completed", "stage_a_version": "test", "entities": [], "stage_a_summary": {}}

    def run_stage_b(record, mode):
        return {**record, "stage_b_status": "completed", "stage_b": {"mode": mode, "llm_status": "skipped"},
                "stage_b_summary": {}, "stage_b_findings": []}

    monkeypatch.setattr(stage_a_handler, "run_stage_a", run_stage_a)
    monkeypatch.setattr(stage_a_handler, "run_stage_b", run_stage_b)
    monkeypatch.setattr(stage_a_handler.rxnorm_client, "save_cache", lambda: None)


def _run_s3_and_poll(monkeypatch, input_payload, *, context=None, job_id="batch-test"):
    bucket = "synthetic-test-bucket"
    s3 = _MemoryS3()
    s3.put_object(Bucket=bucket, Key=f"input/{job_id}.json", Body=json.dumps(input_payload), ContentType="application/json")
    acquired, lock_etag = stage_a_handler._acquire_s3_job_lock(s3, bucket, job_id)
    assert acquired
    stage_a_handler._run_s3_job(s3, bucket, f"input/{job_id}.json", context, lock_etag)
    monkeypatch.setenv("TRANSCRIPTS_BUCKET", bucket)
    monkeypatch.setattr(stage_a_handler, "_create_s3_client", lambda: s3)
    response = stage_a_handler._get_job(job_id, None)
    return json.loads(response["body"]), s3


def test_s3_job_polling_preserves_single_record_contract(monkeypatch):
    _mock_s3_pipeline(monkeypatch)
    body, _ = _run_s3_and_poll(monkeypatch, {"transcript_id": "one", "transcription": "synthetic"})
    assert body["status"] == "completed"
    assert body["result"]["transcript_id"] == "one"
    assert body["results"] == [body["result"]]
    assert body["expected_count"] == body["processed_count"] == 1


def test_s3_job_polling_returns_all_multi_record_results(monkeypatch):
    _mock_s3_pipeline(monkeypatch)
    body, _ = _run_s3_and_poll(monkeypatch, [
        {"transcript_id": "one", "transcription": "synthetic"},
        {"transcript_id": "two", "transcription": "synthetic"},
    ])
    assert body["status"] == "completed"
    assert [item["transcript_id"] for item in body["results"]] == ["one", "two"]
    assert body["result"] == body["results"][0]


def test_s3_job_returns_invalid_and_mixed_record_outcomes(monkeypatch):
    _mock_s3_pipeline(monkeypatch)
    body, _ = _run_s3_and_poll(monkeypatch, [
        {"transcript_id": "ok", "transcription": "synthetic"},
        {"transcript_id": "stage-a-bad", "transcription": "stage-a-failure"},
        None,
    ])
    assert body["status"] == "partial"
    assert len(body["results"]) == 3
    assert body["results"][0]["verification_status"] == "completed"
    assert body["results"][1]["verification_status"] == "error"
    assert body["results"][2]["verification_status"] == "error"
    assert body["successful_count"] == 1
    assert body["failed_count"] == 2


def test_s3_job_partial_batch_keeps_counts_and_processed_records(monkeypatch):
    _mock_s3_pipeline(monkeypatch)
    body, _ = _run_s3_and_poll(monkeypatch, [
        {"transcript_id": "one", "transcription": "synthetic"},
        {"transcript_id": "two", "transcription": "synthetic"},
    ], context=_RemainingTime([40_000, 20_000]), job_id="partial-test")
    assert body["status"] == "partial"
    assert len(body["results"]) == 1
    assert body["expected_count"] == 2
    assert body["processed_count"] == 1
    assert body["results"][0]["transcript_id"] == "one"
