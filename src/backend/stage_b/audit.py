"""
Stage B contextual audit.

This module consumes Stage A output and produces clinician-review findings.
It is intentionally deterministic for the student prototype: no PHI leaves the
runtime, no model key is required, and every finding cites Stage A evidence.
"""

from __future__ import annotations

import datetime
import re
from collections import Counter

from .risk import prioritize_findings, reference_failure_severity

STAGE_B_VERSION = "1.0.0"

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_NEGATION = re.compile(r"\b(no|denies|denied|without|negative for|not)\b", re.I)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _text(value) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in _SENTENCE_SPLIT.split(_text(text)) if item.strip()]


def _contains(text: str, phrase: str) -> bool:
    if not text or not phrase:
        return False
    return re.search(rf"\b{re.escape(phrase)}\b", text, re.I) is not None


def _source_support(source_text: str, phrase: str) -> tuple[bool, str | None]:
    for sentence in _sentences(source_text):
        if _contains(sentence, phrase):
            return True, sentence
    return False, None


def _documented_conditions(record: dict) -> set[str]:
    conditions = set()
    for entity in record.get("entities", []):
        if entity.get("entity_type") != "diagnosis":
            continue
        text = _text(entity.get("text")).lower()
        if text:
            conditions.add(text)
        for candidate in entity.get("retrieval_context", {}).get("icd10_candidates", [])[:3]:
            desc = _text(candidate.get("description")).lower()
            if desc:
                conditions.add(desc)
    return conditions


def _finding(severity: str, category: str, entity: dict, reason: str, evidence: str) -> dict:
    phrase = _text(entity.get("text"))
    span = entity.get("span", [])
    terminology = category in {"Reference verification failure", "Unresolved reference match", "Diagnosis requires coding review"}
    if category == "Unresolved reference match" or category == "Diagnosis requires coding review":
        finding_kind, confidence = "low_confidence_terminology_match", "low"
    elif category == "Reference verification failure":
        finding_kind, confidence = "terminology_lookup_failure", "not_quantified"
    elif category in {"Unsupported source claim", "Negated source context"}:
        finding_kind, confidence = "source_text_comparison_signal", "not_quantified"
    else:
        finding_kind, confidence = "potential_context_concern", "not_quantified"
    return {
        "id": f"{category}-{entity.get('entity_type', 'entity')}-{span[0] if len(span) > 0 else 'na'}-{span[1] if len(span) > 1 else 'na'}-{phrase}-{reason}".lower().replace(" ", "-")[:200],
        "severity": severity,
        "category": category,
        "entity_type": entity.get("entity_type"),
        "phrase": phrase,
        "span": entity.get("span"),
        "reason": reason,
        "evidence": evidence,
        "stage_a_routing": entity.get("routing"),
        "finding_kind": finding_kind,
        "detection_stage": "stage_a" if terminology else "stage_b_deterministic",
        "provenance": "stage_a_terminology_rule" if terminology else "deterministic_audit_rule",
        "confidence": confidence,
        "uncertainty": "This review signal does not independently establish a clinical or regulatory violation.",
    }


def audit_record(stage_a_record: dict, source_transcript: str | None = None) -> dict:
    """Audit one Stage A record and add final Stage B review findings."""
    source_text = _text(source_transcript or stage_a_record.get("source_transcript", ""))
    conditions = _documented_conditions(stage_a_record)
    findings: list[dict] = []

    for entity in stage_a_record.get("entities", []):
        etype = entity.get("entity_type")
        routing = entity.get("routing")
        context = entity.get("retrieval_context", {})
        evidence_text = _text(context.get("evidence_text"))
        phrase = _text(entity.get("text"))

        if routing == "invalid":
            findings.append(_finding(
                reference_failure_severity(etype),
                "Reference verification failure",
                entity,
                "Entity failed Stage A terminology verification.",
                evidence_text,
            ))
            continue

        if routing == "unresolved":
            findings.append(_finding(
                "Medium",
                "Unresolved reference match",
                entity,
                "Stage A could not make a confident ICD-10-CM match.",
                evidence_text,
            ))

        flags = context.get("flags", [])
        for flag in flags:
            if flag.get("flag_type") == "dose_not_a_known_strength":
                findings.append(_finding(
                    "High",
                    "Dose requires confirmation",
                    entity,
                    "Dose was not found among known RxNorm product strengths for the associated medication.",
                    f"{evidence_text} {flag.get('detail', '')}".strip(),
                ))
            if flag.get("flag_type") == "no_confident_icd10_match":
                findings.append(_finding(
                    "Medium",
                    "Diagnosis requires coding review",
                    entity,
                    "Diagnosis wording has low ICD-10-CM similarity and should be checked in context.",
                    evidence_text,
                ))

        if source_text and etype in {"medication", "diagnosis", "procedure"}:
            supported, sentence = _source_support(source_text, phrase)
            if not supported:
                findings.append(_finding(
                    "High",
                    "Unsupported source claim",
                    entity,
                    "Entity appears in the candidate transcript but not in the trusted source transcript.",
                    evidence_text,
                ))
            elif sentence and _NEGATION.search(sentence):
                findings.append(_finding(
                    "High",
                    "Negated source context",
                    entity,
                    "Trusted source mention appears in a negated context.",
                    sentence,
                ))

        if etype == "medication":
            indications = context.get("indications", {}).get("indications", [])
            indication_terms = {_text(item.get("condition")).lower() for item in indications}
            indication_terms.discard("")
            if indication_terms and conditions:
                linked = any(
                    cond in indication or indication in cond
                    for cond in conditions
                    for indication in indication_terms
                )
                if not linked:
                    findings.append(_finding(
                        "Medium",
                        "Medication indication not evident",
                        entity,
                        "Stage A retrieved indications, but no extracted diagnosis clearly supports this medication.",
                        evidence_text,
                    ))

    deduped = prioritize_findings(list({item["id"]: item for item in findings}.values()))
    counts = Counter(item["severity"] for item in deduped)

    output = dict(stage_a_record)
    output["stage_b_version"] = STAGE_B_VERSION
    output["stage_b_status"] = "completed"
    output["stage_b_timestamp"] = _now()
    output["stage_b_findings"] = deduped
    output["stage_b_summary"] = {
        "n_findings": len(deduped),
        "critical": counts.get("Critical", 0),
        "high": counts.get("High", 0),
        "medium": counts.get("Medium", 0),
        "low": counts.get("Low", 0),
        "source_comparison": bool(source_text),
    }
    return output


def audit_batch(stage_a_records: list[dict]) -> list[dict]:
    return [audit_record(record) for record in stage_a_records]
