"""
Entity Extraction v2 for Stage A
---------------------------------
Extracts medications, dosages/measurements, diagnoses, and procedures
from medical transcript text.

Key improvements over v1 (dataset/scripts/extract_entities.py):
- Medications: scanned via RxNorm lexicon n-grams instead of hardcoded list
- Dosages: associated with nearby medication (within ~8 tokens); unassociated
  quantities become "measurement" entities
- Diagnoses: expanded header set (ASSESSMENT, IMPRESSION, FINAL DIAGNOSIS, etc.)
  with numbered/comma list splitting
- Procedures: new extraction from PROCEDURE/OPERATION headers
- ANTI-LEAK: no patterns mirror injection templates; no special-casing
"""

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths — works in both dev layout (src/backend/stage_a/) and Lambda zip
# (backend/stage_a/).
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent

def _find_project_root() -> Path:
    """Walk up from this file to find the directory containing dataset/reference/."""
    d = _THIS_DIR
    for _ in range(5):
        d = d.parent
        if (d / "dataset" / "reference").is_dir():
            return d
    # fallback: assume dev layout
    return _THIS_DIR.parent.parent.parent

_PROJECT_ROOT = _find_project_root()
_LEXICON_PATH = _PROJECT_ROOT / "dataset" / "reference" / "rxnorm_lexicon.json"

# ---------------------------------------------------------------------------
# Stoplist — common English words that collide with RxNorm entries.
# Built by scanning all 270 transcripts against the full lexicon during
# development, sorting by frequency, and manually reviewing the top matches.
# Documented in STAGE_A_REPORT.md.
# ---------------------------------------------------------------------------
MEDICATION_STOPLIST: set[str] = {
    # Very common English words that happen to be RxNorm entries
    "a", "an", "as", "at", "be", "by", "do", "go", "he", "if", "in",
    "is", "it", "me", "my", "no", "of", "on", "or", "so", "to", "up",
    "we", "ice", "oil", "gas", "air", "iron",  # element/substance ambiguity
    "lead", "gold", "silver", "tin", "rice", "corn", "milk", "water",
    "bone", "skin", "fat", "fiber", "salt", "sugar", "starch",
    "normal", "solution", "compound", "complex", "factor",
    "general", "single", "double", "triple",
    # Temporal/common dictation words that collide with brand names
    "today", "yesterday", "tomorrow", "daily", "nightly", "weekly",
    "morning", "evening", "present", "current",
    # Body-related terms that are also supplement/drug names
    "calcium", "zinc", "copper", "selenium", "chromium", "manganese",
    "biotin", "niacin", "thiamine", "riboflavin",
    # Common medical words that are also RxNorm entries
    "oxygen", "nitrogen", "carbon", "hydrogen", "helium",
    "alcohol", "glycerin", "lanolin", "petrolatum", "paraffin",
    "cellulose", "gelatin", "lactose", "dextrose", "sucrose",
    "saline",
}

# ---------------------------------------------------------------------------
# Dose pattern and units
# ---------------------------------------------------------------------------
DOSE_UNITS_WORD = r"mg|mcg|µg|g|kg|mL|ml|cc|units?|IU|mEq|tablets?|caps?(?:ules?)?"
DOSE_PATTERN = re.compile(
    rf"\b(\d+(?:\.\d+)?)\s*(%|(?:{DOSE_UNITS_WORD})\b)",
    re.IGNORECASE,
)
_FREQUENCY_PATTERN = re.compile(r"\b(three times|four times|once daily|twice daily|\d+\s+times\s+(?:daily|per day)|q\d{1,2}h|daily|weekly|nightly|once|twice|bid|tid|qid|qhs|prn)\b", re.I)
_ROUTE_PATTERN = re.compile(r"\b(oral(?:ly)?|po|intravenous|iv|intramuscular|im|subcutaneous|sc|topical|inhaled|by mouth)\b", re.I)
_DURATION_PATTERN = re.compile(r"\bfor\s+(\d+)\s+(days?|weeks?|months?)\b", re.I)
_BP_PATTERN = re.compile(r"\b(?:BP|blood pressure)\s*[:=]?\s*(\d{2,3})\s*/\s*(\d{2,3})(?:\s*mm\s*Hg)?\b", re.I)
_TEMP_PATTERN = re.compile(r"\b(?:temperature|temp)\s*[:=]?\s*(\d{2,3}(?:\.\d+)?)\s*°?\s*([FC])\b", re.I)
_VITAL_PATTERN = re.compile(r"\b(pulse|heart rate|respiratory rate|respirations|oxygen saturation|SpO2|weight|height)\s*[:=]?\s*(\d{1,4}(?:\.\d+)?)\s*(bpm|breaths?/min|%|kg|lb|cm|mm|inches|in)?(?=\s|[.,;]|$)", re.I)

