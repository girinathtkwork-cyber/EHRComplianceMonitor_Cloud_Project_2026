"""
Verification and Retrieval module for Stage A
-----------------------------------------------
Combines RxNorm medication verification and ICD-10-CM diagnosis matching
to produce per-entity results with routing decisions and evidence text.

Routing:
  - "invalid": failed existence check (not in RxNorm, no confident ICD match)
  - "verified_needs_context_check": exists → must go to Stage B

evidence_text: compact plain-text block of retrieved facts, ready to paste
into an LLM prompt (the RAG augmentation payload).
"""

import re
from pathlib import Path

from . import rxnorm_client
from .icd10_searcher import ICD10Searcher

# ---------------------------------------------------------------------------
# ICD-10 confidence threshold — chosen using clean (non-injected) records.
# See STAGE_A_REPORT.md for the calibration procedure.
# ---------------------------------------------------------------------------
ICD10_CONFIDENCE_THRESHOLD = 0.25

# Lazy-loaded ICD-10 searcher
_icd10: ICD10Searcher | None = None


def _get_icd10_searcher() -> ICD10Searcher:
    global _icd10
    if _icd10 is None:
        _icd10 = ICD10Searcher()
    return _icd10


def reset_icd10_searcher(searcher: ICD10Searcher | None = None):
    """Replace the global ICD-10 searcher (useful for testing)."""
    global _icd10
    _icd10 = searcher


# ===========================================================================
# Medication verification
# ===========================================================================

def _check_dose_plausibility(
    dose_value: float,
    dose_unit: str,
    known_strengths: list[str],
) -> dict:
    """Check if a dose value+unit appears among known product strengths.

    Returns {dose_not_a_known_strength: bool, severity_hint, detail}.
    This is a weak signal: strengths describe products, not daily doses.
    """
    if not known_strengths:
        return {
            "dose_not_a_known_strength": False,
            "severity_hint": None,
            "detail": "no_known_strengths_to_compare",
        }

    # Extract numeric + unit from each strength string
    norm_unit = dose_unit.lower().rstrip("s")
    for strength in known_strengths:
        # e.g. "metformin 500 MG Oral Tablet"
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|meq|%|iu|unit)", strength, re.IGNORECASE):
            s_val = float(m.group(1))
            s_unit = m.group(2).lower().rstrip("s")
            if s_unit == norm_unit and s_val == dose_value:
                return {
                    "dose_not_a_known_strength": False,
                    "severity_hint": None,
                    "detail": f"matched_strength:{strength}",
                }

    return {
        "dose_not_a_known_strength": True,
        "severity_hint": "soft",
        "detail": f"dose {dose_value}{dose_unit} not among known product strengths",
    }


def verify_medication_entity(entity: dict) -> dict:
    """Verify a single medication entity against RxNorm.

    Returns the entity dict augmented with retrieval_context and routing.
    """
    drug_name = entity["text"]
    rx_result = rxnorm_client.verify_medication(drug_name)

    exists = rx_result.get("exists", False)
    routing = "verified_needs_context_check" if exists else "invalid"

    # Build evidence_text for RAG
    evidence_parts = []
    if exists:
        evidence_parts.append(
            f"[RxNorm] '{drug_name}' → RXCUI {rx_result.get('rxcui')}, "
            f"canonical: {rx_result.get('canonical_name')}, "
            f"match: {rx_result.get('match_type')}"
        )
        ing = rx_result.get("ingredient")
        if ing:
            evidence_parts.append(
                f"[RxNorm] Ingredient: {ing.get('name')} (RXCUI {ing.get('rxcui')})"
            )
        ind_info = rx_result.get("indications", {})
        if ind_info.get("indications_status") == "found":
            conditions = [i["condition"] for i in ind_info["indications"][:5]]
            evidence_parts.append(
                f"[MED-RT] Indications: {', '.join(conditions)}"
            )
        else:
            evidence_parts.append(
                "[MED-RT] Indications: unknown (no MED-RT data available)"
            )
        strengths = rx_result.get("known_strengths", [])
        if strengths:
            evidence_parts.append(
                f"[RxNorm] Known strengths (sample): {'; '.join(strengths[:5])}"
            )
    else:
        evidence_parts.append(
            f"[RxNorm] '{drug_name}' NOT FOUND in RxNorm "
            f"(tried exact + approximate match)"
        )

    retrieval_context = {
        "exists": exists,
        "match_type": rx_result.get("match_type", "none"),
        "rxcui": rx_result.get("rxcui"),
        "canonical_name": rx_result.get("canonical_name"),
        "ingredient": rx_result.get("ingredient"),
        "indications": rx_result.get("indications", {}),
        "known_strengths": rx_result.get("known_strengths", []),
        "evidence_text": "\n".join(evidence_parts),
    }

    entity["routing"] = routing
    entity["retrieval_context"] = retrieval_context
    return entity


