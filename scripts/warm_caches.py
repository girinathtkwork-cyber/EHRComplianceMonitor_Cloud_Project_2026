"""
scripts/warm_caches.py — Pre-warm the RxNorm cache
-----------------------------------------------------
Runs Stage A over the full dataset once to fill the RxNorm API cache.
After this script runs, subsequent pipeline executions use cached responses
and require no network access.

Usage:
    python scripts/warm_caches.py
"""

import json
import sys
import time
from pathlib import Path

# Fix sys.path
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from backend.stage_a.pipeline import run_stage_a_batch
from backend.stage_a import rxnorm_client


def main():
    input_path = _PROJECT_ROOT / "dataset" / "processed" / "mtsamples_with_errors.json"

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    print(f"Loading records from {input_path}...")
    with open(input_path, encoding="utf-8") as f:
        records = json.load(f)
    print(f"Loaded {len(records)} records.")

    print("Warming caches (this may take several minutes on first run)...")
    start = time.time()

    def progress(i, total):
        elapsed = time.time() - start
        cache = rxnorm_client.get_cache()
        print(
            f"  {i}/{total} records | "
            f"cache entries: {cache.size} | "
            f"hit rate: {cache.hit_rate:.1%} | "
            f"{elapsed:.0f}s"
        )

    # Run the full pipeline to populate caches
    run_stage_a_batch(records, progress_callback=progress)

    elapsed = time.time() - start
    cache = rxnorm_client.get_cache()

    print(f"\nCache warming complete.")
    print(f"Cache entries: {cache.size}")
    print(f"Cache hit rate: {cache.hit_rate:.1%}")
    print(f"Elapsed time: {elapsed:.1f}s")
    print(f"Cache file: {cache.path}")
    if cache.path.exists():
        size_mb = cache.path.stat().st_size / (1024 * 1024)
        print(f"Cache file size: {size_mb:.2f} MB")


if __name__ == "__main__":
    main()
