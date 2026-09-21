"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { analyse } = require("../analyzer");

test("detects an altered dose against a source transcript", () => {
  const result = analyse({ transcript: "Take metformin 500mg daily.", sourceTranscript: "Take metformin 50mg daily." });
  assert.ok(result.findings.some((item) => item.category === "Dosage mismatch" && item.severity === "Critical"));
});

test("detects a new diagnosis against a source transcript", () => {
  const result = analyse({ transcript: "Assessment: asthma. Additional diagnosis: Atrial fibrillation.", sourceTranscript: "Assessment: asthma." });
  assert.ok(result.findings.some((item) => item.category === "Unsupported diagnosis"));
});

test("adds LASA safety review for a known confusable medication", () => {
  const result = analyse({ transcript: "Hydralazine 25mg was continued." });
  assert.ok(result.findings.some((item) => item.category === "Look-alike / sound-alike medication"));
});
