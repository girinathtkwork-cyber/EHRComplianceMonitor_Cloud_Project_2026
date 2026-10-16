"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const importer = require("../src/frontend/fhir_import.js");

const cases = JSON.parse(fs.readFileSync(path.join(__dirname, "../dataset/evaluation/synthea_fhir_cases.json"), "utf8"));

test("synthetic FHIR evaluation set matches expected accept/reject labels", () => {
  for (const row of cases) {
    if (row.expected === "accept") assert.doesNotThrow(() => importer.summarize(row.bundle), row.id);
    else assert.throws(() => importer.summarize(row.bundle), undefined, row.id);
  }
});

test("small synthetic bundle yields readable chart context and preserves medication details", () => {
  const result = importer.summarize(cases.find((row) => row.id === "medication_and_allergy").bundle);
  assert.match(result.summary, /ALLERGIES:[\s\S]*Penicillin/);
  assert.match(result.summary, /MEDICATIONS:[\s\S]*Amoxicillin[\s\S]*500 mg[\s\S]*three times daily/);
  assert.equal(result.resourceCount, 3);
});

test("large but bounded bundle is summarized while oversized input and entry count are rejected", () => {
  const entries = [{ resource: { resourceType: "Patient", id: "large" } }];
  for (let i = 0; i < 2_000; i++) entries.push({ resource: { resourceType: "Observation", id: `o${i}`, subject: { reference: "Patient/large" }, code: { text: `Synthetic observation ${i}` }, valueString: "normal" } });
  assert.equal(importer.summarize({ resourceType: "Bundle", type: "collection", entry: entries }).resourceCount, 2_001);
  assert.throws(() => importer.parseText("{}", importer.MAX_INPUT_BYTES + 1), /10 MB/);
  assert.throws(() => importer.summarize({ resourceType: "Bundle", type: "collection", entry: Array(importer.MAX_RESOURCES + 1).fill({ resource: { resourceType: "Patient" } }) }), /resource limit/);
});

test("malformed JSON, empty and unsupported-only data fail with actionable messages", () => {
  assert.throws(() => importer.parseText("{broken", 7), /not valid JSON/);
  assert.throws(() => importer.summarize({ resourceType: "Bundle", type: "collection", entry: [] }), /no resources/);
  const bundle = { resourceType: "Bundle", type: "collection", entry: [{ resource: { resourceType: "Patient", id: "p" } }, { resource: { resourceType: "Practitioner" } }] };
  assert.throws(() => importer.summarize(bundle), /no supported clinical resources/);
});

test("UTF-8 BOM and Synthea urn:uuid patient references are accepted", () => {
  const bundle = { resourceType: "Bundle", type: "collection", entry: [
    { fullUrl: "urn:uuid:patient-1", resource: { resourceType: "Patient", id: "patient-1" } },
    { fullUrl: "urn:uuid:condition-1", resource: { resourceType: "Condition", subject: { reference: "urn:uuid:patient-1" }, code: { text: "Synthetic condition" } } }
  ] };
  const json = JSON.stringify(bundle);
  assert.match(importer.parseText(`\uFEFF${json}`, Buffer.byteLength(json) + 3).summary, /Synthetic condition/);
});

test("choice values, missing labels, duplicate resources and unsupported resource counting are safe", () => {
  const bundle = { resourceType: "Bundle", type: "collection", entry: [
    { resource: { resourceType: "Patient", id: "edge" } },
    { resource: { resourceType: "Observation", subject: { reference: "Patient/edge" }, code: {}, valueBoolean: false } },
    { resource: { resourceType: "Observation", subject: { reference: "Patient/edge" }, code: { text: "Repeat" }, valueQuantity: { value: 0, unit: "kg" } } },
    { resource: { resourceType: "Practitioner", id: "ignored" } }
  ] };
  const result = importer.summarize(bundle);
  assert.match(result.summary, /false/);
  assert.match(result.summary, /0 kg/);
  assert.equal(result.ignoredResourceCount, 1);
});

test("output size cap is enforced", () => {
  const entry = [{ resource: { resourceType: "Patient", id: "p" } }, { resource: { resourceType: "Observation", code: { text: "large" }, valueString: "x".repeat(50_000) } }, { resource: { resourceType: "Observation", code: { text: "large2" }, valueString: "y".repeat(50_000) } }];
  assert.throws(() => importer.summarize({ resourceType: "Bundle", type: "collection", entry }), /100,000 character/);
});
