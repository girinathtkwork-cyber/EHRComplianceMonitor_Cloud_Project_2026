import json
import os
from typing import Dict, List, Any
from dataclasses import dataclass
from enum import Enum


class Severity(Enum):
    CRITICAL = "Critical"
    MEDIUM = "Medium"
    LOW = "Low"


class FlagType(Enum):
    UNRECOGNIZED_CODE = "UNRECOGNIZED_CODE"
    AMBIGUOUS_TERM = "AMBIGUOUS_TERM"
    POTENTIAL_HALLUCINATION = "POTENTIAL_HALLUCINATION"
    DOSAGE_ANOMALY = "DOSAGE_ANOMALY"
    MISSING_CODE = "MISSING_CODE"


@dataclass
class FlaggedEntity:
    entity_id: str
    flag_type: str
    flag_reason: str
    severity_hint: str
    context_window: str


@dataclass
class Entity:
    entity_id: str
    type: str
    text: str
    normalized: str
    code: str
    confidence: float
    start_char: int
    end_char: int


@dataclass
class StageAOutput:
    transcript_id: str
    timestamp: str
    transcript_text: str
    entities: List[Entity]
    flagged_entities: List[FlaggedEntity]


@dataclass
class SeverityResult:
    severity: str
    reason: str


SEVERITY_PRIORITY = {
    Severity.CRITICAL: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1
}

CRITICAL_DRUG_DISEASE = {
    "metformin": ["type 1 diabetes", "e10", "chronic kidney disease", "n18.3", "egfr < 45"],
    "warfarin": ["inr > 4"],
    "phenytoin": ["loading dose > 20mg/kg"],
    "prednisone": ["copd exacerbation > 40mg"],
    "st. john's wort": ["ssri", "escitalopram", "sertraline", "serotonin syndrome"],
}

DANGEROUS_DOSE_THRESHOLDS = {
    "warfarin": 10,
    "prednisone": 40,
    "phenytoin_loading": 20,
    "metformin_egfr": 45,
}

COMPLIANCE_MISMATCH_RULES = {
    "Z85.3": ["77065", "77066"],
    "E10.9": ["83036", "82947"],
    "E11.9": ["83036", "82947"],
    "I10": ["93000", "80053"],
    "J44.1": ["94060", "71045"],
}


def load_stage_a(filepath: str) -> StageAOutput:
    with open(filepath, 'r') as f:
        data = json.load(f)
    
    entities = [Entity(**e) for e in data.get("entities", [])]
    flagged = [FlaggedEntity(**f) for f in data.get("flagged_entities", [])]
    
    return StageAOutput(
        transcript_id=data["transcript_id"],
        timestamp=data["timestamp"],
        transcript_text=data["transcript_text"],
        entities=entities,
        flagged_entities=flagged
    )


def get_entity_by_id(entities: List[Entity], entity_id: str) -> Entity:
    for e in entities:
        if e.entity_id == entity_id:
            return e
    return None


def is_safety_risk(flag: FlaggedEntity, entities: List[Entity], transcript: str) -> bool:
    entity = get_entity_by_id(entities, flag.entity_id)
    if not entity or entity.type != "MEDICATION":
        return False
    
    med_name = entity.normalized.lower()
    transcript_lower = transcript.lower()
    
    for drug, conditions in CRITICAL_DRUG_DISEASE.items():
        if drug in med_name:
            for condition in conditions:
                if condition in transcript_lower:
                    return True
    
    return False


def is_dangerous_dose(flag: FlaggedEntity, entities: List[Entity]) -> bool:
    entity = get_entity_by_id(entities, flag.entity_id)
    if not entity or entity.type != "DOSAGE":
        return False
    
    dosage_text = entity.normalized.lower()
    med_entity = None
    for e in entities:
        if e.type == "MEDICATION":
            med_entity = e
            break
    
    if not med_entity:
        return False
    
    med_name = med_entity.normalized.lower()
    
    if "warfarin" in med_name:
        import re
        mg_match = re.search(r'(\d+(?:\.\d+)?)\s*mg', dosage_text)
        if mg_match and float(mg_match.group(1)) > DANGEROUS_DOSE_THRESHOLDS["warfarin"]:
            return True
    
    if "prednisone" in med_name:
        import re
        mg_match = re.search(r'(\d+(?:\.\d+)?)\s*mg', dosage_text)
        if mg_match and float(mg_match.group(1)) > DANGEROUS_DOSE_THRESHOLDS["prednisone"]:
            return True
    
    if "phenytoin" in med_name and "load" in dosage_text:
        import re
        mg_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:mg|g)', dosage_text)
        if mg_match:
            val = float(mg_match.group(1))
            if "g" in dosage_text:
                val *= 1000
            if val > DANGEROUS_DOSE_THRESHOLDS["phenytoin_loading"] * 70:
                return True
    
    if "metformin" in med_name:
        for e in entities:
            if e.type == "LAB_VALUE" and "egfr" in e.normalized.lower():
                import re
                egfr_match = re.search(r'(\d+(?:\.\d+)?)', e.normalized)
                if egfr_match and float(egfr_match.group(1)) < DANGEROUS_DOSE_THRESHOLDS["metformin_egfr"]:
                    return True
    
    return False


