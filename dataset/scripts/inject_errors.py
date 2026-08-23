"""
Error Injection Script
------------------------
Simulates AI transcription mistakes by deliberately injecting known errors
into clean MTSamples transcripts. This creates a ground-truth labeled test
set used later to evaluate how well the verification pipeline (Stage A + B)
catches real errors.

IMPORTANT: This is a TESTING/EVALUATION tool, not part of the live pipeline.
In production, the verification system checks whatever a real AI transcription
actually produces. Here, we simulate plausible mistakes ourselves so we can
measure detection accuracy against a known answer key.

Error types (weighted by real-world severity, per project design):
  - wrong_medication   (35%) - swap a real drug for a different, confusable real drug
  - wrong_dosage       (35%) - alter a numeric dosage value
  - fabricated_diagnosis (20%) - insert a plausible but unmentioned diagnosis
  - fabricated_procedure (10%) - insert a procedure that wasn't performed

Run with: python inject_errors.py
"""

import json
import random
import re
import copy

random.seed(42)  # reproducible results

INPUT_JSON = "dataset/processed/mtsamples_clean.json"
OUTPUT_JSON = "dataset/processed/mtsamples_with_errors.json"

CLEAN_RATIO = 0.70  # 70% stay clean, 30% get one injected error

ERROR_TYPE_WEIGHTS = {
    "wrong_medication": 0.35,
    "wrong_dosage": 0.35,
    "fabricated_diagnosis": 0.20,
    "fabricated_procedure": 0.10,
}

# Real, confusable drug pairs (similar sound/spelling, genuinely different drugs)
# Used to simulate realistic AI mishearing/hallucination.
# Expanded after testing showed the original short list matched only 12/270
# records -- these additions were chosen to actually appear across Surgery,
# Cardiovascular, Orthopedic, Radiology, General Medicine, and Gastroenterology.
CONFUSABLE_DRUGS = [
    ("clonidine", "Klonopin"),
    ("hydralazine", "hydroxyzine"),
    ("Celebrex", "Celexa"),
    ("Zantac", "Xanax"),
    ("Metformin", "Metronidazole"),
    ("Lamictal", "Lamisil"),
    ("Lasix", "Losec"),
    ("Prilosec", "Prinivil"),
    ("Coumadin", "Cardura"),
    ("Toradol", "Tegretol"),
    ("Zocor", "Zoloft"),
    ("Vicodin", "hydrocodone"),
    ("morphine", "hydromorphone"),
    ("Ativan", "Benadryl"),
    ("Percocet", "Percodan"),
    ("albuterol", "atenolol"),
    ("Prednisone", "prednisolone"),
    ("heparin", "Hespan"),
    ("insulin", "Humalog"),
]

# Plausible-sounding diagnoses to fabricate/insert (varied by generality
# so they can slot into many specialties without being absurd)
FABRICATED_DIAGNOSES = [
    "Type 2 Diabetes Mellitus",
    "Chronic Kidney Disease, Stage 3",
    "Atrial Fibrillation",
    "Major Depressive Disorder",
    "Hyperlipidemia",
]

FABRICATED_PROCEDURES = [
    "diagnostic laparoscopy",
    "cardiac catheterization",
    "upper endoscopy",
    "MRI-guided biopsy",
]

DOSAGE_PATTERN = re.compile(r"\b(\d+)\s?(mg|mcg|g|ml)\b", re.IGNORECASE)


def find_drug_mention(text):
    """Return (found_drug, replacement) if a known confusable drug appears in text."""
    for real, confusable in CONFUSABLE_DRUGS:
        if re.search(r"\b" + re.escape(real) + r"\b", text, re.IGNORECASE):
            return real, confusable
    return None, None


def inject_wrong_medication(record):
    text = record["transcription"]
    found, replacement = find_drug_mention(text)
    if not found:
        return None  # this transcript has no known drug mention, skip
    new_text = re.sub(r"\b" + re.escape(found) + r"\b", replacement, text, count=1, flags=re.IGNORECASE)
    return new_text, {
        "is_error": True,
        "error_type": "wrong_medication",
        "injected_entity": replacement,
        "correct_value": found,
    }