# ---------------------------------------------------------------------------
# Diagnosis headers (expanded set)
# ---------------------------------------------------------------------------
DIAGNOSIS_HEADERS = [
    "PREOPERATIVE DIAGNOSIS",
    "PREOPERATIVE DIAGNOSES",
    "POSTOPERATIVE DIAGNOSIS",
    "POSTOPERATIVE DIAGNOSES",
    "ADMITTING DIAGNOSIS",
    "ADMITTING DIAGNOSES",
    "DISCHARGE DIAGNOSIS",
    "DISCHARGE DIAGNOSES",
    "PRINCIPAL DIAGNOSIS",
    "PRINCIPAL DIAGNOSES",
    "FINAL DIAGNOSIS",
    "FINAL DIAGNOSES",
    "DIAGNOSIS",
    "DIAGNOSES",
    "ASSESSMENT",
    "IMPRESSION",
]

# ---------------------------------------------------------------------------
# Procedure headers
# ---------------------------------------------------------------------------
PROCEDURE_HEADERS = [
    "PROCEDURES PERFORMED",
    "PROCEDURE PERFORMED",
    "PROCEDURES",
    "PROCEDURE",
    "OPERATIONS PERFORMED",
    "OPERATION PERFORMED",
    "OPERATIONS",
    "OPERATION",
    "NAME OF PROCEDURE",
    "NAME OF OPERATION",
]

SECTION_HEADERS = sorted(set(
    DIAGNOSIS_HEADERS
    + PROCEDURE_HEADERS
    + [
        "ANESTHESIA",
        "BLOOD LOSS",
        "COMPLICATIONS",
        "DESCRIPTION",
        "FINDINGS",
        "HISTORY",
        "INDICATION",
        "INDICATIONS",
        "MEDICATIONS",
        "PLAN",
        "SAMPLES",
        "SPECIMENS",
        "TECHNIQUE",
    ]
), key=len, reverse=True)

_NEXT_SECTION_PATTERN = "|".join(re.escape(h) for h in SECTION_HEADERS)
_NEXT_SECTION_LOOKAHEAD = (
    rf"(?=(?:\n|,|;\s*|\.\s+)(?:{_NEXT_SECTION_PATTERN})\s*[:,]|"
    rf"\n\s*\n|$)"
)


# ===========================================================================
# Lexicon loader
# ===========================================================================
_lexicon_cache: dict | None = None


def load_lexicon(path: Path | None = None) -> dict:
    """Load the RxNorm lexicon (lowercase name -> {rxcui, tty})."""
    global _lexicon_cache
    if _lexicon_cache is not None:
        return _lexicon_cache
    p = path or _LEXICON_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"RxNorm lexicon not found at {p}. "
            "Run scripts/build_reference_data.py first."
        )
    with open(p, encoding="utf-8") as f:
        _lexicon_cache = json.load(f)
    return _lexicon_cache


def reset_lexicon_cache():
    """Clear the cached lexicon (useful for testing)."""
    global _lexicon_cache
    _lexicon_cache = None


# ===========================================================================
# Medication extraction
# ===========================================================================

def _build_token_spans(text: str) -> list[tuple[str, int, int]]:
    """Tokenize text into (token, start, end) triples.
    
    Splits on whitespace and punctuation boundaries while preserving offsets.
    """
    spans = []
    # Include digits and common product-name punctuation (e.g. B-12, XR).
    # Preserve original offsets; NFKC is used only for lookup keys.
    for m in re.finditer(r"[A-Za-z0-9]+(?:['’./-][A-Za-z0-9]+)*", text):
        spans.append((m.group(), m.start(), m.end()))
    return spans


