import json
import os
import sys
from typing import Dict, Any, Optional
from datetime import datetime

from severity_scorer import load_stage_a, score_severity, SeverityResult, check_compliance_mismatch
from llm_reasoning import run_llm_reasoning, combine_with_severity, configure_gemini

try:
    from dynamodb_writer import write_to_dynamodb, StageBOutput, build_stage_b_output
    DYNAMODB_AVAILABLE = True
except ImportError:
    write_to_dynamodb = None
    StageBOutput = None
    build_stage_b_output = None
    DYNAMODB_AVAILABLE = False


def run_stage_b(
    stage_a_input: Dict[str, Any],
    use_llm: bool = True,
    write_db: bool = False,
    table_name: str = "flagged_transcripts"
) -> Dict[str, Any]:
    stage_a_obj = load_stage_a_from_dict(stage_a_input)
    
    severity_result = score_severity(stage_a_obj)
    severity_dict = {"severity": severity_result.severity, "reason": severity_result.reason}
    
    llm_results = []
    if use_llm and configure_gemini():
        llm_results = run_llm_reasoning(stage_a_input)
    
    combined = combine_with_severity(stage_a_input, severity_dict, llm_results)
    combined["compliance_mismatches"] = check_compliance_mismatch(stage_a_obj.entities)
    
    if write_db:
        if not DYNAMODB_AVAILABLE:
            print("DynamoDB writer not available (boto3 not installed)")
        else:
            stage_b_output = build_stage_b_output(
                stage_a_obj,
                severity_result,
                llm_reasoning=llm_results[0].get("overall_assessment", "") if llm_results else None
            )
            write_to_dynamodb(stage_b_output, table_name)
    
    return combined


def load_stage_a_from_dict(data: Dict[str, Any]):
    from severity_scorer import StageAOutput, Entity, FlaggedEntity
    entities = [Entity(**e) for e in data.get("entities", [])]
    flagged = [FlaggedEntity(**f) for f in data.get("flagged_entities", [])]
    return StageAOutput(
        transcript_id=data["transcript_id"],
        timestamp=data["timestamp"],
        transcript_text=data["transcript_text"],
        entities=entities,
        flagged_entities=flagged
    )


def process_file(
    filepath: str,
    use_llm: bool = True,
    write_db: bool = False,
    table_name: str = "flagged_transcripts"
) -> Dict[str, Any]:
    with open(filepath, 'r') as f:
        stage_a = json.load(f)
    
    print(f"Processing: {stage_a['transcript_id']}")
    result = run_stage_b(stage_a, use_llm, write_db, table_name)
    
    print(f"  Severity: {result['overall_severity']}")
    print(f"  Flags analyzed: {len(result['flagged_entities'])}")
    if result.get("llm_overall_assessment"):
        print(f"  LLM: {result['llm_overall_assessment'][:100]}...")
    
    return result


def process_all(
    samples_dir: str,
    use_llm: bool = True,
    write_db: bool = False,
    table_name: str = "flagged_transcripts"
):
    for filename in sorted(os.listdir(samples_dir)):
        if filename.endswith(".json"):
            filepath = os.path.join(samples_dir, filename)
            process_file(filepath, use_llm, write_db, table_name)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Run Stage B pipeline")
    parser.add_argument("input", help="Path to Stage A JSON file or directory")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM reasoning")
    parser.add_argument("--write-db", action="store_true", help="Write to DynamoDB")
    parser.add_argument("--table", default="flagged_transcripts", help="DynamoDB table name")
    
    args = parser.parse_args()
    
    if os.path.isdir(args.input):
        process_all(args.input, use_llm=not args.no_llm, write_db=args.write_db, table_name=args.table)
    else:
        process_file(args.input, use_llm=not args.no_llm, write_db=args.write_db, table_name=args.table)