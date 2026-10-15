"""Fit and evaluate the Phase 2 model locally using prepared CSV splits."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sagemaker" / "phase2"))
from train import train


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261015)
    args = parser.parse_args()
    dataset_dir = args.dataset_dir if args.dataset_dir.is_absolute() else ROOT / args.dataset_dir
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    if output_dir.exists():
        raise SystemExit(f"Refusing to overwrite existing output directory: {output_dir}")
    audit = json.loads((dataset_dir / "dataset_audit.json").read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True)
    metadata = train(
        dataset_dir / "train" / "train.csv", dataset_dir / "validation" / "validation.csv",
        dataset_dir / "test" / "test.csv", output_dir, seed=args.seed,
        dataset_fingerprint=audit["dataset_fingerprint_sha256"],
        schema_path=dataset_dir / "feature_schema_v1.json",
    )
    metadata["baselines"] = {
        "majority": audit["majority_baseline_test"],
        "existing_deterministic_stage_b": audit["existing_deterministic_stage_b_test"],
    }
    (output_dir / "model_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps({"validation": metadata["validation_metrics_at_threshold"],
                      "test": metadata["test_metrics_at_selected_threshold"],
                      "baselines": metadata["baselines"],
                      "model_artifact": str(output_dir / "model.joblib")}, indent=2))


if __name__ == "__main__":
    main()
