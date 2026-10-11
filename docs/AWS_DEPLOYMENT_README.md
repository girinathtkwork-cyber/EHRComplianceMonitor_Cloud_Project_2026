# AWS Deployment README: EHR Compliance Monitor

Last updated: 11 October 2026

This guide explains what is already complete, what you can test now, how to add your Gemini API key safely, and how to deploy the project to AWS using the AWS website.

Official docs used:

- AWS Lambda zip upload: https://docs.aws.amazon.com/lambda/latest/dg/python-package.html
- Lambda environment variables: https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars.html
- Lambda execution role: https://docs.aws.amazon.com/lambda/latest/dg/lambda-intro-execution-role.html
- Lambda CloudWatch logs: https://docs.aws.amazon.com/lambda/latest/dg/monitoring-cloudwatchlogs.html
- S3 event notification to Lambda: https://docs.aws.amazon.com/AmazonS3/latest/userguide/enable-event-notifications.html
- S3 with Lambda: https://docs.aws.amazon.com/lambda/latest/dg/with-s3.html

---

## 1. What is done till now

The local project now has:

- Stage A retrieval and verification using RxNorm, MED-RT, and ICD-10-CM evidence.
- Deterministic Stage B fallback.
- Real Gemini-ready Stage B with three modes:
  - `stage_a_only`: no LLM.
  - `llm_plain`: Gemini sees transcript plus extracted entities, no retrieved facts.
  - `llm_rag`: Gemini sees transcript plus extracted entities plus Stage A retrieved facts.
- Prompt saving under `results/prompts/`.
- Prompt leak protection so ground-truth labels are not sent to Gemini.
- LLM cache at `dataset/cache/llm_cache.jsonl`.
- Ablation script for honest comparison:
  - Stage A only
  - plain LLM
  - RAG-grounded LLM
- Lambda package builder.
- Local Lambda parity test.
- Documentation updates.

Verified commands:

```powershell
python -m pytest -q
```

Result:

```text
57 passed
```

```powershell
python scripts/run_end_to_end.py --mode llm_rag --input dataset/processed/sample_20_for_aws_test.json --dry-run
```

Result: passed for 20 records, saved prompts, made zero API calls.

```powershell
python scripts/run_ablation.py --dry-run
```

Result: passed for all 270 records and wrote:

- `results/ablation_metrics.json`
- `results/ablation_metrics.md`
- `results/ablation_predictions.jsonl`
- `results/split.json`

```powershell
python scripts/build_lambda.py
python scripts/invoke_local.py
```

Result: Lambda zip built and local Lambda parity check passed.

Important: real Gemini evaluation has not been run yet because `GEMINI_API_KEY` was not available. No live LLM metrics were invented.

---

## 2. Should you create a `.env` file?

Yes, create a `.env` file for local testing only.

Do not commit `.env`. It is already ignored by `.gitignore`.

Create this file:

```text
D:\5th Semester\Cloud Architecture\Project\EHRComplianceMonitor_Cloud_Project_2026\.env
```

Put this inside:

```env
PORT=3000
GEMINI_API_KEY=your_real_gemini_api_key_here
GEMINI_MODEL=gemini-3.8-flash
GEMINI_RPM=15
STAGE_B_MODE=llm_rag
```

PowerShell does not automatically load `.env`, so for local commands set the key in the terminal:

```powershell
$env:GEMINI_API_KEY="your_real_gemini_api_key_here"
$env:GEMINI_MODEL="gemini-3.8-flash"
$env:GEMINI_RPM="15"
```

Then run:

```powershell
python scripts/run_end_to_end.py --mode llm_rag --input dataset/processed/sample_20_for_aws_test.json --output results/rag_20_real.json
```

To test plain LLM:

```powershell
python scripts/run_end_to_end.py --mode llm_plain --input dataset/processed/sample_20_for_aws_test.json --output results/plain_20_real.json
```

To run the full ablation:

```powershell
python scripts/run_ablation.py
```

Run the same command again to confirm cache reuse. The second run should use cached LLM responses.

---

## 3. Files you will upload to AWS

Build the Lambda package locally:

```powershell
python scripts/build_lambda.py
```

