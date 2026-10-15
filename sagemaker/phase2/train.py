"""SageMaker script-mode training and local inference entry point."""

from __future__ import annotations

import csv
import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

FEATURE_COLUMNS = (
    "record_entity_count", "medication_count", "dosage_count", "measurement_count",
    "diagnosis_count", "procedure_count", "routing_invalid_count", "routing_unresolved_count",
    "routing_review_count", "medication_verified_count", "medication_ambiguous_count",
    "medication_unknown_count", "medication_lookup_unavailable_count",
    "medication_duplicate_mentions", "duplicate_entities", "unassociated_dosage_count",
    "dosage_missing_value_or_unit_count", "measurement_missing_value_or_unit_count",
    "entities_missing_valid_span_count", "medications_negated_count", "medications_historical_count",
    "medications_allergy_context_count", "medications_planned_count",
    "distinct_medication_sections_count", "stage_a_partial",
)


def _read_csv(path: Path):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        expected = ["target", *FEATURE_COLUMNS]
        if reader.fieldnames != expected:
            raise ValueError(f"Invalid CSV schema in {path.name}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"Empty dataset: {path}")
    y = np.asarray([int(row["target"]) for row in rows], dtype=int)
    if not set(y.tolist()) <= {0, 1} or len(set(y.tolist())) != 2:
        raise ValueError(f"Dataset {path.name} must contain both binary classes")
    x = np.asarray([[np.nan if row[column] == "" else float(row[column]) for column in FEATURE_COLUMNS]
                    for row in rows], dtype=float)
    return x, y


def _metrics(y_true, y_pred, scores=None):
    from sklearn.metrics import (average_precision_score, confusion_matrix,
        f1_score, precision_score, recall_score, roc_auc_score)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    result = {
        "n": int(len(y_true)), "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "false_positive_rate": float(fp / (fp + tn)) if fp + tn else 0.0,
        "false_negative_rate": float(fn / (fn + tp)) if fn + tp else 0.0,
    }
    if scores is not None and len(set(y_true.tolist())) == 2:
        result["roc_auc"] = float(roc_auc_score(y_true, scores))
        result["pr_auc_average_precision"] = float(average_precision_score(y_true, scores))
        result["score_calibration"] = "not evaluated; score is uncalibrated"
    return result


def _threshold(y, scores):
    from sklearn.metrics import f1_score
    choices = sorted({0.0, 1.0, *(float(score) for score in scores)})
    return max(choices, key=lambda threshold: (f1_score(y, scores >= threshold, zero_division=0), threshold))


def train(train_path: Path, validation_path: Path, test_path: Path, model_dir: Path,
          *, seed: int, dataset_fingerprint: str, schema_path: Path):
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if schema.get("feature_columns") != list(FEATURE_COLUMNS):
        raise ValueError("Feature schema does not match the training/inference column contract")
    x_train, y_train = _read_csv(train_path)
    x_val, y_val = _read_csv(validation_path)
    x_test, y_test = _read_csv(test_path)
    pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
        ("scaler", StandardScaler()),
        ("classifier", LogisticRegression(class_weight="balanced", max_iter=2000, random_state=seed)),
    ])
    pipeline.fit(x_train, y_train)
    val_score = pipeline.predict_proba(x_val)[:, 1]
    threshold = _threshold(y_val, val_score)
    test_score = pipeline.predict_proba(x_test)[:, 1]
    test_metrics = _metrics(y_test, (test_score >= threshold).astype(int), test_score)
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "model.joblib"
    joblib.dump({"pipeline": pipeline, "feature_columns": list(FEATURE_COLUMNS), "threshold": threshold}, model_path)
    metadata = {
        "model_type": "sklearn Pipeline: median imputation with indicators, standard scaling, balanced logistic regression",
        "trained_utc": datetime.now(timezone.utc).isoformat(), "feature_schema_version": "1.0.0",
        "dataset_fingerprint_sha256": dataset_fingerprint, "random_seed": seed,
        "split_configuration": "seeded StratifiedGroupKFold; exact/near-duplicate connected components kept within split; requested 60/20/20 train/validation/test",
        "split_files": {"train": train_path.name, "validation": validation_path.name, "test": test_path.name},
        "counts": {"train": int(len(y_train)), "validation": int(len(y_val)), "test": int(len(y_test))},
        "hyperparameters": {"class_weight": "balanced", "max_iter": 2000, "threshold_selection": "validation F1, tie prefers higher threshold"},
        "validation_threshold": float(threshold), "validation_metrics_at_threshold": _metrics(y_val, (val_score >= threshold).astype(int), val_score),
        "test_metrics_at_selected_threshold": test_metrics,
        "versions": {"python": platform.python_version(), "scikit_learn": sklearn.__version__, "numpy": np.__version__, "joblib": joblib.__version__},
        "raw_transcript_or_record_identifiers_included": False,
    }
    (model_dir / "model_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (model_dir / "evaluation.json").write_text(json.dumps({"selected_threshold": float(threshold), "validation": metadata["validation_metrics_at_threshold"], "test": test_metrics}, indent=2), encoding="utf-8")
    (model_dir / "feature_schema_v1.json").write_bytes(schema_path.read_bytes())
    return metadata


def model_fn(model_dir):
    return joblib.load(Path(model_dir) / "model.joblib")


def input_fn(request_body, request_content_type):
    if request_content_type != "application/json":
        raise ValueError("Content-Type must be application/json")
    payload = json.loads(request_body)
    items = payload if isinstance(payload, list) else [payload]
    matrix = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("JSON input must be a feature object or list of feature objects")
        extra = set(item) - set(FEATURE_COLUMNS)
        if extra:
            raise ValueError("Unknown feature keys")
        matrix.append([np.nan if item.get(key) is None else float(item[key]) for key in FEATURE_COLUMNS])
    return np.asarray(matrix, dtype=float)


def predict_fn(input_data, model):
    scores = model["pipeline"].predict_proba(input_data)[:, 1]
    return [{"prediction": int(score >= model["threshold"]), "uncalibrated_score": float(score)} for score in scores]


def output_fn(prediction, accept):
    if accept not in ("application/json", "*/*"):
        raise ValueError("Accept must be application/json")
    return json.dumps(prediction), "application/json"


def main():
    train_channel = Path(os.environ.get("SM_CHANNEL_TRAIN", "/opt/ml/input/data/train"))
    validation_channel = Path(os.environ.get("SM_CHANNEL_VALIDATION", "/opt/ml/input/data/validation"))
    test_channel = Path(os.environ.get("SM_CHANNEL_TEST", "/opt/ml/input/data/test"))
    output = Path(os.environ.get("SM_MODEL_DIR", "/opt/ml/model"))
    config_path = Path(os.environ.get("SM_CHANNEL_CONFIG", "/opt/ml/input/data/config"))
    config = json.loads((config_path / "training_config.json").read_text(encoding="utf-8"))
    train(train_channel / "train.csv", validation_channel / "validation.csv", test_channel / "test.csv", output,
          seed=int(config["seed"]), dataset_fingerprint=config["dataset_fingerprint_sha256"],
          schema_path=config_path / "feature_schema_v1.json")


if __name__ == "__main__":
    main()
