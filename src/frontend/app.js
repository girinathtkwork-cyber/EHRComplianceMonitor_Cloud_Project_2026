const candidate = document.querySelector("#candidate");
const source = document.querySelector("#source");
const status = document.querySelector("#status");
const results = document.querySelector("#results");
const API_BASE_URL = String(window.EHR_API_BASE_URL || "").replace(/\/+$/, "");

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
function severityClass(value) { return String(value || "low").toLowerCase(); }

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
  results.hidden = false;
  document.querySelector("#result-title").textContent = findings.length ? `${findings.length} safety finding${findings.length === 1 ? "" : "s"}` : "No review signals found";
  document.querySelector("#mode").textContent = `Stage B: ${result.stage_b?.mode || "unknown"} (${result.stage_b?.llm_status || result.stage_b_status || "unknown"})`;
  document.querySelector("#summary").innerHTML = ["critical", "high", "medium", "low"].map((severity) => `<div class="count ${severity}"><b>${Number(summary[severity] || 0)}</b><span>${severity}</span></div>`).join("");
  document.querySelector("#findings").innerHTML = findings.length ? findings.map((item) => `<article class="finding ${severityClass(item.severity)}"><div class="finding-title"><span class="badge">${escapeHtml(item.severity)}</span><strong>${escapeHtml(item.category)}</strong></div><h3>${escapeHtml(item.phrase)}</h3><p>${escapeHtml(item.reason)}</p><dl><dt>Evidence</dt><dd>${escapeHtml(item.evidence || "No supporting reference evidence returned.")}</dd>${item.stage_a_routing ? `<dt>Stage A verification</dt><dd>${escapeHtml(item.stage_a_routing)}</dd>` : ""}</dl></article>`).join("") : `<div class="empty">No signals met the prototype rules. A clinician must still review the record.</div>`;
  document.querySelector("#entities").textContent = JSON.stringify({
    verificationStatus: result.verification_status,
    stageAVersion: result.stage_a_version,
    stageASummary: result.stage_a_summary,
    entities: result.entities,
    stageB: result.stage_b,
    stageBSummary: result.stage_b_summary
  }, null, 2);
}

document.querySelector("#analyse").addEventListener("click", async () => {
  if (!candidate.value.trim()) {
    status.textContent = "Enter a candidate transcript before starting the review.";
    candidate.focus();
    return;
  }
  status.textContent = "Running clinical entity extraction, terminology retrieval, and contextual verification…";
  const button = document.querySelector("#analyse");
  button.disabled = true;
  try {
    const payload = directRequestBody();
    const result = await apiRequest("/api/analyse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    render(result);
    const llmStatus = result.stage_b?.llm_status;
    status.textContent = llmStatus === "fallback"
      ? `Review completed using deterministic fallback. ${result.stage_b?.fallback_reason || "Gemini was unavailable."}`
      : `Review completed: ${result.stage_b_findings?.length || 0} finding(s); Stage B status: ${llmStatus || result.stage_b_status}.`;
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
    render(completed.result);
    uploadStatus.textContent = job.storage === "local"
      ? "Verified locally; this upload was not sent to AWS or saved in S3."
      : completed.status === "partial"
        ? "S3 upload completed with a partial result; check the processed-record count in the result details."
        : "S3 upload verified successfully; the result was retrieved from the output object.";
    status.textContent = `Stage B status: ${completed.result.stage_b?.llm_status || completed.result.stage_b_status}.`;
  } catch (error) {
    uploadStatus.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
