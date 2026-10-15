from backend.completeness import assess_completeness
from backend.web_api import format_result


def _med(text, start, end):
    return {"entity_type": "medication", "text": text, "span": [start, end]}


def _dose(text, med):
    start = text.index("500 mg")
    return {"entity_type": "dosage", "text": "500 mg", "span": [start, start + 6], "associated_medication": med}


def test_complete_medication_instruction_has_extracted_dose_and_frequency():
    text = "Start metformin 500 mg twice daily."
    meds = [_med("metformin", text.index("metformin"), text.index("metformin") + 9)]
    dose = _dose(text, "metformin")
    dose["instructions"] = {"frequency": {"text": "twice daily"}}
    review = assess_completeness(text, meds + [dose])
    statuses = {item["field"]: item["status"] for item in review["items"]}
    assert statuses["dosage:metformin"] == "present"
    assert statuses["frequency:metformin"] == "present"
    assert review["human_review_required"] is False


def test_explicit_medication_instruction_without_dose_is_potentially_missing():
    text = "Continue lisinopril."
    review = assess_completeness(text, [_med("lisinopril", 9, 19)])
    dose = next(item for item in review["items"] if item["field"] == "dosage:lisinopril")
    assert dose["status"] == "potentially_missing"
    assert dose["suggested_action"] == "Review in note context"
    assert "No dosage is inferred" in dose["explanation"]


def test_medication_without_instruction_does_not_assume_dose_is_required():
    text = "Medication history includes aspirin."
    review = assess_completeness(text, [_med("aspirin", 26, 33)])
    dose = next(item for item in review["items"] if item["field"] == "dosage:aspirin")
    assert dose["status"] == "not_mentioned"
    assert review["human_review_required"] is False


def test_absent_medication_is_not_a_violation_or_missing_medication():
    review = assess_completeness("Patient reports feeling well.", [])
    medication = next(item for item in review["items"] if item["field"] == "medication_name")
    assert medication["status"] == "not_mentioned"
    assert medication["provenance"] == "stage_a_rule"


def test_medication_wording_without_extractable_candidate_is_ambiguous():
    review = assess_completeness("Medication: an unfamiliar local product.", [])
    medication = next(item for item in review["items"] if item["field"] == "medication_name")
    assert medication["status"] == "ambiguous"
    assert review["human_review_required"] is True


def test_diagnosis_gap_only_suggested_for_explicit_empty_header():
    empty = assess_completeness("Assessment:\nPlan: follow up.", [])
    no_header = assess_completeness("Routine follow-up visit.", [])
    assert next(x for x in empty["items"] if x["field"] == "diagnosis")["status"] == "potentially_missing"
    assert next(x for x in no_header["items"] if x["field"] == "diagnosis")["status"] == "not_mentioned"


def test_explicit_not_applicable_procedure_is_distinguished():
    review = assess_completeness("Procedure: not applicable.", [])
    procedure = next(item for item in review["items"] if item["field"] == "procedure")
    assert procedure["status"] == "not_applicable"


def test_formatter_adds_completeness_without_changing_error_contract():
    result = format_result({"verification_status": "completed", "transcription": "No concerns.", "entities": [], "stage_b_findings": [{"id": "f-1"}]})
    assert result["completeness_review"]["method"] == "deterministic_context_rules"
    assert result["stage_b_findings"][0]["reviewStatus"] == "PENDING"
    error = format_result({"verification_status": "error", "error": {"code": "stage_a_failed"}})
    assert "completeness_review" not in error
