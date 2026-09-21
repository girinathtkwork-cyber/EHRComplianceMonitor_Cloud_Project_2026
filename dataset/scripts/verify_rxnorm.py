"""
RxNorm Verification Module
-----------------------------
Checks extracted medication names against the real RxNorm database
(National Library of Medicine's standardized drug vocabulary).

RxNorm API is free and requires no signup/API key. Full docs:
https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html

Endpoint used: rxcui.json?name=DRUGNAME
  - Returns a matching RxCUI (RxNorm Concept Unique Identifier) if the
    drug name is real and recognized.
  - Returns an empty idGroup if the name is not found - a strong signal
    the name is either mistranscribed or fabricated.

Run standalone with: python verify_rxnorm.py
Requires: pip install requests
"""

import json
import time
import requests

INPUT_JSON = "dataset/processed/mtsamples_with_entities.json"
OUTPUT_JSON = "dataset/processed/mtsamples_with_rxnorm.json"

RXNORM_BASE_URL = "https://rxnav.nlm.nih.gov/REST/rxcui.json"
RXNORM_APPROX_URL = "https://rxnav.nlm.nih.gov/REST/approximateTerm.json"

# Simple in-memory cache so we never look up the same drug name twice -
# saves API calls and speeds up repeated runs during development.
_cache = {}


def _try_exact_match(drug_name):
    """Exact/normalized match via rxcui.json. Fast, but strict - can miss
    real drugs due to formatting quirks (verified during testing: real drugs
    like Zofran, Penicillin, Percodan, Toradol, Navane, Sufenta all returned
    no match here despite being genuine, well-known medications)."""
    response = requests.get(RXNORM_BASE_URL, params={"name": drug_name}, timeout=5)
    response.raise_for_status()
    data = response.json()
    id_group = data.get("idGroup", {})
    return id_group.get("rxnormId")


def _try_approximate_match(drug_name):
    """
    Fallback fuzzy match via approximateTerm.json, per RxNorm's own
    documentation: this endpoint is specifically designed for cases where
    exact match fails to find genuine drugs due to formatting differences.
    """
    response = requests.get(RXNORM_APPROX_URL, params={"term": drug_name, "maxEntries": 1}, timeout=5)
    response.raise_for_status()
    data = response.json()
    candidates = data.get("approximateGroup", {}).get("candidate", [])
    if candidates:
        return [candidates[0]["rxcui"]]
    return None


def check_medication(drug_name):
    """
    Query RxNorm for a given drug name. Tries exact match first (fast);
    if that finds nothing, falls back to approximate/fuzzy match before
    concluding the drug genuinely doesn't exist. This two-step approach
    was added after testing showed exact-match alone produced false
    negatives on real drugs (Zofran, Penicillin, Percodan, Toradol,
    Navane, Sufenta) due to formatting strictness, not because those
    drugs don't exist.
    Returns a dict: { "exists": bool, "rxcui": str or None, "queried_name": str,
                       "match_type": "exact" | "approximate" | "none" }
    """
    if drug_name in _cache:
        return _cache[drug_name]

    try:
        rxnorm_ids = _try_exact_match(drug_name)
        match_type = "exact"

        if not rxnorm_ids:
            rxnorm_ids = _try_approximate_match(drug_name)
            match_type = "approximate"

        if rxnorm_ids:
            result = {"exists": True, "rxcui": rxnorm_ids[0], "queried_name": drug_name, "match_type": match_type}
        else:
            result = {"exists": False, "rxcui": None, "queried_name": drug_name, "match_type": "none"}

    except requests.RequestException as e:
        print(f"  [warning] RxNorm lookup failed for '{drug_name}': {e}")
        result = {"exists": None, "rxcui": None, "queried_name": drug_name, "error": str(e)}

    _cache[drug_name] = result
    return result


def main():
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        records = json.load(f)

    print(f"Loaded {len(records)} records")

    total_checked = 0
    total_not_found = 0

    for i, record in enumerate(records):
        medications = record["extracted_entities"]["medications"]
        rxnorm_results = []

        for med in medications:
            result = check_medication(med)
            rxnorm_results.append(result)
            total_checked += 1
            if result["exists"] is False:
                total_not_found += 1
            time.sleep(0.05)  # be polite to the free public API, avoid hammering it

        record["rxnorm_verification"] = rxnorm_results

        if (i + 1) % 50 == 0:
            print(f"  Processed {i + 1}/{len(records)} records...")

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

    print(f"\nTotal medication lookups performed: {total_checked}")
    print(f"Medications NOT found in RxNorm (flagged): {total_not_found}")

    match_type_counts = {"exact": 0, "approximate": 0, "none": 0}
    for r in records:
        for result in r.get("rxnorm_verification", []):
            mt = result.get("match_type", "none")
            match_type_counts[mt] = match_type_counts.get(mt, 0) + 1
    print(f"Match type breakdown: {match_type_counts}")
    print(f"Saved to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()