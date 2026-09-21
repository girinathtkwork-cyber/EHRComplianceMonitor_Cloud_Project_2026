"use strict";

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { analyse } = require("./analyzer");

const root = path.resolve(__dirname, "../frontend");
const recordsPath = path.resolve(__dirname, "../../dataset/processed/mtsamples_with_errors.json");
const port = Number(process.env.PORT || 3000);

function send(response, status, body, type = "application/json") {
  response.writeHead(status, { "Content-Type": `${type}; charset=utf-8`, "Cache-Control": "no-store" });
  response.end(Buffer.isBuffer(body) || typeof body === "string" ? body : JSON.stringify(body));
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; if (body.length > 2_000_000) request.destroy(); });
    request.on("end", () => { try { resolve(JSON.parse(body || "{}")); } catch { reject(new Error("Request body must be valid JSON.")); } });
    request.on("error", reject);
  });
}

function restoreSource(record) {
  const label = record.ground_truth_label || {};
  let source = record.transcription;
  if (label.error_type === "wrong_medication" || label.error_type === "wrong_dosage") {
    source = source.replace(label.injected_entity, label.correct_value);
  }
  if (label.error_type === "fabricated_diagnosis") {
    source = source.replace(new RegExp(`\\s*Additional diagnosis noted: ${escapeRegex(label.injected_entity)}\\.`, "i"), "");
  }
  if (label.error_type === "fabricated_procedure") {
    source = source.replace(new RegExp(`\\s*Patient also underwent ${escapeRegex(label.injected_entity)} during this visit\\.`, "i"), "");
  }
  return source;
}

function escapeRegex(value) { return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

function demoRecord() {
  const records = JSON.parse(fs.readFileSync(recordsPath, "utf8"));
  const record = records.find((item) => item.ground_truth_label?.correct_value) || records.find((item) => item.ground_truth_label?.is_error);
  return { transcript: record.transcription, sourceTranscript: restoreSource(record), metadata: { transcriptId: record.transcript_id, specialty: record.medical_specialty, injectedErrorType: record.ground_truth_label.error_type } };
}

function evaluateDataset() {
  const records = JSON.parse(fs.readFileSync(recordsPath, "utf8"));
  const categories = { wrong_dosage: "Dosage mismatch", wrong_medication: "Unsupported medication", fabricated_diagnosis: "Unsupported diagnosis", fabricated_procedure: "Unsupported procedure" };
  const metrics = Object.fromEntries(Object.keys(categories).map((type) => [type, { total: 0, detected: 0 }]));
  let cleanTotal = 0;
  let cleanWithFindings = 0;
  records.forEach((record) => {
    const label = record.ground_truth_label || {};
    const result = analyse({ transcript: record.transcription, sourceTranscript: restoreSource(record) });
    if (!label.is_error) { cleanTotal += 1; if (result.findings.length) cleanWithFindings += 1; return; }
    if (!metrics[label.error_type]) return;
    metrics[label.error_type].total += 1;
    if (result.findings.some((item) => item.category === categories[label.error_type])) metrics[label.error_type].detected += 1;
  });
  const total = Object.values(metrics).reduce((sum, item) => sum + item.total, 0);
  const detected = Object.values(metrics).reduce((sum, item) => sum + item.detected, 0);
  return { datasetRecords: records.length, reference: "MTSamples error-injection output", metrics, overallRecall: total ? Number((detected / total).toFixed(3)) : null, cleanRecords: { total: cleanTotal, withReviewSignals: cleanWithFindings } };
}

const server = http.createServer(async (request, response) => {
  try {
    if (request.method === "POST" && request.url === "/api/analyse") return send(response, 200, analyse(await readBody(request)));
    if (request.method === "GET" && request.url === "/api/demo") return send(response, 200, demoRecord());
    if (request.method === "GET" && request.url === "/api/evaluation") return send(response, 200, evaluateDataset());
    const requested = request.url === "/" ? "index.html" : request.url.replace(/^\//, "");
    const file = path.resolve(root, requested);
    if (!file.startsWith(root) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) return send(response, 404, { error: "Not found" });
    const contentType = file.endsWith(".css") ? "text/css" : file.endsWith(".js") ? "application/javascript" : "text/html";
    return send(response, 200, fs.readFileSync(file), contentType);
  } catch (error) {
    return send(response, 400, { error: error.message || "Unable to process request." });
  }
});

server.listen(port, () => console.log(`EHR transcription safety gate running at http://localhost:${port}`));
