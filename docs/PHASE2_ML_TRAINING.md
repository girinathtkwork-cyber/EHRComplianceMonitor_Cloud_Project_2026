# Phase 2: ML experiment and SageMaker preparation

## Status

This phase implements a reproducible local experiment and a SageMaker script-mode entry point. Dataset preparation and model fitting were run locally on 2026-10-15. No SageMaker job, S3 upload, IAM change, endpoint, Lambda deployment, or other AWS resource operation was performed.

The provided `D:\aws.exe` failed to start in this environment with `ModuleNotFoundError: No module named '_socket'`, including for `--version` and STS. Therefore the `ehrguard-dev` profile, caller identity, bucket access, execution role, and current caller permissions could not be verified. Do not use AWS execution until that is corrected and region-specific pricing and authorization have been reviewed.

## Task and labels

The prediction unit is one Stage A record. The target is `ground_truth_label.is_error`, a binary flag identifying examples whose transcript was synthetically modified by `dataset/scripts/inject_errors.py`. It does not label naturally occurring clinical errors, clinical truth, or whether a Stage A extraction was wrong. The results do not establish clinical validity.

The source `dataset/processed/mtsamples_with_errors.json` has 270 records: 180 clean and 90 synthetically injected. The injected labels comprise 18 wrong dosage, 38 fabricated diagnosis, 23 fabricated procedure, and 11 wrong medication examples. All labels were present and structurally valid. `correct_value` is intentionally null for clean and fabricated diagnosis/procedure records; it is excluded from inputs. The 20-record AWS demo subset was not used for training or test evaluation.

Exact normalized transcript duplicates and character TF-IDF near duplicates (cosine >= 0.98) are grouped into connected components before splitting. The audit found four excess exact duplicates and two near-duplicate pairs. The source data does not provide a reliable original-source-transcript key, so grouping is based on text similarity. Split membership is disjoint by group.

## Features and leakage controls

`src/backend/ml/feature_schema_v1.json` defines 25 integer features generated from the current Stage A output (version 2.0.0): entity counts, routing/terminology status counts, duplicate mention counts, structural missingness, medication assertion/section counts, and the partial-verification flag. The exact ordered schema is enforced in CSV and inference input. Transcript text, IDs, filenames, sample names, medical specialty, all label fields, and Stage B findings are excluded. Stage B is measured only as a separate deterministic baseline. Stage A terminology lookups are blocked from making external requests during preparation; local cache misses are represented by Stage A status.

Preprocessing is fitted on training rows only: median imputation with missingness indicators, standard scaling, then class-weighted logistic regression. The decision threshold maximizes validation F1; ties choose the higher threshold. Test rows are used only for the final report. Scores are uncalibrated and must not be presented as probabilities.

## Reproduce locally

Run from the repository root with Python, scikit-learn, NumPy, and joblib already installed:

```powershell
python scripts/prepare_phase2_dataset.py --output-dir results/ml/phase2_features_20261015
python scripts/train_phase2.py --dataset-dir results/ml/phase2_features_20261015 --output-dir results/ml/phase2_local_model_20261015
python -m pytest tests/test_ml_phase2.py -q -p no:cacheprovider --basetemp .phase2_pytest_tmp
```

Preparation refuses to overwrite an existing output directory. It writes only numeric feature CSVs and aggregate metadata; source transcript text and record IDs are not copied to the experiment output. The `config/` folder contains the SageMaker config channel files. Local model artifacts are `model.joblib`, `model_metadata.json`, `evaluation.json`, and a copy of the feature schema.

## Local split and baseline results

Seed: `20261015`. Group-aware stratified nested folds requested 60/20/20; realized split sizes were train 162 (108 clean / 54 injected), validation 55 (35 / 20), test 53 (37 / 16). There were zero groups shared across splits. A small test set and one injected example type represented by a single test record limit the reliability of the findings.

| Test method | Precision | Recall | F1 | FPR | FNR | ROC-AUC | PR-AUC (AP) | Confusion (TN, FP, FN, TP) |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Majority clean | 0.000 | 0.000 | 0.000 | 0.000 | 1.000 | n/a | n/a | 37, 0, 16, 0 |
| Existing deterministic Stage B audit | 0.600 | 0.188 | 0.286 | 0.054 | 0.813 | n/a | n/a | 35, 2, 13, 3 |
| Logistic regression, validation-F1 threshold | 0.300 | 0.938 | 0.455 | 0.946 | 0.063 | 0.537 | 0.436 | 2, 35, 1, 15 |

The logistic model has the best F1 among these methods on this one split, but ROC-AUC is near chance and it raises a review flag on nearly every clean test record. Its high recall comes with an impractically high false-positive burden. This does not demonstrate reliable generalization or superiority. The majority baseline is intentionally uninformative but documents class-skew behavior. XGBoost was not evaluated because it is not installed; no dependency was added.

