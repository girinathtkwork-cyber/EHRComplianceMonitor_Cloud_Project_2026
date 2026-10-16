"""Prompt construction for plain and RAG Stage B runs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

GROUND_TRUTH_KEYS = {"ground_truth_label", "is_error", "error_type", "injected_entity", "correct_value"}

BASE_INSTRUCTIONS = """You are Stage B of a medical transcription safety gate.
Review a candidate transcript, extracted medical entities, and (when supplied) a trusted source transcript.
Return only JSON matching this schema:
{"entity_verdicts":[{"entity_id":"...","verdict":"consistent|suspicious|insufficient_evidence","reason":"...","evidence_used":["F1"],"severity":"none|low|medium|high|critical"}],"source_discrepancies":[{"type":"omission|addition|contradiction","candidate_excerpt":"exact quote or empty for omission","source_excerpt":"exact quote or empty for addition","reason":"brief textual difference"}],"additional_concerns":[{"text":"...","type":"...","reason":"..."}]}

Rules:
- Evaluate whether each entity is clinically or contextually consistent with the transcript.
- Do not diagnose, prescribe, or claim certainty; this is for human review.
- Use severity "none" unless the verdict is "suspicious".
- When a trusted source transcript is supplied, compare it with the candidate only for transcription/documentation fidelity.
- Report material omissions, additions, and contradictions; ignore harmless formatting and wording differences.
- Quote exact, short excerpts from the corresponding input for every discrepancy. For an omission, candidate_excerpt must be empty; for an addition, source_excerpt must be empty.
- Do not infer clinical correctness from the trusted source or assign clinical risk; explain only the textual difference.
- Return an empty source_discrepancies array when no material differences are found or no trusted source is supplied.
- Do not mention hidden labels or answer keys.
"""

PLAIN_LINE = "Evidence rule: use your own medical knowledge."
RAG_LINE = "Evidence rule: base verdicts on the numbered retrieved facts and the transcript; if they are insufficient, answer insufficient_evidence instead of guessing."

_LOGGER = logging.getLogger(__name__)


def _is_lambda_env() -> bool:
    return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or os.environ.get("LAMBDA_TASK_ROOT"))


def _clean_record(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in GROUND_TRUTH_KEYS}


def _entity_id(index: int, entity: dict) -> str:
    return entity.get("entity_id") or f"E{index + 1}"


def build_prompt(record: dict, *, mode: str) -> tuple[str, list[dict], set[str]]:
    if mode not in {"llm_plain", "llm_rag"}:
        raise ValueError(f"prompt mode not supported: {mode}")

    safe_record = _clean_record(record)
    transcript = safe_record.get("transcription", "")
    source_transcript = safe_record.get("source_transcript", "")
    entities = []
    facts = []
    fact_ids = set()

    for idx, entity in enumerate(safe_record.get("entities", [])):
        if entity.get("routing") == "invalid":
            continue
        entity_id = _entity_id(idx, entity)
        entities.append({
            "entity_id": entity_id,
            "entity_type": entity.get("entity_type"),
            "text": entity.get("text"),
        })
        if mode == "llm_rag":
            evidence = (entity.get("retrieval_context") or {}).get("evidence_text", "")
            if evidence:
                fid = f"F{len(facts) + 1}"
                fact_ids.add(fid)
                facts.append({"fact_id": fid, "entity_id": entity_id, "text": evidence})

    evidence_block = "No retrieved facts are provided in this mode."
    if mode == "llm_rag":
        evidence_block = json.dumps(facts, ensure_ascii=True, indent=2)

    prompt = "\n\n".join([
        BASE_INSTRUCTIONS.strip(),
        PLAIN_LINE if mode == "llm_plain" else RAG_LINE,
        "Candidate transcript:\n" + transcript,
        "Trusted source transcript:\n" + (source_transcript or "[Not provided]"),
        "Entities:\n" + json.dumps(entities, ensure_ascii=True, indent=2),
        "Retrieved facts:\n" + evidence_block,
    ])
    assert_no_ground_truth_leak(prompt)
    return prompt, entities, fact_ids


def assert_no_ground_truth_leak(prompt: str) -> None:
    lowered = prompt.lower()
    leaked = [key for key in GROUND_TRUTH_KEYS if key.lower() in lowered]
    if leaked:
        raise ValueError(f"prompt contains ground-truth key(s): {', '.join(leaked)}")


def prompt_hash(prompt: str, model: str = "") -> str:
    return hashlib.sha256((model + "\n" + prompt).encode("utf-8")).hexdigest()


def save_prompt(prompt: str, *, mode: str, transcript_id: str, root: Path) -> Path | None:
    """Persist full transcript-bearing prompts only when explicitly enabled."""
    if os.environ.get("PERSIST_STAGE_B_PROMPTS", "").strip().lower() not in {"1", "true", "yes"}:
        return None
    if _is_lambda_env():
        prompt_root = Path(tempfile.gettempdir()) / "ehr-compliance-monitor"
    else:
        prompt_root = root
    out_dir = prompt_root / "results" / "prompts" / mode
    path = out_dir / f"{transcript_id}.txt"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(prompt, encoding="utf-8")
    except OSError as exc:
        if not _is_lambda_env():
            raise
        _LOGGER.warning("lambda_prompt_persistence_failed error_type=%s", type(exc).__name__)
    return path
