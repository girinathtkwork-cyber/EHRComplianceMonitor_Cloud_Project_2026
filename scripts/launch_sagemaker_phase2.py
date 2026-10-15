"""Explicitly gated SageMaker training launcher; never creates an endpoint."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--role-arn", required=True)
    parser.add_argument("--train-s3-uri", required=True)
    parser.add_argument("--validation-s3-uri", required=True)
    parser.add_argument("--test-s3-uri", required=True)
    parser.add_argument("--config-s3-uri", required=True, help="Prefix containing training_config.json and feature_schema_v1.json")
    parser.add_argument("--output-s3-uri", required=True)
    parser.add_argument("--hourly-rate-usd", type=float, required=True, help="Region-specific rate verified in AWS Pricing Calculator")
    parser.add_argument("--cost-cap-usd", type=float, required=True)
    parser.add_argument("--max-run-seconds", type=int, default=3600)
    parser.add_argument("--seed", type=int, default=20261015)
    parser.add_argument("--authorize-launch", action="store_true", help="Explicitly allow creating one training job after cost check")
    args = parser.parse_args()
    estimate = args.hourly_rate_usd * args.max_run_seconds / 3600
    print(f"Estimated compute upper bound: USD {estimate:.4f} (excludes S3, logs, and taxes)")
    print("Configuration: 1 x ml.m5.large, scikit-learn 1.4-2, max_run_seconds=" + str(args.max_run_seconds))
    print("This submits a training job only; it does not create an endpoint.")
    if args.hourly_rate_usd <= 0 or args.cost_cap_usd < 0 or estimate > args.cost_cap_usd:
        raise SystemExit("Cost estimate exceeds the supplied cap or is invalid; no job submitted")
    if not args.authorize_launch:
        raise SystemExit("No job submitted. Re-run with --authorize-launch after reviewing this estimate.")
    try:
        import boto3
        from sagemaker.sklearn.estimator import SKLearn
    except ImportError as exc:
        raise SystemExit("Install compatible boto3 and SageMaker SDK in an approved environment before launch") from exc

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    identity = session.client("sts").get_caller_identity()
    print(f"Authenticated caller: {identity['Arn']}")
    estimator = SKLearn(
        entry_point="train.py", source_dir="sagemaker/phase2", role=args.role_arn,
        instance_type="ml.m5.large", instance_count=1, framework_version="1.4-2",
        py_version="py3", max_run=args.max_run_seconds, output_path=args.output_s3_uri,
        base_job_name="ehrguard-phase2", hyperparameters={"seed": args.seed},
        sagemaker_session=__import__("sagemaker").Session(boto_session=session),
    )
    estimator.fit({
        "train": args.train_s3_uri,
        "validation": args.validation_s3_uri,
        "test": args.test_s3_uri,
        "config": args.config_s3_uri,
    }, wait=True, logs=True)
    print(f"Training job: {estimator.latest_training_job.name}")
    print(f"Model artifact: {estimator.model_data}")


if __name__ == "__main__":
    main()
