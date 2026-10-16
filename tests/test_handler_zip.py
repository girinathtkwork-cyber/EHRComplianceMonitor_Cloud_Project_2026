"""
tests/test_handler_zip.py — Lambda deployment package isolation test
---------------------------------------------------------------------
Extracts build/stage_a_lambda.zip into an isolated temporary directory,
and invokes stage_a_handler.lambda_handler with a stub S3 client, with
ONLY that temporary directory on the Python path and that temporary
directory as the working directory.
"""

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ZIP_PATH = _PROJECT_ROOT / "build" / "stage_a_lambda.zip"


def test_lambda_zip_isolated_execution():
    """Verify build/stage_a_lambda.zip runs in total isolation."""
    # Ensure zip reflects current source files
    from scripts.build_lambda import build_lambda_zip
    build_lambda_zip()

    assert _ZIP_PATH.exists(), f"Lambda zip missing at {_ZIP_PATH}"

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)

        # 1. Extract zip into temp directory
        with zipfile.ZipFile(_ZIP_PATH, "r") as zf:
            zf.extractall(tmp_dir)

        # Verify key extracted artifacts
        assert (tmp_dir / "stage_a_handler.py").exists()
        assert (tmp_dir / "worker_handler.py").exists()
        assert (tmp_dir / "backend" / "web_api.py").exists()
        assert (tmp_dir / "backend" / "cloud_jobs.py").exists()
        assert (tmp_dir / "backend" / "stage_a" / "pipeline.py").exists()
        assert (tmp_dir / "backend" / "stage_b" / "audit.py").exists()
        assert (tmp_dir / "backend" / "stage_b" / "stage_b.py").exists()
        assert (tmp_dir / "backend" / "completeness.py").exists()
        assert (tmp_dir / "dataset" / "reference" / "rxnorm_lexicon.json").exists()
        assert (tmp_dir / "dataset" / "reference" / "icd10cm_codes.json").exists()
        assert (tmp_dir / "dataset" / "reference" / "icd10cm_index.json").exists()

        # 2. Write an isolated test runner script in tmp_dir
        runner_script = tmp_dir / "_run_isolated_test.py"
        runner_script.write_text(r"""
import sys
import os
import json
import hashlib

# Ensure ONLY the temp directory (zip root) and stdlib are on sys.path
zip_root = os.path.abspath(os.path.dirname(__file__))
# Remove any other non-stdlib paths
sys.path = [zip_root] + [p for p in sys.path if "site-packages" not in p and "EHRComplianceMonitor" not in p]

# Working directory must be zip_root
os.chdir(zip_root)

# Create input directory and test record
os.makedirs("input", exist_ok=True)
os.makedirs("output/stage_b", exist_ok=True)

test_record = {
    "transcript_id": "iso_test_01",
    "transcription": (
        "DIAGNOSIS: Essential hypertension.\n"
        "MEDICATIONS: Aspirin 81mg orally daily.\n"
        "PROCEDURE: None."
    )
}
with open("input/test_record.json", "w", encoding="utf-8") as f:
    json.dump([test_record], f)

# Stub S3 client
class StubS3Client:
    class PreconditionFailed(Exception):
        response = {"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}}

    @staticmethod
    def _etag(data):
        return '"' + hashlib.md5(data).hexdigest() + '"'

    def get_object(self, Bucket, Key):
        try:
            with open(Key, "rb") as f:
                data = f.read()
        except FileNotFoundError as exc:
            exc.response = {"Error": {"Code": "NoSuchKey"}}
            raise
        class Body:
            def __init__(self, d): self.d = d
            def read(self): return self.d
        return {"Body": Body(data), "ETag": self._etag(data)}

    def put_object(self, Bucket, Key, Body, ContentType=None, IfNoneMatch=None, IfMatch=None):
        os.makedirs(os.path.dirname(Key), exist_ok=True)
        data = Body if isinstance(Body, bytes) else Body.encode("utf-8")
        if IfNoneMatch == "*" and os.path.exists(Key):
            raise self.PreconditionFailed()
        if IfMatch is not None:
            if not os.path.exists(Key):
                raise self.PreconditionFailed()
            with open(Key, "rb") as current:
                if self._etag(current.read()) != IfMatch:
                    raise self.PreconditionFailed()
        with open(Key, "w", encoding="utf-8") as f:
            f.write(data.decode("utf-8"))
        return {"ETag": self._etag(data)}

class StubContext:
    def get_remaining_time_in_millis(self):
        return 300000

# Import handler from isolated zip_root
import stage_a_handler
stage_a_handler._create_s3_client = lambda: StubS3Client()

event = {
    "Records": [
        {
            "s3": {
                "bucket": {"name": "isolated-test-bucket"},
                "object": {"key": "input/test_record.json"}
            }
        }
    ]
}

# Run handler in simulated Lambda environment
os.environ["AWS_LAMBDA_FUNCTION_NAME"] = "isolated-test-func"
os.environ["STAGE_B_MODE"] = "stage_a_only"
os.environ["TRANSCRIPTS_BUCKET"] = "isolated-test-bucket"
response = stage_a_handler.lambda_handler(event, StubContext())
assert response["statusCode"] == 200, f"Handler failed: {response}"

# Verify output
out_key = "output/stage_b/test_record.audited.json"
assert os.path.exists(out_key), f"Expected output missing: {out_key}"

with open(out_key, "r", encoding="utf-8") as f:
    out_data = json.load(f)

assert len(out_data) == 1
rec = out_data[0]
assert rec["transcript_id"] == "iso_test_01"
assert rec["verification_status"] == "completed"
assert "entities" in rec
assert rec["stage_a_summary"]["n_entities"] >= 2
assert rec["stage_b_status"] == "completed"
assert rec["stage_b"]["mode"] == "stage_a_only"
assert "stage_b_findings" in rec

# Verify the packaged API direct-review route.
api_response = stage_a_handler.lambda_handler({
    "httpMethod": "POST",
    "path": "/api/analyse",
    "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "synthetic-test-user"}}}},
    "body": json.dumps({"transcript": "Patient reports feeling well."}),
}, StubContext())
assert api_response["statusCode"] == 200
api_result = json.loads(api_response["body"])
assert api_result["verification_status"] == "completed"
assert "transcription" not in api_result
assert api_result["completeness_review"]["method"] == "deterministic_context_rules"
unknown_http_response = stage_a_handler.lambda_handler({
    "requestContext": {"http": {"method": "GET"}},
    "rawPath": "/not-a-route",
    "Records": [],
}, StubContext())
assert unknown_http_response["statusCode"] == 401, "HTTP requests without JWT claims must be rejected."

lock_acquired, lock_etag = stage_a_handler._acquire_s3_job_lock(
    StubS3Client(), "isolated-test-bucket", "lock-concurrency-test"
)
assert lock_acquired and lock_etag
duplicate_lock, _ = stage_a_handler._acquire_s3_job_lock(
    StubS3Client(), "isolated-test-bucket", "lock-concurrency-test"
)
assert not duplicate_lock, "Only one concurrent S3 event may own the job lock."
stage_a_handler._set_s3_job_lock_status(
    StubS3Client(), "isolated-test-bucket", "lock-concurrency-test", lock_etag, "failed"
)
retry_lock, _ = stage_a_handler._acquire_s3_job_lock(
    StubS3Client(), "isolated-test-bucket", "lock-concurrency-test"
)
assert retry_lock, "A failed job lock must be reclaimable for an S3 event retry."

# The legacy S3-triggered pipeline remains active, while job API routes require JWT claims.
job_id = "test_record"
stage_a_calls = []
original_run_stage_a = stage_a_handler.run_stage_a
def count_stage_a_calls(record):
    stage_a_calls.append(record)
    return original_run_stage_a(record)
stage_a_handler.run_stage_a = count_stage_a_calls
stage_a_handler.lambda_handler({
    "Records": [{
        "s3": {
            "bucket": {"name": "isolated-test-bucket"},
            "object": {"key": f"input/{job_id}.json"}
        }
    }]
}, StubContext())
assert not stage_a_calls, "A duplicate completed S3 event must not rerun the pipeline."
job_response = stage_a_handler.lambda_handler({
    "httpMethod": "GET",
    "path": f"/api/jobs/{job_id}",
}, StubContext())
assert job_response["statusCode"] == 401, "Job details must require an authenticated JWT."

failed_job_id = "00000000-0000-0000-0000-000000000001"
stub_s3 = StubS3Client()
stub_s3.put_object(
    Bucket="isolated-test-bucket",
    Key=f"output/stage_b/{failed_job_id}.status.json",
    Body=json.dumps({"status": "failed"}),
)
stub_s3.put_object(
    Bucket="isolated-test-bucket",
    Key=f"output/stage_b/{failed_job_id}.audited.json",
    Body=json.dumps([rec]),
)
failed_job_response = stage_a_handler.lambda_handler({
    "httpMethod": "GET",
    "path": f"/api/jobs/{failed_job_id}",
}, StubContext())
assert failed_job_response["statusCode"] == 401
print("ISOLATED_TEST_SUCCESS")
""", encoding="utf-8")

        # 3. Execute runner script in a clean subprocess
        env = {
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(tmp_dir),
            "AWS_LAMBDA_FUNCTION_NAME": "isolated-test-func",
        }

        proc = subprocess.run(
            [sys.executable, str(runner_script)],
            cwd=str(tmp_dir),
            env=env,
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0, f"Process failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        assert "ISOLATED_TEST_SUCCESS" in proc.stdout
