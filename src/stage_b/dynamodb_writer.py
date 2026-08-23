import json
import os
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict

try:
    import boto3
    from botocore.exceptions import ClientError
    BOTO3_AVAILABLE = True
except ImportError:
    boto3 = None
    ClientError = Exception
    BOTO3_AVAILABLE = False

from severity_scorer import StageAOutput, SeverityResult, Entity, FlaggedEntity, load_stage_a


@dataclass
class StageBOutput:
    transcript_id: str
    timestamp: str
    transcript_text: str
    entities: List[Dict[str, Any]]
    flagged_entities: List[Dict[str, Any]]
    severity: str
    severity_reason: str
    compliance_mismatches: List[str]
    llm_reasoning: Optional[str] = None
    processed_at: str = ""


def get_dynamodb_client():
    if not BOTO3_AVAILABLE:
        raise RuntimeError("boto3 not installed. Run: pip install boto3")
    return boto3.resource(
        "dynamodb",
        region_name=os.getenv("AWS_REGION", "us-east-1")
    )


def get_table(table_name: str = "flagged_transcripts"):
    dynamodb = get_dynamodb_client()
    return dynamodb.Table(table_name)


def stage_a_to_dict(stage_a: StageAOutput) -> Dict[str, Any]:
    return {
        "transcript_id": stage_a.transcript_id,
        "timestamp": stage_a.timestamp,
        "transcript_text": stage_a.transcript_text,
        "entities": [asdict(e) for e in stage_a.entities],
        "flagged_entities": [asdict(f) for f in stage_a.flagged_entities]
    }


def extract_compliance_mismatches(stage_a: StageAOutput) -> List[str]:
    from severity_scorer import check_compliance_mismatch
    return check_compliance_mismatch(stage_a.entities)


def build_stage_b_output(
    stage_a: StageAOutput,
    severity_result: SeverityResult,
    llm_reasoning: Optional[str] = None
) -> StageBOutput:
    return StageBOutput(
        transcript_id=stage_a.transcript_id,
        timestamp=stage_a.timestamp,
        transcript_text=stage_a.transcript_text,
        entities=[asdict(e) for e in stage_a.entities],
        flagged_entities=[asdict(f) for f in stage_a.flagged_entities],
        severity=severity_result.severity,
        severity_reason=severity_result.reason,
        compliance_mismatches=extract_compliance_mismatches(stage_a),
        llm_reasoning=llm_reasoning,
        processed_at=datetime.utcnow().isoformat() + "Z"
    )


def write_to_dynamodb(stage_b: StageBOutput, table_name: str = "flagged_transcripts") -> bool:
    table = get_table(table_name)
    
    item = asdict(stage_b)
    item["ttl"] = int(datetime.utcnow().timestamp()) + (30 * 24 * 60 * 60)
    
    try:
        table.put_item(Item=item)
        print(f"✓ Written to DynamoDB: {stage_b.transcript_id} (severity: {stage_b.severity})")
        return True
    except ClientError as e:
        print(f"✗ DynamoDB write failed: {e.response['Error']['Message']}")
        return False
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        return False


def process_and_write(filepath: str, table_name: str = "flagged_transcripts", llm_reasoning: Optional[str] = None) -> bool:
    stage_a = load_stage_a(filepath)
    from severity_scorer import score_severity
    severity_result = score_severity(stage_a)
    stage_b = build_stage_b_output(stage_a, severity_result, llm_reasoning)
    return write_to_dynamodb(stage_b, table_name)


def process_all_samples(samples_dir: str, table_name: str = "flagged_transcripts") -> Dict[str, int]:
    stats = {"success": 0, "failed": 0, "skipped": 0}
    
    for filename in sorted(os.listdir(samples_dir)):
        if filename.endswith(".json"):
            filepath = os.path.join(samples_dir, filename)
            print(f"Processing {filename}...")
            if process_and_write(filepath, table_name):
                stats["success"] += 1
            else:
                stats["failed"] += 1
    
    return stats


def query_by_transcript_id(transcript_id: str, table_name: str = "flagged_transcripts") -> Optional[Dict]:
    table = get_table(table_name)
    try:
        response = table.get_item(Key={"transcript_id": transcript_id})
        return response.get("Item")
    except ClientError as e:
        print(f"Query failed: {e.response['Error']['Message']}")
        return None


def query_by_severity(severity: str, table_name: str = "flagged_transcripts", limit: int = 50) -> List[Dict]:
    table = get_table(table_name)
    try:
        response = table.scan(
            FilterExpression="#sev = :sev",
            ExpressionAttributeNames={"#sev": "severity"},
            ExpressionAttributeValues={":sev": severity},
            Limit=limit
        )
        return response.get("Items", [])
    except ClientError as e:
        print(f"Query failed: {e.response['Error']['Message']}")
        return []


def query_recent(hours: int = 24, table_name: str = "flagged_transcripts", limit: int = 100) -> List[Dict]:
    from datetime import datetime, timedelta
    table = get_table(table_name)
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat() + "Z"
    
    try:
        response = table.scan(
            FilterExpression="processed_at >= :cutoff",
            ExpressionAttributeValues={":cutoff": cutoff},
            Limit=limit
        )
        return response.get("Items", [])
    except ClientError as e:
        print(f"Query failed: {e.response['Error']['Message']}")
        return []


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python dynamodb_writer.py test <samples_dir>     # Process all samples")
        print("  python dynamodb_writer.py write <filepath>       # Write single file")
        print("  python dynamodb_writer.py query <transcript_id>  # Query by ID")
        print("  python dynamodb_writer.py severity <level>       # Query by severity")
        print("  python dynamodb_writer.py recent [hours]         # Query recent")
        sys.exit(1)
    
    command = sys.argv[1]
    table_name = os.getenv("DYNAMODB_TABLE", "flagged_transcripts")
    
    if command == "test":
        samples_dir = sys.argv[2] if len(sys.argv) > 2 else "/home/yoge/VIT/AWS/EHRComplianceMonitor_Cloud_Project_2026/dataset/fake_samples"
        print(f"Processing all samples from {samples_dir}...")
        stats = process_all_samples(samples_dir, table_name)
        print(f"\nResults: {stats['success']} success, {stats['failed']} failed, {stats['skipped']} skipped")
    
    elif command == "write":
        if len(sys.argv) < 3:
            print("Need filepath")
            sys.exit(1)
        process_and_write(sys.argv[2], table_name)
    
    elif command == "query":
        if len(sys.argv) < 3:
            print("Need transcript_id")
            sys.exit(1)
        item = query_by_transcript_id(sys.argv[2], table_name)
        if item:
            print(json.dumps(item, indent=2, default=str))
        else:
            print("Not found")
    
    elif command == "severity":
        if len(sys.argv) < 3:
            print("Need severity level (Critical/Medium/Low)")
            sys.exit(1)
        items = query_by_severity(sys.argv[2], table_name)
        print(f"Found {len(items)} items with severity {sys.argv[2]}")
        for item in items[:5]:
            print(f"  {item['transcript_id']}: {item['severity_reason'][:100]}...")
    
    elif command == "recent":
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
        items = query_recent(hours, table_name)
        print(f"Found {len(items)} items in last {hours} hours")
        for item in items[:5]:
            print(f"  {item['transcript_id']}: {item['severity']} ({item['processed_at']})")