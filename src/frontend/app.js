const candidate = document.querySelector("#candidate");
const source = document.querySelector("#source");
const status = document.querySelector("#status");
const results = document.querySelector("#results");
const batchRecordControl = document.querySelector("#batch-record-control");
const batchRecordSelect = document.querySelector("#batch-record-select");
const API_BASE_URL = String(window.EHR_API_BASE_URL || "").replace(/\/+$/, "");
const SYNTHETIC_EXAMPLE = "ALLERGIES: penicillin (rash). MEDICATIONS: amoxicillin 500 mg three times daily.";
let batchResults = [];

async function apiRequest(path, options) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 240_000);
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, { ...options, signal: controller.signal });
    let body;
    try { body = await response.json(); }
    catch { throw new Error(`The API returned an invalid response (HTTP ${response.status}).`); }
    if (!response.ok || body.error) throw new Error(body.error || `Request failed (HTTP ${response.status}).`);
    return body;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("The backend request timed out. Check job status or try again.");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[c]); }
function severityClass(value) {
  const severity = String(value || "").toLowerCase();
  return ["critical", "high", "medium", "low"].includes(severity) ? severity : "low";
}

function updateCharacterCount(field, output) {
  document.querySelector(output).textContent = `${field.value.length.toLocaleString()} / 100,000`;
}

function directRequestBody() {
  const text = candidate.value.trim();
  if (text.startsWith("{") || text.startsWith("[")) {
    let parsed;
    try { parsed = JSON.parse(text); }
    catch { throw new Error("The pasted JSON is invalid. Correct it or paste plain transcript text."); }
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error("Paste one JSON object containing a transcript, not a JSON array.");
    }
    const transcript = parsed.transcript ?? parsed.transcription;
    const sourceTranscript = source.value || parsed.sourceTranscript || parsed.source_transcript || "";
    if (typeof transcript !== "string" || !transcript.trim()) {
      throw new Error("The pasted JSON must contain a non-empty transcript or transcription string.");
    }
    return { transcript, sourceTranscript };
  }
  return { transcript: text, sourceTranscript: source.value };
}

