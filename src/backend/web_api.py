"""Synchronous website/API adapter for the existing clinical review pipeline."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import PurePath

from .stage_a.pipeline import run_stage_a
from .stage_b.stage_b import MODES, run_stage_b
from .completeness import assess_completeness

MAX_TRANSCRIPT_CHARS = 100_000


def _mode() -> str:
    mode = os.environ.get("STAGE_B_MODE")
    if not mode:
        mode = "llm_rag" if os.environ.get("GEMINI_API_KEY") else "stage_a_only"
    if mode not in MODES:
        raise RuntimeError("STAGE_B_MODE must be stage_a_only, llm_plain, or llm_rag.")
    return mode


def _normalize_record(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("A transcript must be a JSON object.")
    transcript = payload.get("transcript", payload.get("transcription"))
    source_transcript = payload.get("sourceTranscript", payload.get("source_transcript", ""))
    if not isinstance(transcript, str) or not transcript.strip():
        raise ValueError("Enter a transcript to analyse.")
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        raise ValueError(f"Transcript must be {MAX_TRANSCRIPT_CHARS} characters or fewer.")
    if not isinstance(source_transcript, str):
        raise ValueError("sourceTranscript must be a string.")
    if len(source_transcript) > MAX_TRANSCRIPT_CHARS:
        raise ValueError(f"Source transcript must be {MAX_TRANSCRIPT_CHARS} characters or fewer.")
    return {
        "transcript_id": str(payload.get("transcript_id") or uuid.uuid4()),
        "transcription": transcript.strip(),
        "source_transcript": source_transcript.strip(),
    }


def normalize_upload_file(file_name: object, content: object) -> dict:
    """Convert one JSON or plain-text upload to the S3 pipeline's input schema."""
    if not isinstance(file_name, str) or PurePath(file_name).suffix.lower() not in {".json", ".txt"}:
        raise ValueError("Upload a .json or .txt transcript file.")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("The selected transcript file is empty.")
    if len(content) > 1_000_000:
        raise ValueError("Transcript file must be 1 MB or smaller.")
    if file_name.lower().endswith(".txt"):
        payload = {"transcript": content}
    else:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("The .json file must contain a valid JSON object.") from exc
    return _normalize_record(payload)


def format_result(result: dict) -> dict:
    formatted = {
        "transcript_id": result.get("transcript_id"),
        "verification_status": result.get("verification_status"),
        "stage_a_version": result.get("stage_a_version"),
        "stage_b_version": result.get("stage_b_version"),
        "stage_a_summary": result.get("stage_a_summary", {}),
        "entities": result.get("entities", []),
        "stage_b_status": result.get("stage_b_status", "error"),
        "stage_b": result.get("stage_b", {}),
        "stage_b_summary": result.get("stage_b_summary", {}),
        "stage_b_findings": [
            {**finding, "reviewStatus": finding.get("reviewStatus", "PENDING")}
            for finding in result.get("stage_b_findings", [])
        ],
        "error": result.get("error"),
    }
    if result.get("verification_status") != "error":
        formatted["completeness_review"] = assess_completeness(
            str(result.get("transcription", "")), result.get("entities", [])
        )
    return formatted


def _process_record(record: dict) -> dict:
    stage_a_record = run_stage_a(record)
    if stage_a_record.get("verification_status") not in ("completed", "partial"):
        return {
            "transcript_id": stage_a_record["transcript_id"],
            "verification_status": "error",
            "stage_a_version": stage_a_record.get("stage_a_version"),
            "error": {"code": "stage_a_failed", "message": "Stage A could not process the transcript."},
        }

    return format_result(run_stage_b(stage_a_record, mode=_mode()))


def process_transcript(payload: object) -> dict:
    """Validate a website request and return the stable, PHI-minimized result."""
    return _process_record(_normalize_record(payload))


def process_upload_file(file_name: object, content: object, transcript_id: object = None) -> dict:
    """Process one uploaded transcript using the same pipeline as paste review."""
    record = normalize_upload_file(file_name, content)
    if transcript_id:
        record["transcript_id"] = str(transcript_id)
    return _process_record(record)