Upload this file to AWS Lambda:

```text
build/stage_a_lambda.zip
```

Current build size is about 3.41 MB, so it can be uploaded directly in the AWS Lambda Console.

---

## 4. Recommended AWS region

Use one region consistently. For this project, use:

```text
Asia Pacific (Mumbai) - ap-south-1
```

In the AWS Console, check the top-right region dropdown and select **Asia Pacific (Mumbai)** before creating S3 or Lambda resources.

---

## 5. Create or confirm the S3 bucket

Use S3 for input and output files.

Recommended bucket name:

```text
medical-transcript-verification-2026
```

If the bucket already exists in your account, reuse it.

### Steps in AWS Console

1. Open AWS Console.
2. In the top search bar, type **S3**.
3. Click **S3**.
4. In the left sidebar, click **General purpose buckets**.
5. Click **Create bucket**.
6. For **Bucket name**, enter:

   ```text
   medical-transcript-verification-2026
   ```

7. For **AWS Region**, select:

   ```text
   Asia Pacific (Mumbai) ap-south-1
   ```

8. Leave **Object Ownership** as the default.
9. Leave **Block all public access** enabled.
10. Leave **Bucket Versioning** disabled unless your teacher asks for it.
11. Leave **Default encryption** enabled if AWS shows it by default.
12. Click **Create bucket**.

### Create folders

1. Open the bucket.
2. Click **Create folder**.
3. Folder name:

   ```text
   input
   ```

4. Click **Create folder**.
5. Click **Create folder** again.
6. Folder name:

   ```text
   output
   ```

7. Click **Create folder**.

The Lambda reads:

```text
input/*.json
```

The Lambda writes:

```text
output/stage_b/*.audited.json
```

---

## 6. Create the Lambda function

### Steps in AWS Console

1. In the AWS Console top search bar, type **Lambda**.
2. Click **Lambda**.
3. In the left sidebar, click **Functions**.
4. Click **Create function**.
5. Select **Author from scratch**.
6. For **Function name**, enter:

   ```text
   medical-transcript-stage-b
   ```

7. For **Runtime**, choose a Python runtime. Use the newest available Python 3 runtime in your account, preferably:

   ```text
   Python 3.12
   ```

   Python 3.11 is also fine if 3.12 is not available.

8. For **Architecture**, keep:

   ```text
   x86_64
   ```

9. Expand **Change default execution role**.
10. Select **Create a new role with basic Lambda permissions**.
11. Click **Create function**.

AWS will create a Lambda execution role with CloudWatch logging permission.

---

## 7. Upload the Lambda zip

1. Open the Lambda function:

   ```text
   medical-transcript-stage-b
   ```

2. Click the **Code** tab.
3. In **Code source**, click **Upload from**.
4. Click **.zip file**.
5. Click **Upload**.
6. Select:

   ```text
   D:\5th Semester\Cloud Architecture\Project\EHRComplianceMonitor_Cloud_Project_2026\build\stage_a_lambda.zip
   ```

7. Click **Open**.
8. Click **Save**.

Wait until AWS finishes uploading.

---

## 8. Set Lambda runtime settings

1. In the Lambda function, stay on the **Code** tab.
2. Scroll to **Runtime settings**.
3. Click **Edit**.
4. Set **Handler** to:

   ```text
   stage_a_handler.lambda_handler
   ```

5. Click **Save**.

---

## 9. Set Lambda timeout and memory

The project can run with modest memory, but ICD reference loading and batch processing need more than the smallest default.

1. Open the Lambda function.
2. Click the **Configuration** tab.
3. In the left menu, click **General configuration**.
4. Click **Edit**.
5. Set **Memory** to:

   ```text
   1024 MB
   ```

6. Set **Timeout** to:

   ```text
   5 minutes
   ```

7. Click **Save**.

For larger batch files, increase timeout to 10 or 15 minutes.

---

## 10. Add Lambda environment variables

### Required for real Gemini RAG mode

1. Open the Lambda function.
2. Click **Configuration**.
3. In the left menu, click **Environment variables**.
4. Click **Edit**.
5. Click **Add environment variable**.
6. Add:

   Key:

   ```text
   GEMINI_API_KEY
   ```

   Value:

   ```text
   your_real_gemini_api_key_here
   ```

