"""
Contract validation for Stage A output
-----------------------------------------
Validates Stage A pipeline output against the interface contract.
Hand-written validator — no jsonschema dependency.

Every field is checked for presence, type, and valid enum values.
"""

import datetime
import json
from pathlib import Path


VALID_ENTITY_TYPES = {"medication", "dosage", "measurement", "diagnosis", "procedure"}
VALID_ROUTINGS = {"invalid", "verified_needs_context_check", "unresolved"}
VALID_VERIFICATION_STATUSES = {"completed", "partial", "error"}


def validate_contract(output: dict) -> list[str]:
    """Validate a single Stage A output record against the contract.

    Returns a list of error strings. Empty list = valid.
    """
    errors = []

    def _check(condition: bool, msg: str):
        if not condition:
            errors.append(msg)

    # Top-level required fields
    _check(isinstance(output.get("transcript_id"), (int, str)),
           "transcript_id must be an integer or string")

    # Check for error response
    if output.get("verification_status") == "error":
        err = output.get("error")
        _check(isinstance(err, dict), "error must be a dict when status='error'")
        if isinstance(err, dict):
            _check(isinstance(err.get("code"), str), "error.code must be a string")
            _check(isinstance(err.get("message"), str), "error.message must be a string")
            _check(isinstance(err.get("timestamp"), str), "error.timestamp must be a string")
        return errors

    status = output.get("verification_status")
    _check(isinstance(status, str) and status in VALID_VERIFICATION_STATUSES,
           f"verification_status must be one of {VALID_VERIFICATION_STATUSES}")

    # stage_a_version
    _check(isinstance(output.get("stage_a_version"), str),
           "stage_a_version must be a string")

    # processing_timestamp — ISO 8601
    ts = output.get("processing_timestamp")
    _check(isinstance(ts, str), "processing_timestamp must be a string")
    if isinstance(ts, str):
        try:
            datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"processing_timestamp '{ts}' is not valid ISO 8601")

    # entities array
    entities = output.get("entities")
    _check(isinstance(entities, list), "entities must be a list")
    if isinstance(entities, list):
        for i, entity in enumerate(entities):
            prefix = f"entities[{i}]"
            _check(isinstance(entity, dict), f"{prefix} must be a dict")
            if not isinstance(entity, dict):
                continue

            # entity_type
            et = entity.get("entity_type")
            _check(isinstance(et, str) and et in VALID_ENTITY_TYPES,
                   f"{prefix}.entity_type '{et}' not in {VALID_ENTITY_TYPES}")

            # text
            _check(isinstance(entity.get("text"), str) and len(entity["text"]) > 0,
                   f"{prefix}.text must be a non-empty string")

            # span
            span = entity.get("span")
            _check(isinstance(span, list) and len(span) == 2,
                   f"{prefix}.span must be a list of two ints")
            if isinstance(span, list) and len(span) == 2:
                _check(type(span[0]) is int and type(span[1]) is int,
                       f"{prefix}.span values must be integers")
                valid_span = (
                    type(span[0]) is int and type(span[1]) is int
                    and span[0] >= 0 and span[1] > span[0]
                )
                _check(valid_span,
                       f"{prefix}.span must be non-negative with end > start")
                transcript = output.get("transcription")
                if valid_span and isinstance(transcript, str):
                    _check(span[1] <= len(transcript),
                           f"{prefix}.span extends beyond transcription")
                    if span[1] <= len(transcript) and isinstance(entity.get("text"), str):
                        _check(transcript[span[0]:span[1]] == entity["text"],
                               f"{prefix}.text does not match transcription at span")

            # routing
            routing = entity.get("routing")
            _check(isinstance(routing, str) and routing in VALID_ROUTINGS,
                   f"{prefix}.routing '{routing}' not in {VALID_ROUTINGS}")

            # retrieval_context
            rc = entity.get("retrieval_context")
            _check(isinstance(rc, dict), f"{prefix}.retrieval_context must be a dict")
            if isinstance(rc, dict):
                _check(isinstance(rc.get("evidence_text"), str),
                       f"{prefix}.retrieval_context.evidence_text must be a string")

    # stage_a_summary
    summary = output.get("stage_a_summary")
    _check(isinstance(summary, dict), "stage_a_summary must be a dict")
    if isinstance(summary, dict):
        _check(isinstance(summary.get("n_entities"), int),
               "stage_a_summary.n_entities must be an int")
        _check(isinstance(summary.get("n_invalid"), int),
               "stage_a_summary.n_invalid must be an int")
        _check(isinstance(summary.get("n_forwarded_to_stage_b"), int),
               "stage_a_summary.n_forwarded_to_stage_b must be an int")
        if isinstance(entities, list) and all(isinstance(e, dict) for e in entities):
            _check(summary.get("n_entities") == len(entities),
                   "stage_a_summary.n_entities must match entities")
            _check(summary.get("n_invalid") == sum(e.get("routing") == "invalid" for e in entities),
                   "stage_a_summary.n_invalid must match entity routing")
            _check(
                summary.get("n_forwarded_to_stage_b") == sum(
                    e.get("routing") in ("verified_needs_context_check", "unresolved")
                    for e in entities
                ),
                "stage_a_summary.n_forwarded_to_stage_b must match entity routing",
            )

    return errors


def validate_batch(outputs: list[dict]) -> dict:
    """Validate an array of Stage A outputs.

    Returns {valid: int, invalid: int, errors: [{transcript_id, errors}]}.
    """
    valid = 0
    invalid = 0
    all_errors = []

    for output in outputs:
        errs = validate_contract(output)
        if errs:
            invalid += 1
            all_errors.append({
                "transcript_id": output.get("transcript_id"),
                "errors": errs,
            })
        else:
            valid += 1

    return {
        "valid": valid,
        "invalid": invalid,
        "total": len(outputs),
        "errors": all_errors,
    }
