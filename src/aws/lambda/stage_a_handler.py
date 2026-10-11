import json
import urllib.parse
try:
    import boto3
except ImportError:
    boto3 = None

import time
import os
import sys
from pathlib import Path

# Add backend directory to path if needed (though Lambda sets root)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.stage_a.pipeline import run_stage_a
from backend.stage_a import rxnorm_client

def _create_s3_client():
    """Returns an S3 client. Can be monkey-patched for local testing."""
    if boto3 is None:
        raise RuntimeError("boto3 is not available in this environment.")
    return boto3.client('s3')

def lambda_handler(event, context):
    """
    AWS Lambda handler for Stage A processing.
    """
    start_time = time.time()
    s3_client = _create_s3_client()
    
    for record in event.get('Records', []):
        try:
            bucket = record['s3']['bucket']['name']
            key = urllib.parse.unquote_plus(record['s3']['object']['key'])
            
            # Validation
            if not key.startswith('input/') or not key.endswith('.json'):
                print(f"Skipping key {key}: does not match 'input/*.json'")
                continue
                
            print(f"Processing object {key} from bucket {bucket}")
            
            # Get object
            response = s3_client.get_object(Bucket=bucket, Key=key)
            body = response['Body'].read().decode('utf-8')
            
            input_data = json.loads(body)
            if not isinstance(input_data, list):
                input_data = [input_data]
                
            # Process records with time checking
            processed_records = []
            truncated = False
            total_records = len(input_data)
            
            for i, item in enumerate(input_data):
                # Check remaining time
                if hasattr(context, 'get_remaining_time_in_millis'):
                    remaining_ms = context.get_remaining_time_in_millis()
                    if remaining_ms < 30000:
                        print(f"Warning: Low time remaining ({remaining_ms}ms). Truncating output.")
                        truncated = True
                        break
                        
                # Process
                processed = run_stage_a(item)
                processed_records.append(processed)
                
            # Save cache after processing
            rxnorm_client.save_cache()
            
            # Write output
            basename = os.path.basename(key).replace('.json', '')
            output_key = f"output/stage_a/{basename}.stage_a.json"
            
            output_data = processed_records
            if truncated:
                output_data = {
                    "records": processed_records,
                    "truncated": True,
                    "processed_count": len(processed_records),
                    "total_count": total_records
                }
                
            s3_client.put_object(
                Bucket=bucket,
                Key=output_key,
                Body=json.dumps(output_data, indent=2),
                ContentType='application/json'
            )
            
            # Log summary
            elapsed = time.time() - start_time
            total_entities = sum(
                len(r.get("entities", []))
                for r in processed_records
                if r.get("verification_status") == "completed"
            )
            cache = rxnorm_client.get_cache()
            print(json.dumps({
                "metric": "stage_a_execution",
                "bucket": bucket,
                "input_key": key,
                "output_key": output_key,
                "record_count": len(processed_records),
                "total_input_records": total_records,
                "total_entities": total_entities,
                "cache_hit_rate": round(cache.hit_rate, 4),
                "truncated": truncated,
                "elapsed_seconds": round(elapsed, 3)
            }))
            
        except Exception as e:
            print(f"Error processing record: {e}")
            raise e
            
    return {
        'statusCode': 200,
        'body': json.dumps('Processing complete')
    }
