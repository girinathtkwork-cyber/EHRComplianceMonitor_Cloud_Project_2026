"""Safe, aggregate-only Stage A audit evaluation.

This script never prints or writes transcripts, entity strings, patient-like
content, or full evaluator detail objects. Replay uses local terminology cache
only and turns cache misses into explicit lookup-unavailable outcomes.
"""
import argparse
import json
import sys
import time
import subprocess
import types
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _safe_metrics(outputs, labels):
    from backend.evaluation.evaluate_stage_a import evaluate
    raw = evaluate(outputs, labels)
    return {
        "record_level": raw["record_level"],
        "localized_detection": raw["headline_metrics"],
        "per_error_type": {
            key: {k: v for k, v in value.items() if isinstance(v, (int, float, str, bool))}
            for key, value in raw["per_error_type"].items()
        },
        "funnel_and_throughput": {
            k: v for k, v in raw["funnel_and_throughput"].items()
            if isinstance(v, (int, float, str, bool))
        },
        "wrong_dosage_coverage": {
            k: v for k, v in raw["wrong_dosage_breakdown"].items()
            if isinstance(v, (int, float, str, bool))
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, help="Existing Stage A output JSON")
    parser.add_argument("--replay", action="store_true", help="Run local pipeline without network")
    parser.add_argument("--baseline-extractor", action="store_true", help="Replay extractor from Git HEAD for an extraction-only baseline")
    parser.add_argument("--output", type=Path, required=True, help="Aggregate report output JSON")
    args = parser.parse_args()

    labels = json.loads((ROOT / "dataset/processed/mtsamples_with_errors.json").read_text(encoding="utf-8"))
    started = time.perf_counter()
    api_calls = 0
    if args.replay:
        from backend.stage_a import rxnorm_client
        from backend.stage_a import pipeline
        from backend.stage_a.pipeline import run_stage_a

        def offline_miss(url):
            nonlocal api_calls
            api_calls += 1
            raise rxnorm_client.TerminologyUnavailable("offline evaluation cache miss")

        rxnorm_client._api_get = offline_miss
        if args.baseline_extractor:
            source_path = "src/backend/stage_a/extract.py"
            source = subprocess.check_output(["git", "show", f"HEAD:{source_path}"], cwd=ROOT, text=True)
            baseline_module = types.ModuleType("stage_a_baseline_extract")
            baseline_module.__file__ = str(ROOT / source_path)
            exec(compile(source, source_path, "exec"), baseline_module.__dict__)
            pipeline.extract_entities = baseline_module.extract_entities
        records = labels
        outputs = [run_stage_a(record) for record in records]
        replay_source = ("Git HEAD extractor replay; same current verifier; cache only; external requests blocked"
                         if args.baseline_extractor else "current local replay; cache only; external requests blocked")
    else:
        if not args.input:
            parser.error("--input is required unless --replay is used")
        input_path = args.input if args.input.is_absolute() else ROOT / args.input
        outputs = json.loads(input_path.read_text(encoding="utf-8"))
        replay_source = str(input_path.relative_to(ROOT)) if input_path.is_relative_to(ROOT) else "external input file"

    elapsed = time.perf_counter() - started
    entity_types = Counter(entity.get("entity_type", "unknown") for row in outputs for entity in row.get("entities", []))
    routes = Counter(entity.get("routing", "unknown") for row in outputs for entity in row.get("entities", []))
    statuses = Counter(entity.get("retrieval_context", {}).get("verification_status", "not_reported") for row in outputs for entity in row.get("entities", []) if entity.get("entity_type") == "medication")
    report = {
        "records_processed": len(outputs),
        "record_failures": sum(row.get("verification_status") == "error" for row in outputs),
        "entity_types": dict(entity_types),
        "routes": dict(routes),
        "medication_verification_statuses": dict(statuses),
        "offline_lookup_misses": api_calls,
        "actual_external_api_requests": 0,
        "actual_terminology_api_errors": 0,
        "runtime_seconds": round(elapsed, 3),
        "source": replay_source,
        "metrics": _safe_metrics(outputs, labels),
        "metric_interpretation": "record-level injected-error funnel metrics, not entity-level extraction precision/recall",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("metrics",)}, indent=2))


if __name__ == "__main__":
    main()
