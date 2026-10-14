"""
scripts/make_sample.py — Create a 20-record sample for AWS testing
--------------------------------------------------------------------
Writes dataset/processed/sample_20_for_aws_test.json with 20 records:
a mix of clean and injected, selected with a fixed seed.

Usage:
    python scripts/make_sample.py
"""

import json
import random
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent

SEED = 42
SAMPLE_SIZE = 20


def main():
    input_path = _PROJECT_ROOT / "dataset" / "processed" / "mtsamples_with_errors.json"
    output_path = _PROJECT_ROOT / "dataset" / "processed" / "sample_20_for_aws_test.json"

    if not input_path.exists():
        print(f"Error: Input not found: {input_path}")
        sys.exit(1)

    with open(input_path, encoding="utf-8") as f:
        records = json.load(f)

    # Split into clean and injected
    clean = [r for r in records if not r.get("ground_truth_label", {}).get("is_error", False)]
    injected = [r for r in records if r.get("ground_truth_label", {}).get("is_error", False)]

    print(f"Total records: {len(records)} (clean: {len(clean)}, injected: {len(injected)})")

    rng = random.Random(SEED)

    # Take roughly half from each
    n_injected = min(SAMPLE_SIZE // 2, len(injected))
    n_clean = SAMPLE_SIZE - n_injected

    sample_injected = rng.sample(injected, n_injected)
    sample_clean = rng.sample(clean, n_clean)

    sample = sample_injected + sample_clean
    # Sort by transcript_id for determinism
    sample.sort(key=lambda r: r["transcript_id"])

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sample, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(sample)} records to {output_path}")
    print(f"  Clean: {n_clean}, Injected: {n_injected}")
    error_types = {}
    for r in sample_injected:
        et = r.get("ground_truth_label", {}).get("error_type", "unknown")
        error_types[et] = error_types.get(et, 0) + 1
    print(f"  Error types: {error_types}")


if __name__ == "__main__":
    main()
