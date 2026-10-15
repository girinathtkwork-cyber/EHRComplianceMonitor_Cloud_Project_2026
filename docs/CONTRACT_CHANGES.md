# Contract Changes — Stage A v2.0.0

This document lists all **additive** changes made to the interface contract
by Stage A v2.0.0. Forward this to the Stage B teammate so they can update
their integration code.

> **No existing fields were removed or modified.** All original fields from
> the input records are preserved as-is.

---

## New fields added to each output record

### `verification_status` (string)
- `"completed"`: record processed successfully
- `"partial"`: processing was interrupted (e.g., Lambda timeout)
- `"error"`: processing failed (see `error` object)

### `processing_timestamp` (string, ISO 8601)
When Stage A processed this record.

### `stage_a_version` (string)
Pipeline version, currently `"2.0.0"`.

### `entities` (array of objects)
All extracted and verified medical entities. Each entity contains:

| Field | Type | Description |
|-------|------|-------------|
| `entity_type` | string | `"medication"`, `"dosage"`, `"measurement"`, `"diagnosis"`, or `"procedure"` |
| `text` | string | The extracted entity text |
| `span` | [int, int] | Character offsets `[start, end]` in the transcription |
| `routing` | string | `"invalid"` or `"verified_needs_context_check"` |
| `retrieval_context` | object | Contains `evidence_text` and verification details |

#### Routing values
- **`invalid`**: Entity failed the existence check (not found in RxNorm, or no confident ICD-10-CM match). Stage B should treat this as a potential error.
- **`verified_needs_context_check`**: Entity exists in the reference database. Stage B must judge whether it is *correct for this patient* using the provided `evidence_text`.

#### `retrieval_context.evidence_text` (string)
A compact plain-text block of retrieved facts, ready to paste into an LLM prompt.
Each line names its source (e.g., `[RxNorm]`, `[MED-RT]`, `[ICD-10-CM 2027]`).

**This is the RAG augmentation payload — the key interface between Stage A and Stage B.**

### `stage_a_summary` (object)

| Field | Type | Description |
|-------|------|-------------|
| `n_entities` | int | Total entities extracted |
| `n_invalid` | int | Entities routed as `invalid` |
| `n_forwarded_to_stage_b` | int | Entities routed as `verified_needs_context_check` |

### `error` (object, only when `verification_status == "error"`)

| Field | Type | Description |
|-------|------|-------------|
| `code` | string | Exception class name |
| `message` | string | Error description |
| `timestamp` | string | ISO 8601 timestamp |

---

## Entity-type-specific retrieval_context fields

### Medications
- `exists` (bool), `match_type` (string: "exact"/"approximate"/"none")
- `rxcui` (string | null), `canonical_name` (string | null)
- `ingredient` (object | null): `{rxcui, name, tty}`
- `indications` (object): `{indications: [...], indications_status: "found"|"unknown"}`
- `known_strengths` (array of strings): product forms with strengths

### Dosages
- `flags` (array): may contain `{flag_type: "dose_not_a_known_strength", severity_hint: "soft"}`

## 2026-10-11 - Stage B LLM/RAG additive output

Each output record may now include a `stage_b` object. This is additive; existing
`stage_b_status`, `stage_b_findings`, and `stage_b_summary` remain for dashboard
compatibility.

`stage_b` fields:

- `mode`: `stage_a_only`, `llm_plain`, or `llm_rag`.
- `model`: Gemini model name, or `deterministic` for Stage A only fallback.
- `prompt_hash`: SHA-256 of model plus full prompt for LLM modes.
- `prompt_path`: saved prompt location when prompt persistence is explicitly enabled; otherwise `null`.
- `llm_status`: `ok`, `fallback`, or `skipped`.
- `fallback_reason`: string when deterministic fallback was used.
- `token_usage`: object with `prompt_tokens` and `output_tokens` when available.
- `latency_ms`: per-record LLM elapsed time.
- `cache_hit`: true when `dataset/cache/llm_cache.jsonl` supplied the response.
- `rag_uncited_suspicious_downgrades`: count of RAG verdicts downgraded because a
  suspicious verdict cited no retrieved fact ID.
- `request_count`, `request_failures`, and `duration_ms`: additive per-record
  request and processing metrics; these contain counts and durations only.

Full prompts contain candidate and possibly trusted-source transcript text.
They are not persisted by default. Set `PERSIST_STAGE_B_PROMPTS=true` only for
explicit debugging; local prompts are written under `results/prompts/` and
Lambda prompts under `/tmp/ehr-compliance-monitor/`.

S3 job polling keeps the legacy `result` field for the first record and also
returns `results` containing every processed record. Additive counts include
`expected_count`, `processed_count`, `successful_count`, `failed_count`, and
`fallback_count`. A `partial` status can indicate time truncation, per-record
failures, or Stage B fallback; `truncated` in the stored result distinguishes
time truncation from the other partial outcomes.

Severity mapping is explicit in `src/backend/stage_b/schema.py`: the LLM schema
uses `none`, `low`, `medium`, `high`, `critical`; older title-case severities are
normalized to the same lowercase set. Open decision: the older dashboard displays
title-case severity labels, while the LLM contract stores lowercase values.
- `associated_medication` and `associated_medication_span` on the entity itself

### Diagnoses
- `icd10_candidates` (array): top-3 ICD-10-CM matches with `{code, description, score}`
- `top_score`, `threshold`, `confident_match` (bool)
- `flags`: may contain `{flag_type: "no_confident_icd10_match"}`

### Procedures
- Text only, not verified against any coding system

### Measurements
- Non-drug-associated quantities (e.g., "blood loss 300mL")

---

## JSON Schema

See [`docs/contract_schema.json`](contract_schema.json) for the full JSON Schema.
