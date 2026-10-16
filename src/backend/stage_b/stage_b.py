"""Stage B entry point for deterministic, plain LLM, and RAG LLM modes."""

from __future__ import annotations

import time
from pathlib import Path

from .audit import audit_record
from .fallback import run_fallback
from .llm_client import DEFAULT_MODEL, GeminiClient
from .prompts import build_prompt, prompt_hash, save_prompt
from .risk import prioritize_findings, reference_failure_severity
from .schema import validate_response

MODES = {"stage_a_only", "llm_plain", "llm_rag"}


def _root() -> Path:
    d = Path(__file__).resolve()
    for parent in d.parents:
        if (parent / "dataset").is_dir():
            return parent
    return Path.cwd()


def _invalid_findings(record: dict) -> list[dict]:
    findings = []
    for entity in record.get("entities", []):
        if entity.get("routing") == "invalid":
            findings.append({
                "id": f"invalid-{entity.get('entity_type', 'entity')}-{(entity.get('span') or ['na', 'na'])[0]}-{(entity.get('span') or ['na', 'na'])[1]}-{entity.get('text', '')}".lower().replace(" ", "-")[:180],
                "severity": reference_failure_severity(entity.get("entity_type")),
                "category": "Reference verification failure",
                "entity_type": entity.get("entity_type"),
                "phrase": entity.get("text"),
                "span": entity.get("span"),
                "reason": "Entity failed Stage A terminology verification.",
                "evidence": (entity.get("retrieval_context") or {}).get("evidence_text", ""),
                "stage_a_routing": "invalid",
                "finding_kind": "terminology_lookup_failure",
                "detection_stage": "stage_a",
                "provenance": "stage_a_terminology_rule",
                "confidence": "not_quantified",
                "uncertainty": "A terminology lookup failure is not by itself a clinical or regulatory violation.",
            })
    return findings


def _verdict_findings(record: dict, verdicts: list[dict]) -> list[dict]:
    by_id = {}
    for idx, entity in enumerate(record.get("entities", [])):
        by_id[entity.get("entity_id") or f"E{idx + 1}"] = entity
    findings = _invalid_findings(record)
    for verdict in verdicts:
        if verdict["severity"] == "none" or verdict["verdict"] != "suspicious":
            continue
        entity = by_id.get(verdict["entity_id"], {})
        findings.append({
            "id": f"llm-{verdict['entity_id']}-{entity.get('text', '')}".lower().replace(" ", "-")[:140],
            "severity": verdict["severity"],
            "category": "LLM contextual review",
            "entity_type": entity.get("entity_type"),
            "phrase": entity.get("text", verdict["entity_id"]),
            "span": entity.get("span"),
            "reason": verdict["reason"],
            "evidence": ", ".join(verdict.get("evidence_used", [])),
            "stage_a_routing": entity.get("routing"),
            "finding_kind": "model_generated_suggestion",
            "detection_stage": "stage_b_llm",
            "provenance": "gemini_validated_response",
            "confidence": "not_reported",
            "uncertainty": "Model suggestion; the source evidence and clinical context require human review.",
        })
    return findings


def _summary(findings: list[dict], *, source_comparison: bool = False) -> dict:
    counts = {level: 0 for level in ("critical", "high", "medium", "low")}
    for item in findings:
        sev = str(item.get("severity", "")).lower()
        if sev in counts:
            counts[sev] += 1
    return {"n_findings": len(findings), **counts, "source_comparison": source_comparison}


def _source_comparison_metadata(record: dict, discrepancies: list[dict] | None = None) -> dict:
    source_provided = bool(str(record.get("source_transcript", "")).strip())
    if not source_provided:
        status = "not_provided"
    elif discrepancies is None:
        status = "not_performed"
    else:
        status = "compared"
    return {"status": status, "discrepancies": discrepancies or []}


