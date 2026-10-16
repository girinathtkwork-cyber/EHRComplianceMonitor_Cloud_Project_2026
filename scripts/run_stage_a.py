"""
scripts/run_stage_a.py — CLI wrapper for the Stage A pipeline
--------------------------------------------------------------
Fixes sys.path so imports work without PYTHONPATH on Windows.

Usage:
    python scripts/run_stage_a.py --input dataset/processed/mtsamples_with_errors.json --output results/stage_a_output.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Fix sys.path so we can import from src/
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from backend.stage_a.pipeline import run_stage_a_batch
from backend.stage_a.contract import validate_batch
from backend.stage_a import rxnorm_client


def main():
    parser = argparse.ArgumentParser(
        description="Run Stage A pipeline on medical transcript records."
    )
    parser.add_argument(
        "--input", required=True,
        help="Path to input JSON file (array of transcript records)."
    )
    parser.add_argument(
        "--output", required=True,
        help="Path to write output JSON file."
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    # Load input
    print(f"Loading input from {input_path}...")
    with open(input_path, encoding="utf-8") as f:
        records = json.load(f)
    print(f"Loaded {len(records)} records.")

    # Process
    start = time.time()

    def progress(i, total):
        elapsed = time.time() - start
        print(f"  Processed {i}/{total} records ({elapsed:.1f}s elapsed)...")

    results = run_stage_a_batch(records, progress_callback=progress)
    elapsed = time.time() - start

    # Validate contract
    validation = validate_batch(results)
    print(f"\nContract validation: {validation['valid']}/{validation['total']} valid, "
          f"{validation['invalid']} invalid")
    if validation["errors"]:
        for err in validation["errors"][:5]:
            print(f"  Record {err['transcript_id']}: {err['errors'][:3]}")

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # Summary
    cache = rxnorm_client.get_cache()
    total_entities = sum(r.get("stage_a_summary", {}).get("n_entities", 0)
                        for r in results if r.get("verification_status") in ("completed", "partial"))
    total_invalid = sum(r.get("stage_a_summary", {}).get("n_invalid", 0)
                       for r in results if r.get("verification_status") in ("completed", "partial"))
    total_forwarded = sum(r.get("stage_a_summary", {}).get("n_forwarded_to_stage_b", 0)
                         for r in results if r.get("verification_status") in ("completed", "partial"))
    n_errors = sum(1 for r in results if r.get("verification_status") == "error")

    print(f"\n{'='*60}")
    print(f"Stage A Pipeline Complete")
    print(f"{'='*60}")
    print(f"Records processed: {len(results)}")
    print(f"Successful: {len(results) - n_errors}")
    print(f"Errors: {n_errors}")
    print(f"Total entities: {total_entities}")
    print(f"Invalid entities: {total_invalid}")
    print(f"Forwarded to Stage B: {total_forwarded}")
    print(f"Cache hit rate: {cache.hit_rate:.1%}")
    print(f"Cache entries: {cache.size}")
    print(f"Elapsed time: {elapsed:.1f}s")
    print(f"Output written to: {output_path}")
    print(f"Contract validation errors: {validation['invalid']}")


if __name__ == "__main__":
    main()
