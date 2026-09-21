"use strict";

// This is a safety-review prototype, not a clinical decision support engine.
// Its strongest signal is provenance: compare an AI transcript to a trusted
// source transcript. Reference lists only help identify entities to review.

const fs = require("node:fs");
const path = require("node:path");
const RULES = JSON.parse(fs.readFileSync(path.join(__dirname, "config/review-rules.json"), "utf8"));
const dosagePattern = new RegExp(`\\b(\\d+(?:\\.\\d+)?)\\s*(${RULES.dosageUnits.join("|")})\\b`, "gi");

function loadReferenceTerms() {
  const source = path.resolve(__dirname, "../../dataset/processed/mtsamples_with_rxnorm.json");
  try {
    const records = JSON.parse(fs.readFileSync(source, "utf8"));
    const terms = new Map();
    records.forEach((record) => {
      const medicines = record.extracted_entities?.medications || [];
      const verified = record.rxnorm_verification || [];
      medicines.forEach((medicine, index) => {
        const result = verified[index];
        if (result?.exists) terms.set(medicine.toLowerCase(), { name: medicine, rxcui: result.rxcui, matchType: result.match_type });
      });
    });
    return terms;
  } catch (error) {
    console.warn(`Reference dataset unavailable: ${error.message}`);
    return new Map();
  }
}

const REFERENCE_TERMS = loadReferenceTerms();

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
  const candidateMeds = findTerms(candidate, [...REFERENCE_TERMS.keys()]);
  const sourceMeds = findTerms(source, [...REFERENCE_TERMS.keys()]);
  const candidateDosages = dosages(candidate);
  const sourceDosages = dosages(source);
  const candidateDiagnoses = labelledStatements(candidate, RULES.statementLabels.diagnosis);
  const sourceDiagnoses = labelledStatements(source, RULES.statementLabels.diagnosis);
  const candidateProcedures = labelledStatements(candidate, RULES.statementLabels.procedure);
  const sourceProcedures = labelledStatements(source, RULES.statementLabels.procedure);

  if (!candidate) return { error: "Enter a transcript to analyse.", findings: [], summary: {} };

  for (const dose of candidateDosages) {
    const rule = dose.value === 0 ? RULES.outlierRules.zeroDose : dose.unit === "g" && dose.value > RULES.outlierRules.gramMaximum.value ? RULES.outlierRules.gramMaximum : dose.value > RULES.outlierRules.defaultMaximum.value ? RULES.outlierRules.defaultMaximum : null;
    if (rule) {
      findings.push(flag({
        severity: rule.severity, category: "Dosage anomaly", phrase: dose.phrase,
        reason: rule.message,
        evidence: excerpt(candidate, dose.index, dose.phrase.length), confidence: "Immediate clinician review"
      }));
    }
  }

  if (source) {
    candidateMeds.filter((med) => !sourceMeds.includes(med)).forEach((med) => {
      findings.push(flag({ severity: "High", category: "Unsupported medication", phrase: med,
        reason: "Medication appears in the candidate transcript but not in the supplied source transcript.",
        evidence: `RxNorm reference match: ${REFERENCE_TERMS.get(med)?.rxcui || "available"}. Provenance comparison: no matching source mention.`, confidence: "Strong provenance mismatch" }));
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

  const unique = [...new Map(findings.map((item) => [item.id, item])).values()];
  const rank = { Critical: 0, High: 1, Medium: 2, Low: 3 };
  unique.sort((a, b) => rank[a.severity] - rank[b.severity]);
  const summary = { Critical: 0, High: 0, Medium: 0, Low: 0 };
  unique.forEach((item) => { summary[item.severity] += 1; });
  return { findings: unique, summary, entities: { medications: candidateMeds.map((term) => ({ name: REFERENCE_TERMS.get(term)?.name || term, rxcui: REFERENCE_TERMS.get(term)?.rxcui || null })), dosages: candidateDosages.map((item) => item.phrase), diagnoses: candidateDiagnoses, procedures: candidateProcedures }, mode: source ? "source comparison" : "reference-assisted screening", reference: { medicationTermsLoaded: REFERENCE_TERMS.size, source: "processed MTSamples RxNorm verification output" } };
}

function labelledStatements(text, labelPattern) {
  if (!text) return [];
  const regex = new RegExp(`(?:${labelPattern})\\s*[:,-]\\s*([^.!?]+)`, "gi");
  return [...text.matchAll(regex)].map((match) => match[1].trim().toLowerCase()).filter(Boolean);
}

module.exports = { analyse, dosages, findTerms, labelledStatements };
