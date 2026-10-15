"use strict";

// Single-process local demonstration store. The cloud Lambda path is separate.
const fs = require("node:fs");
const path = require("node:path");
const { createHash, randomUUID } = require("node:crypto");

const root = path.resolve(__dirname, "../..");
const dataDir = path.resolve(process.env.EHR_LOCAL_DATA_DIR || path.join(root, ".ehr-local-data"));
const storePath = path.join(dataDir, "jobs.json");
const pipelineFingerprint = buildFingerprint();

function sha256(value) { return createHash("sha256").update(value).digest("hex"); }

function buildFingerprint() {
  const files = [
    "src/backend/web_api.py", "src/backend/completeness.py", "src/backend/stage_a/contract.py",
    "src/backend/stage_a/extract.py", "src/backend/stage_a/verify.py",
    "src/backend/stage_b/stage_b.py", "src/backend/stage_b/schema.py",
    "src/backend/stage_b/prompts/plain_v1.txt", "src/backend/stage_b/prompts/rag_v1.txt",
    "dataset/reference/rxnorm_lexicon.json", "dataset/reference/icd10cm_index.json",
  ];
  const parts = files.map((file) => {
    try { return [file, sha256(fs.readFileSync(path.join(root, file)))]; }
    catch { return [file, "unavailable"]; }
  });
  return sha256(JSON.stringify({
    stageA: "2.0.0", stageB: "2.0.0", mode: process.env.STAGE_B_MODE || "stage_a_only",
    model: process.env.GEMINI_MODEL || "default", files: parts,
  }));
}

function readJobs() {
  try {
    const parsed = JSON.parse(fs.readFileSync(storePath, "utf8"));
    return Array.isArray(parsed.jobs) ? parsed.jobs : [];
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw new Error("Local job history could not be read. Preserve the data file and inspect its JSON before continuing.");
  }
}

function writeJobs(jobs) {
  fs.mkdirSync(dataDir, { recursive: true });
  const tempPath = `${storePath}.${process.pid}.${randomUUID()}.tmp`;
  fs.writeFileSync(tempPath, JSON.stringify({ schemaVersion: 1, jobs }, null, 2), { encoding: "utf8", mode: 0o600 });
  fs.renameSync(tempPath, storePath);
}

function getJob(jobId) {
  return readJobs().find((job) => job.jobId === jobId) || null;
}

function submitJob({ transcript, sourceTranscript = "", forceRerun = false }) {
  const normalize = (value) => String(value).normalize("NFKC").replace(/\r\n?/g, "\n").trim();
  const normalizedTranscript = normalize(transcript);
  const normalizedSource = normalize(sourceTranscript);
  const key = sha256(JSON.stringify({ transcript: normalizedTranscript, source: normalizedSource, pipelineFingerprint }));
  const jobs = readJobs();
  const active = jobs.find((job) => job.contentHash === key && ["QUEUED", "PROCESSING"].includes(job.status));
  if (active) return { job: active, reusedActive: true, cached: false };
  if (!forceRerun) {
    const cached = jobs.find((job) => job.contentHash === key && job.pipelineFingerprint === pipelineFingerprint && job.cacheable === true && job.status === "COMPLETED");
    if (cached) {
      const now = new Date().toISOString();
      const job = {
        jobId: randomUUID(), submittedAt: now, status: "COMPLETED", completedAt: now,
        processingDurationMs: 0, contentHash: key, pipelineFingerprint, pipelineVersion: cached.pipelineVersion,
        cacheHit: true, cacheSourceJobId: cached.jobId, cacheable: true,
        reviewStatus: "PENDING", result: JSON.parse(JSON.stringify(cached.cachedResult || cached.result)),
        auditHistory: [{ at: now, action: "CACHE_RESULT_REUSED", sourceJobId: cached.jobId }],
      };
      for (const finding of job.result?.stage_b_findings || []) {
        delete finding.reviewStatus; delete finding.reviewReason; delete finding.reviewNote;
      }
      jobs.unshift(job);
      writeJobs(jobs);
      return { job, reusedActive: false, cached: true };
    }
  }
  const job = {
    jobId: randomUUID(), submittedAt: new Date().toISOString(), status: "QUEUED",
    contentHash: key, pipelineFingerprint, pipelineVersion: "stage-a-2.0.0/stage-b-2.0.0",
    cacheHit: false, cacheable: false, reviewStatus: "PENDING", result: null, auditHistory: [],
  };
  jobs.unshift(job);
  writeJobs(jobs);
  return { job, reusedActive: false, cached: false };
}

function transition(jobId, status, patch = {}) {
  const jobs = readJobs();
  const index = jobs.findIndex((job) => job.jobId === jobId);
  if (index < 0) return null;
  const job = jobs[index];
  const allowed = {
    QUEUED: new Set(["PROCESSING", "FAILED"]),
    PROCESSING: new Set(["COMPLETED", "REVIEW_REQUIRED", "FAILED"]),
  };
  if (job.status !== status && !allowed[job.status]?.has(status)) throw new Error(`Invalid job state transition ${job.status} -> ${status}.`);
  Object.assign(job, patch, { status });
  if (patch.result && patch.cacheable) job.cachedResult = JSON.parse(JSON.stringify(patch.result));
  jobs[index] = job;
  writeJobs(jobs);
  return job;
}

