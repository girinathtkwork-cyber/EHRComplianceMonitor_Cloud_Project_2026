"""Deterministic Stage B fallback adapter."""

from __future__ import annotations

from .audit import audit_record


def run_fallback(record: dict, reason: str = "deterministic_fallback") -> dict:
    output = audit_record(record, record.get("source_transcript"))
    output["stage_b"] = {
        "mode": "stage_a_only",
        "model": "deterministic",
        "prompt_hash": None,
        "llm_status": "fallback",
        "fallback_reason": reason,
        "token_usage": {"prompt_tokens": 0, "output_tokens": 0},
        "latency_ms": 0,
        "rag_uncited_suspicious_downgrades": 0,
    }
    return output
