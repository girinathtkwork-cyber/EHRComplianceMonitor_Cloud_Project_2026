(function (root) {
  "use strict";

  const MAX_INPUT_BYTES = 10_000_000;
  const MAX_RESOURCES = 20_000;
  const MAX_OUTPUT_CHARS = 100_000;
  const SUPPORTED_TYPES = new Set([
    "Patient", "Condition", "AllergyIntolerance", "MedicationRequest",
    "MedicationStatement", "MedicationAdministration", "Observation",
    "Encounter", "Procedure", "Immunization", "CarePlan"
  ]);

  function fail(message) { throw new Error(message); }
  function clean(value) {
    return String(value ?? "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();
  }
  function codingText(concept) {
    const codings = Array.isArray(concept?.coding) ? concept.coding : [];
    const labels = codings.map((coding) => clean(coding.display || coding.code)).filter(Boolean);
    return clean(concept?.text) || [...new Set(labels)].join(" / ");
  }
  function quantity(value) {
    if (!value || typeof value !== "object") return "";
    const amount = clean(value.value);
    const unit = clean(value.unit || value.code);
    return [amount, unit].filter(Boolean).join(" ");
  }
  function dateText(resource) {
    const value = resource.onsetDateTime || resource.recordedDate || resource.authoredOn || resource.effectiveDateTime || resource.occurrenceDateTime || resource.performedDateTime || resource.date || resource.created || resource.period?.start || "";
    return clean(value).slice(0, 30);
  }
  function medicationText(resource) {
    const name = codingText(resource.medicationCodeableConcept) || clean(resource.medicationReference?.display) || "Medication name unavailable";
    const dosage = (resource.dosageInstruction || []).map((item) => {
      const dose = quantity(item.doseAndRate?.[0]?.doseQuantity);
      const route = codingText(item.route);
      const repeat = item.timing?.repeat || {};
      const frequency = item.timing?.code ? codingText(item.timing.code)
        : repeat.frequency != null ? `${repeat.frequency} times per ${repeat.period || 1} ${repeat.periodUnit || "period"}` : "";
      return [dose, route, frequency].filter(Boolean).join(" ");
    }).filter(Boolean).join("; ");
    const status = clean(resource.status);
    return [name, dosage, status && `status ${status}`, dateText(resource) && `date ${dateText(resource)}`].filter(Boolean).join(" — ");
  }
  function summarize(bundle) {
    if (!bundle || typeof bundle !== "object" || Array.isArray(bundle)) fail("FHIR input must be one JSON object.");
    if (bundle.resourceType !== "Bundle") fail("Expected a FHIR Bundle (resourceType: Bundle).");
    if (bundle.type && !["collection", "document", "message", "searchset", "transaction", "batch"].includes(bundle.type)) fail("Unsupported FHIR Bundle type.");
    const entries = Array.isArray(bundle.entry) ? bundle.entry : [];
    if (!entries.length) fail("FHIR Bundle has no resources to import.");
    if (entries.length > MAX_RESOURCES) fail(`FHIR Bundle exceeds the ${MAX_RESOURCES.toLocaleString()} resource limit.`);
    const resources = entries.map((entry) => entry?.resource).filter((resource) => resource && typeof resource === "object");
    const patients = resources.filter((resource) => resource.resourceType === "Patient");
    if (patients.length !== 1) fail(patients.length ? "FHIR Bundle must contain exactly one Patient; multi-patient bundles are rejected." : "FHIR Bundle must contain one Patient resource.");
    const patientId = clean(patients[0].id);
    const patientEntry = entries.find((entry) => entry?.resource === patients[0]);
    const patientAliases = new Set([patientId && `Patient/${patientId}`, clean(patientEntry?.fullUrl)].filter(Boolean));
    const patientRefs = resources.filter((resource) => resource.resourceType !== "Patient").map((resource) => resource.subject?.reference || resource.patient?.reference).filter(Boolean);
    if (patientRefs.some((reference) => !patientAliases.has(reference) && !(patientId && reference.endsWith(`/Patient/${patientId}`)))) {
      fail("FHIR Bundle contains a resource referencing a different patient; import was stopped.");
    }
    const sections = [];
    const add = (title, lines) => { if (lines.length) sections.push(`${title}:\n${lines.map((line) => `- ${line}`).join("\n")}`); };
    const conditions = resources.filter((r) => r.resourceType === "Condition").map((r) => [codingText(r.code) || "Condition name unavailable", clean(r.clinicalStatus?.coding?.[0]?.code) && `status ${r.clinicalStatus.coding[0].code}`, dateText(r) && `date ${dateText(r)}`].filter(Boolean).join(" — "));
    const allergies = resources.filter((r) => r.resourceType === "AllergyIntolerance").map((r) => [codingText(r.code) || "Allergen name unavailable", clean(r.reaction?.[0]?.manifestation?.map(codingText).filter(Boolean).join(", ")), clean(r.clinicalStatus?.coding?.[0]?.code) && `status ${r.clinicalStatus.coding[0].code}`].filter(Boolean).join(" — "));
    const medications = resources.filter((r) => ["MedicationRequest", "MedicationStatement", "MedicationAdministration"].includes(r.resourceType)).map(medicationText);
    const observations = resources.filter((r) => r.resourceType === "Observation").map((r) => {
      const value = r.valueQuantity ? quantity(r.valueQuantity) : r.valueString ? clean(r.valueString) : r.valueCodeableConcept ? codingText(r.valueCodeableConcept) : r.valueBoolean != null ? String(r.valueBoolean) : "value unavailable";
      return [codingText(r.code) || "Observation", value, dateText(r)].filter(Boolean).join(" — ");
    });
    const encounters = resources.filter((r) => r.resourceType === "Encounter").map((r) => [codingText(r.type?.[0]) || "Encounter", clean(r.status) && `status ${r.status}`, dateText(r)].filter(Boolean).join(" — "));
    const procedures = resources.filter((r) => ["Procedure", "Immunization", "CarePlan"].includes(r.resourceType)).map((r) => [codingText(r.code) || codingText(r.vaccineCode) || r.resourceType, clean(r.status) && `status ${r.status}`, dateText(r)].filter(Boolean).join(" — "));
    add("PATIENT CONTEXT (synthetic FHIR summary; verify against original chart)", [
      clean(patients[0].gender) && `Administrative gender: ${clean(patients[0].gender)}`,
      clean(patients[0].birthDate) && `Birth date: ${clean(patients[0].birthDate)}`
    ].filter(Boolean));
    add("CONDITIONS", conditions); add("ALLERGIES", allergies); add("MEDICATIONS", medications);
    add("OBSERVATIONS", observations); add("ENCOUNTERS", encounters); add("PROCEDURES / IMMUNIZATIONS / CARE PLANS", procedures);
    if (!sections.length) fail("FHIR Bundle contains no supported clinical resources to summarize.");
    const summary = sections.join("\n\n");
    if (summary.length > MAX_OUTPUT_CHARS) fail("FHIR summary exceeds the 100,000 character transcript limit; use a smaller Bundle.");
    return { summary, resourceCount: resources.length, ignoredResourceCount: resources.filter((r) => !SUPPORTED_TYPES.has(r.resourceType)).length };
  }

  function parseText(text, byteLength) {
    if (byteLength > MAX_INPUT_BYTES) fail("FHIR file is larger than the 10 MB import limit.");
    let parsed;
    try { parsed = JSON.parse(String(text).replace(/^\uFEFF/, "")); } catch { fail("FHIR file is not valid JSON."); }
    return summarize(parsed);
  }

  const api = { MAX_INPUT_BYTES, MAX_RESOURCES, MAX_OUTPUT_CHARS, summarize, parseText };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.EhrFhirImport = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
