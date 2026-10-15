"""Small-sample-safe binary classification metrics for the Phase 2 experiment."""

from __future__ import annotations

from sklearn.metrics import average_precision_score, confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score


def binary_metrics(y_true, y_pred, scores=None) -> dict:
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    result = {
        "n": int(len(y_true)),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "false_positive_rate": float(fp / (fp + tn)) if fp + tn else 0.0,
        "false_negative_rate": float(fn / (fn + tp)) if fn + tp else 0.0,
    }
    if scores is not None and len(set(int(y) for y in y_true)) == 2:
        result["roc_auc"] = float(roc_auc_score(y_true, scores))
        result["pr_auc_average_precision"] = float(average_precision_score(y_true, scores))
        result["score_calibration"] = "not evaluated; scores are uncalibrated"
    else:
        result["roc_auc"] = None
        result["pr_auc_average_precision"] = None
    return result


def select_f1_threshold(y_true, scores) -> float:
    """Select a score threshold on validation only; ties prefer higher threshold."""
    candidates = sorted({0.0, 1.0, *(float(s) for s in scores)})
    best = None
    for threshold in candidates:
        prediction = [int(float(score) >= threshold) for score in scores]
        f1 = f1_score(y_true, prediction, zero_division=0)
        item = (float(f1), float(threshold))
        if best is None or item[0] > best[0] or (item[0] == best[0] and item[1] > best[1]):
            best = item
    return best[1] if best is not None else 0.5