## SageMaker workflow (prepared, not run)

`sagemaker/phase2/train.py` is a SageMaker script-mode entry point and also supplies model serialization and JSON inference handlers. It reads `train`, `validation`, `test`, and `config` channels, then writes model, metadata, schema, and evaluation files to `SM_MODEL_DIR`. The SageMaker scikit-learn container family supports the selected `1.4-2` framework version; see [AWS SageMaker scikit-learn containers](https://docs.aws.amazon.com/sagemaker/latest/dg/sklearn.html). The local environment has scikit-learn 1.7.2; exact cloud package compatibility has not been exercised because the SDK and AWS profile were unavailable.

Before any launch:

1. Verify the signed CLI works and verify `ehrguard-dev` identity in `ap-south-1`.
2. Confirm read access to the intended bucket and obtain an existing SageMaker execution role ARN; this workflow does not create or modify IAM.
3. Upload only the prepared numeric split CSVs, schema, and a `training_config.json` containing the seed and dataset fingerprint. Do not upload raw transcripts.
4. Check the current `ml.m5.large` rate in the [AWS SageMaker pricing page](https://aws.amazon.com/sagemaker/ai/pricing/) or Pricing Calculator for `ap-south-1`. Add estimated S3 storage/requests and CloudWatch charges. No region-specific price was verified for this experiment, so no cost estimate or launch authorization is claimed here.
5. Review the exact job configuration: one `ml.m5.large` instance, scikit-learn `1.4-2`, maximum runtime 3,600 seconds by default, output under the caller-supplied S3 prefix, no spot instances, and no endpoint.
6. Only after cost and permissions are known, run the launcher with a region-specific hourly rate, a cost cap, and `--authorize-launch`. It prints an upper-bound compute estimate and refuses to launch above the supplied cap. The authorization flag is an explicit per-run gate.

The launcher accepts explicit S3 URIs and does not contain account-specific paths or credentials. Install a compatible SageMaker SDK and boto3 only in an approved environment. After the preflight, use the following PowerShell template, replacing every `<...>` value with reviewed values. The upload commands transfer only feature CSVs and config metadata:

```powershell
$bucket = "<approved-bucket>"
$prefix = "<unique-phase2-prefix>"
$data = "D:\5th Semester\Cloud Architecture\Project\EHRComplianceMonitor_Cloud_Project_2026\results\ml\phase2_features_20261015"
& "D:\aws.exe" s3 cp "$data\train\" "s3://$bucket/$prefix/train/" --recursive --profile ehrguard-dev --region ap-south-1
& "D:\aws.exe" s3 cp "$data\validation\" "s3://$bucket/$prefix/validation/" --recursive --profile ehrguard-dev --region ap-south-1
& "D:\aws.exe" s3 cp "$data\test\" "s3://$bucket/$prefix/test/" --recursive --profile ehrguard-dev --region ap-south-1
& "D:\aws.exe" s3 cp "$data\config\" "s3://$bucket/$prefix/config/" --recursive --profile ehrguard-dev --region ap-south-1
```

First review the printed estimate without `--authorize-launch`; it exits without creating a job. Only after explicitly approving the shown configuration and estimate, run the same command with `--authorize-launch` appended:

```powershell
python scripts/launch_sagemaker_phase2.py --profile ehrguard-dev --region ap-south-1 --role-arn "<existing-execution-role-arn>" --train-s3-uri "s3://<approved-bucket>/<unique-phase2-prefix>/train/" --validation-s3-uri "s3://<approved-bucket>/<unique-phase2-prefix>/validation/" --test-s3-uri "s3://<approved-bucket>/<unique-phase2-prefix>/test/" --config-s3-uri "s3://<approved-bucket>/<unique-phase2-prefix>/config/" --output-s3-uri "s3://<approved-bucket>/<unique-phase2-prefix>/model-output/" --hourly-rate-usd <calculator-rate> --cost-cap-usd <approved-cap> --max-run-seconds 3600
```

The launch version of that command requires `--authorize-launch`. These commands were not run in this phase because the CLI and AWS profile could not be verified.

## Limitations and next phase boundary

This is a small experiment on synthetic labels and Stage A aggregate features. Stage A extraction errors and cached terminology coverage affect those features; seven lookup misses were blocked and handled offline. There is no natural error validation set, prospective evaluation, subgroup adequacy, calibration analysis, confidence interval, or clinical review. Do not use this model to make care decisions. It is not integrated with Stage A, Stage B, Lambda, or the dashboard. Phase 3 would first need a representative, reviewed evaluation dataset and a plan for safe human review before any integration design.
