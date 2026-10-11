"""
Stage A Pipeline — Main Orchestrator
---------------------------------------
Processes a single medical transcript record through the full Stage A
retrieval pipeline: extract → verify → build contract output.

This is the RAG retrieval half of the two-stage system. Stage A's output
carries retrieved facts (evidence_text) for Stage B's LLM to use.
"""

import datetime
import traceback

from .extract import extract_entities
from .verify import verify_entities
from .contract import validate_contract
from . import rxnorm_client

STAGE_A_VERSION = "2.0.0"


def run_stage_a(record: dict) -> dict:
    """Process a single transcript record through Stage A.

    Args:
        record: dict with at minimum {transcript_id, transcription}.
               May also contain ground_truth_label, medical_specialty, etc.

    Returns:
        dict conforming to the interface contract. Keeps every existing field
        and adds: entities[], stage_a_summary, stage_a_version,
        verification_status, processing_timestamp.
    """
    transcript_id = record.get("transcript_id", -1)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    try:
        text = record.get("transcription", "")
        if not text:
            raise ValueError("Empty transcription text")

        # Step 1: Extract entities
        raw_entities = extract_entities(text)

        # Step 2: Verify and enrich entities
        verified_entities = verify_entities(raw_entities)

        # Step 3: Build summary
        n_entities = len(verified_entities)
        n_invalid = sum(1 for e in verified_entities if e.get("routing") == "invalid")
        n_forwarded = sum(
            1 for e in verified_entities
            if e.get("routing") == "verified_needs_context_check"
        )

        # Step 4: Build output — keep all existing fields
        output = {}
        for key, value in record.items():
            output[key] = value

        # Add Stage A fields
        output["verification_status"] = "completed"
        output["processing_timestamp"] = now
        output["stage_a_version"] = STAGE_A_VERSION
        output["entities"] = verified_entities
        output["stage_a_summary"] = {
            "n_entities": n_entities,
            "n_invalid": n_invalid,
            "n_forwarded_to_stage_b": n_forwarded,
        }

        return output

    except Exception as exc:
        # Per-record try/except: a failure writes an error entry
        return {
            "transcript_id": transcript_id,
            "verification_status": "error",
            "error": {
                "code": type(exc).__name__,
                "message": str(exc),
                "timestamp": now,
            },
            "stage_a_version": STAGE_A_VERSION,
        }


def run_stage_a_batch(
    records: list[dict],
    *,
    progress_callback=None,
) -> list[dict]:
    """Process a batch of transcript records.

    Args:
        records: list of transcript record dicts.
        progress_callback: optional callable(i, total) for progress reporting.

    Returns:
        list of Stage A outputs, ordered by transcript_id.
    """
    results = []
    total = len(records)

    for i, record in enumerate(records):
        result = run_stage_a(record)
        results.append(result)

        if progress_callback and (i + 1) % 10 == 0:
            progress_callback(i + 1, total)

    # Save cache after batch
    rxnorm_client.save_cache()

    # Sort by transcript_id for deterministic output
    results.sort(key=lambda r: r.get("transcript_id", 0))
    return results
