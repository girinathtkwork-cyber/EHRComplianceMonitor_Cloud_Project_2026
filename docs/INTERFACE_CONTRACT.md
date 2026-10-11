# Interface Contract: Stage A → Stage B

This document defines the formal data contract between Stage A (Deterministic Retrieval & Verification) and Stage B (LLM-based Clinical Context Assessment) in the EHR Compliance Monitor system.

---

## 1. Overview and Architecture

The EHR Compliance Monitor uses a two-stage RAG (Retrieval-Augmented Generation) pipeline:
- **Stage A (Local / AWS Lambda):** Performs deterministic medical entity extraction (medications, dosages, measurements, diagnoses, procedures) and external reference verification (RxNorm, MED-RT, ICD-10-CM 2027). Entities failing existence checks are routed as `invalid`. Entities that exist are routed as `verified_needs_context_check` accompanied by compact, structured `evidence_text` (retrieved clinical facts).
- **Stage B (LLM Judgment):** Receives the augmented records from Stage A. Uses the retrieved `evidence_text` to judge whether verified entities are clinically appropriate in context (e.g., drug-indication mismatch, contraindicated dosages, or unstated procedures).

Stage A adheres to strict additive guarantees: **no original fields from the input record are modified or dropped**.

---

## 2. Top-Level Record Schema

Stage A produces an array of JSON objects (or a single JSON object in streaming mode) matching the following specification:

| Field | Type | Required | Description |
|---|---|---|---|
| `transcript_id` | `integer` \| `string` | Yes | Unique record identifier preserved from input. |
| `medical_specialty` | `string` | No | Medical specialty (preserved from input). |
| `sample_name` | `string` | No | Sample/procedure title (preserved from input). |
| `description` | `string` | No | Short clinical summary (preserved from input). |
| `transcription` | `string` | Yes | Raw clinical transcription text (preserved from input). |
| `ground_truth_label` | `object` | No | Evaluation ground truth metadata (if present). |
| `verification_status` | `string` | Yes | Enum: `"completed"`, `"partial"`, `"error"`. |
| `processing_timestamp` | `string` | Yes | UTC ISO 8601 timestamp (e.g., `2026-10-10T12:00:00+00:00`). |
| `stage_a_version` | `string` | Yes | Semantic pipeline version (e.g., `"2.0.0"`). |
| `entities` | `array[object]` | Yes | List of all extracted and verified entity records. |
| `stage_a_summary` | `object` | Yes | Batch summary metrics for the transcript. |
| `error` | `object` | Conditional | Present only when `verification_status == "error"`. |

### `stage_a_summary` Schema
```json
{
  "n_entities": 14,
  "n_invalid": 1,
  "n_forwarded_to_stage_b": 13
}
```

### `error` Schema (when `verification_status == "error"`)
```json
{
  "code": "ValueError",
  "message": "Empty transcription text",
  "timestamp": "2026-10-10T12:00:00+00:00"
}
```

---

## 3. Entity Object Specification

Each item in `entities[]` has the following structure:

```json
{
  "entity_type": "medication",
  "text": "amoxicillin",
  "span": [124, 135],
  "routing": "verified_needs_context_check",
  "retrieval_context": {
    "evidence_text": "[RxNorm] 'amoxicillin' → RXCUI 723, canonical: amoxicillin, match: exact\n[RxNorm] Ingredient: amoxicillin (RXCUI 723)\n[MED-RT] Indications: Bacterial Infections, Otitis Media, Pharyngitis\n[RxNorm] Known strengths (sample): amoxicillin 250 MG Oral Capsule; amoxicillin 500 MG Oral Tablet",
    "exists": true,
    "match_type": "exact",
    "rxcui": "723",
    "canonical_name": "amoxicillin",
    "ingredient": {
      "rxcui": "723",
      "name": "amoxicillin",
      "tty": "IN"
    },
    "indications": {
      "indications": [
        {"condition": "Bacterial Infections", "relationship": "may_treat", "source": "MED-RT"},
        {"condition": "Otitis Media", "relationship": "may_treat", "source": "MED-RT"}
      ],
      "indications_status": "found"
    },
    "known_strengths": [
      "amoxicillin 250 MG Oral Capsule",
      "amoxicillin 500 MG Oral Tablet"
    ]
  }
}
```

### Required Fields per Entity:
- `entity_type` (string enum):
  - `"medication"`: Extracted via RxNorm n-gram lexicon scan.
  - `"dosage"`: Extracted numeric + unit associated with a nearby medication token within 8 tokens.
  - `"measurement"`: Numeric + unit not associated with a drug (e.g., blood loss, vital sign).
  - `"diagnosis"`: Extracted from explicit clinical assessment/diagnosis headers.
  - `"procedure"`: Extracted from explicit procedure/operation headers.
- `text` (string): Verbatim entity substring from transcription.
- `span` ([int, int]): Character offsets `[start, end]` in `transcription`.
- `routing` (string enum):
  - `"invalid"`: Failed the existence check (e.g., unrecognized drug or no confident ICD-10 match).
  - `"verified_needs_context_check"`: Validated against reference standard; must be evaluated by Stage B for clinical context appropriateness.
- `retrieval_context` (object): Contains `evidence_text` and reference retrieval metadata.

---

## 4. RAG Prompt Ingestion Guideline for Stage B

Stage B LLM prompts should ingest `retrieval_context.evidence_text` directly as factual RAG grounding:

```markdown
Patient Record: {{ transcription }}

Extracted Entities & Grounded Reference Knowledge:
{% for entity in entities %}
- Entity: "{{ entity.text }}" ({{ entity.entity_type }})
  Routing: {{ entity.routing }}
  Evidence:
  {{ entity.retrieval_context.evidence_text }}
{% endfor %}
```

Stage B evaluates:
1. Are medications indicated by any documented diagnosis?
2. Are prescribed dosages within standard therapeutic ranges based on `known_strengths`?
3. Are procedures consistent with documented indications?
