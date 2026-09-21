# Evaluation and demonstration guide

## Dataset and ground truth

The project uses the de-identified MTSamples corpus already stored in `dataset/raw/mtsamples.csv`. The preparation scripts create copies with known injected errors. Each altered record carries `ground_truth_label` containing the error type, injected entity, and—where applicable—the original correct value.

This enables repeatable evaluation without pretending that an unlabelled transcript is ground truth.

## Demo tomorrow

1. In the project folder run `npm.cmd run check`.
2. Run `npm.cmd start` and open `http://localhost:3000`.
3. Click **Load MTSamples demo case**.
4. Point out that the candidate and trusted source are different versions of the same labelled MTSamples item.
5. Click **Run safety review** and show the critical/high flag, source excerpt, severity count, and RxNorm reference provenance.
6. State the safety boundary: the output is a clinician-review signal, never an automatic correction or diagnosis.

## Measures to produce next

Run the analyser across every labelled evaluation record, compare its flags to `ground_truth_label`, then report precision, recall, F1-score, false-positive rate, and processing time. Report results separately for wrong medication, wrong dosage, fabricated diagnosis, and fabricated procedure; do not combine them into one misleading score.