function listJobs({ status, reviewStatus, from, to, search, limit = 20, cursor = 0 } = {}) {
  const safeLimit = Math.max(1, Math.min(100, Number(limit) || 20));
  const safeCursor = Math.max(0, Number(cursor) || 0);
  let jobs = readJobs();
  if (status) jobs = jobs.filter((job) => job.status === status);
  if (reviewStatus) jobs = jobs.filter((job) => job.reviewStatus === reviewStatus);
  if (from) jobs = jobs.filter((job) => job.submittedAt >= from);
  if (to) jobs = jobs.filter((job) => job.submittedAt <= to);
  if (search) jobs = jobs.filter((job) => job.jobId.toLowerCase().includes(String(search).toLowerCase()));
  return { items: jobs.slice(safeCursor, safeCursor + safeLimit).map(({ result, ...summary }) => ({
    ...summary, findingCount: result?.stage_b_findings?.length || 0,
  })), nextCursor: safeCursor + safeLimit < jobs.length ? safeCursor + safeLimit : null, total: jobs.length };
}

function overview() {
  const jobs = readJobs();
  const counts = { total: jobs.length, completed: 0, active: 0, failed: 0, reviewRequired: 0, cacheHits: 0 };
  const durations = [];
  const severity = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const job of jobs) {
    if (job.status === "COMPLETED") counts.completed++;
    if (["QUEUED", "PROCESSING"].includes(job.status)) counts.active++;
    if (job.status === "FAILED") counts.failed++;
    const pendingFinding = (job.result?.stage_b_findings || []).some((finding) => finding.reviewStatus !== "DISMISSED");
    const needsReview = job.reviewStatus !== "REVIEWED"
      && (job.status === "REVIEW_REQUIRED" || job.result?.completeness_review?.human_review_required || pendingFinding || job.reviewStatus === "ESCALATED");
    if (needsReview) counts.reviewRequired++;
    if (job.cacheHit) counts.cacheHits++;
    if (Number.isFinite(job.processingDurationMs)) durations.push(job.processingDurationMs);
    for (const finding of job.result?.stage_b_findings || []) {
      const level = String(finding.severity || "").toLowerCase();
      if (Object.hasOwn(severity, level)) severity[level]++;
    }
  }
  return {
    counts,
    averageProcessingDurationMs: durations.length ? Math.round(durations.reduce((a, b) => a + b, 0) / durations.length) : null,
    cacheHitRate: jobs.length ? counts.cacheHits / jobs.length : null,
    findingDistribution: severity,
    recentActivity: jobs.slice(0, 6).map(({ result, ...summary }) => ({ ...summary, findingCount: result?.stage_b_findings?.length || 0 })),
    metricsDefinition: "Job counts are per request record; review required counts each job once; average duration uses completed jobs with a recorded duration; cache-hit rate uses all recorded jobs.",
  };
}

function addReviewAction(jobId, { action, findingId, reason = "", note = "" }) {
  const jobs = readJobs();
  const index = jobs.findIndex((job) => job.jobId === jobId);
  if (index < 0) return null;
  const job = jobs[index];
  const allowed = new Set(["mark_reviewed", "dismiss_finding", "escalate_finding"]);
  if (!allowed.has(action)) throw new Error("Unsupported review action.");
  if ((action === "dismiss_finding" || action === "escalate_finding") && !reason.trim()) throw new Error("A reason is required for dismissing or escalating a finding.");
  if (action !== "mark_reviewed") {
    const finding = (job.result?.stage_b_findings || []).find((item) => item.id === findingId);
    if (!finding) throw new Error("The finding was not found in this job.");
    finding.reviewStatus = action === "dismiss_finding" ? "DISMISSED" : "ESCALATED";
    if (action === "escalate_finding") job.reviewStatus = "ESCALATED";
    finding.reviewReason = reason.trim();
    finding.reviewNote = note.trim();
  } else {
    job.reviewStatus = "REVIEWED";
    job.reviewedAt = new Date().toISOString();
  }
  const at = new Date().toISOString();
  job.auditHistory = [...(job.auditHistory || []), {
    at, action: action.toUpperCase(), ...(findingId ? { findingId } : {}),
    ...(reason.trim() ? { reason: reason.trim() } : {}), ...(note.trim() ? { note: note.trim() } : {}),
    actor: "unauthenticated local demonstration session",
  }];
  jobs[index] = job;
  writeJobs(jobs);
  return job;
}

function markRestartedJobsFailed() {
  const jobs = readJobs();
  let changed = false;
  for (const job of jobs) {
    if (["QUEUED", "PROCESSING"].includes(job.status)) {
      job.status = "FAILED";
      job.errorCategory = "local_process_interrupted";
      job.completedAt = new Date().toISOString();
      job.auditHistory = [...(job.auditHistory || []), { at: job.completedAt, action: "PROCESS_RESTART_INTERRUPTED_JOB" }];
      changed = true;
    }
  }
  if (changed) writeJobs(jobs);
}

module.exports = { addReviewAction, getJob, listJobs, markRestartedJobsFailed, overview, submitJob, transition };
