"""
Entity Extraction Module
--------------------------
Extracts candidate medical entities (medications, dosages, diagnoses) from
transcript text, to be verified downstream against RxNorm/ICD-10 (Day 5-6).

Design choice: starts with simple pattern-matching (regex + keyword lists)
rather than a heavy NLP library. This is a deliberate "start simple, measure,
upgrade only if needed" engineering decision - not a shortcut. Dosages are
structured enough for regex to handle well; drug names are checked against
a reference list (to be replaced by live RxNorm lookups in Day 5); diagnoses
are extracted from text following common diagnosis-indicating headers.

Run standalone with: python extract_entities.py
"""

import json
import re

INPUT_JSON = "dataset/processed/mtsamples_with_errors.json"
OUTPUT_JSON = "dataset/processed/mtsamples_with_entities.json"

# Dosage pattern: a number followed by a unit (mg, mcg, g, ml)
DOSAGE_PATTERN = re.compile(r"\b(\d+)\s?(mg|mcg|g|ml)\b", re.IGNORECASE)

# Reference drug list for candidate matching. This is intentionally the same
# list used in error injection (Day 2) PLUS common drugs likely to appear
# elsewhere in MTSamples - in Day 5 this gets replaced by real-time RxNorm
# lookups instead of a fixed local list.
KNOWN_DRUGS = [
    "clonidine", "Klonopin", "hydralazine", "hydroxyzine", "Celebrex", "Celexa",
    "Zantac", "Xanax", "Metformin", "Metronidazole", "Lamictal", "Lamisil",
    "Lasix", "Losec", "Prilosec", "Prinivil", "Coumadin", "Cardura", "Toradol",
    "Tegretol", "Zocor", "Zoloft", "Vicodin", "hydrocodone", "morphine",
    "hydromorphone", "Ativan", "Benadryl", "Percocet", "Percodan", "albuterol",
    "atenolol", "Prednisone", "prednisolone", "heparin", "Hespan", "insulin",
    "Humalog", "Tylenol", "acetaminophen", "ibuprofen", "aspirin", "Motrin",
    "Amoxicillin", "Penicillin", "Lisinopril", "Atorvastatin", "Omeprazole",
]

# Headers that typically precede a diagnosis statement in clinical dictation
DIAGNOSIS_HEADERS = [
    "PREOPERATIVE DIAGNOSIS", "PREOPERATIVE DIAGNOSES",
    "POSTOPERATIVE DIAGNOSIS", "POSTOPERATIVE DIAGNOSES",
    "DIAGNOSIS", "DIAGNOSES",
    "ADMITTING DIAGNOSIS", "DISCHARGE DIAGNOSIS",
]


def extract_dosages(text):
    """Return every dosage mention found, e.g. '500mg'."""
    matches = DOSAGE_PATTERN.findall(text)
    return [f"{value}{unit}" for value, unit in matches]


def extract_medications(text):
    """Return every known drug name found in the text (case-insensitive)."""
    found = []
    for drug in KNOWN_DRUGS:
        if re.search(r"\b" + re.escape(drug) + r"\b", text, re.IGNORECASE):
            found.append(drug)
    return found


def extract_diagnoses(text):
    """
    Extract the text immediately following a diagnosis header, up to a
    reasonable stopping point.

    Bug found during testing: MTSamples formats headers like
    'PREOPERATIVE DIAGNOSES:,  Chronic otitis media...' - note the colon
    AND comma back-to-back. The original pattern '[:,]' only consumed one
    separator character, so it failed to match this extremely common format
    at all (verified: 0 matches on a record known to contain a diagnosis).
    Fixed with '[:,]+' to consume one or more separator characters, and
    allowing the captured phrase to include one internal comma (diagnosis
    phrases are often themselves comma-separated, e.g. "effusion, hearing
    loss") before stopping at the next clause.
    """
    diagnoses = []
    for header in DIAGNOSIS_HEADERS:
        pattern = re.compile(
            re.escape(header) + r"[:,]+\s*([^,]+(?:,\s*[^,]+)?)",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            candidate = match.group(1).strip().rstrip(".")
            if candidate and len(candidate) > 3:
                diagnoses.append(candidate)
    # Dedupe: same diagnosis often appears under both PREOPERATIVE and
    # POSTOPERATIVE headers (genuinely the same condition stated twice),
    # and generic "DIAGNOSIS" can overlap-match inside "PREOPERATIVE DIAGNOSIS".
    # Preserve order while removing exact duplicates.
    seen = set()
    deduped = []
    for d in diagnoses:
        key = d.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(d)
    return deduped


def extract_entities(text):
    return {
        "medications": extract_medications(text),
        "dosages": extract_dosages(text),
        "diagnoses": extract_diagnoses(text),
    }


def main():
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        records = json.load(f)

    print(f"Loaded {len(records)} records")

    total_meds = total_dosages = total_diag = 0

    for record in records:
        entities = extract_entities(record["transcription"])
        record["extracted_entities"] = entities
        total_meds += len(entities["medications"])
        total_dosages += len(entities["dosages"])
        total_diag += len(entities["diagnoses"])

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

    print(f"Total medications found across dataset: {total_meds}")
    print(f"Total dosages found across dataset: {total_dosages}")
    print(f"Total diagnosis phrases found across dataset: {total_diag}")
    print(f"Records with zero medications found: {sum(1 for r in records if not r['extracted_entities']['medications'])}")
    print(f"\nSaved to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()