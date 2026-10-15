"""Validation and normalization for Stage B LLM output."""

from __future__ import annotations

ALLOWED_VERDICTS = {"consistent", "suspicious", "insufficient_evidence"}
ALLOWED_SEVERITIES = {"none", "low", "medium", "high", "critical"}
SEVERITY_MAP = {
    "None": "none",
    "Low": "low",
    "Medium": "medium",
    "High": "high",
    "Critical": "critical",
    "none": "none",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "critical": "critical",
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "entity_verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string"},
                    "verdict": {"type": "string"},
                    "reason": {"type": "string"},
                    "evidence_used": {"type": "array", "items": {"type": "string"}},
                    "severity": {"type": "string"},
                },
                "required": ["entity_id", "verdict", "reason", "evidence_used", "severity"],
            },
        },
        "additional_concerns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "type": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["text", "type", "reason"],
            },
        },
        "source_discrepancies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "candidate_excerpt": {"type": "string"},
                    "source_excerpt": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["type", "candidate_excerpt", "source_excerpt", "reason"],
            },
        },
    },
    "required": ["entity_verdicts", "additional_concerns", "source_discrepancies"],
}


def normalize_severity(value: str | None) -> str:
    return SEVERITY_MAP.get(value or "", "medium")


def _normalized_quote(value: str) -> str:
    return " ".join(value.split()).casefold()


def _validate_source_discrepancies(
    items: object,
    *,
    candidate_transcript: str,
    source_transcript: str,
) -> list[dict]:
    if not isinstance(items, list):
        raise ValueError("LLM response missing source_discrepancies array")
    discrepancies = []
    candidate_normalized = _normalized_quote(candidate_transcript)
    source_normalized = _normalized_quote(source_transcript)
    allowed_types = {"omission", "addition", "contradiction"}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("source_discrepancies items must be objects")
        discrepancy_type = str(item.get("type", "")).strip().lower()
        candidate_excerpt = str(item.get("candidate_excerpt", "")).strip()
        source_excerpt = str(item.get("source_excerpt", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if discrepancy_type not in allowed_types:
            raise ValueError(f"invalid source discrepancy type: {discrepancy_type}")
        if not reason:
            raise ValueError("source discrepancy missing reason")
        if not source_transcript:
            raise ValueError("source discrepancies require a trusted source transcript")
        if discrepancy_type == "omission":
            valid_quotes = (
                not candidate_excerpt
                and bool(source_excerpt)
                and _normalized_quote(source_excerpt) in source_normalized
            )
        elif discrepancy_type == "addition":
            valid_quotes = (
                bool(candidate_excerpt)
                and not source_excerpt
                and _normalized_quote(candidate_excerpt) in candidate_normalized
            )
        else:
            valid_quotes = (
                bool(candidate_excerpt)
                and bool(source_excerpt)
                and _normalized_quote(candidate_excerpt) in candidate_normalized
                and _normalized_quote(source_excerpt) in source_normalized
            )
        if not valid_quotes:
            raise ValueError("source discrepancy quotes must match their input transcripts")
        if len(candidate_excerpt) > 280 or len(source_excerpt) > 280:
            raise ValueError("source discrepancy excerpts must be 280 characters or fewer")
        discrepancies.append({
            "type": discrepancy_type,
            "candidate_excerpt": candidate_excerpt,
            "source_excerpt": source_excerpt,
            "reason": reason,
        })
    return discrepancies


def validate_response(
    data: dict,
    *,
    mode: str,
    valid_fact_ids: set[str],
    candidate_transcript: str = "",
    source_transcript: str = "",
) -> tuple[dict, dict]:
    """Validate by hand and downgrade uncited RAG suspicions."""
    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object")
    verdicts = data.get("entity_verdicts")
    concerns = data.get("additional_concerns")
    if not isinstance(verdicts, list) or not isinstance(concerns, list):
        raise ValueError("LLM response missing entity_verdicts/additional_concerns arrays")
    source_discrepancies = _validate_source_discrepancies(
        data.get("source_discrepancies"),
        candidate_transcript=candidate_transcript,
        source_transcript=source_transcript,
    )

    stats = {"rag_uncited_suspicious_downgrades": 0}
    clean_verdicts = []
    for item in verdicts:
        if not isinstance(item, dict):
            raise ValueError("entity_verdicts items must be objects")
        entity_id = str(item.get("entity_id", "")).strip()
        verdict = str(item.get("verdict", "")).strip()
        reason = str(item.get("reason", "")).strip()
        evidence_used = item.get("evidence_used", [])
        severity = normalize_severity(str(item.get("severity", "")))
        if not entity_id:
            raise ValueError("verdict missing entity_id")
        if verdict not in ALLOWED_VERDICTS:
            raise ValueError(f"invalid verdict: {verdict}")
        if not isinstance(evidence_used, list):
            raise ValueError("evidence_used must be an array")
        evidence_used = [str(v).strip() for v in evidence_used if str(v).strip()]

        if mode == "llm_rag" and verdict == "suspicious":
            if not any(fid in valid_fact_ids for fid in evidence_used):
                verdict = "insufficient_evidence"
                severity = "none"
                stats["rag_uncited_suspicious_downgrades"] += 1

        if verdict != "suspicious" and severity != "none":
            severity = "none" if verdict == "consistent" else severity

        clean_verdicts.append({
            "entity_id": entity_id,
            "verdict": verdict,
            "reason": reason,
            "evidence_used": evidence_used,
            "severity": severity if severity in ALLOWED_SEVERITIES else "medium",
        })

    clean_concerns = []
    for item in concerns:
        if not isinstance(item, dict):
            raise ValueError("additional_concerns items must be objects")
        clean_concerns.append({
            "text": str(item.get("text", "")).strip(),
            "type": str(item.get("type", "")).strip(),
            "reason": str(item.get("reason", "")).strip(),
        })

    return {
        "entity_verdicts": clean_verdicts,
        "additional_concerns": clean_concerns,
        "source_discrepancies": source_discrepancies,
    }, stats
