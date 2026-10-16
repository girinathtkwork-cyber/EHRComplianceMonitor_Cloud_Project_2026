"""Conservative, deterministic completeness signals for human review.

Absence alone is not a compliance finding. Only an explicitly documented
medication instruction with no extracted dose is marked potentially missing.
"""

from __future__ import annotations

import re
from typing import Any


_INSTRUCTION = re.compile(
    r"\b(take|taking|prescribe[ds]?|start(?:ed|ing)?|continue[ds]?|"
    r"increase[ds]?|decrease[ds]?|administer(?:ed|ing)?)\b",
    re.IGNORECASE,
)
_MEDICATION_CUE = re.compile(r"\b(medication|medications|meds|prescription|prescribed)\b", re.I)
_EMPTY_CLINICAL_HEADER = re.compile(
    r"(?:^|\n)\s*(?:diagnosis|diagnoses|assessment|impression)\s*:\s*(?=\n|$)", re.I
)
_EXPLICIT_NOT_APPLICABLE_PROCEDURE = re.compile(
    r"\b(?:no procedure(?: is)? planned|procedure\s*:\s*(?:not applicable|n/?a))\b", re.I
)


def assess_completeness(transcript: str, entities: list[dict[str, Any]]) -> dict[str, Any]:
    """Return evidence-linked field statuses without inferring clinical facts."""
    if not isinstance(transcript, str):
        transcript = ""
    if not isinstance(entities, list):
        entities = []

    medications = [e for e in entities if e.get("entity_type") == "medication"]
    dosages = [e for e in entities if e.get("entity_type") == "dosage"]
    diagnoses = [e for e in entities if e.get("entity_type") == "diagnosis"]
    procedures = [e for e in entities if e.get("entity_type") == "procedure"]
    reviews: list[dict[str, Any]] = []

    def add(field: str, status: str, evidence: str, explanation: str, *, provenance: str = "stage_a_rule") -> None:
        reviews.append({
            "field": field,
            "status": status,
            "evidence": evidence,
            "explanation": explanation,
            "provenance": provenance,
            "suggested_action": "Review in note context" if status in {"potentially_missing", "ambiguous"} else None,
        })

    if medications:
        add("medication_name", "present", "; ".join(e.get("text", "") for e in medications),
            "Medication terminology candidates were extracted. This does not establish that the medication is appropriate.")
        for medication in medications:
            phrase = str(medication.get("text", ""))
            start, end = medication.get("span", [0, 0])
            # Keep context local to the sentence/clause containing this mention.
            left = max(transcript.rfind(".", 0, start), transcript.rfind(";", 0, start), transcript.rfind("\n", 0, start)) + 1
            stops = [p for p in (transcript.find(".", end), transcript.find(";", end), transcript.find("\n", end)) if p >= 0]
            right = min(stops) if stops else len(transcript)
            clause = transcript[left:right]
            associated = [d for d in dosages if str(d.get("associated_medication", "")).casefold() == phrase.casefold()]
            if associated:
                dose_status = "present"
                evidence = "; ".join(str(d.get("text", "")) for d in associated)
                explanation = "A dosage value is linked to this medication mention; verify the source and clinical context."
            elif _INSTRUCTION.search(clause):
                dose_status = "potentially_missing"
                evidence = phrase
                explanation = "The note contains an explicit medication instruction, but Stage A did not extract a linked dosage. No dosage is inferred."
            else:
                dose_status = "not_mentioned"
                evidence = phrase
                explanation = "No dosage was linked to this medication mention; the available text does not establish that a dose is expected here."
            add(f"dosage:{phrase}", dose_status, evidence, explanation)

            dose = associated[0] if associated else None
            instructions = dose.get("instructions", {}) if dose else {}
            for key, label in (("route", "route"), ("frequency", "frequency")):
                value = instructions.get(key) if isinstance(instructions, dict) else None
                add(f"{label}:{phrase}", "present" if value else "not_mentioned",
                    str(value.get("text", "")) if isinstance(value, dict) else phrase,
                    f"{label.capitalize()} text was extracted." if value else
                    f"No {label} was extracted. The note context does not establish that it is required.")
    else:
        status = "ambiguous" if _MEDICATION_CUE.search(transcript) else "not_mentioned"
        add("medication_name", status, "medication or prescription wording" if status == "ambiguous" else "",
            "Medication-related wording is present but Stage A produced no medication candidate; a human should determine whether this is a medication or a false positive." if status == "ambiguous" else
            "No medication mention was extracted. Absence alone is not treated as a compliance concern.")

    if diagnoses:
        add("diagnosis", "present", "; ".join(e.get("text", "") for e in diagnoses),
            "Diagnosis candidates were extracted from a clinical section; local terminology matches are candidates for review.")
    else:
        status = "potentially_missing" if _EMPTY_CLINICAL_HEADER.search(transcript) else "not_mentioned"
        add("diagnosis", status, "Empty diagnosis/assessment header" if status == "potentially_missing" else "",
            "A diagnosis or assessment header is present without text; review whether the section should be completed." if status == "potentially_missing" else
            "No diagnosis was extracted, and the note does not contain an empty diagnosis/assessment header. This is not a violation finding.")

    if procedures:
        add("procedure", "present", "; ".join(e.get("text", "") for e in procedures),
            "Procedure text was extracted from an explicit procedure section; procedure coding is not verified by this feature.")
    else:
        not_applicable = bool(_EXPLICIT_NOT_APPLICABLE_PROCEDURE.search(transcript))
        add("procedure", "not_applicable" if not_applicable else "not_mentioned",
            "Explicitly documented as not applicable" if not_applicable else "",
            "The transcript explicitly states that no procedure is planned or applicable." if not_applicable else
            "No procedure was extracted from an explicit procedure section; procedures may not apply to this note.")

    needs_review = any(item["status"] in {"potentially_missing", "ambiguous"} for item in reviews)
    return {
        "status": "review_suggested" if needs_review else "no_contextual_gap_detected",
        "human_review_required": needs_review,
        "method": "deterministic_context_rules",
        "items": reviews,
        "disclaimer": "Completeness signals are context-limited review aids, not confirmed violations or clinical advice.",
    }
