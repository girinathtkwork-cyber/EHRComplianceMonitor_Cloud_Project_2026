import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from backend.stage_b.audit import audit_record


def _stage_a_record():
    return {
        "transcript_id": "case-1",
        "transcription": "Diagnosis: otitis media. Medication: amoxicillin 750mg.",
        "verification_status": "completed",
        "entities": [
            {
                "entity_type": "diagnosis",
                "text": "otitis media",
                "span": [11, 23],
                "routing": "verified_needs_context_check",
                "retrieval_context": {
                    "evidence_text": "[ICD-10-CM 2027] H6690: Otitis media",
                    "icd10_candidates": [{"code": "H6690", "description": "Otitis media", "score": 0.8}],
                },
            },
            {
                "entity_type": "medication",
                "text": "amoxicillin",
                "span": [37, 48],
                "routing": "verified_needs_context_check",
                "retrieval_context": {
                    "evidence_text": "[RxNorm] amoxicillin exists\n[MED-RT] Indications: Otitis Media",
                    "indications": {
                        "indications": [{"condition": "Otitis Media", "relationship": "may_treat"}],
                        "indications_status": "found",
                    },
                },
            },
            {
                "entity_type": "dosage",
                "text": "750mg",
                "span": [49, 54],
                "routing": "verified_needs_context_check",
                "retrieval_context": {
                    "evidence_text": "Dose 750mg associated with amoxicillin",
                    "flags": [{"flag_type": "dose_not_a_known_strength", "detail": "not among strengths"}],
                },
            },
        ],
    }


def test_audit_record_adds_stage_b_summary_and_findings():
    output = audit_record(_stage_a_record(), source_transcript="Diagnosis: otitis media.")

    assert output["stage_b_status"] == "completed"
    assert output["stage_b_summary"]["n_findings"] >= 2
    categories = {item["category"] for item in output["stage_b_findings"]}
    assert "Dose requires confirmation" in categories
    assert "Unsupported source claim" in categories
    finding = next(item for item in output["stage_b_findings"] if item["category"] == "Unsupported source claim")
    assert finding["detection_stage"] == "stage_b_deterministic"
    assert finding["provenance"] == "deterministic_audit_rule"
    assert "violation" in finding["uncertainty"]


def test_supported_indication_does_not_flag_medication_indication():
    output = audit_record(_stage_a_record())

    categories = {item["category"] for item in output["stage_b_findings"]}
    assert "Medication indication not evident" not in categories
