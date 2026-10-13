"use strict";

const fs = require("node:fs");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..");
const { getPublicApiBaseUrl, loadEnvFile } = require("./load_env");

loadEnvFile(path.join(projectRoot, ".env"));

const source = path.join(projectRoot, "src", "frontend");
const output = path.join(projectRoot, "build", "frontend");
const apiBaseUrl = getPublicApiBaseUrl();

fs.rmSync(output, { recursive: true, force: true });
fs.cpSync(source, output, { recursive: true });
fs.writeFileSync(
  path.join(output, "config.js"),
  `window.EHR_API_BASE_URL = ${JSON.stringify(apiBaseUrl)};\n`,
  "utf8"
);
console.log(`Frontend built at ${path.relative(projectRoot, output)} (API base ${apiBaseUrl || "same origin"}).`);
