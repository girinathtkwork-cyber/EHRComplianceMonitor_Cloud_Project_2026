"""Build a privacy-minimized, grouped Stage A feature dataset for Phase 2."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.model_selection import StratifiedGroupKFold

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from backend.ml.features import FEATURE_COLUMNS, FEATURE_SCHEMA, extract_record_features
from backend.ml.metrics import binary_metrics


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _source_fingerprint(records: list[dict]) -> str:
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda item: str(item.get("transcript_id", ""))):
        digest.update(_normalized(record.get("transcription", "")).encode("utf-8"))
        digest.update(b"\x00")
        label = record.get("ground_truth_label") or {}
        digest.update(str(bool(label.get("is_error"))).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _group_indices(texts: list[str], threshold: float = 0.98) -> tuple[list[int], int, int]:
    """Connected components over normalized exact and char-TF-IDF near duplicates."""
    parent = list(range(len(texts)))

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    exact: dict[str, int] = {}
    normalized = [_normalized(text) for text in texts]
    for i, value in enumerate(normalized):
        if value in exact:
            union(i, exact[value])
        else:
            exact[value] = i
    near_pairs = 0
    if len(texts) > 1:
        matrix = TfidfVectorizer(analyzer="char", ngram_range=(4, 5), max_features=50000).fit_transform(texts)
        similarities = cosine_similarity(matrix)
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                if normalized[i] != normalized[j] and similarities[i, j] >= threshold:
                    near_pairs += 1
                    union(i, j)
    roots = [find(i) for i in range(len(texts))]
    group_map = {root: index for index, root in enumerate(sorted(set(roots)))}
    return [group_map[root] for root in roots], len(texts) - len(exact), near_pairs


def _grouped_split(labels: list[int], groups: list[int], seed: int) -> dict[str, list[int]]:
    all_indices = list(range(len(labels)))
    y = list(labels)
    outer = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=seed)
    train_val_idx, test_idx = next(outer.split(all_indices, y, groups))
    train_val_groups = [groups[i] for i in train_val_idx]
    train_val_y = [y[i] for i in train_val_idx]
    inner = StratifiedGroupKFold(n_splits=4, shuffle=True, random_state=seed + 1)
    train_local, val_local = next(inner.split(train_val_idx, train_val_y, train_val_groups))
    train_idx = [train_val_idx[i] for i in train_local]
    val_idx = [train_val_idx[i] for i in val_local]
    split = {"train": train_idx, "validation": val_idx, "test": list(test_idx)}
    for name, indices in split.items():
        if len({y[i] for i in indices}) < 2:
            raise ValueError(f"{name} split does not contain both classes")
    for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
        overlap = {groups[i] for i in split[left]} & {groups[i] for i in split[right]}
        if overlap:
            raise AssertionError(f"Grouped leakage detected between {left} and {right}")
    return split


def _write_split_csv(path: Path, rows: list[dict], labels: list[int], indices: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["target", *FEATURE_COLUMNS])
        writer.writeheader()
        for index in indices:
            writer.writerow({"target": labels[index], **rows[index]})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ROOT / "dataset/processed/mtsamples_with_errors.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261015)
    args = parser.parse_args()
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    if output_dir.exists():
        raise SystemExit(f"Refusing to overwrite existing output directory: {output_dir}")

    records = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise ValueError("Training input must be a non-empty JSON array")
    seen_ids = set()
    labels: list[int] = []
    label_types: list[str] = []
    for row_no, record in enumerate(records, start=1):
        if not isinstance(record, dict) or not isinstance(record.get("transcription"), str):
            raise ValueError(f"Record {row_no} is missing a transcription")
        transcript_id = record.get("transcript_id")
        if transcript_id is not None and transcript_id in seen_ids:
            raise ValueError("Duplicate transcript_id values in selected source dataset")
        if transcript_id is not None:
            seen_ids.add(transcript_id)
        label = record.get("ground_truth_label")
        if not isinstance(label, dict) or not isinstance(label.get("is_error"), bool):
            raise ValueError(f"Record {row_no} is missing boolean ground_truth_label.is_error")
        if label["is_error"] and (not label.get("error_type") or not label.get("injected_entity")):
            raise ValueError(f"Record {row_no} has an incomplete injected-error label")
        if not label["is_error"] and label.get("error_type") not in (None, ""):
            raise ValueError(f"Record {row_no} has contradictory clean/error labels")
        labels.append(int(label["is_error"]))
        label_types.append(str(label.get("error_type") or "clean"))

    # Network access is blocked. Existing local cache hits are used; misses are
    # represented by Stage A as unavailable and counted, never sent externally.
    from backend.stage_a import rxnorm_client
    from backend.stage_a.pipeline import run_stage_a

    blocked_lookups = 0

    def block_lookup(url, retries=None):
        nonlocal blocked_lookups
        blocked_lookups += 1
        raise rxnorm_client.TerminologyUnavailable("offline Phase 2 feature generation")

    rxnorm_client._api_get = block_lookup
    feature_rows = []
    stage_a_errors = 0
    deterministic_predictions = []
    for record in records:
        output = run_stage_a(record)
        if output.get("verification_status") == "error":
            stage_a_errors += 1
            continue
        feature_rows.append(extract_record_features(output))
        from backend.stage_b.audit import audit_record
        deterministic = audit_record(output)
        deterministic_predictions.append(int(bool(deterministic.get("stage_b_findings"))))
    if stage_a_errors:
        raise ValueError(f"Stage A failed on {stage_a_errors} records; refusing to silently drop labeled rows")
    if len(feature_rows) != len(records):
        raise AssertionError("Feature generation did not preserve one row per record")

    source_texts = [record["transcription"] for record in records]
    groups, exact_duplicate_excess, near_duplicate_pairs = _group_indices(source_texts)
    splits = _grouped_split(labels, groups, args.seed)
    output_dir.mkdir(parents=True, exist_ok=False)
    for name, indices in splits.items():
        _write_split_csv(output_dir / name / f"{name}.csv", feature_rows, labels, indices)

    test_indices = splits["test"]
    test_y = [labels[i] for i in test_indices]
    majority = int(sum(labels[i] for i in splits["train"]) > len(splits["train"]) / 2)
    major_metrics = binary_metrics(test_y, [majority] * len(test_y))
    deterministic_metrics = binary_metrics(test_y, [deterministic_predictions[i] for i in test_indices])
    schema_path = ROOT / "src/backend/ml/feature_schema_v1.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    source_hashes = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in (
            "src/backend/stage_a/extract.py", "src/backend/stage_a/verify.py",
            "src/backend/stage_a/pipeline.py", "src/backend/stage_a/rxnorm_client.py",
        )
    }
    fingerprint = _source_fingerprint(records)
    split_summary = {}
    for name, indices in splits.items():
        split_summary[name] = {
            "records": len(indices),
            "class_counts": dict(Counter("injected_error" if labels[i] else "clean" for i in indices)),
            "error_type_counts": dict(Counter(label_types[i] for i in indices)),
            "groups": len({groups[i] for i in indices}),
        }

    schema_path_out = output_dir / "feature_schema_v1.json"
    schema_path_out.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    config_dir = output_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "feature_schema_v1.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")
    (config_dir / "training_config.json").write_text(json.dumps({
        "seed": args.seed,
        "dataset_fingerprint_sha256": fingerprint,
    }, indent=2), encoding="utf-8")
    (output_dir / "split_metadata.json").write_text(json.dumps({
        "seed": args.seed,
        "strategy": "StratifiedGroupKFold outer 5-fold test, then inner 4-fold validation; connected components of exact normalized transcript matches and char-TFIDF cosine >= 0.98",
        "proportions_requested": {"train": 0.60, "validation": 0.20, "test": 0.20},
        "actual": split_summary,
        "cross_split_group_overlap": 0,
    }, indent=2), encoding="utf-8")
    audit = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "prediction_task": schema["prediction_task"],
        "prediction_unit": schema["prediction_unit"],
        "target": schema["target_field"],
        "source_file": str(args.input),
        "source_records": len(records),
        "usable_records": len(feature_rows),
        "stage_a_errors": stage_a_errors,
        "class_counts": dict(Counter("injected_error" if y else "clean" for y in labels)),
        "error_type_counts": dict(Counter(label_types)),
        "label_validation": {
            "valid": True,
            "correct_value_null_by_error_type": dict(Counter(
                label_types[i] for i, row in enumerate(records)
                if (row.get("ground_truth_label") or {}).get("correct_value") is None
            )),
            "note": "correct_value is intentionally null for clean examples and fabricated diagnosis/procedure injections; not used as a model input.",
        },
        "duplicate_audit": {"exact_duplicate_excess": exact_duplicate_excess, "near_duplicate_pairs_at_cosine_0_98": near_duplicate_pairs, "groups_cross_split": 0},
        "split_summary": split_summary,
        "feature_columns": list(FEATURE_COLUMNS),
        "feature_count": len(FEATURE_COLUMNS),
        "stage_a_version": schema["stage_a_version"],
        "stage_a_source_sha256": source_hashes,
        "dataset_fingerprint_sha256": fingerprint,
        "offline_terminology_cache_misses_blocked": blocked_lookups,
        "external_api_requests": 0,
        "majority_baseline_test": major_metrics,
        "existing_deterministic_stage_b_test": deterministic_metrics,
        "identifier_and_raw_text_policy": "No IDs, transcript text, label metadata, or injection strings are serialized in feature splits or this report.",
    }
    (output_dir / "dataset_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps({k: audit[k] for k in ("source_records", "usable_records", "class_counts", "error_type_counts", "duplicate_audit", "split_summary", "feature_count", "offline_terminology_cache_misses_blocked", "external_api_requests", "majority_baseline_test", "existing_deterministic_stage_b_test")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