def inject_wrong_dosage(record):
    text = record["transcription"]
    match = DOSAGE_PATTERN.search(text)
    if not match:
        return None
    original_value = int(match.group(1))
    unit = match.group(2)
    # Alter the dosage meaningfully (10x or /10, to be clearly clinically wrong)
    new_value = original_value * 10 if original_value < 100 else original_value // 10
    new_text = text[:match.start()] + f"{new_value}{unit}" + text[match.end():]
    return new_text, {
        "is_error": True,
        "error_type": "wrong_dosage",
        "injected_entity": f"{new_value}{unit}",
        "correct_value": f"{original_value}{unit}",
    }


def inject_fabricated_diagnosis(record):
    text = record["transcription"]
    diagnosis = random.choice(FABRICATED_DIAGNOSES)
    insertion = f" Additional diagnosis noted: {diagnosis}."
    # Insert after the first sentence for plausibility
    first_period = text.find(".")
    if first_period == -1:
        new_text = text + insertion
    else:
        new_text = text[:first_period + 1] + insertion + text[first_period + 1:]
    return new_text, {
        "is_error": True,
        "error_type": "fabricated_diagnosis",
        "injected_entity": diagnosis,
        "correct_value": None,
    }


def inject_fabricated_procedure(record):
    text = record["transcription"]
    procedure = random.choice(FABRICATED_PROCEDURES)
    insertion = f" Patient also underwent {procedure} during this visit."
    new_text = text + insertion
    return new_text, {
        "is_error": True,
        "error_type": "fabricated_procedure",
        "injected_entity": procedure,
        "correct_value": None,
    }


INJECTORS = {
    "wrong_medication": inject_wrong_medication,
    "wrong_dosage": inject_wrong_dosage,
    "fabricated_diagnosis": inject_fabricated_diagnosis,
    "fabricated_procedure": inject_fabricated_procedure,
}


def available_error_types(record):
    """
    Check which error types actually have a valid target in this record's text.
    fabricated_diagnosis and fabricated_procedure can always be inserted (they
    don't require finding an existing match) - wrong_medication and wrong_dosage
    require the text to actually contain a matching drug/dosage pattern.
    """
    text = record["transcription"]
    available = ["fabricated_diagnosis", "fabricated_procedure"]  # always insertable

    found_drug, _ = find_drug_mention(text)
    if found_drug:
        available.append("wrong_medication")

    if DOSAGE_PATTERN.search(text):
        available.append("wrong_dosage")

    return available


def weighted_choice_from(available_types):
    """Pick an error type using our target weights, restricted to what's
    actually available for this specific record."""
    weights = [ERROR_TYPE_WEIGHTS[t] for t in available_types]
    return random.choices(available_types, weights=weights, k=1)[0]


def main():
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        records = json.load(f)

    print(f"Loaded {len(records)} clean records")

    output = []
    error_count = 0

    for record in records:
        new_record = copy.deepcopy(record)

        should_inject = random.random() > CLEAN_RATIO
        if not should_inject:
            new_record["ground_truth_label"] = {"is_error": False, "error_type": None,
                                                   "injected_entity": None, "correct_value": None}
            output.append(new_record)
            continue

        # Only choose among error types that actually have a valid target
        # in THIS record's text - fixes earlier bug where a type was picked
        # blindly first, then fell back to diagnosis if it didn't match.
        available = available_error_types(record)
        error_type = weighted_choice_from(available)
        result = INJECTORS[error_type](record)

        new_text, label = result
        new_record["transcription"] = new_text
        new_record["ground_truth_label"] = label
        error_count += 1
        output.append(new_record)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"Injected errors into {error_count} of {len(records)} records ({error_count/len(records)*100:.1f}%)")

    # Breakdown by error type
    breakdown = {}
    for r in output:
        et = r["ground_truth_label"]["error_type"]
        breakdown[et] = breakdown.get(et, 0) + 1
    print("Error type breakdown:", breakdown)
    print(f"\nSaved to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()