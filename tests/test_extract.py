"""
Tests for Stage A extraction module (M2).
"""

import json
import pytest
from pathlib import Path

# Provide a minimal lexicon for testing (no disk dependency)
MINI_LEXICON = {
    "amoxicillin": {"rxcui": "723", "tty": "IN"},
    "prednisone": {"rxcui": "8640", "tty": "IN"},
    "metformin": {"rxcui": "6809", "tty": "IN"},
    "lisinopril": {"rxcui": "29046", "tty": "IN"},
    "tylenol": {"rxcui": "202433", "tty": "BN"},
    "aspirin": {"rxcui": "1191", "tty": "IN"},
    "ibuprofen": {"rxcui": "5640", "tty": "IN"},
    "ciprofloxacin": {"rxcui": "2551", "tty": "IN"},
    "omeprazole": {"rxcui": "7646", "tty": "IN"},
    "acetaminophen": {"rxcui": "161", "tty": "IN"},
    "atenolol": {"rxcui": "1202", "tty": "IN"},
    "hydroxyzine": {"rxcui": "5553", "tty": "IN"},
    "today": {"rxcui": "896747", "tty": "BN"},
}

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from backend.stage_a.extract import (
    extract_medications,
    extract_dosages_and_measurements,
    extract_diagnoses,
    extract_procedures,
    extract_entities,
    _build_token_spans,
    _split_diagnosis_text,
    reset_lexicon_cache,
)


class TestTokenSpans:
    def test_simple(self):
        spans = _build_token_spans("Hello world")
        assert len(spans) == 2
        assert spans[0][0] == "Hello"
        assert spans[1][0] == "world"

    def test_offsets(self):
        spans = _build_token_spans("abc def")
        assert spans[0] == ("abc", 0, 3)
        assert spans[1] == ("def", 4, 7)


class TestMedicationExtraction:
    def test_finds_known_drug(self):
        meds = extract_medications("Patient takes Amoxicillin daily.", lexicon=MINI_LEXICON)
        names = [m["text"].lower() for m in meds]
        assert "amoxicillin" in names

    def test_finds_multiple_drugs(self):
        text = "Started on Amoxicillin 500mg and Prednisone 10mg."
        meds = extract_medications(text, lexicon=MINI_LEXICON)
        names = {m["text"].lower() for m in meds}
        assert "amoxicillin" in names
        assert "prednisone" in names

    def test_no_false_positive_stoplist(self):
        # "ice", "water", "iron" are in the stoplist
        meds = extract_medications("Patient drinks water and uses ice packs with iron supplements.", lexicon=MINI_LEXICON)
        names = [m["text"].lower() for m in meds]
        assert "water" not in names
        assert "ice" not in names
        assert "iron" not in names

    def test_today_is_not_medication(self):
        meds = extract_medications("She presents today with foot pain.", lexicon=MINI_LEXICON)
        assert [m["text"].lower() for m in meds] == []

    def test_temporal_words_near_medications_are_not_extra_medications(self):
        text = "Today she started aspirin and took ibuprofen yesterday."
        meds = extract_medications(text, lexicon=MINI_LEXICON)
        assert [item["text"].lower() for item in meds] == ["aspirin", "ibuprofen"]

    def test_negated_medication_is_extracted_as_a_mention(self):
        # Extraction identifies mentions; it does not determine whether the
        # patient takes or denies the medication.
        meds = extract_medications("Patient denies aspirin.", lexicon=MINI_LEXICON)
        assert [item["text"].lower() for item in meds] == ["aspirin"]

    def test_unrecognized_term_is_not_claimed_as_rxnorm_match(self):
        meds = extract_medications("Patient reports taking unlistedmedication.", lexicon=MINI_LEXICON)
        assert meds == []

    def test_has_span_offsets(self):
        meds = extract_medications("Give Aspirin now.", lexicon=MINI_LEXICON)
        assert len(meds) == 1
        assert meds[0]["span"][0] >= 0
        assert meds[0]["span"][1] > meds[0]["span"][0]

    def test_embedded_name_is_not_matched(self):
        meds = extract_medications("The nonsensicalwordaspirinlike token is unrelated.", lexicon=MINI_LEXICON)
        assert meds == []

    def test_negation_context_is_preserved(self):
        meds = extract_medications("Patient denies aspirin.", lexicon=MINI_LEXICON)
        assert meds[0]["assertion_status"] == "negated"

    def test_historical_section_is_preserved(self):
        meds = extract_medications("Past medications: aspirin was used years ago.", lexicon=MINI_LEXICON)
        assert meds[0]["section"] == "historical"

    def test_unicode_normalization_for_lexicon_lookup(self):
        meds = extract_medications("Take aspirin now.", lexicon={"aspirin": MINI_LEXICON["aspirin"]})
        assert meds[0]["text"] == "aspirin"