def normalize_medication_key(value: str) -> str:
    """Normalize safe Unicode/spacing differences without removing name punctuation."""
    value = unicodedata.normalize("NFKC", value).replace("’", "'")
    return re.sub(r"\s+", " ", value).strip().casefold()


_CONTEXT_PATTERNS = (
    ("allergy", re.compile(r"\b(allerg(?:y|ic|ies)|adverse reaction|reaction to)\b", re.I)),
    ("negated", re.compile(r"\b(denies|denied|not taking|does not take|never took|without)\b", re.I)),
    ("discontinued", re.compile(r"\b(discontinued|stopped|held|no longer taking)\b", re.I)),
    ("planned", re.compile(r"\b(consider|considered|may start|plan to start|will start)\b", re.I)),
    ("historical", re.compile(r"\b(previously|formerly|used to take|history of|past medication)\b", re.I)),
)


def _mention_context(text: str, start: int, end: int) -> tuple[str, str]:
    """Return a conservative assertion label and section label for a mention."""
    left = max(text.rfind(".", 0, start), text.rfind(";", 0, start), text.rfind("\n", 0, start)) + 1
    right_candidates = [p for p in (text.find(".", end), text.find(";", end), text.find("\n", end)) if p >= 0]
    right = min(right_candidates) if right_candidates else len(text)
    clause = text[left:right]
    local_start = start - left
    prefix = clause[max(0, local_start - 70):local_start]
    status = "mentioned"
    for label, pattern in _CONTEXT_PATTERNS:
        if pattern.search(prefix):
            status = label
            break
    section = "unknown"
    for line in reversed(text[:start].splitlines()):
        match = re.match(r"\s*([A-Za-z][A-Za-z /_-]{1,40})\s*:\s*", line)
        if match:
            heading = re.sub(r"\s+", " ", match.group(1)).strip().casefold()
            if any(x in heading for x in ("allerg", "past", "history")):
                section = "historical" if "past" in heading or "history" in heading else "allergy"
            elif any(x in heading for x in ("medication", "meds", "plan")):
                section = "current_medications" if "med" in heading else "plan"
            else:
                section = heading
            break
    return status, section


def extract_medications(text: str, lexicon: dict | None = None) -> list[dict]:
    """Extract medication mentions by scanning 1-to-3-word n-grams against
    the RxNorm lexicon.
    
    Returns list of {entity_type, text, span, rxcui, tty, source}.
    """
    if lexicon is None:
        lexicon = load_lexicon()

    token_spans = _build_token_spans(text)
    medications = []
    seen_spans: set[tuple[int, int]] = set()

    # Scan n-grams from longest to shortest to prefer multi-word matches
    for n in (3, 2, 1):
        for i in range(len(token_spans) - n + 1):
            tokens = token_spans[i:i + n]
            # Build the candidate string from the original text
            start = tokens[0][1]
            end = tokens[-1][2]
            candidate = normalize_medication_key(text[start:end])

            # Skip if this span overlaps with an already-found medication
            if any(
                not (end <= s[0] or start >= s[1])
                for s in seen_spans
            ):
                continue

            # Check stoplist
            # Common-word collisions are suppressed only when isolated and
            # unsupported by prescription context. Medication cues can rescue
            # legitimate entries such as supplements and common substances.
            context = text[max(0, start - 48):min(len(text), end + 48)]
            med_cue = re.search(r"\b(take|taking|prescribed|started|start|dose|tablet|capsule|mg|mcg|\d+\s*(?:mg|mcg|g|ml|iu))\b", context, re.I)
            if candidate == "today" or (candidate in MEDICATION_STOPLIST and not med_cue):
                continue

            # Look up in lexicon
            entry = lexicon.get(candidate)
            if entry is not None:
                status, section = _mention_context(text, start, end)
                medications.append({
                    "entity_type": "medication",
                    "text": text[start:end],
                    "span": [start, end],
                    "rxcui": entry["rxcui"],
                    "tty": entry["tty"],
                    "source": "rxnorm_lexicon",
                    "assertion_status": status,
                    "section": section,
                })
                seen_spans.add((start, end))

    # Sort by span start
    medications.sort(key=lambda e: e["span"][0])
    return medications


