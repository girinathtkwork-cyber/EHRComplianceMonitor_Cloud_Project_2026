"""
Stage A Evaluation — Metrics and Analysis
--------------------------------------------
Computes record-level detection, per-error-type funnel coverage,
audit tables, and cost proxies from Stage A pipeline output.

Usage:
    python src/backend/evaluation/evaluate_stage_a.py
    python src/backend/evaluation/evaluate_stage_a.py --stage-b-results results/stage_b.json
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

# Fix sys.path
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))


# ===========================================================================
# Wilson score interval
# ===========================================================================

def wilson_ci(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson confidence interval for a proportion."""
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z**2 / total
    center = (p + z**2 / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


# ===========================================================================
# Overlap checker for funnel coverage
# ===========================================================================

def entity_overlaps_injected(entity: dict, injected_entity: str) -> bool:
    """Check if an extracted entity overlaps the ground-truth injected entity.
    
    Uses case-insensitive substring matching in both directions.
    """
    if not injected_entity:
        return False
    et = entity.get("text", "").lower().strip()
    gt = injected_entity.lower().strip()
    if not et or not gt:
        return False
    return et in gt or gt in et


# ===========================================================================
# Main evaluation
# ===========================================================================

def evaluate(stage_a_results: list[dict], ground_truth: list[dict]) -> dict:
    """Run the full Stage A evaluation."""
    
    # Build ground truth lookup by transcript_id
    gt_by_id = {r["transcript_id"]: r for r in ground_truth}

    # -----------------------------------------------------------------------
    # 1. Record-level detection
    # -----------------------------------------------------------------------
    tp_strict = fp_strict = fn_strict = tn_strict = 0
    tp_soft = fp_soft = fn_soft = tn_soft = 0

    # -----------------------------------------------------------------------
    # 2. Per error type
    # -----------------------------------------------------------------------
    error_stats = defaultdict(lambda: {"n": 0, "detected_stage_a": 0, "funnel_coverage": 0})

    # -----------------------------------------------------------------------
    # 3. Audit table
    # -----------------------------------------------------------------------
    misses = []
    dosage_audit = {"drug_associated": [], "non_drug": []}

    # -----------------------------------------------------------------------
    # 4. Cost proxy
    # -----------------------------------------------------------------------
    total_entities = 0
    forwarded_entities = 0
    zero_forwarded_count = 0

    for result in stage_a_results:
        tid = result.get("transcript_id")
        gt_rec = gt_by_id.get(tid)
        if gt_rec is None:
            continue

        entities = result.get("entities", [])
        total_entities += len(entities)

        # Count entities with each routing
        n_invalid = sum(1 for e in entities if e.get("routing") == "invalid")
        n_forwarded = sum(1 for e in entities if e.get("routing") == "verified_needs_context_check")
        forwarded_entities += n_forwarded

        # Check for soft flags (dose_not_a_known_strength)
        has_soft_flag = any(
            any(f.get("flag_type") == "dose_not_a_known_strength"
                for f in e.get("retrieval_context", {}).get("flags", []))
            for e in entities
        )

        has_invalid = n_invalid > 0
        has_soft_signal = has_invalid or has_soft_flag

        if n_forwarded == 0 and n_invalid == 0:
            zero_forwarded_count += 1

        # Ground truth
        gt_label = gt_rec.get("ground_truth_label", {})
        is_error = gt_label.get("is_error", False)
        error_type = gt_label.get("error_type")
        injected_entity = gt_label.get("injected_entity", "")

        if is_error:
            # Positive case
            if has_invalid:
                tp_strict += 1
            else:
                fn_strict += 1
            if has_soft_signal:
                tp_soft += 1
            else:
                fn_soft += 1

            # Per error type analysis
            if error_type:
                error_stats[error_type]["n"] += 1

                # Check localized detection: does any invalid entity overlap injected_entity?
                detected = False
                in_funnel = False
                miss_reason = "not_extracted"

                for e in entities:
                    if entity_overlaps_injected(e, injected_entity):
                        # Entity was extracted
                        routing = e.get("routing", "")
                        flags = e.get("retrieval_context", {}).get("flags", [])
                        has_flag = any(
                            f.get("flag_type") in ("dose_not_a_known_strength", "no_confident_icd10_match")
                            for f in flags
                        )

                        if routing == "invalid":
                            detected = True
                            in_funnel = True
                            miss_reason = None
                            break
                        elif routing == "verified_needs_context_check":
                            in_funnel = True
                            miss_reason = "extracted_but_forwarded"
                            if has_flag:
                                miss_reason = "extracted_flagged_forwarded"
                        else:
                            miss_reason = "extracted_but_passed"

                if detected:
                    error_stats[error_type]["detected_stage_a"] += 1
                if in_funnel:
                    error_stats[error_type]["funnel_coverage"] += 1
                
                if not in_funnel:
                    miss_info = {
                        "transcript_id": tid,
                        "error_type": error_type,
                        "injected_entity": injected_entity,
                        "reason": miss_reason,
                    }
                    misses.append(miss_info)

                    # Wrong dosage audit
                    if error_type == "wrong_dosage":
                        # Check if the injected entity overlaps a dosage vs measurement
                        is_drug_dose = any(
                            e.get("entity_type") == "dosage"
                            and entity_overlaps_injected(e, injected_entity)
                            for e in entities
                        )
                        if is_drug_dose:
                            dosage_audit["drug_associated"].append(miss_info)
                        else:
                            dosage_audit["non_drug"].append(miss_info)
        else:
            # Clean record (negative case)
            if has_invalid:
                fp_strict += 1
            else:
                tn_strict += 1
            if has_soft_signal:
                fp_soft += 1
            else:
                tn_soft += 1

    # -----------------------------------------------------------------------
    # Compute derived metrics
    # -----------------------------------------------------------------------
    def safe_div(a, b):
        return a / b if b > 0 else 0.0

    prec_strict = safe_div(tp_strict, tp_strict + fp_strict)
    rec_strict = safe_div(tp_strict, tp_strict + fn_strict)
    f1_strict = safe_div(2 * prec_strict * rec_strict, prec_strict + rec_strict)
    fpr_strict = safe_div(fp_strict, fp_strict + tn_strict)

    prec_soft = safe_div(tp_soft, tp_soft + fp_soft)
    rec_soft = safe_div(tp_soft, tp_soft + fn_soft)
    f1_soft = safe_div(2 * prec_soft * rec_soft, prec_soft + rec_soft)
    fpr_soft = safe_div(fp_soft, fp_soft + tn_soft)

    # Wilson CIs
    ci_rec_strict = wilson_ci(tp_strict, tp_strict + fn_strict)
    ci_fpr_strict = wilson_ci(fp_strict, fp_strict + tn_strict)
    ci_rec_soft = wilson_ci(tp_soft, tp_soft + fn_soft)
    ci_fpr_soft = wilson_ci(fp_soft, fp_soft + tn_soft)

    # Per error type rates
    per_error = {}
    for etype, stats in error_stats.items():
        n = stats["n"]
        per_error[etype] = {
            "n": n,
            "detected_stage_a": stats["detected_stage_a"],
            "detection_recall": safe_div(stats["detected_stage_a"], n),
            "funnel_coverage": stats["funnel_coverage"],
            "funnel_coverage_rate": safe_div(stats["funnel_coverage"], n),
        }

    metrics = {
        "record_level": {
            "strict": {
                "TP": tp_strict, "FP": fp_strict, "FN": fn_strict, "TN": tn_strict,
                "precision": round(prec_strict, 4),
                "recall": round(rec_strict, 4),
                "f1": round(f1_strict, 4),
                "fpr": round(fpr_strict, 4),
                "recall_95ci": [round(ci_rec_strict[0], 4), round(ci_rec_strict[1], 4)],
                "fpr_95ci": [round(ci_fpr_strict[0], 4), round(ci_fpr_strict[1], 4)],
            },
            "soft": {
                "TP": tp_soft, "FP": fp_soft, "FN": fn_soft, "TN": tn_soft,
                "precision": round(prec_soft, 4),
                "recall": round(rec_soft, 4),
                "f1": round(f1_soft, 4),
                "fpr": round(fpr_soft, 4),
                "recall_95ci": [round(ci_rec_soft[0], 4), round(ci_rec_soft[1], 4)],
                "fpr_95ci": [round(ci_fpr_soft[0], 4), round(ci_fpr_soft[1], 4)],
            },
        },
        "per_error_type": per_error,
        "cost_proxy": {
            "total_entities": total_entities,
            "forwarded_entities": forwarded_entities,
            "forwarded_ratio": round(safe_div(forwarded_entities, total_entities), 4),
            "zero_forwarded_transcripts": zero_forwarded_count,
            "zero_forwarded_ratio": round(safe_div(zero_forwarded_count, len(stage_a_results)), 4),
        },
        "audit": {
            "total_misses": len(misses),
            "misses": misses,
            "wrong_dosage_breakdown": dosage_audit,
        },
    }
    return metrics


def write_metrics_md(metrics: dict, path: Path):
    """Write metrics as formatted markdown."""
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Stage A Evaluation Metrics\n\n")

        # Record-level
        f.write("## Record-Level Detection\n\n")
        f.write("| Mode | TP | FP | FN | TN | Precision | Recall | F1 | FPR |\n")
        f.write("|------|----|----|----|----|-----------|--------|-----|-----|\n")
        for mode in ("strict", "soft"):
            d = metrics["record_level"][mode]
            f.write(
                f"| {mode.title()} | {d['TP']} | {d['FP']} | {d['FN']} | {d['TN']} | "
                f"{d['precision']:.4f} | {d['recall']:.4f} | {d['f1']:.4f} | {d['fpr']:.4f} |\n"
            )
        f.write("\n")

        # Wilson CIs
        s = metrics["record_level"]["strict"]
        f.write("### 95% Wilson Confidence Intervals (Strict)\n\n")
        f.write(f"- **Recall**: [{s['recall_95ci'][0]:.4f}, {s['recall_95ci'][1]:.4f}]\n")
        f.write(f"- **FPR**: [{s['fpr_95ci'][0]:.4f}, {s['fpr_95ci'][1]:.4f}]\n\n")

        so = metrics["record_level"]["soft"]
        f.write("### 95% Wilson Confidence Intervals (Soft)\n\n")
        f.write(f"- **Recall**: [{so['recall_95ci'][0]:.4f}, {so['recall_95ci'][1]:.4f}]\n")
        f.write(f"- **FPR**: [{so['fpr_95ci'][0]:.4f}, {so['fpr_95ci'][1]:.4f}]\n\n")

        # Per error type
        f.write("## Per Error Type\n\n")
        f.write("| Error Type | N | Stage-A Detected | Detection Recall | Funnel Coverage | Funnel Rate |\n")
        f.write("|------------|---|------------------|-----------------|-----------------|-------------|\n")
        for etype, stats in metrics["per_error_type"].items():
            f.write(
                f"| {etype} | {stats['n']} | {stats['detected_stage_a']} | "
                f"{stats['detection_recall']:.4f} | {stats['funnel_coverage']} | "
                f"{stats['funnel_coverage_rate']:.4f} |\n"
            )
        f.write("\n")

        # Cost proxy
        cp = metrics["cost_proxy"]
        f.write("## Cost Proxy\n\n")
        f.write(f"- Entities forwarded to Stage B / Total: {cp['forwarded_entities']} / {cp['total_entities']} ({cp['forwarded_ratio']:.2%})\n")
        f.write(f"- Transcripts with zero forwarded entities: {cp['zero_forwarded_transcripts']} ({cp['zero_forwarded_ratio']:.2%})\n\n")

        # Audit table
        f.write("## Audit Table: Misses\n\n")
        audit = metrics["audit"]
        f.write(f"Total misses (injected entity not in funnel): {audit['total_misses']}\n\n")
        if audit["misses"]:
            f.write("| Transcript ID | Error Type | Injected Entity | Reason |\n")
            f.write("|---------------|------------|-----------------|--------|\n")
            for m in audit["misses"]:
                inj = m["injected_entity"][:50] if m["injected_entity"] else ""
                f.write(f"| {m['transcript_id']} | {m['error_type']} | {inj} | {m['reason']} |\n")
            f.write("\n")

        # Wrong dosage breakdown
        db = audit["wrong_dosage_breakdown"]
        f.write("### Wrong Dosage Audit\n\n")
        f.write(f"- Drug-associated misses: {len(db['drug_associated'])}\n")
        f.write(f"- Non-drug (measurement) misses: {len(db['non_drug'])}\n")


def main():
    parser = argparse.ArgumentParser(description="Evaluate Stage A pipeline output.")
    parser.add_argument("--stage-b-results", type=str, default=None)
    args = parser.parse_args()

    stage_a_path = _PROJECT_ROOT / "results" / "stage_a_output.json"
    gt_path = _PROJECT_ROOT / "dataset" / "processed" / "mtsamples_with_errors.json"
    metrics_json_path = _PROJECT_ROOT / "results" / "stage_a_metrics.json"
    metrics_md_path = _PROJECT_ROOT / "results" / "stage_a_metrics.md"

    if not stage_a_path.exists():
        print(f"Error: Stage A output not found at {stage_a_path}")
        print("Run: python scripts/run_stage_a.py --input dataset/processed/mtsamples_with_errors.json --output results/stage_a_output.json")
        sys.exit(1)

    print(f"Loading Stage A results from {stage_a_path}...")
    with open(stage_a_path, encoding="utf-8") as f:
        stage_a_results = json.load(f)

    print(f"Loading ground truth from {gt_path}...")
    with open(gt_path, encoding="utf-8") as f:
        ground_truth = json.load(f)

    print(f"Evaluating {len(stage_a_results)} results against {len(ground_truth)} ground truth records...")
    metrics = evaluate(stage_a_results, ground_truth)

    # Write JSON
    with open(metrics_json_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    print(f"Metrics JSON written to {metrics_json_path}")

    # Write Markdown
    write_metrics_md(metrics, metrics_md_path)
    print(f"Metrics report written to {metrics_md_path}")

    # Print summary
    s = metrics["record_level"]["strict"]
    print(f"\nRecord-level (strict): P={s['precision']:.4f} R={s['recall']:.4f} F1={s['f1']:.4f} FPR={s['fpr']:.4f}")
    so = metrics["record_level"]["soft"]
    print(f"Record-level (soft):   P={so['precision']:.4f} R={so['recall']:.4f} F1={so['f1']:.4f} FPR={so['fpr']:.4f}")
    print(f"\nPer error type:")
    for etype, stats in metrics["per_error_type"].items():
        print(f"  {etype}: n={stats['n']}, detected={stats['detected_stage_a']}, funnel={stats['funnel_coverage']}")

    # Stage B integration (optional)
    if args.stage_b_results:
        sb_path = Path(args.stage_b_results)
        if sb_path.exists():
            print(f"\nLoading Stage B results from {sb_path}...")
            # TODO: end-to-end metrics when Stage B output is available
        else:
            print(f"\nStage B results not found at {sb_path}, skipping.")


if __name__ == "__main__":
    main()
