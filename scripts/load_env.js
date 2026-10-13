"use strict";

const fs = require("node:fs");

function parseEnv(content) {
  const values = {};

  for (const [index, originalLine] of content.replace(/^\uFEFF/, "").split(/\r?\n/).entries()) {
    const line = originalLine.trim();
    if (!line || line.startsWith("#")) continue;

    const match = line.match(/^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!match) throw new Error(`Invalid .env syntax on line ${index + 1}.`);

    let value = match[2].trim();
    if (value.startsWith('"') || value.startsWith("'")) {
      const quote = value[0];
      if (value.length < 2 || value.at(-1) !== quote) {
        throw new Error(`Unclosed quoted value in .env on line ${index + 1}.`);
      }
      value = value.slice(1, -1);
      if (quote === '"') value = value.replace(/\\n/g, "\n").replace(/\\r/g, "\r").replace(/\\"/g, '"').replace(/\\\\/g, "\\");
    } else {
      value = value.replace(/\s+#.*$/, "").trim();
    }
    values[match[1]] = value;
  }

  return values;
}

function loadEnvFile(filePath, environment = process.env) {
  if (!fs.existsSync(filePath)) return;
  const values = parseEnv(fs.readFileSync(filePath, "utf8"));
  for (const [key, value] of Object.entries(values)) {
    if (environment[key] === undefined) environment[key] = value;
  }
}

function getPublicApiBaseUrl(value = process.env.EHR_API_BASE_URL || "") {
  const apiBaseUrl = value.trim().replace(/\/+$/, "");
  if (!apiBaseUrl) return "";

  let parsed;
  try {
    parsed = new URL(apiBaseUrl);
  } catch {
    throw new Error("EHR_API_BASE_URL must be an absolute HTTP(S) URL.");
  }
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("EHR_API_BASE_URL must be an HTTP(S) URL without credentials, query, or fragment.");
  }
  return apiBaseUrl;
}

module.exports = { getPublicApiBaseUrl, loadEnvFile, parseEnv };
