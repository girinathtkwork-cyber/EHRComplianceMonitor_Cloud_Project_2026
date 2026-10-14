# Offline evaluation data

## Dataset and ground truth

The project uses the de-identified MTSamples corpus already stored in `dataset/raw/mtsamples.csv`. The preparation scripts create copies with known injected errors. Each altered record carries `ground_truth_label` containing the error type, injected entity, and—where applicable—the original correct value.

This enables repeatable evaluation without pretending that an unlabelled transcript is ground truth.

These datasets are for offline development and evaluation only. They are not preloaded into the dashboard or returned from its API. The dashboard processes only transcript text a user enters or uploads.

The local demo and dataset-evaluation routes have been removed from the dashboard server. Keep any future offline evaluation scripts separate from the user-facing transcript workflow, and report their results as prototype measurements rather than clinical validation.