class TestDosageMeasurement:
    def test_blood_loss_is_measurement(self):
        """Critical test: 'Estimated blood loss is 300mL' should be a
        measurement, NOT a drug dose."""
        text = "Estimated blood loss is 300mL."
        meds = extract_medications(text, lexicon=MINI_LEXICON)
        dosages, measurements = extract_dosages_and_measurements(text, meds)
        assert len(dosages) == 0, "Blood loss should NOT be a dosage"
        assert len(measurements) == 1, "Blood loss should be a measurement"
        assert measurements[0]["value"] == 300
        assert measurements[0]["unit"] == "ml"

    def test_drug_dose_associated(self):
        text = "Amoxicillin 500mg three times daily."
        meds = extract_medications(text, lexicon=MINI_LEXICON)
        dosages, measurements = extract_dosages_and_measurements(text, meds)
        assert len(dosages) == 1
        assert dosages[0]["associated_medication"].lower() == "amoxicillin"

    def test_preserves_route_frequency_and_duration_spans(self):
        text = "Amoxicillin 500mg orally twice daily for 7 days."
        meds = extract_medications(text, lexicon=MINI_LEXICON)
        dosages, _ = extract_dosages_and_measurements(text, meds)
        attrs = dosages[0]["instructions"]
        assert attrs["route"]["text"].lower() == "orally"
        assert attrs["frequency"]["text"].lower() == "twice daily"
        assert attrs["duration"]["text"].lower() == "for 7 days"
        assert all(text[a:b] == value["text"] for value in attrs.values() for a, b in [value["span"]])

    def test_instructions_do_not_cross_to_next_medication(self):
        text = "Amoxicillin 500mg orally twice daily and Prednisone 10mg nightly."
        meds = extract_medications(text, lexicon=MINI_LEXICON)
        dosages, _ = extract_dosages_and_measurements(text, meds)
        by_med = {dose["associated_medication"].lower(): dose.get("instructions", {}) for dose in dosages}
        assert by_med["amoxicillin"]["frequency"]["text"].lower() == "twice daily"
        assert "route" in by_med["amoxicillin"]
        assert by_med["prednisone"]["frequency"]["text"].lower() == "nightly"
        assert "route" not in by_med["prednisone"]

    def test_dose_far_from_drug_is_measurement(self):
        text = ("Amoxicillin prescribed. "
                "The patient has a history of smoking. "
                "Blood pressure 120. Hemoglobin 12g.")
        meds = extract_medications(text, lexicon=MINI_LEXICON)
        dosages, measurements = extract_dosages_and_measurements(text, meds)
        # 12g is far from amoxicillin and should be measurement
        assert any(m["value"] == 12 for m in measurements)

    def test_percentage_unit(self):
        text = "Oxygen saturation 98%."
        meds = []
        dosages, measurements = extract_dosages_and_measurements(text, meds)
        assert len(measurements) == 1
        assert measurements[0]["unit"] == "%"

    def test_blood_pressure_format(self):
        _, measurements = extract_dosages_and_measurements("Vitals: BP 120/80 mmHg", [])
        assert len(measurements) == 1
        assert measurements[0]["value"] == "120/80"
        assert measurements[0]["unit"] == "mmHg"

    def test_unrelated_date_is_not_measurement(self):
        _, measurements = extract_dosages_and_measurements("Visit date 10/12/2026", [])
        assert measurements == []


