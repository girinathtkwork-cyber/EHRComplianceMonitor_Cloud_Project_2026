"""
Run Stage A retrieval plus Stage B over a JSON file.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from backend.stage_a import rxnorm_client
from backend.stage_a.pipeline import run_stage_a_batch
from backend.stage_b.stage_b import run_stage_b_batch


def _cache_snapshot() -> tuple[int, int]:
    cache = rxnorm_client.get_cache()
    return cache._hits, cache._misses


def process_records(records: list[dict], *, mode: str, dry_run: bool, max_calls: int | None, project_root: Path) -> tuple[list[dict], dict]:
    """Run both stages while preserving one output entry per input record."""
    total_started = time.monotonic()
    cache_before = _cache_snapshot()
    rxnorm_before = rxnorm_client.metrics_snapshot()
    stage_a_started = time.monotonic()
    stage_a_records = run_stage_a_batch(records)
    stage_a_duration_ms = int((time.monotonic() - stage_a_started) * 1000)

    stage_a_ok = [i for i, item in enumerate(stage_a_records) if item.get("verification_status") in ("completed", "partial")]
    stage_b_started = time.monotonic()
    stage_b_results = run_stage_b_batch(
        [stage_a_records[i] for i in stage_a_ok], mode=mode, dry_run=dry_run,
        max_calls=max_calls, project_root=project_root,
    )
    stage_b_duration_ms = int((time.monotonic() - stage_b_started) * 1000)
    audited_by_index = dict(zip(stage_a_ok, stage_b_results))
    audited = [audited_by_index.get(i, item) for i, item in enumerate(stage_a_records)]

    stage_a_failures = len(records) - len(stage_a_ok)
    stage_b_successes = sum(
        1 for i in stage_a_ok
        if audited[i].get("stage_b_status") == "completed"
        and (audited[i].get("stage_b") or {}).get("llm_status") != "fallback"
    )
    fallbacks = sum(
        1 for i in stage_a_ok
        if (audited[i].get("stage_b") or {}).get("llm_status") == "fallback"
    )
    stage_b_failures = len(stage_a_ok) - stage_b_successes - fallbacks
    processed_count = len(audited)
    if not records or stage_a_ok == [] or (stage_b_failures and not stage_b_successes and not fallbacks):
        status = "failed"
    elif processed_count != len(records) or stage_a_failures or stage_b_failures:
        status = "partial"
    else:
        status = "completed"

    cache_after = _cache_snapshot()
    rxnorm_after = rxnorm_client.metrics_snapshot()
    summary = {
        "status": status,
        "expected_count": len(records),
        "processed_count": processed_count,
        "stage_a_successes": len(stage_a_ok),
        "stage_a_failures": stage_a_failures,
        "stage_b_successes": stage_b_successes,
        "stage_b_failures": stage_b_failures,
        "stage_b_fallbacks": fallbacks,
        "stage_a_duration_ms": stage_a_duration_ms,
        "stage_b_duration_ms": stage_b_duration_ms,
        "total_duration_ms": int((time.monotonic() - total_started) * 1000),
        "rxnorm_cache_hits": cache_after[0] - cache_before[0],
        "rxnorm_cache_misses": cache_after[1] - cache_before[1],
        "terminology_request_count": rxnorm_after["requests"] - rxnorm_before["requests"],
        "terminology_request_failures": rxnorm_after["failures"] - rxnorm_before["failures"],
        "terminology_request_duration_ms": rxnorm_after["duration_ms"] - rxnorm_before["duration_ms"],
        "gemini_request_count": sum((item.get("stage_b") or {}).get("request_count", 0) for item in audited),
        "gemini_latency_ms": sum((item.get("stage_b") or {}).get("latency_ms", 0) for item in audited),
    }
    return audited, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the full EHR compliance monitor pipeline.")
    parser.add_argument("--input", required=True, help="Input JSON record or array of records.")
    parser.add_argument("--output", default="results/end_to_end_output.json", help="Output JSON path for audited records.")
    parser.add_argument("--mode", choices=["stage_a_only", "llm_plain", "llm_rag"], default="stage_a_only")
    parser.add_argument("--dry-run", action="store_true", help="Build and save prompts without API calls.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-calls", type=int, default=None)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    records = json.loads(input_path.read_text(encoding="utf-8"))
    if isinstance(records, dict):
        records = [records]
    if args.limit is not None:
        records = records[:args.limit]

    audited, summary = process_records(
        records, mode=args.mode, dry_run=args.dry_run,
        max_calls=args.max_calls, project_root=ROOT,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(audited, indent=2), encoding="utf-8")

    total_findings = sum(item.get("stage_b_summary", {}).get("n_findings", 0) for item in audited)
    summary.update({"records": len(audited), "mode": args.mode, "dry_run": args.dry_run,
                    "stage_b_findings": total_findings, "output": str(output_path)})
    print(json.dumps(summary, indent=2))
    return 1 if summary["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