def is_high_risk_interaction(flag: FlaggedEntity, entities: List[Entity]) -> bool:
    if flag.flag_type != "MISSING_CODE":
        return False
    
    entity = get_entity_by_id(entities, flag.entity_id)
    if not entity:
        return False
    
    med_name = entity.normalized.lower()
    if "st. john" in med_name or "hypericum" in med_name:
        for e in entities:
            if e.type == "MEDICATION":
                other_med = e.normalized.lower()
                if any(ssri in other_med for ssri in ["escitalopram", "sertraline", "fluoxetine", "paroxetine", "citalopram"]):
                    return True
    
    return False


def needs_clinical_review(flag: FlaggedEntity, entities: List[Entity]) -> bool:
    if flag.flag_type == "POTENTIAL_HALLUCINATION":
        entity = get_entity_by_id(entities, flag.entity_id)
        if entity and entity.type == "PROCEDURE":
            return "screening" in flag.flag_reason.lower() and "history" in flag.context_window.lower()
        return True
    
    if flag.flag_type == "DOSAGE_ANOMALY":
        ambiguous_keywords = ["ambiguous", "unclear", "not specified", "taper", "sliding scale"]
        return any(kw in flag.flag_reason.lower() for kw in ambiguous_keywords)
    
    if flag.flag_type == "AMBIGUOUS_TERM":
        return True
    
    return False


def check_compliance_mismatch(entities: List[Entity]) -> List[str]:
    mismatches = []
    diagnoses = [e for e in entities if e.type == "DIAGNOSIS" and e.code]
    procedures = [e for e in entities if e.type == "PROCEDURE" and e.code]
    
    for dx in diagnoses:
        dx_code = dx.code.split(".")[0]
        if dx_code in COMPLIANCE_MISMATCH_RULES:
            expected_procs = COMPLIANCE_MISMATCH_RULES[dx_code]
            for proc in procedures:
                if proc.code not in expected_procs:
                    mismatches.append(f"Diagnosis {dx.code} ({dx.normalized}) may not justify procedure {proc.code} ({proc.normalized})")
    
    return mismatches


def determine_severity(flag: FlaggedEntity, entities: List[Entity], transcript: str) -> Severity:
    if flag.flag_type == "POTENTIAL_HALLUCINATION" and is_safety_risk(flag, entities, transcript):
        return Severity.CRITICAL
    
    if flag.flag_type == "DOSAGE_ANOMALY" and is_dangerous_dose(flag, entities):
        return Severity.CRITICAL
    
    if flag.flag_type == "MISSING_CODE" and is_high_risk_interaction(flag, entities):
        return Severity.CRITICAL
    
    if flag.flag_type in ["POTENTIAL_HALLUCINATION", "DOSAGE_ANOMALY", "AMBIGUOUS_TERM"] and needs_clinical_review(flag, entities):
        return Severity.MEDIUM
    
    if flag.flag_type == "COMPLIANCE_MISMATCH":
        return Severity.MEDIUM
    
    return Severity.LOW


def score_severity(stage_a: StageAOutput) -> SeverityResult:
    if not stage_a.flagged_entities:
        return SeverityResult(severity="Low", reason="No flagged entities")
    
    max_severity = Severity.LOW
    reasons = []
    
    for flag in stage_a.flagged_entities:
        severity = determine_severity(flag, stage_a.entities, stage_a.transcript_text)
        if SEVERITY_PRIORITY[severity] > SEVERITY_PRIORITY[max_severity]:
            max_severity = severity
        reasons.append(f"{flag.entity_id} ({flag.flag_type}): {severity.value} - {flag.flag_reason}")
    
    compliance_mismatches = check_compliance_mismatch(stage_a.entities)
    if compliance_mismatches:
        if SEVERITY_PRIORITY[Severity.MEDIUM] > SEVERITY_PRIORITY[max_severity]:
            max_severity = Severity.MEDIUM
        reasons.extend([f"COMPLIANCE: {m}" for m in compliance_mismatches])
    
    return SeverityResult(
        severity=max_severity.value,
        reason="; ".join(reasons)
    )


def test_all_samples(samples_dir: str):
    print("=" * 80)
    print("SEVERITY SCORING TEST RESULTS")
    print("=" * 80)
    
    results = []
    for filename in sorted(os.listdir(samples_dir)):
        if filename.endswith(".json"):
            filepath = os.path.join(samples_dir, filename)
            stage_a = load_stage_a(filepath)
            result = score_severity(stage_a)
            results.append((filename, stage_a.transcript_id, result))
            
            print(f"\n{filename}")
            print(f"  Transcript ID: {stage_a.transcript_id}")
            print(f"  Severity: {result.severity}")
            print(f"  Reason: {result.reason[:200]}...")
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    severity_counts = {"Critical": 0, "Medium": 0, "Low": 0}
    for filename, tid, result in results:
        severity_counts[result.severity] += 1
        print(f"  {filename}: {result.severity}")
    
    print(f"\n  Critical: {severity_counts['Critical']}")
    print(f"  Medium: {severity_counts['Medium']}")
    print(f"  Low: {severity_counts['Low']}")


if __name__ == "__main__":
    samples_dir = "/home/yoge/VIT/AWS/EHRComplianceMonitor_Cloud_Project_2026/dataset/fake_samples"
    test_all_samples(samples_dir)