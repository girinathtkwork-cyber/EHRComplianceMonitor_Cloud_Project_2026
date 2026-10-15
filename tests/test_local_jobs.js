"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "ehr-local-jobs-"));
process.env.EHR_LOCAL_DATA_DIR = tempDir;
const jobs = require("../src/backend/local_jobs");

test("same active normalized input reuses one job", () => {
  const first = jobs.submitJob({ transcript: "  Patient reports well.  " });
  const retry = jobs.submitJob({ transcript: "Patient reports well." });
  const forced = jobs.submitJob({ transcript: "Patient reports well.", forceRerun: true });
  assert.equal(retry.reusedActive, true);
  assert.equal(forced.reusedActive, true);
  assert.equal(retry.job.jobId, first.job.jobId);
});

test("state transitions reject invalid paths and allow processing completion", () => {
  const submission = jobs.submitJob({ transcript: "A different synthetic note." });
  assert.throws(() => jobs.transition(submission.job.jobId, "COMPLETED"), /Invalid job state/);
  jobs.transition(submission.job.jobId, "PROCESSING", { startedAt: new Date().toISOString() });
  const completed = jobs.transition(submission.job.jobId, "COMPLETED", {
    result: { verification_status: "completed", stage_b_status: "completed", stage_b_findings: [] },
    cacheable: true, processingDurationMs: 42,
  });
  assert.equal(completed.status, "COMPLETED");
});

test("only valid completed results are reused and force rerun bypasses cache", () => {
  const original = jobs.submitJob({ transcript: "Cache candidate transcript." });
  jobs.transition(original.job.jobId, "PROCESSING");
  jobs.transition(original.job.jobId, "COMPLETED", {
    result: { verification_status: "completed", stage_b_status: "completed", stage_b: { llm_status: "ok" }, stage_b_findings: [{ id: "f-1" }] },
    cacheable: true,
  });
  jobs.addReviewAction(original.job.jobId, { action: "dismiss_finding", findingId: "f-1", reason: "Synthetic test review." });
  const cached = jobs.submitJob({ transcript: "Cache candidate transcript." });
  assert.equal(cached.cached, true);
  assert.equal(cached.job.cacheSourceJobId, original.job.jobId);
  assert.equal(cached.job.result.stage_b_findings[0].reviewStatus, undefined);
  assert.equal(jobs.submitJob({ transcript: "Cache candidate transcript.", forceRerun: true }).job.status, "QUEUED");
});

test("failed and fallback results do not become cache candidates", () => {
  const failed = jobs.submitJob({ transcript: "Failure path synthetic note." });
  jobs.transition(failed.job.jobId, "PROCESSING");
  jobs.transition(failed.job.jobId, "FAILED", { errorCategory: "pipeline_error" });
  assert.equal(jobs.submitJob({ transcript: "Failure path synthetic note." }).cached, false);

  const fallback = jobs.submitJob({ transcript: "Fallback path synthetic note." });
  jobs.transition(fallback.job.jobId, "PROCESSING");
  jobs.transition(fallback.job.jobId, "COMPLETED", {
    result: { verification_status: "completed", stage_b_status: "completed", stage_b: { llm_status: "fallback" } },
    cacheable: false,
  });
  assert.equal(jobs.submitJob({ transcript: "Fallback path synthetic note." }).cached, false);
});

test("history paginates and review actions append unauthenticated audit events", () => {
  const submission = jobs.submitJob({ transcript: "Reviewable synthetic note." });
  jobs.transition(submission.job.jobId, "PROCESSING");
  jobs.transition(submission.job.jobId, "COMPLETED", {
    result: { stage_b_findings: [{ id: "finding-1", severity: "low" }] }, cacheable: true,
  });
  const firstPage = jobs.listJobs({ limit: 2, cursor: 0 });
  assert.equal(firstPage.items.length, 2);
  assert.notEqual(firstPage.nextCursor, null);
  const updated = jobs.addReviewAction(submission.job.jobId, {
    action: "dismiss_finding", findingId: "finding-1", reason: "Reviewed in note context.",
  });
  assert.equal(updated.result.stage_b_findings[0].reviewStatus, "DISMISSED");
  assert.equal(updated.auditHistory.at(-1).actor, "unauthenticated local demonstration session");
  assert.throws(() => jobs.addReviewAction(submission.job.jobId, {
    action: "dismiss_finding", findingId: "finding-1",
  }), /reason is required/);
});

test("overview counts review-needed jobs once and clears the count when reviewed", () => {
  const baselineReviewRequired = jobs.overview().counts.reviewRequired;
  const submission = jobs.submitJob({ transcript: "Review metric synthetic note." });
  jobs.transition(submission.job.jobId, "PROCESSING");
  jobs.transition(submission.job.jobId, "COMPLETED", {
    result: { stage_b_findings: [{ id: "f-1" }, { id: "f-2" }] }, cacheable: false,
  });
  const before = jobs.overview();
  assert.equal(before.counts.reviewRequired, baselineReviewRequired + 1);
  jobs.addReviewAction(submission.job.jobId, { action: "mark_reviewed", note: "Synthetic review complete." });
  assert.equal(jobs.overview().counts.reviewRequired, baselineReviewRequired);
});
