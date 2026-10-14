"""
Run Stage A retrieval plus Stage B over a JSON file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from backend.stage_a.pipeline import run_stage_a_batch
from backend.stage_b.stage_b import run_stage_b_batch


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

    stage_a_records = run_stage_a_batch(records)
    audited = run_stage_b_batch(stage_a_records, mode=args.mode, dry_run=args.dry_run, max_calls=args.max_calls, project_root=ROOT)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(audited, indent=2), encoding="utf-8")

    total_findings = sum(item.get("stage_b_summary", {}).get("n_findings", 0) for item in audited)
    print(json.dumps({
        "records": len(audited),
        "mode": args.mode,
        "dry_run": args.dry_run,
        "stage_b_findings": total_findings,
        "llm_ok": sum(1 for item in audited if item.get("stage_b", {}).get("llm_status") == "ok"),
        "fallback": sum(1 for item in audited if item.get("stage_b", {}).get("llm_status") == "fallback"),
        "output": str(output_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
