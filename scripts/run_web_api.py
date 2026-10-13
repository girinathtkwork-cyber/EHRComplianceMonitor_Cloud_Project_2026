"""Read one website request from stdin and emit one JSON response to stdout."""

from __future__ import annotations

import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from backend.web_api import process_transcript, process_upload_file


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            if isinstance(payload, dict) and payload.get("operation") == "upload":
                result = process_upload_file(
                    payload.get("fileName"),
                    payload.get("content"),
                    payload.get("transcript_id"),
                )
            else:
                result = process_transcript(payload)
        status = 422 if result.get("verification_status") == "error" else 200
        print(json.dumps({"statusCode": status, "body": result}, separators=(",", ":")))
    except ValueError as exc:
        print(json.dumps({"statusCode": 400, "body": {"error": str(exc)}}))
    except Exception:
        print(json.dumps({
            "statusCode": 500,
            "body": {"error": "The verification pipeline failed unexpectedly."},
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
