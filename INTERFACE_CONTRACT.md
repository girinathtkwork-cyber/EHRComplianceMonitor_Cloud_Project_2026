# Stage A → Stage B Interface Contract

## Overview
This document defines the JSON schema for the output of **Stage A (Entity Extraction & Flagging)** that serves as input to **Stage B (LLM Reasoning + Severity + Compliance)**.

---

## Top-Level Structure

```json
{
  "transcript_id": "string (UUID)",
  "timestamp": "ISO 8601 datetime",
  "transcript_text": "string (full raw transcript)",
  "entities": [Entity],
  "flagged_entities": [FlaggedEntity]
}
```

---

## Entity Object

```json
{
  "entity_id": "string (UUID)",
  "type": "MEDICATION | DIAGNOSIS | PROCEDURE | DOSAGE | LAB_VALUE | ANATOMY",
  "text": "string (exact span from transcript)",
  "normalized": "string (standardized term, e.g., RxNorm, ICD-10, CPT)",
  "code": "string (standard code: RxNorm CUI, ICD-10, CPT, LOINC)",
  "confidence": "number (0.0 - 1.0)",
  "start_char": "integer",
  "end_char": "integer"
}
```

---

## FlaggedEntity Object

```json
{
  "entity_id": "string (UUID, references Entity.entity_id)",
  "flag_type": "UNRECOGNIZED_CODE | AMBIGUOUS_TERM | POTENTIAL_HALLUCINATION | DOSAGE_ANOMALY | MISSING_CODE",
  "flag_reason": "string (human-readable explanation)",
  "severity_hint": "CRITICAL | MEDIUM | LOW (Stage A's preliminary assessment)",
  "context_window": "string (surrounding transcript text, ~100 chars each side)"
}
```

---

## Example

```json
{
  "transcript_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp": "2026-01-15T14:32:11Z",
  "transcript_text": "Patient presents with Type 1 Diabetes. Prescribed Metformin 1000mg twice daily. Also ordered HbA1c test.",
  "entities": [
    {
      "entity_id": "e1",
      "type": "DIAGNOSIS",
      "text": "Type 1 Diabetes",
      "normalized": "Type 1 diabetes mellitus",
      "code": "E10.9",
      "confidence": 0.98,
      "start_char": 21,
      "end_char": 36
    },
    {
      "entity_id": "e2",
      "type": "MEDICATION",
      "text": "Metformin",
      "normalized": "Metformin",
      "code": "860975",
      "confidence": 0.99,
      "start_char": 50,
      "end_char": 59
    },
    {
      "entity_id": "e3",
      "type": "DOSAGE",
      "text": "1000mg twice daily",
      "normalized": "1000 mg BID",
      "code": "",
      "confidence": 0.95,
      "start_char": 60,
      "end_char": 78
    },
    {
      "entity_id": "e4",
      "type": "PROCEDURE",
      "text": "HbA1c test",
      "normalized": "Hemoglobin A1c",
      "code": "83036",
      "confidence": 0.97,
      "start_char": 90,
      "end_char": 100
    }
  ],
  "flagged_entities": [
    {
      "entity_id": "e2",
      "flag_type": "POTENTIAL_HALLUCINATION",
      "flag_reason": "Metformin is first-line for Type 2 Diabetes, not typically prescribed for Type 1",
      "severity_hint": "CRITICAL",
      "context_window": "Patient presents with Type 1 Diabetes. Prescribed Metformin 1000mg twice daily."
    }
  ]
}
```

---

## Field Requirements

| Field | Required | Notes |
|-------|----------|-------|
| transcript_id | Yes | UUID v4 |
| timestamp | Yes | UTC, ISO 8601 |
| transcript_text | Yes | Full original transcript |
| entities | Yes | Array, can be empty |
| flagged_entities | Yes | Array, can be empty |
| Entity.entity_id | Yes | UUID v4 |
| Entity.type | Yes | Enum: MEDICATION, DIAGNOSIS, PROCEDURE, DOSAGE, LAB_VALUE, ANATOMY |
| Entity.code | Conditional | Required if normalized maps to standard vocabulary |
| FlaggedEntity.entity_id | Yes | Must exist in entities array |
| FlaggedEntity.flag_type | Yes | Enum: UNRECOGNIZED_CODE, AMBIGUOUS_TERM, POTENTIAL_HALLUCINATION, DOSAGE_ANOMALY, MISSING_CODE |
| FlaggedEntity.severity_hint | Yes | Stage A's preliminary hint; Stage B makes final determination |

---

## Flag Type Definitions

| Flag Type | Description |
|-----------|-------------|
| UNRECOGNIZED_CODE | Extracted code not found in target vocabulary (RxNorm, ICD-10, CPT, LOINC) |
| AMBIGUOUS_TERM | Term maps to multiple possible codes; needs disambiguation |
| POTENTIAL_HALLUCINATION | Entity appears medically implausible in context (e.g., wrong drug for diagnosis) |
| DOSAGE_ANOMALY | Dosage value outside typical range for medication/patient |
| MISSING_CODE | Entity recognized but no standard code could be assigned |

---

## Notes for Stage B Consumers

1. **Do not trust `severity_hint` blindly** — it's Stage A's quick heuristic. Stage B must perform independent reasoning.
2. **Use `context_window`** — provides local transcript context for LLM reasoning.
3. **Cross-reference `entities`** — flagged entities reference full entity objects by `entity_id`; join to get normalized terms, codes, confidence.
4. **Empty arrays are valid** — a clean transcript may have zero flagged entities.