"use strict";

// This is a safety-review prototype, not a clinical decision support engine.
// Its strongest signal is provenance: compare an AI transcript to a trusted
// source transcript. Reference lists only help identify entities to review.

const MEDICATIONS = {
  acetaminophen: "RxNorm 161",
  amoxicillin: "RxNorm 723",
  aspirin: "RxNorm 1191",
  atorvastatin: "RxNorm 83367",
  clonidine: "RxNorm 2599",
  digoxin: "RxNorm 3407",
  fentanyl: "RxNorm 4337",
  hydralazine: "RxNorm 5470",
  hydroxyzine: "RxNorm 5553",
  ibuprofen: "RxNorm 5640",
  lisinopril: "RxNorm 29046",
  metformin: "RxNorm 6809",
  metoprolol: "RxNorm 6918",
  morphine: "RxNorm 7052",
  omeprazole: "RxNorm 7646",
  prednisone: "RxNorm 8640",
  warfarin: "RxNorm 8558",
  zofran: "RxNorm 26225"
};

const CONFUSABLE_PAIRS = [
  ["hydralazine", "hydroxyzine"],
  ["metformin", "metronidazole"],
  ["morphine", "hydromorphone"],
  ["fentanyl", "sufenta"],
  ["zofran", "zosyn"]
];

const DIAGNOSES = [
  "type 2 diabetes mellitus", "chronic kidney disease", "atrial fibrillation",
  "major depressive disorder", "hyperlipidemia", "hypertension", "pneumonia",
  "asthma", "heart failure", "otitis media"
];

const PROCEDURES = ["diagnostic laparoscopy", "cardiac catheterization", "upper endoscopy", "mri-guided biopsy"];
const dosagePattern = /\b(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml)\b/gi;

function textOf(value) {
  return typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
}

function findTerms(text, terms) {
  const lower = text.toLowerCase();
  return terms.filter((term) => new RegExp(`\\b${term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i").test(lower));
}

function dosages(text) {
  return [...text.matchAll(dosagePattern)].map((match) => ({ value: Number(match[1]), unit: match[2].toLowerCase(), phrase: match[0], index: match.index }));
}

function excerpt(text, index, length) {
  const start = Math.max(0, index - 50);
  const end = Math.min(text.length, index + length + 70);
  return `${start > 0 ? "…" : ""}${text.slice(start, end)}${end < text.length ? "…" : ""}`;
}

function flag({ severity, category, phrase, reason, evidence, confidence = "Review required" }) {
  return { id: `${category}-${phrase}-${reason}`.replace(/[^a-z0-9]+/gi, "-").toLowerCase(), severity, category, phrase, reason, evidence, confidence };
}

function analyse({ transcript, sourceTranscript = "" }) {
  const candidate = textOf(transcript);
  const source = textOf(sourceTranscript);
  const findings = [];
  const candidateMeds = findTerms(candidate, Object.keys(MEDICATIONS));
  const sourceMeds = findTerms(source, Object.keys(MEDICATIONS));
  const candidateDosages = dosages(candidate);
  const sourceDosages = dosages(source);
  const candidateDiagnoses = findTerms(candidate, DIAGNOSES);
  const sourceDiagnoses = findTerms(source, DIAGNOSES);
  const candidateProcedures = findTerms(candidate, PROCEDURES);
  const sourceProcedures = findTerms(source, PROCEDURES);

  if (!candidate) return { error: "Enter a transcript to analyse.", findings: [], summary: {} };

  for (const dose of candidateDosages) {
    if (dose.value === 0 || dose.value > 1000 || (dose.unit === "g" && dose.value > 2)) {
      findings.push(flag({
        severity: "Critical", category: "Dosage anomaly", phrase: dose.phrase,
        reason: "Dose falls outside this prototype's conservative formatting threshold.",
        evidence: excerpt(candidate, dose.index, dose.phrase.length), confidence: "Immediate clinician review"
      }));
    }
  }

  if (source) {
    candidateMeds.filter((med) => !sourceMeds.includes(med)).forEach((med) => {
      findings.push(flag({ severity: "High", category: "Unsupported medication", phrase: med,
        reason: "Medication appears in the candidate transcript but not in the supplied source transcript.",
        evidence: `Reference: ${MEDICATIONS[med]}. Provenance comparison: no matching source mention.`, confidence: "Strong provenance mismatch" }));
    });
    candidateDosages.forEach((dose, i) => {
      const sourceDose = sourceDosages[i];
      if (sourceDose && (sourceDose.value !== dose.value || sourceDose.unit !== dose.unit)) {
        findings.push(flag({ severity: "Critical", category: "Dosage mismatch", phrase: dose.phrase,
          reason: `Candidate dosage differs from source dosage ${sourceDose.phrase}.`,
          evidence: `Source: ${excerpt(source, sourceDose.index, sourceDose.phrase.length)}`, confidence: "Strong provenance mismatch" }));
      }
    });
    candidateDiagnoses.filter((item) => !sourceDiagnoses.includes(item)).forEach((item) => findings.push(flag({
      severity: "High", category: "Unsupported diagnosis", phrase: item,
      reason: "Diagnosis appears only in the candidate transcript.", evidence: "No matching diagnosis was found in the supplied source transcript.", confidence: "Strong provenance mismatch"
    })));
    candidateProcedures.filter((item) => !sourceProcedures.includes(item)).forEach((item) => findings.push(flag({
      severity: "High", category: "Unsupported procedure", phrase: item,
      reason: "Procedure appears only in the candidate transcript.", evidence: "No matching procedure was found in the supplied source transcript.", confidence: "Strong provenance mismatch"
    })));
  }

  for (const [one, two] of CONFUSABLE_PAIRS) {
    if (candidateMeds.includes(one) || candidateMeds.includes(two)) {
      const shown = candidateMeds.includes(one) ? one : two;
      const counterpart = shown === one ? two : one;
      findings.push(flag({ severity: "Medium", category: "Look-alike / sound-alike medication", phrase: shown,
        reason: `${shown} can be confused with ${counterpart}; confirm against the dictation source.`,
        evidence: `Medication terminology match: ${MEDICATIONS[shown] || "reference lookup pending"}.`, confidence: "Safety review signal" }));
    }
  }

  const unique = [...new Map(findings.map((item) => [item.id, item])).values()];
  const rank = { Critical: 0, High: 1, Medium: 2, Low: 3 };
  unique.sort((a, b) => rank[a.severity] - rank[b.severity]);
  const summary = { Critical: 0, High: 0, Medium: 0, Low: 0 };
  unique.forEach((item) => { summary[item.severity] += 1; });
  return { findings: unique, summary, entities: { medications: candidateMeds, dosages: candidateDosages.map((item) => item.phrase), diagnoses: candidateDiagnoses, procedures: candidateProcedures }, mode: source ? "source comparison" : "reference-assisted screening" };
}

module.exports = { analyse, dosages, findTerms };