# ===========================================================================
# Dosage / measurement extraction
# ===========================================================================

def extract_dosages_and_measurements(
    text: str,
    medications: list[dict],
    association_window: int = 8,
) -> tuple[list[dict], list[dict]]:
    """Extract dosage mentions and classify them as drug-associated doses
    or standalone measurements.

    A dose is associated with a medication only if the medication appears
    in the same clause within approximately `association_window` tokens.

    Returns (dosages, measurements).
    """
    dosages = []
    measurements = []

    # Structured vital signs need patterns of their own (notably BP, which
    # has no unit). Keep the exact source span and avoid a second generic hit.
    structured_spans: set[tuple[int, int]] = set()
    for pattern, kind in ((_BP_PATTERN, "blood_pressure"), (_TEMP_PATTERN, "temperature"), (_VITAL_PATTERN, "vital_sign")):
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(not (end <= a or start >= b) for a, b in structured_spans):
                continue
            structured_spans.add((start, end))
            if kind == "blood_pressure":
                value, unit = f"{match.group(1)}/{match.group(2)}", "mmHg"
            elif kind == "temperature":
                value, unit = match.group(1), match.group(2).upper()
            else:
                value, unit = match.group(2), (match.group(3) or "").lower()
            measurements.append({"entity_type": "measurement", "text": match.group(),
                "span": [start, end], "value": value, "unit": unit,
                "measurement_type": kind, "source": "vital_pattern"})

    token_spans = _build_token_spans(text)

    for match in DOSE_PATTERN.finditer(text):
        dose_start = match.start()
        dose_end = match.end()
        if any(not (dose_end <= a or dose_start >= b) for a, b in structured_spans):
            continue
        value = float(match.group(1))
        unit = match.group(2).lower()

        # Find the token index closest to this dose
        dose_token_idx = None
        for idx, (_, ts, te) in enumerate(token_spans):
            if ts <= dose_start <= te or ts <= dose_end <= te:
                dose_token_idx = idx
                break
            if ts > dose_end:
                dose_token_idx = idx
                break
        if dose_token_idx is None:
            dose_token_idx = len(token_spans) - 1

        # Check for clause boundary (period, semicolon, newline) between
        # dose and candidate medication
        def _same_clause(med_start: int, med_end: int) -> bool:
            lo = min(med_start, dose_start)
            hi = max(med_end, dose_end)
            segment = text[lo:hi]
            # Clause breakers
            if re.search(r"[.;]\s", segment):
                return False
            if "\n\n" in segment:
                return False
            return True

        # Find nearest medication within token window
        associated_med = None
        best_distance = association_window + 1
        for med in medications:
            med_start, med_end = med["span"]
            # Token distance: find med's token index
            med_token_idx = None
            for idx, (_, ts, te) in enumerate(token_spans):
                if ts <= med_start < te or (ts >= med_start and ts < med_end):
                    med_token_idx = idx
                    break
            if med_token_idx is None:
                continue
            distance = abs(dose_token_idx - med_token_idx)
            if distance <= association_window and distance < best_distance:
                if _same_clause(med_start, med_end):
                    best_distance = distance
                    associated_med = med

        entity = {
            "text": match.group(),
            "span": [dose_start, dose_end],
            "value": value,
            "unit": unit,
        }

        if associated_med is not None:
            entity["entity_type"] = "dosage"
            entity["associated_medication"] = associated_med["text"]
            entity["associated_medication_span"] = associated_med["span"]
            # Limit instruction capture to this medication's part of the
            # clause. This avoids copying one drug's route/frequency onto the
            # next drug when several are mentioned together.
            clause_left = max(text.rfind(".", 0, dose_start), text.rfind(";", 0, dose_start), text.rfind("\n", 0, dose_start)) + 1
            clause_rights = [p for p in (text.find(".", dose_end), text.find(";", dose_end), text.find("\n", dose_end)) if p >= 0]
            clause_right = min(clause_rights) if clause_rights else len(text)
            med_start, med_end = associated_med["span"]
            earlier_meds = [m["span"][1] for m in medications if m is not associated_med and clause_left <= m["span"][0] < med_start]
            later_meds = [m["span"][0] for m in medications if m is not associated_med and med_start < m["span"][0] < clause_right]
            if med_end <= dose_start:
                instruction_start = max([clause_left, med_end] + earlier_meds)
                instruction_end = min([clause_right] + later_meds)
            else:
                instruction_start = max(clause_left, dose_start)
                instruction_end = min([clause_right, med_start] + later_meds)
            instruction_text = text[instruction_start:instruction_end]
            instruction_attrs = {}
            for name, pattern in (("frequency", _FREQUENCY_PATTERN), ("route", _ROUTE_PATTERN), ("duration", _DURATION_PATTERN)):
                instruction_match = pattern.search(instruction_text)
                if instruction_match:
                    instruction_attrs[name] = {"text": instruction_match.group(), "span": [instruction_start + instruction_match.start(), instruction_start + instruction_match.end()]}
            if instruction_attrs:
                entity["instructions"] = instruction_attrs
            dosages.append(entity)
        else:
            entity["entity_type"] = "measurement"
            measurements.append(entity)

    return dosages, measurements


