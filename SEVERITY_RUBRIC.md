# Severity Scoring Rubric

## Overview
This rubric defines how Stage B determines final severity for flagged entities. Stage A provides a `severity_hint`, but Stage B makes the final determination based on clinical reasoning, compliance checks, and context.

---

## Severity Levels

| Severity | Definition | Action Required | SLA |
|----------|------------|-----------------|-----|
| **Critical** | Direct patient safety risk — wrong drug, contraindicated dosage, dangerous interaction, life-threatening error | Immediate alert (SNS), compliance officer review within 1 hour | 1 hour |
| **Medium** | Potential error needing clinical review — ambiguous dosing, terminology mismatch, compliance gap, non-standard but not immediately dangerous | Queue for review within 24 hours | 24 hours |
| **Low** | Minor inconsistency — missing codes, unrecognized codes with known mapping, phrasing variations unlikely to cause harm | Log for audit, batch review weekly | 7 days |

---

## Decision Matrix

### Critical (Patient Safety Risk)
| Flag Type | Criteria | Examples |
|-----------|----------|----------|
| `POTENTIAL_HALLUCINATION` | Drug contraindicated for diagnosis; drug-disease interaction; wrong drug for condition | Metformin for Type 1 DM; Metformin with eGFR <45; Phenytoin 1g IV load; St. John's Wort + SSRI |
| `DOSAGE_ANOMALY` | Dose exceeds maximum safe range; dose incompatible with patient factors (renal/hepatic function, age, weight) | Warfarin 15mg daily with INR 4.2; Prednisone 60mg daily for COPD; Insulin dose without parameters |
| `MISSING_CODE` | High-risk medication/herbal with no code AND known dangerous interaction | Herbal supplement interacting with prescribed med (serotonin syndrome risk) |

### Medium (Clinical Review Needed)
| Flag Type | Criteria | Examples |
|-----------|----------|----------|
| `POTENTIAL_HALLUCINATION` | Procedure/diagnosis mismatch; screening vs diagnostic code mismatch | Screening mammogram for breast cancer history |
| `DOSAGE_ANOMALY` | Ambiguous/unclear dosing regimen; taper not specified; sliding scale without parameters | Azithromycin "day 1 then 250mg days 2-5"; Prednisone "then taper"; Lispro sliding scale no units |
| `AMBIGUOUS_TERM` | Term maps to multiple codes; unclear intent; dosing frequency ambiguous | "Sliding scale with meals" no correction factor |
| `COMPLIANCE_MISMATCH` | ICD-10 does not justify CPT procedure; medical necessity gap | Procedure code not supported by diagnosis code |

### Low (Minor Inconsistency)
| Flag Type | Criteria | Examples |
|-----------|----------|----------|
| `UNRECOGNIZED_CODE` | Code not in vocabulary but correct mapping known; typo in code | Xyzal code `INVALID_CODE_123` → actual RxNorm `855486` |
| `AMBIGUOUS_TERM` | Minor terminology variation; synonym not in standard vocabulary | Brand vs generic name variation |
| `MISSING_CODE` | Low-risk entity (vitamin, supplement) with no code; no interaction risk | Daily multivitamin no RxNorm code |

---

## Scoring Algorithm (Pseudocode)

```python
def scoreSeverity(flaggedEntities):
    max_severity = "Low"
    reasons = []
    
    for flag in flaggedEntities:
        severity = determine_severity(flag)
        if severity_priority(severity) > severity_priority(max_severity):
            max_severity = severity
        reasons.append(f"{flag.entity_id}: {severity} - {flag.flag_reason}")
    
    return {
        "severity": max_severity,
        "reason": "; ".join(reasons)
    }

def determine_severity(flag):
    # Critical conditions
    if flag.flag_type == "POTENTIAL_HALLUCINATION" and is_safety_risk(flag):
        return "Critical"
    if flag.flag_type == "DOSAGE_ANOMALY" and is_dangerous_dose(flag):
        return "Critical"
    if flag.flag_type == "MISSING_CODE" and is_high_risk_interaction(flag):
        return "Critical"
    
    # Medium conditions
    if flag.flag_type in ["POTENTIAL_HALLUCINATION", "DOSAGE_ANOMALY", "AMBIGUOUS_TERM"] and needs_clinical_review(flag):
        return "Medium"
    if flag.flag_type == "COMPLIANCE_MISMATCH":
        return "Medium"
    
    # Low conditions
    return "Low"
```

---

## Helper Functions

| Function | Logic |
|----------|-------|
| `is_safety_risk(flag)` | Check if flagged medication contraindicated for patient's diagnosis (drug-disease interaction), or drug-drug interaction |
| `is_dangerous_dose(flag)` | Compare dose against max safe dose for medication; consider patient factors (renal function, age, weight) from context |
| `is_high_risk_interaction(flag)` | Check if missing-code entity has known dangerous interaction with other entities in transcript |
| `needs_clinical_review(flag)` | Ambiguous dosing, terminology mismatch, or compliance gap requiring clinician judgment |

---

## Compliance Mismatch Rules (Separate Check)

| Diagnosis (ICD-10) | Expected Procedures (CPT) | Mismatch If |
|--------------------|---------------------------|-------------|
| E10/E11 (Diabetes) | 83036 (HbA1c), 82947 (Glucose) | Billed for unrelated procedure (e.g., 71045 Chest X-ray without respiratory dx) |
| I10 (Hypertension) | 93000 (ECG), 80053 (CMP) | Billed for 77067 (Mammogram) without breast dx |
| J44.1 (COPD exacerbation) | 94060 (Spirometry), 71045 (Chest X-ray) | Billed for 93000 (ECG) without cardiac dx |
| Z85.3 (Breast cancer hx) | 77065/77066 (Diagnostic mammo) | Billed for 77067 (Screening mammo) |

**Note:** This is a representative rule set for prototype. Production would use full NCCI/Medical Policy mappings.