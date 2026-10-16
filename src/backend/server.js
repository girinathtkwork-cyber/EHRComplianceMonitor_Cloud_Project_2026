"use strict";

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const localJobs = require("./local_jobs");

const root = path.resolve(__dirname, "../frontend");
const projectRoot = path.resolve(__dirname, "../..");
const { getPublicApiBaseUrl, loadEnvFile } = require(path.join(projectRoot, "scripts", "load_env.js"));

loadEnvFile(path.join(projectRoot, ".env"));

const port = Number(process.env.PORT || 3000);
const analysisTimeoutMs = Number(process.env.PIPELINE_TIMEOUT_MS || 240000);
localJobs.markRestartedJobsFailed();

function validateJobInput(transcript, sourceTranscript = "") {
  if (typeof transcript !== "string" || !transcript.trim()) {
    const error = new Error("Enter a transcript to analyse."); error.statusCode = 400; throw error;
  }
  if (transcript.length > 100_000 || typeof sourceTranscript !== "string" || sourceTranscript.length > 100_000) {
    const error = new Error("Transcript and trusted source must each be 100,000 characters or fewer."); error.statusCode = 413; throw error;
  }
  return { transcript: transcript.trim(), sourceTranscript: sourceTranscript.trim() };
}

function startLocalJob(jobId, payload) {
  setImmediate(async () => {
    const startedAt = new Date().toISOString();
    const started = Date.now();
    try {
      localJobs.transition(jobId, "PROCESSING", { startedAt });
      const response = await runPipeline(payload);
      if (response.statusCode !== 200 || !response.body || response.body.verification_status === "error") {
        const category = response.statusCode >= 500 ? "pipeline_error" : "verification_error";
        localJobs.transition(jobId, "FAILED", { completedAt: new Date().toISOString(), processingDurationMs: Date.now() - started, errorCategory: category });
        return;
      }
      const result = response.body;
      const cacheable = result.verification_status === "completed"
        && result.stage_b_status === "completed"
        && result.stage_b?.llm_status !== "fallback";
      localJobs.transition(jobId, "COMPLETED", {
        completedAt: new Date().toISOString(), processingDurationMs: Date.now() - started,
        pipelineVersion: `stage-a-${result.stage_a_version || "unknown"}/stage-b-${result.stage_b_version || "unknown"}`,
        result, cacheable, reviewStatus: "PENDING",
      });
    } catch {
      try {
        localJobs.transition(jobId, "FAILED", { completedAt: new Date().toISOString(), processingDurationMs: Date.now() - started, errorCategory: "pipeline_unavailable" });
      } catch { /* a process interruption will be recovered as FAILED on restart */ }
    }
  });
}

function getPublicCognitoConfig() {
  return {
    domain: String(process.env.EHR_COGNITO_DOMAIN || "").trim().replace(/\/+$/, ""),
    clientId: String(process.env.EHR_COGNITO_CLIENT_ID || "").trim(),
    redirectUri: String(process.env.EHR_COGNITO_REDIRECT_URI || "http://localhost:3000/").trim(),
    logoutUri: String(process.env.EHR_COGNITO_LOGOUT_URI || process.env.EHR_COGNITO_REDIRECT_URI || "http://localhost:3000/").trim(),
  };
}

function parseUploadInput(fileName, content) {
  if (typeof fileName !== "string" || ![".txt", ".json"].includes(path.extname(fileName).toLowerCase())) {
    const error = new Error("Upload a .json or .txt transcript file."); error.statusCode = 400; throw error;
  }
  if (typeof content !== "string" || !content.trim() || Buffer.byteLength(content, "utf8") > 1_000_000) {
    const error = new Error("Transcript file must be non-empty and 1 MB or smaller."); error.statusCode = 413; throw error;
  }
  if (path.extname(fileName).toLowerCase() === ".txt") return validateJobInput(content);
  let parsed;
  try { parsed = JSON.parse(content); }
  catch { const error = new Error("The .json file must contain one valid JSON object."); error.statusCode = 400; throw error; }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    const error = new Error("The .json file must contain one object with a transcript field."); error.statusCode = 400; throw error;
  }
  return validateJobInput(parsed.transcript ?? parsed.transcription, parsed.sourceTranscript ?? parsed.source_transcript ?? "");
}

function send(response, status, body, type = "application/json") {
  response.writeHead(status, { "Content-Type": `${type}; charset=utf-8`, "Cache-Control": "no-store" });
  response.end(Buffer.isBuffer(body) || typeof body === "string" ? body : JSON.stringify(body));
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    let tooLarge = false;
    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > 1_000_000) tooLarge = true;
      else chunks.push(chunk);
    });
    request.on("end", () => {
      if (tooLarge) {
        const error = new Error("Request body must be 1 MB or smaller.");
        error.statusCode = 413;
        reject(error);
        return;
      }
      try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}")); }
      catch {
        const error = new Error("Request body must be valid JSON.");
        error.statusCode = 400;
        reject(error);
      }
    });
    request.on("error", reject);
  });
}