# ===========================================================================
# Diagnosis extraction
# ===========================================================================

def _split_diagnosis_text(raw: str) -> list[str]:
    """Split a raw diagnosis block into individual diagnoses.
    
    Handles:
    - Numbered lists: "1. Foo  2. Bar"
    - Comma-separated: "Foo, Bar"  
    - Period-separated lines
    """
    items = []

    # Try numbered list first: "1. ...", "2. ..."
    numbered = re.split(r"\d+\.\s+", raw)
    if len(numbered) > 2:  # First element is usually empty or preamble
        for item in numbered:
            cleaned = item.strip().rstrip(".,;")
            if cleaned and len(cleaned) > 3:
                items.append(cleaned)
        return items

    # Try splitting on newlines first
    lines = [ln.strip().rstrip(".,;") for ln in raw.split("\n") if ln.strip()]
    if len(lines) > 1:
        for line in lines:
            # Each line might be comma-separated
            parts = [p.strip().rstrip(".,;") for p in line.split(",")]
            for part in parts:
                if part and len(part) > 3:
                    items.append(part)
        return items

    # Single block — try comma split but be careful with commas in diagnoses
    # Only split on ", " followed by uppercase or a number
    parts = re.split(r",\s+(?=[A-Z0-9])", raw)
    for part in parts:
        cleaned = part.strip().rstrip(".,;")
        if cleaned and len(cleaned) > 3:
            items.append(cleaned)

    return items


def extract_diagnoses(text: str) -> list[dict]:
    """Extract diagnoses from text following diagnosis/assessment headers."""
    diagnoses = []
    seen_lower: set[str] = set()

    # Sort headers by length (longest first) to avoid substring matches
    sorted_headers = sorted(DIAGNOSIS_HEADERS, key=len, reverse=True)

    for header in sorted_headers:
        # Match header followed by colon/comma and content up to next header or double newline
        pattern = re.compile(
            # Do not let `\s*` consume the newline after an empty header; that
            # would swallow the following section as if it were diagnosis text.
            re.escape(header) + r"[ \t]*[:,][ \t]*(.*?)" + _NEXT_SECTION_LOOKAHEAD,
            re.IGNORECASE | re.DOTALL,
        )
        for match in pattern.finditer(text):
            raw = match.group(1).strip()
            block_start = match.start(1)
            
            items = _split_diagnosis_text(raw)
            for item in items:
                key = item.lower().strip()
                if key in seen_lower:
                    continue
                seen_lower.add(key)

                # Find the span of this item in the original text
                item_idx = text.find(item, block_start)
                if item_idx == -1:
                    # Fallback: use the block start
                    item_idx = block_start

                diagnoses.append({
                    "entity_type": "diagnosis",
                    "text": item,
                    "span": [item_idx, item_idx + len(item)],
                    "source": "header_extraction",
                    "header": header,
                })

    # Deduplicate by span
    unique = []
    seen_spans: set[tuple[int, int]] = set()
    for d in diagnoses:
        sp = tuple(d["span"])
        if sp not in seen_spans:
            seen_spans.add(sp)
            unique.append(d)

    return unique


