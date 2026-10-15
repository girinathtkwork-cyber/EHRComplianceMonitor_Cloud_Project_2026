# Stage A audit and stabilization — 2026-10-15

## Scope and baseline

The labeled evaluation set contains 270 clean records and 270 injected-error records: 18 wrong dosages, 38 fabricated diagnoses, 23 fabricated procedures, and 11 wrong medications. The labels identify an injected entity and correction for affected records, but do not provide complete entity spans or gold annotations for medications, instructions, measurements, diagnoses, or procedures. Entity-level precision, recall, and F1 are therefore unavailable.

The supplied dev/test split contains 80/190 records. A case-folded, whitespace-normalized SHA-256 comparison found one exact transcript-text group crossing the split (one shared copy). The historical `results/stage_a_output.json` has the same record IDs and transcript hashes as the current set, but its entity counts differ from a Git HEAD extractor replay. It is preserved as historical evidence and is not used for the controlled comparison.

The controlled offline comparison is under `results/evaluation/`. It uses the same 270 records, current verifier, local terminology cache, and blocked HTTP calls; only the extractor differs (Git HEAD versus working tree). This isolates extractor changes and does not measure the separate verification changes against their pre-task implementation.

## Root causes and existing design

- Medication extraction scanned one to three whitespace/punctuation tokens against the RxNorm lexicon. A long manual stoplist suppressed common-word collisions, including legitimate substances and supplements. Normalization was lowercase-only, and tokenization excluded numbers/product punctuation.
- Numeric values with units were assigned to the nearest medication within an eight-token window. Remaining values were called measurements. This missed blood pressure formats without a unit and did not preserve route, frequency, or duration as structured attributes. RxNorm product strengths were compared with prescribed doses even though they describe different things.
- Diagnoses and procedures were extracted only after fixed headers and split by simple punctuation/list rules. Diagnoses used a local TF-IDF lexical index; its cosine similarity is a candidate ranking, not a terminology confirmation. Procedures have no coding-system verification in this project.
- Medication mentions were not given explicit negation/history/allergy/planning metadata. RxNorm API errors and absent results were not reliably distinguishable to the high-level verifier; approximate results could be accepted from the first candidate, and missing properties could fall back to the input term.
- Stage B reads `entities`, `routing`, and `retrieval_context.evidence_text`; routing values are contract-limited to `invalid`, `verified_needs_context_check`, and `unresolved`. Lambda, local runner, API, and dashboard pass through this contract. Additive nested metadata preserves it.

## Changes

- Added NFKC/case-folded medication lookup normalization, number/product punctuation token support, and source-backed medication assertion/section attributes. Kept the `today` regression blocked explicitly while allowing other common lexicon collisions when medication cues are present.
- Added structured blood-pressure, temperature, and common vital-sign patterns with original source spans. Medication dose entities can carry source-backed route, frequency, and duration text/spans, bounded by neighboring medication mentions. No conversions or inferred values are made.
- RxNorm exact matches now require a unique returned identifier and validated concept properties. Approximate candidates are review-only and never confirmed. Cache membership distinguishes stored null (negative result) from a miss. Optional enrichment failures remain visible without erasing an exact concept match.
- Unavailable, ambiguous, and unknown medication lookups route to `unresolved`; they no longer imply invalidity. Record verification becomes `partial` when lookup or enrichment is incomplete. Medication lookups are deduplicated within a record.
- Diagnosis output now labels the local ICD-10 lexical results as candidates, with method/status metadata. Procedure and measurement records explicitly say they are unverified. Product strength is no longer used to flag a prescribed dose as implausible.
- Added `scripts/evaluate_stage_a_audit.py`, which emits aggregate-only reports and blocks network access during replay. It does not print or save transcript/entity strings.

## Evaluation

| Entity type | Git HEAD extractor | Current extractor | Change |
|---|---:|---:|---:|
| Medication | 858 | 857 | -1 |
| Dosage | 309 | 308 | -1 |
| Measurement | 261 | 364 | +103 |
| Diagnosis | 572 | 572 | 0 |
| Procedure | 503 | 503 | 0 |

The measurement increase comes from the added structured vital-sign patterns; no gold spans exist to establish its precision. Wrong-dosage funnel classification remained 11 medication-associated, 7 measurement, and 0 missed in both runs.

| Record-level injected-error metric | Baseline | Current | Change |
|---|---:|---:|---:|
| Soft precision | 0.1818 | 0.2222 | +0.0404 |
| Soft recall | 0.0444 | 0.0667 | +0.0223 |
| Soft F1 | 0.0714 | 0.1026 | +0.0312 |
| Soft clean-record FPR | 0.1000 | 0.1167 | +0.0167 |
| Localized injected-entity soft recall | 0.0111 | 0.0111 | 0 |

These are the project evaluator's record-level injected-error funnel measures, not entity extraction accuracy. The record-level gain is small, and the clean-record false-positive rate rose by three records. Strict detection remained 0/90 because exact invalid-routing signals were absent; no reliable entity-level before/after claim can be made.

The baseline replay took 7.21 seconds and current replay 16.81 seconds on the final observed runs. This is not a reliable speed comparison because local index/cache startup and runtime varied. The baseline had zero blocked cache misses; the current replay had seven. Actual external terminology requests were zero; the seven cache misses were intercepted before networking. No API error rates can be inferred from this offline run.

## Tests and integration

Focused Stage A/integration tests: `python -B -m pytest -q -p no:cacheprovider --basetemp .stagea-focused-integration-tmp tests/test_run_end_to_end.py tests/test_web_api.py tests/test_handler_zip.py tests/test_contract.py tests/test_rxnorm_client.py tests/test_extract.py` — 73 passed.

Full suite: `python -B -m pytest -q -p no:cacheprovider --basetemp .stagea-full-last-tmp tests` — 97 passed.

A synthetic end-to-end `stage_a_only` pass wrote only to `.stagea-e2e-tmp/output.json`; Stage A and Stage B completed, contract validation returned no errors, and terminology/Gemini request counts were zero. The test fixture blocks network access. No production data or reference outputs were overwritten.

## Files and compatibility

Stage A changes are in `src/backend/stage_a/extract.py`, `src/backend/stage_a/verify.py`, `src/backend/stage_a/rxnorm_client.py`, and `src/backend/stage_a/pipeline.py`. New tests cover extraction context, vitals, instruction spans/associations, offline terminology behavior, and additive contract compatibility. The safe evaluator is `scripts/evaluate_stage_a_audit.py`; aggregate reports are `results/evaluation/stage_a_audit_baseline_20261015.json`, `stage_a_repro_baseline_20261015.json`, and `stage_a_after_20261015.json`.

Existing entity types, route enums, required contract fields, and Stage B routing inputs remain unchanged. Additional context is nested/additive. The API, Lambda, CLI summary, and local end-to-end runner now pass `partial` Stage A results through to Stage B so lookup uncertainty remains reviewable. The full suite includes contract, Stage B, Lambda-handler, and end-to-end runner checks.

## Limitations and next step

Section detection remains heuristic; negation/context detection is conservative and medication-specific, not a general clinical assertion model. The current evaluator's string-overlap method is limited and does not establish clinical correctness. Frequency/route/duration patterns cover only a small explicit set, and there is no terminology source for procedure coding or measurement validity. The evaluation split has one exact duplicate group across dev and test. Add adjudicated, span-level annotations and deduplicate before using this set for tuning or performance claims.

AWS actions: none. No model training or deployment was performed.
