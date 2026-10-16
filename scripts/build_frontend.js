"use strict";

const fs = require("node:fs");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..");
const { getPublicApiBaseUrl, loadEnvFile } = require("./load_env");

loadEnvFile(path.join(projectRoot, ".env"));

const source = path.join(projectRoot, "src", "frontend");
const output = path.join(projectRoot, "build", "frontend");
const apiBaseUrl = getPublicApiBaseUrl();
const cognito = {
  domain: String(process.env.EHR_COGNITO_DOMAIN || "").trim().replace(/\/+$/, ""),
  clientId: String(process.env.EHR_COGNITO_CLIENT_ID || "").trim(),
  redirectUri: String(process.env.EHR_COGNITO_REDIRECT_URI || "http://localhost:3000/").trim(),
  logoutUri: String(process.env.EHR_COGNITO_LOGOUT_URI || process.env.EHR_COGNITO_REDIRECT_URI || "http://localhost:3000/").trim(),
};

fs.rmSync(output, { recursive: true, force: true });
fs.cpSync(source, output, { recursive: true });
fs.writeFileSync(
  path.join(output, "config.js"),
  `window.EHR_API_BASE_URL = ${JSON.stringify(apiBaseUrl)};\nwindow.EHR_COGNITO_CONFIG = ${JSON.stringify(cognito)};\n`,
  "utf8"
);
console.log(`Frontend built at ${path.relative(projectRoot, output)} (API base ${apiBaseUrl || "same origin"}).`);
