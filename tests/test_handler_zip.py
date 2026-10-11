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
    # Ensure zip exists
    if not _ZIP_PATH.exists():
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
        assert (tmp_dir / "backend" / "stage_a" / "pipeline.py").exists()
        assert (tmp_dir / "dataset" / "reference" / "rxnorm_lexicon.json").exists()
        assert (tmp_dir / "dataset" / "reference" / "icd10cm_codes.json").exists()
        assert (tmp_dir / "dataset" / "reference" / "icd10cm_index.json").exists()

        # 2. Write an isolated test runner script in tmp_dir
        runner_script = tmp_dir / "_run_isolated_test.py"
        runner_script.write_text(r"""
import sys
import os
import json

# Ensure ONLY the temp directory (zip root) and stdlib are on sys.path
zip_root = os.path.abspath(os.path.dirname(__file__))
# Remove any other non-stdlib paths
sys.path = [zip_root] + [p for p in sys.path if "site-packages" not in p and "EHRComplianceMonitor" not in p]

# Working directory must be zip_root
os.chdir(zip_root)

# Create input directory and test record
os.makedirs("input", exist_ok=True)
os.makedirs("output/stage_a", exist_ok=True)

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
    def get_object(self, Bucket, Key):
        with open(Key, "rb") as f:
            data = f.read()
        class Body:
            def __init__(self, d): self.d = d
            def read(self): return self.d
        return {"Body": Body(data)}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        os.makedirs(os.path.dirname(Key), exist_ok=True)
        with open(Key, "w", encoding="utf-8") as f:
            if isinstance(Body, bytes):
                f.write(Body.decode("utf-8"))
            else:
                f.write(Body)

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
response = stage_a_handler.lambda_handler(event, StubContext())
assert response["statusCode"] == 200, f"Handler failed: {response}"

# Verify output
out_key = "output/stage_a/test_record.stage_a.json"
assert os.path.exists(out_key), f"Expected output missing: {out_key}"

with open(out_key, "r", encoding="utf-8") as f:
    out_data = json.load(f)

assert len(out_data) == 1
rec = out_data[0]
assert rec["transcript_id"] == "iso_test_01"
assert rec["verification_status"] == "completed"
assert "entities" in rec
assert rec["stage_a_summary"]["n_entities"] >= 2
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
