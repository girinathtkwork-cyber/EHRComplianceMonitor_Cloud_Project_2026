"""SQS-triggered verification worker."""

from __future__ import annotations

import json
import os

try:
    import boto3
except ImportError:
    boto3 = None


def lambda_handler(event, context):
    if boto3 is None:
        raise RuntimeError("AWS SDK is unavailable.")
    function_name = os.environ.get("PROCESSOR_FUNCTION_NAME")
    if not function_name:
        raise RuntimeError("The worker processor is not configured.")
    try:
        response = boto3.client("lambda").invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(event, separators=(",", ":")).encode("utf-8"),
        )
        if response.get("FunctionError"):
            raise RuntimeError("The processor Lambda returned an invocation error.")
        result = json.loads(response["Payload"].read().decode("utf-8"))
        return result if isinstance(result, dict) and isinstance(result.get("batchItemFailures"), list) else {"batchItemFailures": []}
    except Exception as exc:
        print(json.dumps({"event": "worker_invoke_failed", "error_type": type(exc).__name__}))
        return {"batchItemFailures": [{"itemIdentifier": record.get("messageId", "unknown")} for record in event.get("Records", [])]}
