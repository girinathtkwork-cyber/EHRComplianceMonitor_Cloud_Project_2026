const candidate = document.querySelector("#candidate");
const source = document.querySelector("#source");
const status = document.querySelector("#status");
const results = document.querySelector("#results");

function escapeHtml(value) { return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[c]); }
function severityClass(value) { return value.toLowerCase(); }

async function review() {
  status.textContent = "Reviewing transcript…";
  document.querySelector("#analyse").disabled = true;
  try {
    const response = await fetch("/api/analyse", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ transcript: candidate.value, sourceTranscript: source.value }) });
    const result = await response.json();
    if (!response.ok || result.error) throw new Error(result.error || "Review failed.");
    render(result);
    status.textContent = `${result.findings.length} review signal(s) returned.`;
  } catch (error) { status.textContent = error.message; }
  document.querySelector("#analyse").disabled = false;
}

function render(result) {
  results.hidden = false;
  document.querySelector("#result-title").textContent = result.findings.length ? `${result.findings.length} safety finding${result.findings.length === 1 ? "" : "s"}` : "No review signals found";
  document.querySelector("#mode").textContent = result.mode === "source comparison" ? "Evidence mode: source comparison" : "Evidence mode: reference-assisted screening";
  document.querySelector("#summary").innerHTML = Object.entries(result.summary).map(([severity, count]) => `<div class="count ${severityClass(severity)}"><b>${count}</b><span>${severity}</span></div>`).join("");
  document.querySelector("#findings").innerHTML = result.findings.length ? result.findings.map((item) => `<article class="finding ${severityClass(item.severity)}"><div class="finding-title"><span class="badge">${escapeHtml(item.severity)}</span><strong>${escapeHtml(item.category)}</strong></div><h3>${escapeHtml(item.phrase)}</h3><p>${escapeHtml(item.reason)}</p><dl><dt>Evidence</dt><dd>${escapeHtml(item.evidence)}</dd><dt>Confidence</dt><dd>${escapeHtml(item.confidence)}</dd></dl></article>`).join("") : `<div class="empty">No signals met the prototype rules. A clinician must still review the record.</div>`;
  document.querySelector("#entities").textContent = JSON.stringify({ entities: result.entities, reference: result.reference }, null, 2);
}

document.querySelector("#analyse").addEventListener("click", review);
document.querySelector("#load-demo").addEventListener("click", async () => {
  status.textContent = "Loading a labelled MTSamples evaluation case…";
  const response = await fetch("/api/demo");
  const demo = await response.json();
  candidate.value = demo.transcript;
  source.value = demo.sourceTranscript;
  status.textContent = `Loaded ${demo.metadata.specialty} case ${demo.metadata.transcriptId}; injected evaluation error: ${demo.metadata.injectedErrorType}.`;
  review();
});
