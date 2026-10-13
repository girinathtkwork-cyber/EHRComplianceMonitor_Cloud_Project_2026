"use strict";

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { randomUUID } = require("node:crypto");

const root = path.resolve(__dirname, "../frontend");
const projectRoot = path.resolve(__dirname, "../..");
const { getPublicApiBaseUrl, loadEnvFile } = require(path.join(projectRoot, "scripts", "load_env.js"));

loadEnvFile(path.join(projectRoot, ".env"));

const port = Number(process.env.PORT || 3000);
const analysisTimeoutMs = Number(process.env.PIPELINE_TIMEOUT_MS || 240000);
const localJobs = new Map();

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
    if (request.method === "GET" && request.url === "/health") return send(response, 200, { status: "ok", backend: "local", s3UploadConfigured: false });
    if (request.method === "POST" && request.url === "/api/analyse") {
      const result = await runPipeline(await readBody(request));
      return send(response, result.statusCode, result.body);
    }
    if (request.method === "POST" && request.url === "/api/upload") {
      const requestBody = await readBody(request);
      if (!requestBody || typeof requestBody.fileName !== "string" || typeof requestBody.content !== "string") {
        return send(response, 400, { error: "Provide a transcript file name and file content." });
      }
      const jobId = randomUUID();
      const result = await runPipeline({ operation: "upload", fileName: requestBody.fileName, content: requestBody.content, transcript_id: jobId });
      if (result.statusCode !== 200) return send(response, result.statusCode, result.body);
      localJobs.set(jobId, result.body);
      return send(response, 202, { jobId, status: "completed", storage: "local" });
    }
    const jobMatch = request.method === "GET" && request.url.match(/^\/api\/jobs\/([0-9a-f-]{36})$/i);
    if (jobMatch) {
      const result = localJobs.get(jobMatch[1]);
      return result
        ? send(response, 200, { jobId: jobMatch[1], status: "completed", result })
        : send(response, 404, { error: "Upload job was not found on this local server." });
    }
    if (request.method === "GET" && request.url === "/config.js") {
      const apiBaseUrl = getPublicApiBaseUrl();
      return send(response, 200, `window.EHR_API_BASE_URL = ${JSON.stringify(apiBaseUrl)};\n`, "application/javascript");
    }
    const requested = request.url === "/" ? "index.html" : request.url.replace(/^\//, "");
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
