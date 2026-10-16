# Stage B LLM + RAG Report

Last updated: 11 October 2026.

## Architecture

Stage A extracts entities and retrieves RxNorm, MED-RT, and ICD-10-CM evidence.
Stage B now supports three modes through `backend.stage_b.stage_b.run_stage_b`:

- `stage_a_only`: no LLM; deterministic audit fallback.
- `llm_plain`: Gemini sees the transcript and extracted entities, but no retrieved facts.
- `llm_rag`: Gemini sees the same transcript and entities plus numbered Stage A `evidence_text` facts.

The Lambda handler uses `llm_rag` only when `GEMINI_API_KEY` is set. Otherwise it
uses `stage_a_only`, so local and AWS runs remain safe without secrets.

## Gemini docs checked

Checked on 11 October 2026:

- REST endpoint: `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`
- Authentication: `x-goog-api-key: $GEMINI_API_KEY`
- JSON output: `response_format` with `type: text`, `mime_type: application/json`,
  and the output schema.
- Configured default model: `gemini-3.8-flash`
- Rate limits: current docs say active limits should be checked in AI Studio; free tier availability/rate limits can change by project.

Sources: Google AI Gemini API generate-content, structured-output, rate-limit, and pricing docs.

## Exact prompt skeleton

Both LLM modes share the same base instructions and output schema. The only
intentional experiment difference is the evidence rule and the retrieved facts block.

Plain mode evidence rule:

```text
Evidence rule: use your own medical knowledge.
```

RAG mode evidence rule:

```text
Evidence rule: base verdicts on the numbered retrieved facts and the transcript; if they are insufficient, answer insufficient_evidence instead of guessing.
```

The full rendered prompts are saved for every run under `results/prompts/<mode>/`.

## Ground-truth protection

Prompt construction removes `ground_truth_label`, `is_error`, `error_type`,
`injected_entity`, and `correct_value`. A prompt leak check raises before any API
call if those keys appear in the prompt.

## Evaluation protocol

`scripts/run_ablation.py` creates a fixed stratified 30/70 dev/test split in
`results/split.json`, runs `stage_a_only`, `llm_plain`, and `llm_rag`, and writes:

- `results/ablation_metrics.json`
- `results/ablation_metrics.md`
- `results/ablation_predictions.jsonl`
- `results/split.json`

Headline metrics are test-split, no-reference metrics. The report also writes an
all-records table labelled as including dev records.

No prompt tuning has been done yet. Iteration log:

1. Initial implementation only. No dev-set tuning performed.

## Commands actually run so far

```text
python scripts/run_end_to_end.py --input dataset/processed/sample_20_for_aws_test.json --output results/end_to_end_output.json
```

Result:

```json
{
  "records": 20,
  "stage_b_findings": 40,
  "output": "results\\end_to_end_output.json"
}
```

```text
python -m pytest -q
```

Result:

```text
57 passed in 17.78s
```

```text
python scripts/run_end_to_end.py --mode llm_rag --input dataset/processed/sample_20_for_aws_test.json --dry-run
```

Result:

```json
{
  "records": 20,
  "mode": "llm_rag",
  "dry_run": true,
  "stage_b_findings": 0,
  "llm_ok": 0,
  "fallback": 0,
  "output": "results\\end_to_end_output.json"
}
```

```text
python scripts/run_ablation.py --dry-run
```

Result:

```json
{
  "records": 270,
  "dry_run": true,
  "outputs": [
    "results/ablation_metrics.json",
    "results/ablation_metrics.md",
    "results/ablation_predictions.jsonl",
    "results/split.json"
  ]
}
```

```text
python scripts/build_lambda.py
python scripts/invoke_local.py
```

Results: Lambda zip built successfully at 3.41 MB, and local Lambda parity check passed for 20 records.

No real Gemini result is reported here because no API key was available in this session.

## Dry-run ablation metrics

These are dry-run metrics. `llm_plain` and `llm_rag` made zero API calls, so their
zero recall is only a plumbing check, not a model-quality result.

```text
test_headline_no_reference / stage_a_only:
TP=15 FP=45 FN=49 TN=81, precision=0.250, recall=0.234, F1=0.242, clean FPR=0.357

all_270_includes_dev / stage_a_only:
TP=22 FP=80 FN=68 TN=100, precision=0.216, recall=0.244, F1=0.229, clean FPR=0.444
```

## Human commands for real LLM runs

```powershell
$env:GEMINI_API_KEY="your_key"
python scripts/run_end_to_end.py --mode llm_plain --input dataset/processed/sample_20_for_aws_test.json --output results/plain_20.json
python scripts/run_end_to_end.py --mode llm_rag --input dataset/processed/sample_20_for_aws_test.json --output results/rag_20.json
python scripts/run_ablation.py
```

Run the same commands a second time to confirm cache reuse from
`dataset/cache/llm_cache.jsonl`.

## Limitations

- Only four injected error types are evaluated.
- Errors are synthetic, not real audio-transcription errors.
- Per-type sample sizes are small.
- Gemini model behavior and free-tier limits can change.
- MTSamples is public and may be present in model training data.
- Prompts were written by us and have not been clinically validated.
- Real patient data must never be sent to a free-tier or consumer API.

## Still to do

- Run real Gemini calls with `GEMINI_API_KEY`.
- Paste real ablation tables into this report after the command completes.
- Decide whether dashboard display should keep title-case severity while the LLM contract uses lowercase.
