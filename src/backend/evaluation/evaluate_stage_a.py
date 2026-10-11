"""
Stage A Evaluation — Metrics and Analysis
--------------------------------------------
Computes localized detection recall, clean-record false positive rate,
per-error-type funnel coverage, wrong_dosage breakdown, transcript-level
routing statistics, and cost proxies from Stage A pipeline output.

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
    """Check if an extracted entity overlaps the ground-truth injected entity."""
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
    gt_by_id = {r["transcript_id"]: r for r in ground_truth}

    # 1. Record-level detection counters
    tp_strict = fp_strict = fn_strict = tn_strict = 0
    tp_soft = fp_soft = fn_soft = tn_soft = 0

    # 2. Localized detection counters (injected entity itself flagged)
    localized_strict_tp = 0
    localized_soft_tp = 0

    # 3. Per error type tracking
    error_stats = defaultdict(lambda: {
        "n": 0,
        "detected_strict": 0,
        "detected_soft": 0,
        "funnel_coverage": 0,
    })

    # 4. Detailed wrong_dosage audit (drug vs measurement)
    wrong_dosage_audit = {
        "total": 0,
        "drug_associated": [],
        "non_drug_measurement": [],
        "misses": [],
    }

    # 5. Misses tracking
    misses = []

    # 6. Cost & transcript-level forwarding proxies
    total_entities = 0
    forwarded_entities = 0
    invalid_entities = 0
    unresolved_entities = 0

    clean_records_count = 0
    injected_records_count = 0

    transcripts_forwarded_to_stage_b = 0
    transcripts_zero_forwarded = 0

    for result in stage_a_results:
        tid = result.get("transcript_id")
        gt_rec = gt_by_id.get(tid)
        if gt_rec is None:
            continue

        entities = result.get("entities", [])
        total_entities += len(entities)

        n_invalid = sum(1 for e in entities if e.get("routing") == "invalid")
        n_unresolved = sum(1 for e in entities if e.get("routing") == "unresolved")
        n_forwarded = sum(1 for e in entities if e.get("routing") in ("verified_needs_context_check", "unresolved"))

        invalid_entities += n_invalid
        unresolved_entities += n_unresolved
        forwarded_entities += n_forwarded

        if n_forwarded > 0:
            transcripts_forwarded_to_stage_b += 1
        else:
            transcripts_zero_forwarded += 1

        # Check soft flags (dose_not_a_known_strength or unresolved)
        has_soft_flag = any(
            e.get("routing") == "unresolved" or
            any(f.get("flag_type") in ("dose_not_a_known_strength", "no_confident_icd10_match")
                for f in e.get("retrieval_context", {}).get("flags", []))
            for e in entities
        )

        has_invalid = n_invalid > 0
        has_soft_signal = has_invalid or has_soft_flag

        # Ground truth
        gt_label = gt_rec.get("ground_truth_label", {})
        is_error = gt_label.get("is_error", False)
        error_type = gt_label.get("error_type")
        injected_entity = gt_label.get("injected_entity", "")

        if is_error:
            injected_records_count += 1
            if has_invalid:
                tp_strict += 1
            else:
                fn_strict += 1

            if has_soft_signal:
                tp_soft += 1
            else:
                fn_soft += 1

            if error_type:
                error_stats[error_type]["n"] += 1

                # Localized check on the specific injected entity
                inj_strict_detected = False
                inj_soft_detected = False
                in_funnel = False
                miss_reason = "not_extracted"

                for e in entities:
                    if entity_overlaps_injected(e, injected_entity):
                        in_funnel = True
                        routing = e.get("routing", "")
                        flags = e.get("retrieval_context", {}).get("flags", [])
                        has_flag = any(
                            f.get("flag_type") in ("dose_not_a_known_strength", "no_confident_icd10_match")
                            for f in flags
                        )

                        if routing == "invalid":
                            inj_strict_detected = True
                            inj_soft_detected = True
                            miss_reason = None
                            break
                        elif routing == "unresolved" or has_flag:
                            inj_soft_detected = True
                            miss_reason = "extracted_flagged_forwarded"
                        else:
                            miss_reason = "extracted_but_forwarded"

                if inj_strict_detected:
                    localized_strict_tp += 1
                    error_stats[error_type]["detected_strict"] += 1
                if inj_soft_detected:
                    localized_soft_tp += 1
                    error_stats[error_type]["detected_soft"] += 1

                if in_funnel:
                    error_stats[error_type]["funnel_coverage"] += 1
                else:
                    miss_info = {
                        "transcript_id": tid,
                        "error_type": error_type,
                        "injected_entity": injected_entity,
                        "reason": miss_reason,
                    }
                    misses.append(miss_info)

                # Wrong dosage classification
                if error_type == "wrong_dosage":
                    wrong_dosage_audit["total"] += 1
                    # Check whether any matching entity is a drug-associated dosage vs non-drug measurement
                    matched_dosages = [
                        e for e in entities
                        if entity_overlaps_injected(e, injected_entity) and e.get("entity_type") == "dosage"
                    ]
                    matched_measurements = [
                        e for e in entities
                        if entity_overlaps_injected(e, injected_entity) and e.get("entity_type") == "measurement"
                    ]

                    info = {
                        "transcript_id": tid,
                        "injected_entity": injected_entity,
                        "correct_value": gt_label.get("correct_value"),
                        "in_funnel": in_funnel,
                    }

                    if matched_dosages:
                        info["associated_medication"] = matched_dosages[0].get("associated_medication")
                        info["flags"] = matched_dosages[0].get("retrieval_context", {}).get("flags", [])
                        wrong_dosage_audit["drug_associated"].append(info)
                    elif matched_measurements:
                        info["type"] = "measurement"
                        wrong_dosage_audit["non_drug_measurement"].append(info)
                    else:
                        wrong_dosage_audit["misses"].append(info)

        else:
            clean_records_count += 1
            if has_invalid:
                fp_strict += 1
            else:
                tn_strict += 1

            if has_soft_signal:
                fp_soft += 1
            else:
                tn_soft += 1

    # Derived metrics
    def safe_div(a, b):
        return a / b if b > 0 else 0.0

    # Record-level metrics
    p_strict = safe_div(tp_strict, tp_strict + fp_strict)
    r_strict = safe_div(tp_strict, tp_strict + fn_strict)
    f1_strict = safe_div(2 * p_strict * r_strict, p_strict + r_strict)
    fpr_strict = safe_div(fp_strict, fp_strict + tn_strict)

    p_soft = safe_div(tp_soft, tp_soft + fp_soft)
    r_soft = safe_div(tp_soft, tp_soft + fn_soft)
    f1_soft = safe_div(2 * p_soft * r_soft, p_soft + r_soft)
    fpr_soft = safe_div(fp_soft, fp_soft + tn_soft)

    ci_rec_strict = wilson_ci(tp_strict, injected_records_count)
    ci_fpr_strict = wilson_ci(fp_strict, clean_records_count)
    ci_rec_soft = wilson_ci(tp_soft, injected_records_count)
    ci_fpr_soft = wilson_ci(fp_soft, clean_records_count)

    # Localized detection metrics (headline figure)
    loc_r_strict = safe_div(localized_strict_tp, injected_records_count)
    loc_r_soft = safe_div(localized_soft_tp, injected_records_count)
    ci_loc_strict = wilson_ci(localized_strict_tp, injected_records_count)
    ci_loc_soft = wilson_ci(localized_soft_tp, injected_records_count)

    # Per error type
    per_error = {}
    for etype, stats in error_stats.items():
        n = stats["n"]
        per_error[etype] = {
            "n": n,
            "detected_strict": stats["detected_strict"],
            "detected_strict_rate": safe_div(stats["detected_strict"], n),
            "detected_soft": stats["detected_soft"],
            "detected_soft_rate": safe_div(stats["detected_soft"], n),
            "funnel_coverage": stats["funnel_coverage"],
            "funnel_coverage_rate": safe_div(stats["funnel_coverage"], n),
        }

    total_records = len(stage_a_results)

    metrics = {
        "headline_metrics": {
            "localized_detection_recall_strict": round(loc_r_strict, 4),
            "localized_detection_recall_strict_ci": [round(ci_loc_strict[0], 4), round(ci_loc_strict[1], 4)],
            "localized_detection_recall_soft": round(loc_r_soft, 4),
            "localized_detection_recall_soft_ci": [round(ci_loc_soft[0], 4), round(ci_loc_soft[1], 4)],
            "clean_record_fpr_strict": round(fpr_strict, 4),
            "clean_record_fpr_strict_ci": [round(ci_fpr_strict[0], 4), round(ci_fpr_strict[1], 4)],
            "clean_record_fpr_soft": round(fpr_soft, 4),
            "clean_record_fpr_soft_ci": [round(ci_fpr_soft[0], 4), round(ci_fpr_soft[1], 4)],
        },
        "record_level": {
            "strict": {
                "TP": tp_strict, "FP": fp_strict, "FN": fn_strict, "TN": tn_strict,
                "precision": round(p_strict, 4), "recall": round(r_strict, 4), "f1": round(f1_strict, 4),
                "fpr": round(fpr_strict, 4),
                "recall_95ci": [round(ci_rec_strict[0], 4), round(ci_rec_strict[1], 4)],
                "fpr_95ci": [round(ci_fpr_strict[0], 4), round(ci_fpr_strict[1], 4)],
            },
            "soft": {
                "TP": tp_soft, "FP": fp_soft, "FN": fn_soft, "TN": tn_soft,
                "precision": round(p_soft, 4), "recall": round(r_soft, 4), "f1": round(f1_soft, 4),
                "fpr": round(fpr_soft, 4),
                "recall_95ci": [round(ci_rec_soft[0], 4), round(ci_rec_soft[1], 4)],
                "fpr_95ci": [round(ci_fpr_soft[0], 4), round(ci_fpr_soft[1], 4)],
            },
        },
        "per_error_type": per_error,
        "funnel_and_throughput": {
            "total_records": total_records,
            "clean_records": clean_records_count,
            "injected_records": injected_records_count,
            "transcripts_forwarded_to_stage_b": transcripts_forwarded_to_stage_b,
            "transcripts_forwarded_ratio": round(safe_div(transcripts_forwarded_to_stage_b, total_records), 4),
            "transcripts_zero_forwarded": transcripts_zero_forwarded,
            "transcripts_zero_forwarded_ratio": round(safe_div(transcripts_zero_forwarded, total_records), 4),
            "total_entities": total_entities,
            "forwarded_entities": forwarded_entities,
            "forwarded_entities_ratio": round(safe_div(forwarded_entities, total_entities), 4),
            "invalid_entities": invalid_entities,
            "unresolved_entities": unresolved_entities,
        },
        "wrong_dosage_breakdown": {
            "total_injected_wrong_dosage": wrong_dosage_audit["total"],
            "drug_associated_count": len(wrong_dosage_audit["drug_associated"]),
            "non_drug_measurement_count": len(wrong_dosage_audit["non_drug_measurement"]),
            "misses_count": len(wrong_dosage_audit["misses"]),
            "drug_associated": wrong_dosage_audit["drug_associated"],
            "non_drug_measurement": wrong_dosage_audit["non_drug_measurement"],
        },
        "audit_misses": {
            "total_misses": len(misses),
            "misses": misses,
        },
    }
    return metrics


def write_metrics_md(metrics: dict, path: Path):
    """Write metrics report as markdown."""
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Stage A Evaluation Metrics (Updated Routing)\n\n")

        # Headline
        h = metrics["headline_metrics"]
        f.write("## Headline Detection & Clean FPR\n\n")
        f.write("| Metric | Value | 95% Wilson CI |\n")
        f.write("|---|---|---|\n")
        f.write(f"| **Localized Detection Recall (Strict)** | {h['localized_detection_recall_strict']:.4f} | [{h['localized_detection_recall_strict_ci'][0]:.4f}, {h['localized_detection_recall_strict_ci'][1]:.4f}] |\n")
        f.write(f"| **Localized Detection Recall (Soft / Unresolved)** | {h['localized_detection_recall_soft']:.4f} | [{h['localized_detection_recall_soft_ci'][0]:.4f}, {h['localized_detection_recall_soft_ci'][1]:.4f}] |\n")
        f.write(f"| **Clean-Record False Positive Rate (Strict)** | {h['clean_record_fpr_strict']:.4f} | [{h['clean_record_fpr_strict_ci'][0]:.4f}, {h['clean_record_fpr_strict_ci'][1]:.4f}] |\n")
        f.write(f"| **Clean-Record False Positive Rate (Soft / Unresolved)** | {h['clean_record_fpr_soft']:.4f} | [{h['clean_record_fpr_soft_ci'][0]:.4f}, {h['clean_record_fpr_soft_ci'][1]:.4f}] |\n\n")

        # Record level
        f.write("## Record-Level Detection\n\n")
        f.write("| Mode | TP | FP | FN | TN | Precision | Recall | F1 | FPR |\n")
        f.write("|---|---|---|---|---|---|---|---|---|\n")
        for mode in ("strict", "soft"):
            d = metrics["record_level"][mode]
            f.write(
                f"| {mode.title()} | {d['TP']} | {d['FP']} | {d['FN']} | {d['TN']} | "
                f"{d['precision']:.4f} | {d['recall']:.4f} | {d['f1']:.4f} | {d['fpr']:.4f} |\n"
            )
        f.write("\n")

        # Per error type
        f.write("## Per Error Type & Funnel Delivery\n\n")
        f.write("| Error Type | Injected (N) | Localized Strict | Localized Soft | Funnel Coverage | Funnel Delivery Rate |\n")
        f.write("|---|---|---|---|---|---|\n")
        for etype, stats in metrics["per_error_type"].items():
            f.write(
                f"| `{etype}` | {stats['n']} | {stats['detected_strict']} ({stats['detected_strict_rate']:.1%}) | "
                f"{stats['detected_soft']} ({stats['detected_soft_rate']:.1%}) | "
                f"**{stats['funnel_coverage']}** | **{stats['funnel_coverage_rate']:.1%}** |\n"
            )
        f.write("\n")

        # Throughput & Transcripts Forwarded
        th = metrics["funnel_and_throughput"]
        f.write("## Forwarding Throughput & Cost Proxy\n\n")
        f.write(f"- **Transcripts Forwarded to Stage B:** {th['transcripts_forwarded_to_stage_b']} / {th['total_records']} ({th['transcripts_forwarded_ratio']:.2%})\n")
        f.write(f"- **Transcripts with Zero Forwarded Entities:** {th['transcripts_zero_forwarded']} ({th['transcripts_zero_forwarded_ratio']:.2%})\n")
        f.write(f"- **Entities Forwarded to Stage B:** {th['forwarded_entities']} / {th['total_entities']} ({th['forwarded_entities_ratio']:.2%})\n")
        f.write(f"- **Entities Classified Invalid (RxNorm Absence):** {th['invalid_entities']}\n")
        f.write(f"- **Entities Classified Unresolved (Low-Confidence ICD-10):** {th['unresolved_entities']}\n\n")

        # Wrong dosage audit
        wda = metrics["wrong_dosage_breakdown"]
        f.write("## Wrong Dosage Audit: Drug-Linked vs. Measurement Split\n\n")
        f.write(f"Total wrong dosage injections: {wda['total_injected_wrong_dosage']}\n")
        f.write(f"- **Drug-associated dosages:** {wda['drug_associated_count']} / 18 ({wda['drug_associated_count']/18:.1%})\n")
        f.write(f"- **Non-drug surgical measurements:** {wda['non_drug_measurement_count']} / 18 ({wda['non_drug_measurement_count']/18:.1%})\n")
        f.write(f"- **Missed during extraction:** {wda['misses_count']} / 18 (0.0%)\n\n")

        f.write("### Detail of Drug-Associated Injections:\n")
        for item in wda["drug_associated"]:
            flags_str = ", ".join(f.get("flag_type", "") for f in item.get("flags", [])) or "none"
            f.write(f"- `{item['transcript_id']}`: injected `{item['injected_entity']}` (was `{item['correct_value']}`) → linked to drug `{item['associated_medication']}` (flags: {flags_str})\n")
        f.write("\n### Detail of Non-Drug Measurements:\n")
        for item in wda["non_drug_measurement"]:
            f.write(f"- `{item['transcript_id']}`: injected `{item['injected_entity']}` (was `{item['correct_value']}`) → categorized as `measurement`\n")
        f.write("\n")

        # Misses
        m = metrics["audit_misses"]
        f.write("## Audit of Missed Injections (Not in Funnel)\n\n")
        f.write(f"Total missed injections: {m['total_misses']} / 90 (Funnel coverage: {90 - m['total_misses']} / 90 = {(90 - m['total_misses'])/90:.1%})\n\n")
        if m["misses"]:
            f.write("| Transcript ID | Error Type | Injected Entity | Extraction Reason |\n")
            f.write("|---|---|---|---|\n")
            for item in m["misses"]:
                f.write(f"| `{item['transcript_id']}` | `{item['error_type']}` | `{item['injected_entity']}` | `{item['reason']}` |\n")


def main():
    parser = argparse.ArgumentParser(description="Evaluate Stage A pipeline output.")
    parser.add_argument("--stage-b-results", type=str, default=None)
    args = parser.parse_args()

    stage_a_path = _PROJECT_ROOT / "results" / "stage_a_output.json"
    gt_path = _PROJECT_ROOT / "dataset" / "processed" / "mtsamples_with_errors.json"
    metrics_json_path = _PROJECT_ROOT / "results" / "stage_a_metrics.json"
    metrics_md_path = _PROJECT_ROOT / "results" / "stage_a_metrics.md"

    if not stage_a_path.exists():
        print(f"Error: {stage_a_path} does not exist.")
        sys.exit(1)

    with open(stage_a_path, encoding="utf-8") as f:
        stage_a_results = json.load(f)

    with open(gt_path, encoding="utf-8") as f:
        ground_truth = json.load(f)

    metrics = evaluate(stage_a_results, ground_truth)

    with open(metrics_json_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    write_metrics_md(metrics, metrics_md_path)

    h = metrics["headline_metrics"]
    th = metrics["funnel_and_throughput"]
    print("=" * 60)
    print("STAGE A EVALUATION COMPLETE")
    print("=" * 60)
    print(f"Localized Recall (Strict):   {h['localized_detection_recall_strict']:.4f} {h['localized_detection_recall_strict_ci']}")
    print(f"Localized Recall (Soft):     {h['localized_detection_recall_soft']:.4f} {h['localized_detection_recall_soft_ci']}")
    print(f"Clean-record FPR (Strict):   {h['clean_record_fpr_strict']:.4f} {h['clean_record_fpr_strict_ci']}")
    print(f"Clean-record FPR (Soft):     {h['clean_record_fpr_soft']:.4f} {h['clean_record_fpr_soft_ci']}")
    print(f"Transcripts forwarded to B:  {th['transcripts_forwarded_to_stage_b']}/{th['total_records']} ({th['transcripts_forwarded_ratio']:.2%})")
    print(f"Entities forwarded to B:     {th['forwarded_entities']}/{th['total_entities']} ({th['forwarded_entities_ratio']:.2%})")
    print(f"Invalid entities:            {th['invalid_entities']}")
    print(f"Unresolved entities:         {th['unresolved_entities']}")
    print("Per error type funnel coverage:")
    for etype, s in metrics["per_error_type"].items():
        print(f"  {etype:22s}: {s['funnel_coverage']:2d}/{s['n']:2d} ({s['funnel_coverage_rate']:.1%})")


if __name__ == "__main__":
    main()
