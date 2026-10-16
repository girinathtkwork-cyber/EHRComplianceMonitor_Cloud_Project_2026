const candidate = document.querySelector("#candidate");
const source = document.querySelector("#source");
const status = document.querySelector("#status");
const results = document.querySelector("#results");
const batchRecordControl = document.querySelector("#batch-record-control");
const batchRecordSelect = document.querySelector("#batch-record-select");
const API_BASE_URL = String(window.EHR_API_BASE_URL || "").replace(/\/+$/, "");
const SYNTHETIC_EXAMPLE = "ALLERGIES: penicillin (rash). MEDICATIONS: amoxicillin 500 mg three times daily.";
let batchResults = [];

async function apiRequest(path, options, baseUrl = API_BASE_URL) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 240_000);
  try {
    if (baseUrl && !window.ehrAuth?.enabled) throw new Error("Cloud OAuth settings are missing; sign-in is disabled until they are configured.");
    const headers = new Headers(options?.headers || {});
    if (window.ehrAuth?.enabled) {
      const token = await window.ehrAuth.getAccessToken();
      if (!token) throw new Error("Sign in to access cloud verification jobs.");
      headers.set("Authorization", `Bearer ${token}`);
    }
    const response = await fetch(`${baseUrl}${path}`, { ...options, headers, signal: controller.signal });
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

const workspaceRequest = (path, options) => apiRequest(path, options, API_BASE_URL);
let currentJobId = null;
let historyCursor = 0;
let retryMode = "text";
const HISTORY_PAGE_SIZE = 20;

function showView(name) {
  document.querySelectorAll(".page-view").forEach((view) => { view.hidden = true; });
  const mainView = document.querySelector(`#${name}-view`);
  if (mainView) mainView.hidden = false;
  const how = document.querySelector("#new-how-view");
  if (how) how.hidden = name !== "new";
  document.querySelectorAll(".primary-nav [data-view]").forEach((button) => {
    if (button.dataset.view === name) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  if (name === "overview") refreshOverview();
  if (name === "history") refreshHistory(0);
  if (name === "operations") refreshOperations();
}

document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
document.querySelector("#back-to-history").addEventListener("click", () => showView("history"));

const cloudMode = Boolean(API_BASE_URL);
const loginButton = document.querySelector("#auth-login");
const logoutButton = document.querySelector("#auth-logout");
const authState = document.querySelector("#auth-state");
function refreshAuthControls() {
  if (!window.ehrAuth?.enabled) {
    loginButton.hidden = true;
    logoutButton.hidden = true;
    authState.textContent = cloudMode ? "Cloud login is not configured" : "Local demo";
    return;
  }
  const signedIn = window.ehrAuth.hasSession();
  loginButton.hidden = signedIn;
  logoutButton.hidden = !signedIn;
  authState.textContent = signedIn ? "Signed in" : "Sign in required";
}
loginButton.addEventListener("click", () => window.ehrAuth.signIn().catch((error) => { authState.textContent = error.message; }));
logoutButton.addEventListener("click", () => window.ehrAuth.signOut());
window.ehrAuth?.onChange(refreshAuthControls);
window.ehrAuth?.ready.catch((error) => { authState.textContent = error.message; });

function statusBadge(status) { return `<span class="state-badge ${escapeHtml(String(status || "unknown").toLowerCase())}">${escapeHtml(status || "unknown")}</span>`; }
function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "—" : date.toLocaleString();
}
function jobRow(job) {
  return `<tr><td><button class="link-button" type="button" data-open-job="${escapeHtml(job.jobId)}">${escapeHtml(job.jobId)}</button></td><td>${escapeHtml(formatDate(job.submittedAt))}</td><td>${statusBadge(job.status)}</td><td>${job.processingDurationMs == null ? "—" : `${(job.processingDurationMs / 1000).toFixed(1)} s`}</td><td>${Number(job.findingCount || 0)}</td><td>${statusBadge(job.reviewStatus || "PENDING")}</td><td>${job.cacheHit ? "Hit" : "Miss"}</td><td>${escapeHtml(job.pipelineVersion || "—")}</td></tr>`;
}
function renderJobTable(items) {
  return items.length ? `<table><thead><tr><th>Job ID</th><th>Submitted</th><th>Status</th><th>Duration</th><th>Findings</th><th>Review</th><th>Cache</th><th>Pipeline</th></tr></thead><tbody>${items.map(jobRow).join("")}</tbody></table>`
    : `<div class="empty-state">No verification records match this view. Start a synthetic verification to create the first record.</div>`;
}
function bindOpenJobButtons(container) {
  container.querySelectorAll("[data-open-job]").forEach((button) => button.addEventListener("click", () => openJob(button.dataset.openJob)));
}

async function refreshOverview() {
  const error = document.querySelector("#overview-error");
  error.hidden = true;
  try {
    const data = await workspaceRequest("/api/overview");
    const c = data.counts || {};
    const metrics = [
      ["Verification jobs", c.total ?? 0], ["Completed", c.completed ?? 0], ["Queued / processing", c.active ?? 0],
      ["Failed", c.failed ?? 0], ["Human review suggested", c.reviewRequired ?? 0],
      ["Average duration", data.averageProcessingDurationMs == null ? "—" : `${(data.averageProcessingDurationMs / 1000).toFixed(1)} s`],
      ["Cache hit rate", data.cacheHitRate == null ? "—" : `${(data.cacheHitRate * 100).toFixed(0)}%`],
    ];
    document.querySelector("#metric-cards").innerHTML = metrics.map(([label, value]) => `<article class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>`).join("");
    const recent = document.querySelector("#recent-activity");
    recent.innerHTML = renderJobTable(data.recentActivity || []);
    bindOpenJobButtons(recent);
    const distribution = data.findingDistribution || {};
    document.querySelector("#finding-distribution").innerHTML = ["critical", "high", "medium", "low"].map((level) => `<div class="distribution-row"><span>${escapeHtml(level)}</span><strong>${Number(distribution[level] || 0)}</strong><div class="distribution-track"><i class="${level}" style="width:${Math.min(100, Number(distribution[level] || 0) * 10)}%"></i></div></div>`).join("");
    document.querySelector("#metrics-definition").textContent = data.metricsDefinition || "Metrics unavailable.";
  } catch (err) {
    error.textContent = `Dashboard metrics could not be loaded: ${err.message}`;
    error.hidden = false;
    document.querySelector("#metric-cards").replaceChildren();
  }
}

async function refreshHistory(cursor = historyCursor) {
  historyCursor = cursor;
  const state = document.querySelector("#history-state");
  const table = document.querySelector("#history-table");
  state.textContent = "Loading saved verification records…";
  table.setAttribute("aria-busy", "true");
  const params = new URLSearchParams({ limit: String(HISTORY_PAGE_SIZE), cursor: String(cursor) });
  const statusFilter = document.querySelector("#filter-status").value;
  const reviewFilter = document.querySelector("#filter-review").value;
  const search = document.querySelector("#filter-search").value.trim();
  const from = document.querySelector("#filter-from").value;
  const to = document.querySelector("#filter-to").value;
  if (statusFilter) params.set("status", statusFilter);
  if (reviewFilter) params.set("reviewStatus", reviewFilter);
  if (search) params.set("search", search);
  if (from) params.set("from", `${from}T00:00:00.000Z`);
  if (to) params.set("to", `${to}T23:59:59.999Z`);
  try {
    const data = await workspaceRequest(`/api/jobs?${params}`);
    table.innerHTML = renderJobTable(data.items || []);
    bindOpenJobButtons(table);
    state.textContent = data.total ? `${data.total} matching job(s)` : "No saved verification records yet.";
    document.querySelector("#history-prev").disabled = cursor <= 0;
    document.querySelector("#history-next").disabled = data.nextCursor == null;
    document.querySelector("#history-page").textContent = `Showing ${data.total ? cursor + 1 : 0}–${Math.min(cursor + HISTORY_PAGE_SIZE, data.total || 0)} of ${data.total || 0}`;
  } catch (err) {
    state.textContent = `History could not be loaded: ${err.message}`;
    table.innerHTML = `<div class="error-state">Check the workspace service connection and retry.</div>`;
  } finally { table.removeAttribute("aria-busy"); }
}

async function refreshOperations() {
  const health = document.querySelector("#health-state");
  health.innerHTML = `<div class="loading-state">Checking backend and worker status…</div>`;
  try {
    const [backend, overview] = await Promise.all([workspaceRequest("/health"), workspaceRequest("/api/overview")]);
    health.innerHTML = [
      ["Backend", backend.status === "ok" ? "Available" : "Unavailable"],
      ["Worker", cloudMode ? "AWS Lambda SQS worker" : backend.worker || "Unavailable"],
      ["Queue depth", cloudMode ? "Managed by Amazon SQS" : "Unavailable · local runner has no SQS queue"],
      [cloudMode ? "Active cloud jobs" : "Active local jobs", overview.counts?.active ?? 0],
      ["AWS dependency status", cloudMode ? "Connected" : "Not connected from local demo"],
    ].map(([label, value]) => `<article class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>`).join("");
    const failed = await workspaceRequest("/api/jobs?status=FAILED&limit=6");
    document.querySelector("#recent-failures").innerHTML = failed.items?.length ? renderJobTable(failed.items) : `<div class="empty-state">No failed jobs recorded.</div>`;
    bindOpenJobButtons(document.querySelector("#recent-failures"));
  } catch (err) {
    health.innerHTML = `<div class="error-state">Health information unavailable: ${escapeHtml(err.message)}</div>`;
  }
}

async function openJob(jobId) {
  currentJobId = jobId;
  showView("details");
  document.querySelector("#details-empty").hidden = false;
  document.querySelector("#details-empty").textContent = "Loading the stored verification record…";
  document.querySelector("#results").hidden = true;
  try {
    const job = await workspaceRequest(`/api/jobs/${encodeURIComponent(jobId)}`);
    document.querySelector("#details-job-id").textContent = `Job ${job.jobId} · ${job.status}`;
    if (!job.result) {
      document.querySelector("#details-empty").textContent = job.status === "FAILED"
        ? `Verification failed (${job.errorCategory || "unknown error category"}). No successful result is available.`
        : `Job is ${job.status.toLowerCase()}. Refresh History to check its status.`;
      return;
    }
    document.querySelector("#details-empty").hidden = true;
    document.querySelector("#results").hidden = false;
    render(job.result);
    document.querySelector("#review-meta").insertAdjacentHTML("afterbegin", [
      ["Job ID", job.jobId], ["Submitted", formatDate(job.submittedAt)], ["Completed", formatDate(job.completedAt)],
      ["Duration", job.processingDurationMs == null ? "—" : `${(job.processingDurationMs / 1000).toFixed(1)} sec`],
      ["Cache", job.cacheHit ? `Hit · ${job.cacheSourceJobId || "prior result"}` : "Miss"], ["Pipeline", job.pipelineVersion || "unknown"],
      ["Review state", job.reviewStatus || "PENDING"],
    ].map(([label, value]) => `<div class="meta-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join(""));
    const findingSelect = document.querySelector("#review-finding");
    findingSelect.innerHTML = (job.result.stage_b_findings || []).map((finding) => `<option value="${escapeHtml(finding.id)}">${escapeHtml(finding.category)} · ${escapeHtml(finding.phrase)}</option>`).join("");
    document.querySelector("#review-finding-field").hidden = findingSelect.options.length === 0;
    const audit = document.querySelector("#audit-history");
    audit.innerHTML = job.auditHistory?.length ? [...job.auditHistory].reverse().map((entry) => `<article><strong>${escapeHtml(entry.action)}</strong><span>${escapeHtml(formatDate(entry.at))}</span><p>${escapeHtml(entry.reason || entry.note || "No note recorded")}</p><small>${escapeHtml(entry.actor || "System event")}</small></article>`).join("") : `<p class="panel-help">No review actions have been recorded.</p>`;
    document.querySelector("#review-message").textContent = job.reviewStatus === "REVIEWED"
      ? `Reviewed ${formatDate(job.reviewedAt)}${cloudMode ? " · authenticated reviewer" : " · local demo session"}.`
      : cloudMode ? "Review actions are recorded for the signed-in user." : "Review actions are recorded as an unauthenticated local demo session.";
  } catch (err) {
    document.querySelector("#details-empty").textContent = `Stored result could not be loaded: ${err.message}`;
  }
}

document.querySelector("#history-filters").addEventListener("submit", (event) => { event.preventDefault(); refreshHistory(0); });
document.querySelector("#history-prev").addEventListener("click", () => refreshHistory(Math.max(0, historyCursor - HISTORY_PAGE_SIZE)));
document.querySelector("#history-next").addEventListener("click", async () => {
  try { const page = await workspaceRequest(`/api/jobs?limit=${HISTORY_PAGE_SIZE}&cursor=${historyCursor}`); if (page.nextCursor != null) refreshHistory(page.nextCursor); }
  catch (err) { document.querySelector("#history-state").textContent = `Could not load the next page: ${err.message}`; }
});
document.querySelector("#review-action").addEventListener("change", (event) => {
  const needsFinding = event.target.value !== "mark_reviewed";
  document.querySelector("#review-finding-field").hidden = !needsFinding || !document.querySelector("#review-finding").options.length;
});
document.querySelector("#review-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.querySelector("#review-message");
  if (!currentJobId) { message.textContent = "Open a saved job before recording a review action."; return; }
  const action = document.querySelector("#review-action").value;
  const reason = document.querySelector("#review-reason").value.trim();
  if (action !== "mark_reviewed" && !reason) { message.textContent = "Enter a reason to dismiss or escalate a finding."; return; }
  const body = { action, reason };
  if (action !== "mark_reviewed") body.findingId = document.querySelector("#review-finding").value;
  try {
    await workspaceRequest(`/api/jobs/${encodeURIComponent(currentJobId)}/review`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    document.querySelector("#review-reason").value = "";
    await openJob(currentJobId);
    refreshOverview();
  } catch (err) { message.textContent = `Review action was not saved: ${err.message}`; }
});

function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[c]); }
function severityClass(value) {
  const severity = String(value || "").toLowerCase();
  return ["critical", "high", "medium", "low"].includes(severity) ? severity : "low";
}
function orderedFindings(findings) {
  const severityScore = { critical: 90, high: 70, medium: 40, low: 15 };
  return findings.map((finding, index) => ({ finding, index })).sort((a, b) =>
    (Number(b.finding.risk_score) || severityScore[String(b.finding.severity || "").toLowerCase()] || 0)
      - (Number(a.finding.risk_score) || severityScore[String(a.finding.severity || "").toLowerCase()] || 0)
    || a.index - b.index
  ).map(({ finding }) => finding);
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
  const findings = orderedFindings(result.stage_b_findings || []);
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
  document.querySelector("#findings").innerHTML = findings.length ? findings.map((item) => `<article class="finding ${severityClass(item.severity)}"><div class="finding-title"><span class="badge">${escapeHtml(item.severity)}</span><strong>${escapeHtml(item.category)}</strong><span class="risk-priority">${item.risk_score == null ? "" : `${escapeHtml(item.risk_rank || "Priority")} · ${escapeHtml(item.risk_score)}/100`}</span><span class="finding-kind">${escapeHtml(String(item.finding_kind || "review_signal").replaceAll("_", " "))}</span></div><h4>${escapeHtml(item.phrase)}</h4><p>${escapeHtml(item.reason)}</p><dl><dt>Finding ID</dt><dd>${escapeHtml(item.id || "Not reported")}</dd><dt>Detection stage</dt><dd>${escapeHtml(item.detection_stage || "Not reported")}</dd><dt>Provenance</dt><dd>${escapeHtml(item.provenance || "Not reported")}</dd><dt>Confidence</dt><dd>${escapeHtml(item.confidence || "Not reported")}</dd><dt>Evidence</dt><dd>${escapeHtml(item.evidence || "No supporting reference evidence returned.")}</dd>${item.uncertainty ? `<dt>Uncertainty</dt><dd>${escapeHtml(item.uncertainty)}</dd>` : ""}${item.reviewStatus ? `<dt>Review status</dt><dd>${escapeHtml(item.reviewStatus)}</dd>` : ""}${item.stage_a_routing ? `<dt>Stage A verification</dt><dd>${escapeHtml(item.stage_a_routing)}</dd>` : ""}</dl></article>`).join("") : `<div class="empty-state">No signals were returned for this transcript. This does not confirm that the transcript is complete or clinically correct; clinician review is still required.</div>`;
  const completeness = result.completeness_review;
  const completenessPanel = document.querySelector("#completeness-panel");
  completenessPanel.hidden = !completeness || !Array.isArray(completeness.items);
  if (completenessPanel.hidden === false) {
    document.querySelector("#completeness-status").textContent = completeness.human_review_required ? "Reviewer attention suggested" : "No contextual gap detected";
    document.querySelector("#completeness-items").innerHTML = completeness.items.map((item) => `<article class="completeness-item"><div><strong>${escapeHtml(item.field.replaceAll(":", " · "))}</strong><span class="completeness-badge ${escapeHtml(item.status)}">${escapeHtml(item.status.replaceAll("_", " "))}</span></div><p>${escapeHtml(item.explanation)}</p>${item.evidence ? `<small>Evidence: ${escapeHtml(item.evidence)}</small>` : ""}${item.suggested_action ? `<small class="suggested-action">Suggested action: ${escapeHtml(item.suggested_action)}</small>` : ""}</article>`).join("");
  }
  document.querySelector("#entities").textContent = JSON.stringify({
    verificationStatus: result.verification_status,
    stageAVersion: result.stage_a_version,
    stageASummary: result.stage_a_summary,
    entities: result.entities,
    stageB,
    stageBSummary: result.stage_b_summary,
    completenessReview: completeness || "Not available from this backend"
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

async function waitForJob(jobId, onStatus = () => {}) {
  const deadline = Date.now() + 240_000;
  while (Date.now() < deadline) {
    const current = await workspaceRequest(`/api/jobs/${encodeURIComponent(jobId)}`);
    onStatus(current);
    if (!["QUEUED", "PROCESSING"].includes(current.status)) return current;
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
  throw new Error(`Job ${jobId} is still processing. Open History to check its current status.`);
}

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
  document.querySelector("#fhir-file").value = "";
  document.querySelector("#fhir-status").textContent = "";
  document.querySelector("#upload-status").textContent = "";
  document.querySelector("#retry-verification").hidden = true;
  results.hidden = true;
  updateCharacterCount(candidate, "#candidate-count");
  updateCharacterCount(source, "#source-count");
  status.textContent = "Inputs cleared. Add a transcript to begin.";
  candidate.focus();
});

document.querySelector("#import-fhir").addEventListener("click", async () => {
  const file = document.querySelector("#fhir-file").files[0];
  const message = document.querySelector("#fhir-status");
  if (!file) { message.textContent = "Choose a Synthea FHIR JSON Bundle first."; return; }
  if (file.size > window.EhrFhirImport.MAX_INPUT_BYTES) { message.textContent = "FHIR file is larger than the 10 MB import limit."; return; }
  const button = document.querySelector("#import-fhir");
  button.disabled = true;
  message.textContent = "Reading and validating the synthetic FHIR Bundle locally in this browser…";
  try {
    const imported = window.EhrFhirImport.parseText(await file.text(), file.size);
    source.value = imported.summary;
    if (!candidate.value.trim()) candidate.value = imported.summary;
    updateCharacterCount(candidate, "#candidate-count");
    updateCharacterCount(source, "#source-count");
    message.textContent = `Imported ${imported.resourceCount.toLocaleString()} resource(s); skipped ${imported.ignoredResourceCount.toLocaleString()} unsupported resource(s). Review the chart context and candidate text before submitting.`;
    status.textContent = "FHIR chart context loaded. Replace or edit the candidate transcription as needed.";
    candidate.focus();
  } catch (error) {
    message.textContent = error.message;
  } finally { button.disabled = false; }
});

document.querySelector("#analyse").addEventListener("click", async () => {
  if (!candidate.value.trim()) {
    status.textContent = "Enter a candidate transcript before starting the review.";
    candidate.focus();
    return;
  }
  status.textContent = "Submitting verification job…";
  const button = document.querySelector("#analyse");
  document.querySelector("#retry-verification").hidden = true;
  results.hidden = true;
  button.disabled = true;
  try {
    const payload = { ...directRequestBody(), forceRerun: document.querySelector("#force-rerun").checked };
    const submitted = await workspaceRequest("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    status.textContent = `Job ${submitted.jobId} accepted (${submitted.status}); processing asynchronously…`;
    const job = await waitForJob(submitted.jobId, (current) => {
      status.textContent = `Job ${submitted.jobId}: ${current.status.toLowerCase()}…`;
    });
    if (job.status === "FAILED") throw new Error(`Verification failed (${job.errorCategory || "unknown error category"}).`);
    status.textContent = job.cacheHit ? `Validated result reused from ${job.cacheSourceJobId}.` : cloudMode ? "Verification completed and saved to cloud job history." : "Verification completed and saved to local history.";
    await openJob(submitted.jobId);
    refreshOverview();
  } catch (error) {
    status.textContent = error.message;
    retryMode = "text";
    document.querySelector("#retry-verification").hidden = false;
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#upload-transcript").addEventListener("click", async () => {
  const file = document.querySelector("#transcript-file").files[0];
  const uploadStatus = document.querySelector("#upload-status");
  const button = document.querySelector("#upload-transcript");
  if (!file) { uploadStatus.textContent = "Choose a .txt or .json transcript file first."; return; }
  if (file.size > 800_000) { uploadStatus.textContent = "Choose a transcript file no larger than 800 KB."; return; }
  button.disabled = true;
  document.querySelector("#retry-verification").hidden = true;
  results.hidden = true;
  uploadStatus.textContent = cloudMode ? "Submitting transcript to the authenticated cloud job workspace..." : "Submitting transcript to the local job workspace...";
  try {
    const content = await file.text();
    const job = await workspaceRequest("/api/upload", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fileName: file.name, content, forceRerun: document.querySelector("#force-rerun").checked })
    });
    uploadStatus.textContent = `Job ${job.jobId} accepted; waiting for processing...`;
    const completed = await waitForJob(job.jobId, (current) => { uploadStatus.textContent = `Job ${job.jobId}: ${current.status.toLowerCase()}...`; });
    if (completed.status === "FAILED") throw new Error(`Verification failed (${completed.errorCategory || "unknown error category"}).`);
    uploadStatus.textContent = completed.cacheHit ? "A validated result was reused." : cloudMode ? "File verified and saved to cloud history; the input is held only in the one-day queue." : "File verified and saved to local history; transcript text was not stored.";
    status.textContent = "Verification completed. Open the stored result to review findings.";
    await openJob(job.jobId);
    refreshOverview();
  } catch (error) {
    uploadStatus.textContent = error.message;
    retryMode = "file";
    document.querySelector("#retry-verification").hidden = false;
  }
  finally { button.disabled = false; }
});

document.querySelector("#retry-verification").addEventListener("click", () => {
  if (retryMode === "file" && document.querySelector("#transcript-file").files.length) document.querySelector("#upload-transcript").click();
  else document.querySelector("#analyse").click();
});

showView("overview");