class TestDiagnosisExtraction:
    def test_simple_diagnosis(self):
        text = "DIAGNOSIS: Type 2 diabetes mellitus.\n\nPLAN: Continue medications."
        dx = extract_diagnoses(text)
        assert len(dx) >= 1
        assert any("diabetes" in d["text"].lower() for d in dx)

    def test_multiple_headers(self):
        text = ("PREOPERATIVE DIAGNOSIS: Chronic otitis media.\n"
                "POSTOPERATIVE DIAGNOSIS: Chronic otitis media.\n\n"
                "PROCEDURE: Myringotomy.")
        dx = extract_diagnoses(text)
        # Should deduplicate
        texts = [d["text"].lower() for d in dx]
        assert "chronic otitis media" in texts
        # Should not have duplicate
        assert texts.count("chronic otitis media") == 1

    def test_assessment_header(self):
        text = "ASSESSMENT: Essential hypertension, well controlled.\n\nPLAN: Continue atenolol."
        dx = extract_diagnoses(text)
        assert len(dx) >= 1

    def test_numbered_list(self):
        text = "DIAGNOSES: 1. Hypertension 2. Diabetes 3. Obesity\n\nMEDICATIONS:"
        dx = extract_diagnoses(text)
        assert len(dx) >= 3

    def test_comma_delimited_headers_do_not_swallow_document(self):
        text = (
            "POSTOPERATIVE DIAGNOSES:,1. Severe tracheobronchitis.,"
            "2. Mild venous engorgement.,"
            "3. Right upper lobe hemorrhage status post biopsy.,"
            "PROCEDURE PERFORMED: , Flexible fiberoptic bronchoscopy with lavage.,"
            "SAMPLES: , Bronchoalveolar lavage for cytology.,"
            "INDICATIONS: , Persistent hemoptysis."
        )
        dx = extract_diagnoses(text)
        assert len(dx) == 3
        assert all("PROCEDURE PERFORMED" not in d["text"] for d in dx)
        assert all(len(d["text"]) < 100 for d in dx)


class TestProcedureExtraction:
    def test_simple_procedure(self):
        text = "PROCEDURE PERFORMED: Bilateral myringotomy and tube placement.\n\nANESTHESIA:"
        procs = extract_procedures(text)
        assert len(procs) >= 1
        assert any("myringotomy" in p["text"].lower() for p in procs)

    def test_operation_header(self):
        text = "OPERATION: Total knee replacement.\n\nINDICATIONS:"
        procs = extract_procedures(text)
        assert len(procs) >= 1

    def test_procedure_performed_not_extracted_as_performed_fragment(self):
        text = "PROCEDURE PERFORMED: Flexible fiberoptic bronchoscopy.\n\nANESTHESIA:"
        procs = extract_procedures(text)
        texts = [p["text"] for p in procs]
        assert "PERFORMED:" not in texts
        assert any("bronchoscopy" in p.lower() for p in texts)


class TestExtractEntities:
    def test_combined_extraction(self):
        text = (
            "PREOPERATIVE DIAGNOSIS: Chronic otitis media with effusion.\n"
            "POSTOPERATIVE DIAGNOSIS: Same.\n\n"
            "PROCEDURE PERFORMED: Bilateral myringotomy.\n\n"
            "The patient was given Amoxicillin 500mg orally. "
            "Estimated blood loss was 300mL. "
        )
        entities = extract_entities(text, lexicon=MINI_LEXICON)
        types = {e["entity_type"] for e in entities}
        # Should have at least medication, dosage, measurement, diagnosis, procedure
        assert "diagnosis" in types
        assert "procedure" in types

    def test_all_entities_have_required_fields(self):
        text = "DIAGNOSIS: Hypertension.\nPrescribed Aspirin 81mg daily."
        entities = extract_entities(text, lexicon=MINI_LEXICON)
        for e in entities:
            assert "entity_type" in e
            assert "text" in e
            assert "span" in e
            assert len(e["span"]) == 2


class TestSplitDiagnosisText:
    def test_numbered(self):
        items = _split_diagnosis_text("1. Hypertension  2. Diabetes  3. Obesity")
        assert len(items) == 3

    def test_comma_separated(self):
        items = _split_diagnosis_text("Hypertension, Diabetes, Obesity")
        assert len(items) >= 2
