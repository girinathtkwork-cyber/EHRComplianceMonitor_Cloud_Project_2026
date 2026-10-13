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
        "requestContext": {"http": {"method": "POST"}},
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


def test_lambda_s3_upload_triggers_pipeline_and_job_polling(monkeypatch):
    monkeypatch.setenv("STAGE_B_MODE", "stage_a_only")
    monkeypatch.setenv("TRANSCRIPTS_BUCKET", "synthetic-test-bucket")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://ehr.example")
    s3 = _MemoryS3()
    monkeypatch.setattr(stage_a_handler, "_create_s3_client", lambda: s3)

    upload = stage_a_handler.lambda_handler({
        "httpMethod": "POST",
        "path": "/api/upload",
        "headers": {"origin": "https://ehr.example"},
        "body": json.dumps({
            "fileName": "synthetic.txt",
            "content": "Patient reports feeling well.",
        }),
    }, None)
    assert upload["statusCode"] == 202
    assert upload["headers"]["Access-Control-Allow-Origin"] == "https://ehr.example"
    job_id = json.loads(upload["body"])["jobId"]

    pending = stage_a_handler.lambda_handler({
        "httpMethod": "GET",
        "path": f"/api/jobs/{job_id}",
    }, None)
    assert pending["statusCode"] == 202
    assert json.loads(pending["body"])["status"] == "processing"

    stage_a_handler.lambda_handler({
        "Records": [{
            "s3": {
                "bucket": {"name": "synthetic-test-bucket"},
                "object": {"key": f"input/{job_id}.json"},
            },
        }],
    }, None)
    complete = stage_a_handler.lambda_handler({
        "httpMethod": "GET",
        "path": f"/api/jobs/{job_id}",
    }, None)
    body = json.loads(complete["body"])
    assert complete["statusCode"] == 200
    assert body["status"] == "completed"
    assert body["result"]["stage_b"]["mode"] == "stage_a_only"

    failed_id = "00000000-0000-0000-0000-000000000001"
    s3.objects[("synthetic-test-bucket", f"output/stage_b/{failed_id}.status.json")] = json.dumps({"status": "failed"}).encode()
    failed = stage_a_handler.lambda_handler({
        "httpMethod": "GET",
        "path": f"/api/jobs/{failed_id}",
    }, None)
    assert json.loads(failed["body"])["status"] == "failed"
