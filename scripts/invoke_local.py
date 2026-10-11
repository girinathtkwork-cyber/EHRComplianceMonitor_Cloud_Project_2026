"""
scripts/invoke_local.py — Local Lambda invocation + parity test
-----------------------------------------------------------------
Builds a fake S3 event, swaps in a stub S3 client, runs the real handler,
and checks the output matches the CLI pipeline output.

Usage:
    python scripts/invoke_local.py
"""

import json
import sys
import time
from pathlib import Path

# Fix sys.path
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))
sys.path.insert(0, str(_PROJECT_ROOT / "src" / "aws" / "lambda"))


# =========================================================================
# Stub S3 client
# =========================================================================

class LocalS3Client:
    """Stub S3 client that reads/writes local files."""

    def __init__(self, root: Path):
        self.root = root

    def get_object(self, Bucket, Key):
        path = self.root / Key
        if not path.exists():
            raise FileNotFoundError(f"Local file not found: {path}")
        content = path.read_bytes()

        class Body:
            def __init__(self, data):
                self._data = data
            def read(self):
                return self._data

        return {"Body": Body(content)}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        path = self.root / Key
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(Body, bytes):
            path.write_bytes(Body)
        else:
            path.write_text(Body, encoding="utf-8")
        print(f"  [LocalS3] Wrote {path} ({path.stat().st_size} bytes)")


class FakeContext:
    """Fake Lambda context with generous time limit."""
    def get_remaining_time_in_millis(self):
        return 300_000  # 5 minutes


def main():
    # Use the 20-record sample if available, otherwise create one
    sample_path = _PROJECT_ROOT / "dataset" / "processed" / "sample_20_for_aws_test.json"
    if not sample_path.exists():
        print("Creating sample first...")
        import subprocess
        subprocess.run(
            [sys.executable, str(_PROJECT_ROOT / "scripts" / "make_sample.py")],
            check=True
        )

    if not sample_path.exists():
        print("Error: Could not create sample file")
        sys.exit(1)

    # Copy sample to input/ directory (Lambda expects input/ prefix)
    input_dir = _PROJECT_ROOT / "input"
    input_dir.mkdir(exist_ok=True)
    input_path = input_dir / "sample_20_for_aws_test.json"

    import shutil
    shutil.copy2(sample_path, input_path)
    print(f"Copied sample to {input_path}")

    # Import the handler (after sys.path is set)
    import stage_a_handler

    # Monkey-patch the S3 client
    stage_a_handler._create_s3_client = lambda: LocalS3Client(_PROJECT_ROOT)

    # Build fake S3 event
    event = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "local-test-bucket"},
                    "object": {"key": "input/sample_20_for_aws_test.json"},
                }
            }
        ]
    }

    # Run Lambda handler
    print("\n--- Running Lambda handler ---")
    start = time.time()
    response = stage_a_handler.lambda_handler(event, FakeContext())
    lambda_elapsed = time.time() - start
    print(f"Lambda response: {response}")
    print(f"Lambda elapsed: {lambda_elapsed:.1f}s")

    # Load Lambda output
    lambda_output_path = _PROJECT_ROOT / "output" / "stage_a" / "sample_20_for_aws_test.stage_a.json"
    if not lambda_output_path.exists():
        print(f"Error: Lambda output not found at {lambda_output_path}")
        sys.exit(1)

    with open(lambda_output_path, encoding="utf-8") as f:
        lambda_output = json.load(f)

    # Handle truncated output
    if isinstance(lambda_output, dict) and "records" in lambda_output:
        lambda_records = lambda_output["records"]
    else:
        lambda_records = lambda_output

    # Run CLI pipeline on same input
    print("\n--- Running CLI pipeline ---")
    from backend.stage_a.pipeline import run_stage_a_batch

    with open(input_path, encoding="utf-8") as f:
        input_data = json.load(f)

    start = time.time()
    cli_output = run_stage_a_batch(input_data)
    cli_elapsed = time.time() - start
    print(f"CLI elapsed: {cli_elapsed:.1f}s")

    # Parity check
    print("\n--- Parity Check ---")
    errors = []

    if len(lambda_records) != len(cli_output):
        errors.append(f"Record count mismatch: Lambda={len(lambda_records)}, CLI={len(cli_output)}")
    else:
        for i, (l_rec, c_rec) in enumerate(zip(lambda_records, cli_output)):
            l_tid = l_rec.get("transcript_id")
            c_tid = c_rec.get("transcript_id")
            if l_tid != c_tid:
                errors.append(f"Record {i}: transcript_id mismatch {l_tid} vs {c_tid}")
                continue

            # Check same number of entities
            l_ents = l_rec.get("entities", [])
            c_ents = c_rec.get("entities", [])
            if len(l_ents) != len(c_ents):
                errors.append(
                    f"Record {l_tid}: entity count mismatch "
                    f"Lambda={len(l_ents)}, CLI={len(c_ents)}"
                )
                continue

            # Check routing decisions match
            for j, (le, ce) in enumerate(zip(l_ents, c_ents)):
                if le.get("routing") != ce.get("routing"):
                    errors.append(
                        f"Record {l_tid}, entity {j}: routing mismatch "
                        f"Lambda={le.get('routing')}, CLI={ce.get('routing')}"
                    )

    if errors:
        print(f"PARITY CHECK FAILED with {len(errors)} errors:")
        for err in errors[:10]:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print(f"PARITY CHECK PASSED!")
        print(f"  Lambda records: {len(lambda_records)}")
        print(f"  CLI records: {len(cli_output)}")
        print(f"  All transcript_ids, entity counts, and routing decisions match.")

    # Cleanup
    print("\nDone.")


if __name__ == "__main__":
    main()
