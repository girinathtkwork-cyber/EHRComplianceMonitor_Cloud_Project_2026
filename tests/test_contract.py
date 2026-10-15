"""
Tests for contract validation (M4).
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from backend.stage_a.contract import validate_contract, validate_batch


def _make_valid_output(transcript_id=1):
    """Build a minimal valid Stage A output."""
    return {
        "transcript_id": transcript_id,
        "transcription": " " * 10 + "Aspirin" + " " * 33 + "Hypertension",
        "verification_status": "completed",
        "processing_timestamp": "2026-10-10T12:00:00+00:00",
        "stage_a_version": "2.0.0",
        "entities": [
            {
                "entity_type": "medication",
                "text": "Aspirin",
                "span": [10, 17],
                "routing": "verified_needs_context_check",
                "retrieval_context": {
                    "evidence_text": "[RxNorm] Aspirin exists",
                    "exists": True,
                },
            },
            {
                "entity_type": "diagnosis",
                "text": "Hypertension",
                "span": [50, 62],
                "routing": "invalid",
                "retrieval_context": {
                    "evidence_text": "[ICD-10-CM] No match",
                    "flags": [{"flag_type": "no_confident_icd10_match"}],
                },
            },
        ],
        "stage_a_summary": {
            "n_entities": 2,
            "n_invalid": 1,
            "n_forwarded_to_stage_b": 1,
        },
    }


class TestContractValidation:
    def test_valid_output_passes(self):
        output = _make_valid_output()
        errors = validate_contract(output)
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_additive_verification_metadata_is_backward_compatible(self):
        output = _make_valid_output()
        output["entities"][0]["assertion_status"] = "negated"
        output["entities"][0]["retrieval_context"].update({
            "verification_status": "lookup_unavailable",
            "verification_method": "RxNorm exact lookup",
        })
        assert validate_contract(output) == []

    def test_missing_transcript_id(self):
        output = _make_valid_output()
        del output["transcript_id"]
        errors = validate_contract(output)
        assert any("transcript_id" in e for e in errors)

    def test_invalid_verification_status(self):
        output = _make_valid_output()
        output["verification_status"] = "unknown_status"
        errors = validate_contract(output)
        assert any("verification_status" in e for e in errors)

    def test_invalid_entity_type(self):
        output = _make_valid_output()
        output["entities"][0]["entity_type"] = "unknown"
        errors = validate_contract(output)
        assert any("entity_type" in e for e in errors)

    def test_invalid_routing(self):
        output = _make_valid_output()
        output["entities"][0]["routing"] = "maybe_invalid"
        errors = validate_contract(output)
        assert any("routing" in e for e in errors)

    def test_missing_evidence_text(self):
        output = _make_valid_output()
        del output["entities"][0]["retrieval_context"]["evidence_text"]
        errors = validate_contract(output)
        assert any("evidence_text" in e for e in errors)

    def test_bad_timestamp(self):
        output = _make_valid_output()
        output["processing_timestamp"] = "not-a-date"
        errors = validate_contract(output)
        assert any("timestamp" in e.lower() or "iso" in e.lower() for e in errors)

    def test_error_status_valid(self):
        output = {
            "transcript_id": 1,
            "verification_status": "error",
            "error": {
                "code": "ValueError",
                "message": "Empty text",
                "timestamp": "2026-10-10T12:00:00+00:00",
            },
            "stage_a_version": "2.0.0",
        }
        errors = validate_contract(output)
        assert errors == []

    def test_bad_span(self):
        output = _make_valid_output()
        output["entities"][0]["span"] = [20, 10]  # end < start
        errors = validate_contract(output)
        assert any("span" in e for e in errors)

    def test_span_must_point_to_entity_text_in_transcript(self):
        output = _make_valid_output()
        output["entities"][0]["span"] = [0, 7]
        errors = validate_contract(output)
        assert any("does not match transcription" in e for e in errors)

    def test_span_cannot_extend_beyond_transcript(self):
        output = _make_valid_output()
        output["entities"][0]["span"] = [100, 107]
        errors = validate_contract(output)
        assert any("extends beyond transcription" in e for e in errors)

    def test_summary_must_match_entity_routing(self):
        output = _make_valid_output()
        output["stage_a_summary"]["n_forwarded_to_stage_b"] = 2
        errors = validate_contract(output)
        assert any("n_forwarded_to_stage_b must match" in e for e in errors)

    def test_validate_batch(self):
        outputs = [_make_valid_output(i) for i in range(10)]
        result = validate_batch(outputs)
        assert result["valid"] == 10
        assert result["invalid"] == 0