function render(result) {
  const findings = result.stage_b_findings || [];
  const summary = result.stage_b_summary || {};
  const stageB = result.stage_b || {};
  const llmStatus = stageB.llm_status || result.stage_b_status || "unknown";
  const stageMode = stageB.mode || "unknown";
  const sourceCompared = summary.source_comparison === true;
  const sourceComparison = stageB.source_comparison || {};
  const sourceComparisonStatus = sourceComparison.status || (sourceCompared ? "compared" : "not_performed");
  const discrepancies = Array.isArray(sourceComparison.discrepancies) ? sourceComparison.discrepancies : [];
  results.hidden = false;
  document.querySelector("#result-title").textContent = result.error
    ? "Record could not be processed"
    : findings.length
    ? `${findings.length} review signal${findings.length === 1 ? "" : "s"}`
    : "No review signals returned";
  const badge = document.querySelector("#stage-b-badge");
  badge.textContent = result.error
    ? "Processing failed"
    : llmStatus === "fallback"
    ? "Deterministic fallback"
    : `${stageMode === "llm_rag" ? "Gemini · RAG" : stageMode} · ${llmStatus}`;
  badge.classList.toggle("fallback", llmStatus === "fallback");
  document.querySelector("#finding-count").textContent = `${findings.length} total`;
  document.querySelector("#summary").innerHTML = ["critical", "high", "medium", "low"]
    .map((severity) => `<div class="count ${severity}"><b>${Number(summary[severity] || 0)}</b><span>${severity}</span></div>`)
    .join("");
  const latency = Number(stageB.latency_ms);
  document.querySelector("#review-meta").innerHTML = [
    ["Processing status", result.error ? (result.error.code || "error") : (result.verification_status || "unknown")],
    ["Entities reviewed", Number(result.stage_a_summary?.n_entities || 0)],
    ["Stage B model", stageB.model || (stageMode === "stage_a_only" ? "Deterministic" : "Not reported")],
    ["Trusted-source comparison", sourceCompared ? "Compared" : sourceComparisonStatus === "not_provided" ? "Not provided" : "Not performed"]
  ].map(([label, value]) => `<div class="meta-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  if (latency > 0) {
    document.querySelector("#review-meta").insertAdjacentHTML("beforeend",
      `<div class="meta-item"><span>Stage B response time</span><strong>${escapeHtml((latency / 1000).toFixed(1))} sec</strong></div>`);
  }
  const comparisonNote = document.querySelector("#comparison-note");
  comparisonNote.hidden = sourceCompared;
  comparisonNote.textContent = sourceComparisonStatus === "not_provided"
    ? "No trusted source transcript was provided; source comparison was not run."
    : "A trusted source transcript was provided, but comparison was not completed for this result. Check Stage B status.";
  const comparisonPanel = document.querySelector("#source-comparison-panel");
  comparisonPanel.hidden = !sourceCompared;
  document.querySelector("#source-discrepancy-count").textContent = `${discrepancies.length} discrepancy${discrepancies.length === 1 ? "" : "ies"}`;
  document.querySelector("#source-discrepancies").innerHTML = discrepancies.length
    ? discrepancies.map((item) => `<article class="source-diff"><h4>${escapeHtml(item.type)} detected</h4><p>${escapeHtml(item.reason)}</p>${item.source_excerpt ? `<p class="quote-label">Trusted source</p><blockquote>${escapeHtml(item.source_excerpt)}</blockquote>` : ""}${item.candidate_excerpt ? `<p class="quote-label">Candidate transcription</p><blockquote>${escapeHtml(item.candidate_excerpt)}</blockquote>` : ""}</article>`).join("")
    : `<p class="empty-state">No material textual differences were returned. A reviewer should still verify the source and context.</p>`;
  document.querySelector("#findings").innerHTML = findings.length ? findings.map((item) => `<article class="finding ${severityClass(item.severity)}"><div class="finding-title"><span class="badge">${escapeHtml(item.severity)}</span><strong>${escapeHtml(item.category)}</strong></div><h4>${escapeHtml(item.phrase)}</h4><p>${escapeHtml(item.reason)}</p><dl><dt>Evidence</dt><dd>${escapeHtml(item.evidence || "No supporting reference evidence returned.")}</dd>${item.stage_a_routing ? `<dt>Stage A verification</dt><dd>${escapeHtml(item.stage_a_routing)}</dd>` : ""}</dl></article>`).join("") : `<div class="empty-state">No signals were returned for this transcript. This does not confirm that the transcript is complete or clinically correct; clinician review is still required.</div>`;
  document.querySelector("#entities").textContent = JSON.stringify({
    verificationStatus: result.verification_status,
    stageAVersion: result.stage_a_version,
    stageASummary: result.stage_a_summary,
    entities: result.entities,
    stageB,
    stageBSummary: result.stage_b_summary
  }, null, 2);
}

function renderJobResults(job) {
  batchResults = Array.isArray(job.results) && job.results.length
    ? job.results
    : (job.result ? [job.result] : []);
  batchRecordControl.hidden = batchResults.length < 2;
  batchRecordSelect.replaceChildren();
  batchResults.forEach((_, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = `Record ${index + 1}`;
    batchRecordSelect.append(option);
  });
  if (batchResults.length) render(batchResults[0]);
}

batchRecordSelect.addEventListener("change", () => {
  const selected = batchResults[Number(batchRecordSelect.value)];
  if (selected) render(selected);
});

candidate.addEventListener("input", () => updateCharacterCount(candidate, "#candidate-count"));
source.addEventListener("input", () => updateCharacterCount(source, "#source-count"));

document.querySelector("#load-example").addEventListener("click", () => {
  candidate.value = SYNTHETIC_EXAMPLE;
  source.value = "";
  updateCharacterCount(candidate, "#candidate-count");
  updateCharacterCount(source, "#source-count");
  status.textContent = "Synthetic example loaded. Results can vary between model runs.";
  candidate.focus();
});

document.querySelector("#clear-inputs").addEventListener("click", () => {
  candidate.value = "";
  source.value = "";
  document.querySelector("#transcript-file").value = "";
  document.querySelector("#upload-status").textContent = "";
  results.hidden = true;
  updateCharacterCount(candidate, "#candidate-count");
  updateCharacterCount(source, "#source-count");
  status.textContent = "Inputs cleared. Add a transcript to begin.";
  candidate.focus();
});

document.querySelector("#analyse").addEventListener("click", async () => {
  if (!candidate.value.trim()) {
    status.textContent = "Enter a candidate transcript before starting the review.";
    candidate.focus();
    return;
  }
  status.textContent = "Running clinical entity extraction, terminology retrieval, and contextual verification…";
  const button = document.querySelector("#analyse");
  results.hidden = true;
  button.disabled = true;
  try {
    const payload = directRequestBody();
    const result = await apiRequest("/api/analyse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    batchRecordControl.hidden = true;
    batchResults = [];
    render(result);
    const llmStatus = result.stage_b?.llm_status;
    status.textContent = llmStatus === "fallback"
      ? `Review completed using deterministic fallback. ${result.stage_b?.fallback_reason || "Gemini was unavailable."}`
      : `Review complete. ${result.stage_b_findings?.length || 0} review signal(s) returned.`;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#upload-transcript").addEventListener("click", async () => {
  const file = document.querySelector("#transcript-file").files[0];
  const uploadStatus = document.querySelector("#upload-status");
  const button = document.querySelector("#upload-transcript");
  if (!file) {
    uploadStatus.textContent = "Choose a .txt or .json transcript file first.";
    return;
  }
  if (file.size > 800_000) {
    uploadStatus.textContent = "Choose a transcript file no larger than 800 KB.";
    return;
  }
  button.disabled = true;
  results.hidden = true;
  uploadStatus.textContent = "Uploading transcript…";
  try {
    const content = await file.text();
    const job = await apiRequest("/api/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fileName: file.name, content })
    });
    uploadStatus.textContent = job.storage === "local"
      ? "Running the file through the local pipeline…"
      : "Saved to S3. Waiting for the transcript-verification job…";

    let completed;
    const deadline = Date.now() + 240_000;
    while (Date.now() < deadline) {
      completed = await apiRequest(`/api/jobs/${encodeURIComponent(job.jobId)}`);
      if (completed.status === "completed" || completed.status === "partial") break;
      if (completed.status === "failed") throw new Error(completed.error || "Transcript processing failed.");
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
    if (!completed || !["completed", "partial"].includes(completed.status)) {
      throw new Error("The job is still processing or no S3 result arrived before the dashboard timed out.");
    }
    renderJobResults(completed);
    const expected = Number(completed.expected_count ?? 1);
    const processed = Number(completed.processed_count ?? batchResults.length);
    uploadStatus.textContent = job.storage === "local"
      ? "Verified locally; this upload was not sent to AWS or saved in S3."
      : completed.status === "partial"
        ? `S3 job is partial: ${processed} of ${expected} records processed, ${Number(completed.failed_count || 0)} failed, ${Number(completed.fallback_count || 0)} used fallback. Use the record selector to review each result.`
        : "S3 upload verified successfully; the result was retrieved from the output object.";
    status.textContent = `${batchResults.length} record result(s) returned. Use the selector to review each record.`;
  } catch (error) {
    uploadStatus.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
