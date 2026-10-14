"""End-to-end metrics for Stage B ablation outputs."""

from __future__ import annotations

import math
from collections import Counter, defaultdict

SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def wilson(successes: int, n: int, z: float = 1.96) -> dict:
    if n == 0:
        return {"value": 0.0, "low": 0.0, "high": 0.0}
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return {"value": p, "low": max(0.0, center - margin), "high": min(1.0, center + margin)}


def _has_positive(record: dict, threshold: str = "low") -> bool:
    needed = SEVERITY_ORDER[threshold]
    for finding in record.get("stage_b_findings", []):
        if SEVERITY_ORDER.get(str(finding.get("severity", "")).lower(), 0) >= needed:
            return True
    return False


def _localized(record: dict) -> bool:
    label = record.get("ground_truth_label", {})
    target = str(label.get("injected_entity") or "").lower()
    if not target:
        return _has_positive(record)
    for finding in record.get("stage_b_findings", []):
        phrase = str(finding.get("phrase") or "").lower()
        evidence = str(finding.get("evidence") or "").lower()
        if target in phrase or phrase in target or target in evidence:
            return True
    return False


def _prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def exact_mcnemar(only_a: int, only_b: int) -> float:
    n = only_a + only_b
    if n == 0:
        return 1.0
    k = min(only_a, only_b)
    prob = sum(math.comb(n, i) * (0.5 ** n) for i in range(k + 1))
    return min(1.0, 2 * prob)


def evaluate(records_by_mode: dict[str, list[dict]], split_ids: set[str] | None = None) -> dict:
    metrics = {}
    correctness_by_mode = {}
    for mode, records in records_by_mode.items():
        if split_ids is not None:
            records = [r for r in records if str(r.get("transcript_id")) in split_ids]
        tp = fp = fn = tn = 0
        clean_findings = []
        by_type = defaultdict(lambda: {"hit": 0, "n": 0})
        correctness = {}
        for rec in records:
            label = rec.get("ground_truth_label", {})
            truth = bool(label.get("is_error"))
            pred = _has_positive(rec, "low")
            loc = _localized(rec) if pred else False
            correct = truth and loc
            correctness[str(rec.get("transcript_id"))] = correct
            if truth:
                etype = label.get("error_type", "unknown")
                by_type[etype]["n"] += 1
                by_type[etype]["hit"] += int(correct)
                if correct:
                    tp += 1
                else:
                    fn += 1
            elif pred:
                fp += 1
                clean_findings.append(len(rec.get("stage_b_findings", [])))
            else:
                tn += 1
                clean_findings.append(0)

        prf = _prf(tp, fp, fn)
        metrics[mode] = {
            "n": len(records),
            "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
            **prf,
            "precision_ci": wilson(tp, tp + fp),
            "recall_ci": wilson(tp, tp + fn),
            "false_positive_rate_clean": fp / (fp + tn) if fp + tn else 0.0,
            "false_positive_rate_ci": wilson(fp, fp + tn),
            "findings_per_clean_record": sum(clean_findings) / len(clean_findings) if clean_findings else 0.0,
            "per_error_type": {
                k: {"n": v["n"], "localized_recall": v["hit"] / v["n"] if v["n"] else 0.0, "ci": wilson(v["hit"], v["n"])}
                for k, v in sorted(by_type.items())
            },
            "llm_calls": sum(1 for r in records if r.get("stage_b", {}).get("llm_status") == "ok" and not r.get("stage_b", {}).get("cache_hit")),
            "fallback_rate": sum(1 for r in records if r.get("stage_b", {}).get("llm_status") == "fallback") / len(records) if records else 0.0,
            "rag_uncited_suspicious_downgrades": sum(r.get("stage_b", {}).get("rag_uncited_suspicious_downgrades", 0) for r in records),
        }
        correctness_by_mode[mode] = correctness

    if "llm_plain" in correctness_by_mode and "llm_rag" in correctness_by_mode:
        ids = set(correctness_by_mode["llm_plain"]) & set(correctness_by_mode["llm_rag"])
        only_rag = sum(correctness_by_mode["llm_rag"][i] and not correctness_by_mode["llm_plain"][i] for i in ids)
        only_plain = sum(correctness_by_mode["llm_plain"][i] and not correctness_by_mode["llm_rag"][i] for i in ids)
        both = sum(correctness_by_mode["llm_plain"][i] and correctness_by_mode["llm_rag"][i] for i in ids)
        neither = sum(not correctness_by_mode["llm_plain"][i] and not correctness_by_mode["llm_rag"][i] for i in ids)
        metrics["paired_llm_rag_vs_plain"] = {
            "only_rag": only_rag,
            "only_plain": only_plain,
            "both": both,
            "neither": neither,
            "mcnemar_p": exact_mcnemar(only_rag, only_plain),
        }
    return metrics