def run_stage_b(record: dict, mode: str = "stage_a_only", *, client=None, dry_run: bool = False, max_calls: int | None = None, project_root: Path | None = None) -> dict:
    if mode not in MODES:
        raise ValueError(f"unknown Stage B mode: {mode}")
    if mode == "stage_a_only":
        output = audit_record(record, record.get("source_transcript"))
        output["stage_b_summary"]["source_comparison"] = False
        output["stage_b"] = {
            "mode": "stage_a_only",
            "model": "deterministic",
            "prompt_hash": None,
            "llm_status": "skipped",
            "fallback_reason": None,
            "request_count": 0,
            "request_failures": 0,
            "duration_ms": 0,
            "token_usage": {"prompt_tokens": 0, "output_tokens": 0},
            "latency_ms": 0,
            "rag_uncited_suspicious_downgrades": 0,
            "source_comparison": _source_comparison_metadata(record),
        }
        return output

    client = client or GeminiClient()
    if max_calls is not None and getattr(client, "calls_made", 0) >= max_calls and not dry_run:
        output = run_fallback(record, "max_calls_reached")
        output["stage_b_summary"]["source_comparison"] = False
        output["stage_b"]["mode"] = mode
        output["stage_b"]["source_comparison"] = _source_comparison_metadata(record)
        return output

    transcript_id = str(record.get("transcript_id", "unknown"))
    stage_b_started = time.monotonic()
    request_count_before = getattr(client, "calls_made", 0)
    try:
        prompt, entities, fact_ids = build_prompt(record, mode=mode)
        model = getattr(client, "model", DEFAULT_MODEL)
        phash = prompt_hash(prompt, model)
        prompt_path = save_prompt(prompt, mode=mode, transcript_id=transcript_id, root=project_root or _root())
        start = time.monotonic()
        result = client.generate_json(prompt, dry_run=dry_run)
        source_transcript = str(record.get("source_transcript", "")).strip()
        candidate_transcript = str(record.get("transcription", ""))
        validated, stats = validate_response(
            result["json"],
            mode=mode,
            valid_fact_ids=fact_ids,
            candidate_transcript=candidate_transcript,
            source_transcript=source_transcript,
        )
        latency_ms = result.get("latency_ms") or int((time.monotonic() - start) * 1000)
        findings = prioritize_findings(_verdict_findings(record, validated["entity_verdicts"]))
        source_comparison = _source_comparison_metadata(
            record,
            validated["source_discrepancies"] if source_transcript else None,
        )
        output = dict(record)
        output.update({
            "stage_b_version": "2.0.0",
            "stage_b_status": "completed",
            "stage_b_findings": findings,
            "stage_b_summary": _summary(
                findings,
                source_comparison=source_comparison["status"] == "compared",
            ),
            "stage_b": {
                "mode": mode,
                "model": model,
                "prompt_hash": phash,
                "prompt_path": (
                    str(Path("results") / "prompts" / mode / f"{transcript_id}.txt")
                    if prompt_path is not None else None
                ),
                "llm_status": "skipped" if dry_run else "ok",
                "fallback_reason": None,
                "request_count": getattr(client, "calls_made", 0) - request_count_before,
                "request_failures": 0,
                "duration_ms": int((time.monotonic() - stage_b_started) * 1000),
                "token_usage": result.get("usage", {}),
                "latency_ms": latency_ms,
                "cache_hit": result.get("cache_hit", False),
                "rag_uncited_suspicious_downgrades": stats["rag_uncited_suspicious_downgrades"],
                "entity_count_sent": len(entities),
                "source_comparison": source_comparison,
            },
            "llm_response": validated,
        })
        return output
    except Exception as exc:
        output = run_fallback(record, str(exc))
        output["stage_b_summary"]["source_comparison"] = False
        output["stage_b"]["mode"] = mode
        output["stage_b"]["model"] = getattr(client, "model", DEFAULT_MODEL)
        request_count = getattr(client, "calls_made", 0) - request_count_before
        output["stage_b"].update({
            "request_count": request_count,
            "request_failures": 1 if request_count else 0,
            "duration_ms": int((time.monotonic() - stage_b_started) * 1000),
            "latency_ms": int((time.monotonic() - stage_b_started) * 1000) if request_count else 0,
        })
        output["stage_b"]["source_comparison"] = _source_comparison_metadata(record)
        return output


def run_stage_b_batch(records: list[dict], mode: str = "stage_a_only", **kwargs) -> list[dict]:
    return [run_stage_b(record, mode=mode, **kwargs) for record in records]