def verify_dosage_entity(entity: dict, med_context: dict | None = None) -> dict:
    """Verify a dosage entity's plausibility using its associated medication's
    known strengths.

    med_context: the retrieval_context from the associated medication, if any.
    """
    flags = []

    if med_context and entity.get("associated_medication"):
        known_strengths = med_context.get("known_strengths", [])
        dose_check = _check_dose_plausibility(
            entity["value"], entity["unit"], known_strengths
        )
        if dose_check["dose_not_a_known_strength"]:
            flags.append({
                "flag_type": "dose_not_a_known_strength",
                "severity_hint": "soft",
                "detail": dose_check["detail"],
            })

    entity["routing"] = "verified_needs_context_check"
    entity["retrieval_context"] = {
        "flags": flags,
        "evidence_text": (
            f"Dose {entity['value']}{entity['unit']} "
            f"associated with {entity.get('associated_medication', 'unknown')}"
            + (f" — {flags[0]['detail']}" if flags else "")
        ),
    }
    return entity


# ===========================================================================
# Diagnosis verification
# ===========================================================================

def verify_diagnosis_entity(entity: dict) -> dict:
    """Verify a diagnosis by searching the ICD-10-CM index.

    Routing:
      - "invalid" if top score < threshold
      - "verified_needs_context_check" otherwise
    """
    searcher = _get_icd10_searcher()
    diagnosis_text = entity["text"]

    candidates = searcher.search(diagnosis_text, k=3)

    top_score = candidates[0]["score"] if candidates else 0.0
    confident = top_score >= ICD10_CONFIDENCE_THRESHOLD

    routing = "verified_needs_context_check" if confident else "unresolved"

    # Build evidence_text
    evidence_parts = []
    if candidates:
        for c in candidates:
            evidence_parts.append(
                f"[ICD-10-CM 2027] {c['code']}: {c['description']} "
                f"(score: {c['score']:.4f})"
            )
        if not confident:
            evidence_parts.append(
                f"[ICD-10-CM 2027] Match unresolved (top score {top_score:.4f} < {ICD10_CONFIDENCE_THRESHOLD}); "
                f"forwarded to Stage B with top-{len(candidates)} candidates for context evaluation."
            )
    else:
        evidence_parts.append(
            f"[ICD-10-CM 2027] No matches found for '{diagnosis_text}' (unresolved; forwarded to Stage B)"
        )

    flags = []
    if not confident:
        flags.append({
            "flag_type": "no_confident_icd10_match",
            "severity_hint": "warning",
            "detail": (
                f"Top ICD-10 score {top_score:.4f} < "
                f"threshold {ICD10_CONFIDENCE_THRESHOLD} (unresolved; forwarded to Stage B)"
            ),
        })

    entity["routing"] = routing
    entity["retrieval_context"] = {
        "icd10_candidates": candidates,
        "top_score": top_score,
        "threshold": ICD10_CONFIDENCE_THRESHOLD,
        "confident_match": confident,
        "flags": flags,
        "evidence_text": "\n".join(evidence_parts),
    }
    return entity


# ===========================================================================
# Procedure and measurement passthrough
# ===========================================================================

def verify_procedure_entity(entity: dict) -> dict:
    """Procedures are extracted but not verified (text only).
    Always route to Stage B for context check."""
    entity["routing"] = "verified_needs_context_check"
    entity["retrieval_context"] = {
        "evidence_text": (
            f"[Procedure] '{entity['text']}' — extracted from header, "
            f"not verified against any coding system"
        ),
    }
    return entity


def verify_measurement_entity(entity: dict) -> dict:
    """Measurements are informational (not drug-associated doses)."""
    entity["routing"] = "verified_needs_context_check"
    entity["retrieval_context"] = {
        "evidence_text": (
            f"[Measurement] {entity['value']}{entity['unit']} — "
            f"not associated with any medication"
        ),
    }
    return entity


# ===========================================================================
# Full record verification
# ===========================================================================

def verify_entities(entities: list[dict]) -> list[dict]:
    """Verify all entities in a list, adding routing and retrieval_context.

    Builds a lookup of medication contexts so dosage verification can
    reference the associated medication's known strengths.
    """
    verified = []

    # First pass: verify medications to build context lookup
    med_contexts: dict[str, dict] = {}
    for entity in entities:
        if entity["entity_type"] == "medication":
            verified_entity = verify_medication_entity(entity.copy())
            med_contexts[entity["text"]] = verified_entity.get(
                "retrieval_context", {}
            )
            verified.append(verified_entity)

    # Second pass: everything else
    for entity in entities:
        if entity["entity_type"] == "medication":
            continue  # Already handled

        e = entity.copy()
        if e["entity_type"] == "dosage":
            med_name = e.get("associated_medication")
            med_ctx = med_contexts.get(med_name)
            verified.append(verify_dosage_entity(e, med_ctx))
        elif e["entity_type"] == "diagnosis":
            verified.append(verify_diagnosis_entity(e))
        elif e["entity_type"] == "procedure":
            verified.append(verify_procedure_entity(e))
        elif e["entity_type"] == "measurement":
            verified.append(verify_measurement_entity(e))
        else:
            e["routing"] = "verified_needs_context_check"
            e["retrieval_context"] = {"evidence_text": ""}
            verified.append(e)

    # Re-sort by span
    verified.sort(key=lambda e: (e.get("span", [0])[0], -(e.get("span", [0, 0])[-1])))
    return verified