7. Click **Add environment variable** again.
8. Add:

   Key:

   ```text
   GEMINI_MODEL
   ```

   Value:

   ```text
   gemini-3.8-flash
   ```

9. Click **Add environment variable** again.
10. Add:

   Key:

   ```text
   GEMINI_RPM
   ```

   Value:

   ```text
   15
   ```

11. Click **Add environment variable** again.
12. Add:

   Key:

   ```text
   STAGE_B_MODE
   ```

   Value:

   ```text
   llm_rag
   ```

13. Click **Save**.

### If you want no Gemini calls

Set:

```text
STAGE_B_MODE = stage_a_only
```

Or remove `GEMINI_API_KEY`. The code will use deterministic fallback when no key is present.

---

## 11. Give Lambda permission to read and write S3

The default Lambda role can write CloudWatch logs, but it still needs S3 access.

### Find the Lambda role

1. Open your Lambda function.
2. Click **Configuration**.
3. In the left menu, click **Permissions**.
4. Under **Execution role**, click the role name link.

This opens IAM in a new page.

### Add an inline policy

1. In IAM, open the role.
2. Click the **Permissions** tab.
3. Click **Add permissions**.
4. Click **Create inline policy**.
5. Click the **JSON** tab.
6. Paste this policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::medical-transcript-verification-2026/input/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject"
      ],
      "Resource": "arn:aws:s3:::medical-transcript-verification-2026/output/*"
    }
  ]
}
```

7. Click **Next**.
8. For policy name, enter:

   ```text
   EHRStageBS3ReadWritePolicy
   ```

9. Click **Create policy**.

---

## 12. Add the S3 trigger

You can add the trigger from Lambda or from S3. The Lambda page is usually easier.

### Lambda trigger method

1. Open the Lambda function.
2. Click the **Function overview** area near the top.
3. Click **Add trigger**.
4. In **Select a source**, choose:

   ```text
   S3
   ```

5. For **Bucket**, choose:

   ```text
   medical-transcript-verification-2026
   ```

6. For **Event types**, choose:

   ```text
   All object create events
   ```

7. For **Prefix**, enter:

   ```text
   input/
   ```

8. For **Suffix**, enter:

   ```text
   .json
   ```

9. Check the acknowledgement box if AWS shows one.
10. Click **Add**.

This makes S3 invoke Lambda whenever a JSON file is uploaded under `input/`.

---

## 13. Test Lambda manually before S3 trigger

Before uploading to S3, test with a manual Lambda event.

1. Open the Lambda function.
2. Click the **Test** tab.
3. Click **Create new event**.
4. Event name:

   ```text
   s3_sample_test
   ```

5. Paste this event:

```json
{
  "Records": [
    {
      "s3": {
        "bucket": {
          "name": "medical-transcript-verification-2026"
        },
        "object": {
          "key": "input/sample_20_for_aws_test.json"
        }
      }
    }
  ]
}
```

6. Click **Save**.

This test needs the file to already exist in S3. Upload it first using the next section.

---

## 14. Upload the sample input to S3

1. Open **S3**.
2. Open bucket:

   ```text
   medical-transcript-verification-2026
   ```

3. Open the `input/` folder.
4. Click **Upload**.
5. Click **Add files**.
6. Select:

   ```text
   D:\5th Semester\Cloud Architecture\Project\EHRComplianceMonitor_Cloud_Project_2026\dataset\processed\sample_20_for_aws_test.json
   ```

7. Click **Upload**.

If the S3 trigger is already enabled, uploading this file should automatically invoke Lambda.

---

## 15. Check Lambda output in S3

1. Open **S3**.
2. Open bucket:

   ```text
   medical-transcript-verification-2026
   ```

3. Open:

   ```text
   output/
   ```

4. Open:

   ```text
   stage_b/
   ```

5. Look for:

   ```text
   sample_20_for_aws_test.audited.json
   ```

6. Click the file.
7. Click **Open** or **Download**.

If the file exists, AWS processing worked.

---

## 16. Check CloudWatch logs

1. Open the Lambda function.
2. Click the **Monitor** tab.
3. Click **View CloudWatch logs**.
4. Open the latest log stream.
5. Look for lines like:

```text
Processing object input/sample_20_for_aws_test.json from bucket medical-transcript-verification-2026
```

and:

```json
{"metric":"stage_a_execution", ...}
```

If there is an error, the log stream will show the Python error message.

CloudWatch logs may take a few minutes to appear.

---

## 17. Common errors and fixes

### Error: AccessDenied for S3

Cause: Lambda role does not have `s3:GetObject` or `s3:PutObject`.

Fix:

- Go back to IAM role.
- Confirm inline policy allows:
  - `arn:aws:s3:::medical-transcript-verification-2026/input/*`
  - `arn:aws:s3:::medical-transcript-verification-2026/output/*`

### Error: No output file appears

Check:

- File was uploaded under `input/`.
- File name ends with `.json`.
- S3 trigger prefix is exactly `input/`.
- S3 trigger suffix is exactly `.json`.
- Lambda timeout did not expire.
- CloudWatch logs show no exception.

### Error: Gemini API key missing

Cause: `GEMINI_API_KEY` not set.

Fix:

- Add it under Lambda **Configuration → Environment variables**.
- If you want deterministic mode only, set `STAGE_B_MODE=stage_a_only`.

### Error: Gemini quota or 429

Cause: API rate limit.

Fix:

- Lower `GEMINI_RPM`, for example:

  ```text
  GEMINI_RPM=5
  ```

- Use smaller input batches.
- Retry later.

### Error: Lambda timeout

Fix:

- Increase timeout under **Configuration → General configuration**.
- Use smaller JSON batches.

---

## 18. What you can test now locally

### Local deterministic mode

```powershell
python scripts/run_end_to_end.py --mode stage_a_only --input dataset/processed/sample_20_for_aws_test.json --output results/stage_a_only_20.json
```

### Local RAG dry-run, no API cost

```powershell
python scripts/run_end_to_end.py --mode llm_rag --input dataset/processed/sample_20_for_aws_test.json --dry-run
```

### Local real Gemini run

```powershell
$env:GEMINI_API_KEY="your_real_gemini_api_key_here"
python scripts/run_end_to_end.py --mode llm_rag --input dataset/processed/sample_20_for_aws_test.json --output results/rag_20_real.json
```

### Local full honest ablation

```powershell
$env:GEMINI_API_KEY="your_real_gemini_api_key_here"
python scripts/run_ablation.py
```

Read:

```text
results/ablation_metrics.md
```

---

## 19. What you can test now on AWS

After deployment:

1. Upload `sample_20_for_aws_test.json` to:

   ```text
   s3://medical-transcript-verification-2026/input/sample_20_for_aws_test.json
   ```

2. Wait for Lambda to run.
3. Check:

   ```text
   s3://medical-transcript-verification-2026/output/stage_b/sample_20_for_aws_test.audited.json
   ```

4. Check CloudWatch logs.

---

## 20. What not to do

- Do not commit `.env`.
- Do not paste `GEMINI_API_KEY` into README files, reports, screenshots, GitHub, or presentation slides.
- Do not upload real patient data to a free-tier or consumer Gemini API.
- Do not claim live Gemini metrics until `python scripts/run_ablation.py` has been run with a real key.
- Do not mix dry-run metrics with real LLM metrics.

---

## 21. Final demo story

For the project review, explain it like this:

1. A transcript JSON is uploaded to S3 under `input/`.
2. S3 triggers Lambda.
3. Lambda runs Stage A retrieval:
   - medications checked against RxNorm,
   - indications from MED-RT,
   - diagnoses matched against ICD-10-CM.
4. Stage B runs:
   - deterministic fallback if no Gemini key,
   - real Gemini RAG if `GEMINI_API_KEY` is configured.
5. The audited result is written to S3 under `output/stage_b/`.
6. The honest evaluation compares:
   - Stage A only,
   - plain LLM,
   - RAG-grounded LLM.

The important claim is not “RAG is automatically better.” The correct claim is:

```text
We built an evaluation that can measure whether retrieved medical facts improve contextual error detection, and we report whatever the data shows.
```

