"""Deterministic, label-blind Stage A aggregate feature extraction."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import json

_SCHEMA_PATH = Path(__file__).with_name("feature_schema_v1.json")
FEATURE_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
FEATURE_COLUMNS = tuple(FEATURE_SCHEMA["feature_columns"])


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def extract_record_features(stage_a_record: dict) -> dict[str, int]:
    """Build numeric features from Stage A fields only; labels/text are ignored."""
    entities = stage_a_record.get("entities")
    if not isinstance(entities, list):
        raise ValueError("Stage A record must contain an entities list")
    if any(not isinstance(entity, dict) for entity in entities):
        raise ValueError("Stage A entities must all be objects")

    types = Counter(str(e.get("entity_type", "")) for e in entities if isinstance(e, dict))
    routes = Counter(str(e.get("routing", "")) for e in entities if isinstance(e, dict))
    med_status = Counter(
        str((e.get("retrieval_context") or {}).get("verification_status", "unknown"))
        for e in entities if isinstance(e, dict) and e.get("entity_type") == "medication"
    )
    med_mentions = Counter(
        _norm(e.get("text")) for e in entities
        if isinstance(e, dict) and e.get("entity_type") == "medication" and _norm(e.get("text"))
    )
    entity_mentions = Counter(
        (str(e.get("entity_type", "")), _norm(e.get("text"))) for e in entities
        if isinstance(e, dict) and _norm(e.get("text"))
    )
    medications = [e for e in entities if isinstance(e, dict) and e.get("entity_type") == "medication"]
    sections = {
        _norm(e.get("section")) for e in medications if _norm(e.get("section")) not in ("", "unknown")
    }
    values = {
        "record_entity_count": len(entities),
        "medication_count": types["medication"],
        "dosage_count": types["dosage"],
        "measurement_count": types["measurement"],
        "diagnosis_count": types["diagnosis"],
        "procedure_count": types["procedure"],
        "routing_invalid_count": routes["invalid"],
        "routing_unresolved_count": routes["unresolved"],
        "routing_review_count": routes["verified_needs_context_check"],
        "medication_verified_count": med_status["verified"],
        "medication_ambiguous_count": med_status["ambiguous"],
        "medication_unknown_count": med_status["unknown"],
        "medication_lookup_unavailable_count": med_status["lookup_unavailable"],
        "medication_duplicate_mentions": sum(n - 1 for n in med_mentions.values() if n > 1),
        "duplicate_entities": sum(n - 1 for n in entity_mentions.values() if n > 1),
        "unassociated_dosage_count": sum(
            e.get("entity_type") == "dosage" and not e.get("associated_medication") for e in entities
        ),
        "dosage_missing_value_or_unit_count": sum(
            e.get("entity_type") == "dosage" and (e.get("value") is None or not e.get("unit")) for e in entities
        ),
        "measurement_missing_value_or_unit_count": sum(
            e.get("entity_type") == "measurement" and (e.get("value") is None or not e.get("unit")) for e in entities
        ),
        "entities_missing_valid_span_count": sum(
            not (isinstance(e.get("span"), list) and len(e["span"]) == 2
                 and isinstance(e["span"][0], int) and isinstance(e["span"][1], int)
                 and 0 <= e["span"][0] < e["span"][1]) for e in entities
        ),
        "medications_negated_count": sum(e.get("assertion_status") == "negated" for e in medications),
        "medications_historical_count": sum(
            e.get("assertion_status") == "historical" or e.get("section") == "historical" for e in medications
        ),
        "medications_allergy_context_count": sum(
            e.get("assertion_status") == "allergy" or e.get("section") == "allergy" for e in medications
        ),
        "medications_planned_count": sum(e.get("assertion_status") == "planned" for e in medications),
        "distinct_medication_sections_count": len(sections),
        "stage_a_partial": int(stage_a_record.get("verification_status") == "partial"),
    }
    # Fixed-order, fixed-type output is shared by preparation, training and inference.
    return {name: int(values[name]) for name in FEATURE_COLUMNS}
