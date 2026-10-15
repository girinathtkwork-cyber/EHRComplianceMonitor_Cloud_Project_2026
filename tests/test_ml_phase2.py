import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "sagemaker" / "phase2"))
sys.path.insert(0, str(ROOT / "scripts"))

from backend.ml.features import FEATURE_COLUMNS, extract_record_features
from backend.ml.metrics import binary_metrics, select_f1_threshold
from train import input_fn, model_fn, output_fn, predict_fn, train
from prepare_phase2_dataset import _grouped_split


def test_feature_columns_are_fixed_numeric_and_label_blind():
    record = {
        "entities": [{"entity_type": "medication", "text": "example", "routing": "invalid",
                      "retrieval_context": {"verification_status": "ambiguous"}, "span": [1, 3]}],
        "verification_status": "partial", "ground_truth_label": {"is_error": True},
        "transcription": "must not enter features", "transcript_id": "private",
    }
    output = extract_record_features(record)
    assert list(output) == list(FEATURE_COLUMNS)
    assert all(type(value) is int for value in output.values())
    assert output["medication_count"] == 1
    assert output["routing_invalid_count"] == 1
    assert "ground_truth_label" not in output and "transcript_id" not in output


def test_feature_extraction_rejects_invalid_entities():
    with pytest.raises(ValueError, match="objects"):
        extract_record_features({"entities": [None]})


def test_metrics_and_threshold_tie_rule():
    assert select_f1_threshold([0, 1], [0.4, 0.6]) == 0.6
    metrics = binary_metrics([0, 1], [0, 1], [0.1, 0.9])
    assert metrics["confusion_matrix"] == {"tn": 1, "fp": 0, "fn": 0, "tp": 1}
    assert metrics["roc_auc"] == 1.0
    assert "not evaluated" in metrics["score_calibration"]


def test_grouped_split_keeps_related_rows_together():
    labels = [index % 2 for index in range(80)]
    groups = [index // 2 for index in range(80)]
    splits = _grouped_split(labels, groups, 7)
    group_sets = {name: {groups[index] for index in indices} for name, indices in splits.items()}
    assert not (group_sets["train"] & group_sets["validation"])
    assert not (group_sets["train"] & group_sets["test"])
    assert not (group_sets["validation"] & group_sets["test"])
    assert all({labels[index] for index in indices} == {0, 1} for indices in splits.values())


def _write_csv(path, labels):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["target", *FEATURE_COLUMNS])
        writer.writeheader()
        for i, label in enumerate(labels):
            writer.writerow({"target": label, **{name: int(label * 3 + (i % 3)) for name in FEATURE_COLUMNS}})


def test_train_save_load_and_inference_contract(tmp_path):
    train_path, val_path, test_path = [tmp_path / name for name in ("train.csv", "val.csv", "test.csv")]
    _write_csv(train_path, [0, 1] * 10)
    _write_csv(val_path, [0, 1] * 4)
    _write_csv(test_path, [0, 1] * 4)
    schema = tmp_path / "feature_schema.json"
    schema.write_text(json.dumps({"schema_version": "test", "feature_columns": list(FEATURE_COLUMNS)}), encoding="utf-8")
    model_dir = tmp_path / "model"
    train(train_path, val_path, test_path, model_dir, seed=13,
          dataset_fingerprint="test-fingerprint", schema_path=schema)
    loaded = model_fn(model_dir)
    payload = {name: 0 for name in FEATURE_COLUMNS}
    prepared = input_fn(json.dumps(payload), "application/json")
    prediction = predict_fn(prepared, loaded)
    body, content_type = output_fn(prediction, "application/json")
    assert content_type == "application/json"
    assert set(json.loads(body)[0]) == {"prediction", "uncalibrated_score"}
    assert (model_dir / "model_metadata.json").exists()
    assert (model_dir / "evaluation.json").exists()
    assert (model_dir / "feature_schema_v1.json").read_text(encoding="utf-8") == schema.read_text(encoding="utf-8")


def test_training_rejects_schema_mismatch(tmp_path):
    train_path, val_path, test_path = [tmp_path / name for name in ("train.csv", "val.csv", "test.csv")]
    _write_csv(train_path, [0, 1] * 3)
    _write_csv(val_path, [0, 1] * 2)
    _write_csv(test_path, [0, 1] * 2)
    schema = tmp_path / "bad_schema.json"
    schema.write_text(json.dumps({"feature_columns": ["wrong"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        train(train_path, val_path, test_path, tmp_path / "model", seed=1,
              dataset_fingerprint="test", schema_path=schema)


def test_inference_rejects_unknown_features():
    with pytest.raises(ValueError, match="Unknown"):
        input_fn('{"patient_id":"no"}', "application/json")
