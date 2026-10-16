"""Gemini REST client with stdlib-only cache, rate-limit, and retries."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from threading import Lock

from .schema import OUTPUT_SCHEMA

DEFAULT_MODEL = "gemini-3.8-flash"
_LOGGER = logging.getLogger(__name__)


def _generate_content_output_text(response: dict) -> str:
    parts = (
        response.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )
    text_parts = [p.get("text", "") for p in parts if isinstance(p, dict)]
    text = "".join(text_parts).strip()
    if not text:
        raise ValueError("Gemini generateContent response contains no model output text")
    return text


def _is_lambda_env() -> bool:
    return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or os.environ.get("LAMBDA_TASK_ROOT"))


def _project_root() -> Path:
    d = Path(__file__).resolve()
    for parent in d.parents:
        if (parent / "dataset").is_dir():
            return parent
    return Path.cwd()


class JsonlCache:
    def __init__(self, path: Path | None = None):
        if path is None:
            path = Path("/tmp/llm_cache.jsonl") if _is_lambda_env() else _project_root() / "dataset" / "cache" / "llm_cache.jsonl"
        self.path = Path(path)
        self._items = {}
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    self._items[item["key"]] = item["value"]
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            self._items = {}
            _LOGGER.warning("llm_cache_load_failed error_type=%s", type(exc).__name__)

    def get(self, key: str):
        return self._items.get(key)

    def set(self, key: str, value: dict):
        if key in self._items:
            return
        self._items[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "value": value}, separators=(",", ":")) + "\n")
        except OSError as exc:
            _LOGGER.warning("llm_cache_persist_failed error_type=%s", type(exc).__name__)


class RateLimiter:
    def __init__(self, rpm: int):
        self.interval = 60.0 / max(1, rpm)
        self.lock = Lock()
        self.last = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = self.interval - (now - self.last)
            if delay > 0:
                time.sleep(delay)
            self.last = time.monotonic()


class GeminiClient:
    def __init__(self, *, api_key: str | None = None, model: str | None = None, rpm: int | None = None, cache: JsonlCache | None = None):
        self.api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY")
        self.model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
        self.cache = cache or JsonlCache()
        self.rate_limiter = RateLimiter(int(rpm or os.environ.get("GEMINI_RPM") or "15"))
        self.calls_made = 0

    def cache_key(self, prompt: str) -> str:
        return hashlib.sha256((self.model + "\n" + prompt).encode("utf-8")).hexdigest()

    def generate_json(self, prompt: str, *, dry_run: bool = False) -> dict:
        key = self.cache_key(prompt)
        cached = self.cache.get(key)
        if cached:
            return {**cached, "cache_hit": True}
        if dry_run:
            return {
                "json": {
                    "entity_verdicts": [],
                    "additional_concerns": [],
                    "source_discrepancies": [],
                },
                "raw": "",
                "usage": {"prompt_tokens": len(prompt.split()), "output_tokens": 0},
                "latency_ms": 0,
                "cache_hit": False,
                "dry_run": True,
            }
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")

        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": OUTPUT_SCHEMA}},
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        raw = self._post_json(url, body)
        text = _generate_content_output_text(raw)
        parsed = json.loads(text)
        usage = raw.get("usageMetadata", {})
        value = {
            "json": parsed,
            "raw": text,
            "usage": {
                "prompt_tokens": usage.get("promptTokenCount", len(prompt.split())),
                "output_tokens": usage.get("candidatesTokenCount", len(text.split())),
            },
            "latency_ms": raw.get("_latency_ms", 0),
            "cache_hit": False,
            "dry_run": False,
        }
        self.cache.set(key, value)
        return value

    def _post_json(self, url: str, body: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        last_error = None
        for attempt in range(3):
            self.rate_limiter.wait()
            start = time.monotonic()
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
                method="POST",
            )
            try:
                self.calls_made += 1
                with urllib.request.urlopen(req, timeout=60) as resp:
                    out = json.loads(resp.read().decode("utf-8"))
                    out["_latency_ms"] = int((time.monotonic() - start) * 1000)
                    return out
            except urllib.error.HTTPError as exc:
                message = _gemini_http_error_message(exc, self.api_key)
                last_error = RuntimeError(f"HTTP {exc.code}: {message}")
                if exc.code not in {429, 500, 502, 503, 504}:
                    raise last_error from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
            time.sleep(2 ** attempt)
        raise RuntimeError(f"Gemini request failed after retries: {last_error}")


def _gemini_http_error_message(exc: urllib.error.HTTPError, api_key: str | None) -> str:
    try:
        response = json.loads(exc.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "The provider did not return a readable error detail."

    error = response.get("error") if isinstance(response, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str) or not message.strip():
        return "The provider did not return an error message."

    message = " ".join(message.split())
    if api_key:
        message = message.replace(api_key, "[REDACTED]")
    message = re.sub(r"[\x00-\x1f\x7f]", " ", message)
    return message[:400]