# ===========================================================================
# Procedure extraction
# ===========================================================================

def extract_procedures(text: str) -> list[dict]:
    """Extract procedures from text following procedure/operation headers."""
    procedures = []
    seen_lower: set[str] = set()

    sorted_headers = sorted(PROCEDURE_HEADERS, key=len, reverse=True)

    for header in sorted_headers:
        pattern = re.compile(
            re.escape(header) + r"[ \t]*[:,][ \t]*(.*?)" + _NEXT_SECTION_LOOKAHEAD,
            re.IGNORECASE | re.DOTALL,
        )
        for match in pattern.finditer(text):
            raw = match.group(1).strip()
            block_start = match.start(1)

            # Split similarly to diagnoses
            items = _split_diagnosis_text(raw)
            for item in items:
                key = item.lower().strip()
                if key in seen_lower:
                    continue
                seen_lower.add(key)

                item_idx = text.find(item, block_start)
                if item_idx == -1:
                    item_idx = block_start

                procedures.append({
                    "entity_type": "procedure",
                    "text": item,
                    "span": [item_idx, item_idx + len(item)],
                    "source": "header_extraction",
                    "header": header,
                })

    # Deduplicate by span
    unique = []
    seen_spans: set[tuple[int, int]] = set()
    for p in procedures:
        sp = tuple(p["span"])
        if sp not in seen_spans:
            seen_spans.add(sp)
            unique.append(p)

    return unique


# ===========================================================================
# Main extraction entry point
# ===========================================================================

def extract_entities(text: str, lexicon: dict | None = None) -> list[dict]:
    """Extract all medical entities from transcript text.
    
    Returns a flat list of entity dicts, each with at minimum:
    - entity_type: "medication" | "dosage" | "measurement" | "diagnosis" | "procedure"
    - text: the extracted text
    - span: [start_offset, end_offset]
    """
    medications = extract_medications(text, lexicon=lexicon)
    dosages, measurements = extract_dosages_and_measurements(text, medications)
    diagnoses = extract_diagnoses(text)
    procedures = extract_procedures(text)

    entities = medications + dosages + measurements + diagnoses + procedures

    # Sort all entities by span start for consistent ordering
    entities.sort(key=lambda e: (e["span"][0], -e["span"][1]))
    return entities


# ===========================================================================
# Stoplist builder utility (run once during development)
# ===========================================================================

def build_stoplist_from_corpus(
    transcripts: list[str],
    lexicon: dict,
    top_n: int = 100,
) -> list[tuple[str, int]]:
    """Scan transcripts against the lexicon and return the most frequent
    matches, to help identify false-positive stoplist candidates.
    
    This function is used during development to build MEDICATION_STOPLIST.
    Not called at runtime.
    """
    counter: Counter = Counter()
    for text in transcripts:
        token_spans = _build_token_spans(text)
        for n in (1, 2, 3):
            for i in range(len(token_spans) - n + 1):
                tokens = token_spans[i:i + n]
                start = tokens[0][1]
                end = tokens[-1][2]
                candidate = text[start:end].lower()
                if candidate in lexicon:
                    counter[candidate] += 1

    return counter.most_common(top_n)


# ===========================================================================
# CLI for development/testing
# ===========================================================================
if __name__ == "__main__":
    import sys

    # Quick test with a sample text
    sample = (
        "PREOPERATIVE DIAGNOSIS: Chronic otitis media with effusion, hearing loss.\n"
        "POSTOPERATIVE DIAGNOSIS: Same.\n\n"
        "PROCEDURE PERFORMED: Bilateral myringotomy and tube placement.\n\n"
        "The patient was given Amoxicillin 500mg orally. "
        "Estimated blood loss was 300mL. "
        "Vital signs showed BP 120/80. "
        "Also prescribed Prednisone 10mg daily.\n"
    )

    try:
        entities = extract_entities(sample)
        print(f"Found {len(entities)} entities:")
        for e in entities:
            print(f"  [{e['entity_type']}] '{e['text']}' at {e['span']}")
    except FileNotFoundError as exc:
        print(f"Note: {exc}")
        print("Extraction requires the RxNorm lexicon. Run build_reference_data.py first.")