function runPipeline(payload) {
  return new Promise((resolve, reject) => {
    const python = process.env.PYTHON_EXECUTABLE || (process.platform === "win32" ? "python" : "python3");
    const child = spawn(python, [path.join(projectRoot, "scripts", "run_web_api.py")], {
      cwd: projectRoot,
      env: process.env,
      windowsHide: true,
      stdio: ["pipe", "pipe", "ignore"]
    });
    let output = "";
    let settled = false;
    const finish = (error, result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (error) reject(error);
      else resolve(result);
    };
    const timeout = setTimeout(() => {
      child.kill();
      const error = new Error("The verification request timed out. Please try again.");
      error.statusCode = 504;
      finish(error);
    }, analysisTimeoutMs);
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      output += chunk;
      if (output.length > 5_000_000) {
        child.kill();
        finish(new Error("The verification response exceeded the allowed size."));
      }
    });
    child.on("error", () => finish(new Error("The Python verification runtime could not be started.")));
    child.on("close", () => {
      if (settled) return;
      try {
        const result = JSON.parse(output);
        if (!Number.isInteger(result.statusCode) || !result.body) throw new Error("Invalid pipeline response.");
        finish(null, result);
      } catch {
        finish(new Error("The verification pipeline returned an invalid response."));
      }
    });
    child.stdin.on("error", () => {});
    child.stdin.end(JSON.stringify(payload));
  });
}

const server = http.createServer(async (request, response) => {
  try {
    const requestUrl = new URL(request.url, "http://127.0.0.1");
    if (request.method === "GET" && requestUrl.pathname === "/health") return send(response, 200, { status: "ok", backend: "local", storage: "local_json_demo", worker: "local_background_executor", cloudQueueConfigured: false });
    if (request.method === "GET" && requestUrl.pathname === "/api/overview") return send(response, 200, localJobs.overview());
    if (request.method === "GET" && requestUrl.pathname === "/api/jobs") {
      return send(response, 200, localJobs.listJobs({
        status: requestUrl.searchParams.get("status") || undefined,
        reviewStatus: requestUrl.searchParams.get("reviewStatus") || undefined,
        from: requestUrl.searchParams.get("from") || undefined,
        to: requestUrl.searchParams.get("to") || undefined,
        search: requestUrl.searchParams.get("search") || undefined,
        limit: requestUrl.searchParams.get("limit") || 20,
        cursor: requestUrl.searchParams.get("cursor") || 0,
      }));
    }
    if (request.method === "POST" && requestUrl.pathname === "/api/jobs") {
      const body = await readBody(request);
      if (!body || Array.isArray(body) || typeof body !== "object") return send(response, 400, { error: "Provide a JSON object with a transcript string." });
      const input = validateJobInput(body.transcript ?? body.transcription, body.sourceTranscript ?? body.source_transcript ?? "");
      const submission = localJobs.submitJob({ ...input, forceRerun: body.forceRerun === true });
      if (!submission.reusedActive && !submission.cached) startLocalJob(submission.job.jobId, input);
      return send(response, submission.reusedActive ? 200 : 202, {
        jobId: submission.job.jobId, status: submission.job.status,
        cacheHit: submission.cached, reusedActiveJob: submission.reusedActive,
        storage: "local_json_demo",
      });
    }
    if (request.method === "POST" && request.url === "/api/analyse") {
      const result = await runPipeline(await readBody(request));
      return send(response, result.statusCode, result.body);
    }
    if (request.method === "POST" && requestUrl.pathname === "/api/upload") {
      const requestBody = await readBody(request);
      if (!requestBody || typeof requestBody.fileName !== "string" || typeof requestBody.content !== "string") {
        return send(response, 400, { error: "Provide a transcript file name and file content." });
      }
      const input = parseUploadInput(requestBody.fileName, requestBody.content);
      const submission = localJobs.submitJob({ ...input, forceRerun: requestBody.forceRerun === true });
      if (!submission.reusedActive && !submission.cached) startLocalJob(submission.job.jobId, input);
      return send(response, submission.reusedActive ? 200 : 202, {
        jobId: submission.job.jobId, status: submission.job.status,
        cacheHit: submission.cached, reusedActiveJob: submission.reusedActive,
        storage: "local_json_demo",
      });
    }
    const reviewMatch = request.method === "POST" && requestUrl.pathname.match(/^\/api\/jobs\/([0-9a-f-]{36})\/review$/i);
    if (reviewMatch) {
      const body = await readBody(request);
      if (!body || Array.isArray(body) || typeof body !== "object") return send(response, 400, { error: "Provide a review action object." });
      const job = localJobs.addReviewAction(reviewMatch[1], body);
      return job ? send(response, 200, job) : send(response, 404, { error: "Verification job was not found." });
    }
    const jobMatch = request.method === "GET" && requestUrl.pathname.match(/^\/api\/jobs\/([0-9a-f-]{36})$/i);
    if (jobMatch) {
      const job = localJobs.getJob(jobMatch[1]);
      return job ? send(response, 200, job) : send(response, 404, { error: "Verification job was not found." });
    }
    if (request.method === "GET" && request.url === "/config.js") {
      const apiBaseUrl = getPublicApiBaseUrl();
      const cognito = getPublicCognitoConfig();
      return send(response, 200, `window.EHR_API_BASE_URL = ${JSON.stringify(apiBaseUrl)};\nwindow.EHR_COGNITO_CONFIG = ${JSON.stringify(cognito)};\n`, "application/javascript");
    }
    // OAuth redirects return to `/` with `?code=...&state=...`; resolve the
    // static asset from the URL path while leaving query handling to auth.js.
    const requested = requestUrl.pathname === "/" ? "index.html" : decodeURIComponent(requestUrl.pathname.replace(/^\//, ""));
    const file = path.resolve(root, requested);
    if (!file.startsWith(root) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) return send(response, 404, { error: "Not found" });
    const contentType = file.endsWith(".css") ? "text/css" : file.endsWith(".js") ? "application/javascript" : "text/html";
    return send(response, 200, fs.readFileSync(file), contentType);
  } catch (error) {
    const status = error.statusCode || 500;
    return send(response, status, { error: status < 500 ? error.message : "Unable to process the request." });
  }
});

server.listen(port, "127.0.0.1", () => console.log(`EHR transcription safety gate running at http://127.0.0.1:${port}`));
