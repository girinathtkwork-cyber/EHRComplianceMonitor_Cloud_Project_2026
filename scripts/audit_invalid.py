"""
scripts/audit_invalid.py — Audit of invalid entities
---------------------------------------------------
Reports invalid entities by entity type and clean-vs-injected record,
along with the 25 most common invalid texts on clean records.

Usage:
    python scripts/audit_invalid.py
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main():
    stage_a_path = _PROJECT_ROOT / "results" / "stage_a_output.json"
    if not stage_a_path.exists():
        print(f"Error: {stage_a_path} does not exist.")
        sys.exit(1)

    with open(stage_a_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    # Counters
    by_type = Counter()
    by_record_type = Counter()
    by_type_and_record = defaultdict(Counter)
    clean_invalid_texts = Counter()

    total_records = len(records)
    clean_records_count = 0
    injected_records_count = 0

    for rec in records:
        gt = rec.get("ground_truth_label", {})
        is_injected = gt.get("is_error", False)
        rec_type = "injected" if is_injected else "clean"

        if is_injected:
            injected_records_count += 1
        else:
            clean_records_count += 1

        for ent in rec.get("entities", []):
            if ent.get("routing") == "invalid":
                etype = ent.get("entity_type", "unknown")
                text = ent.get("text", "").strip()

                by_type[etype] += 1
                by_record_type[rec_type] += 1
                by_type_and_record[rec_type][etype] += 1

                if not is_injected:
                    clean_invalid_texts[(text, etype)] += 1

    print("=" * 60)
    print("AUDIT OF INVALID ENTITIES")
    print("=" * 60)
    print(f"Total Records: {total_records} (Clean: {clean_records_count}, Injected: {injected_records_count})")
    print(f"Total Invalid Entities: {sum(by_type.values())}\n")

    print("--- Invalid Entities by Entity Type ---")
    for etype, count in by_type.most_common():
        print(f"  {etype:15s}: {count:4d}")
    print()

    print("--- Invalid Entities by Record Class (Clean vs Injected) ---")
    for rtype in ["clean", "injected"]:
        print(f"  {rtype.capitalize():8s} records: {by_record_type[rtype]:4d} invalid entities")
        for etype, count in by_type_and_record[rtype].most_common():
            print(f"    - {etype:12s}: {count:4d}")
    print()

    print("--- Top 25 Most Common Invalid Texts on Clean Records ---")
    print(f"{'Rank':<5} {'Count':<6} {'Entity Type':<14} {'Text'}")
    print("-" * 60)
    for rank, (((text, etype)), count) in enumerate(clean_invalid_texts.most_common(25), 1):
        print(f"{rank:<5} {count:<6} {etype:<14} {text}")


if __name__ == "__main__":
    main()
