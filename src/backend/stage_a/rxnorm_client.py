"""
RxNorm API Client for Stage A
-------------------------------
Provides verified medication lookups using the public RxNorm REST API.
Uses only urllib.request (stdlib) â€” no `requests` dependency.
All responses are cached to disk for performance and politeness.

API Documentation: https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html
RxClass API: https://lhncbc.nlm.nih.gov/RxNav/APIs/RxClassAPIs.html
"""

import json
import logging
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_LOGGER = logging.getLogger(__name__)

def _find_project_root() -> Path:
    d = _THIS_DIR
    for _ in range(5):
        d = d.parent
        if (d / "dataset").is_dir():
            return d
    return _THIS_DIR.parent.parent.parent

_PROJECT_ROOT = _find_project_root()
_CACHE_PATH = _PROJECT_ROOT / "dataset" / "cache" / "rxnorm_cache.json"

RXNORM_BASE = "https://rxnav.nlm.nih.gov/REST"
RXCLASS_BASE = "https://rxnav.nlm.nih.gov/REST/rxclass"

# Rate limiting: max ~10 req/s â†’ at least 0.1s between requests
_MIN_REQUEST_INTERVAL = 0.1
_last_request_time = 0.0
_MAX_RETRIES = 3
_TIMEOUT = 15  # seconds
_api_metrics = {"requests": 0, "failures": 0, "duration_ms": 0}


class TerminologyUnavailable(RuntimeError):
    """RxNorm could not be reached or returned an unusable response."""


def metrics_snapshot() -> dict:
    return dict(_api_metrics)


# ===========================================================================
# Disk cache
# ===========================================================================

import os
import tempfile

def _is_lambda_env() -> bool:
    return bool(
        os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
        or os.environ.get("LAMBDA_TASK_ROOT")
    )


class DiskCache:
    """Simple JSON-backed disk cache for API responses.
    
    When running in AWS Lambda, writes automatically route to /tmp
    and read-only filesystem errors are gracefully tolerated.
    """

    def __init__(self, path: Path | None = None):
        if path:
            self.path = Path(path)
        elif _is_lambda_env():
            self.path = Path("/tmp") / "rxnorm_cache.json"
        else:
            self.path = _CACHE_PATH

        self._cache: dict = {}
        self._dirty = False
        self._hits = 0
        self._misses = 0
        self._load()

    def _load(self):
        # 1. Try to load from self.path if it exists
        if self.path.exists():
            try:
                with open(self.path, encoding="utf-8") as f:
                    self._cache = json.load(f)
                    return
            except (json.JSONDecodeError, OSError):
                self._cache = {}

        # 2. If self.path does not exist (or empty in /tmp), try packaged cache locations
        candidates = [
            _CACHE_PATH,
            Path("dataset/cache/rxnorm_cache.json"),
            _THIS_DIR.parent.parent.parent / "dataset" / "cache" / "rxnorm_cache.json",
        ]
        for c in candidates:
            if c.exists():
                try:
                    with open(c, encoding="utf-8") as f:
                        self._cache = json.load(f)
                        return
                except (json.JSONDecodeError, OSError):
                    continue

    def get(self, key: str):
        if key in self._cache:
            self._hits += 1
            return self._cache[key]
        self._misses += 1
        return None

    def contains(self, key: str) -> bool:
        return key in self._cache

    def set(self, key: str, value):
        self._cache[key] = value
        self._dirty = True

    def save(self):
        if not self._dirty:
            return

        target_path = self.path
        if _is_lambda_env():
            target_path = Path("/tmp") / "rxnorm_cache.json"

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, separators=(",", ":"))
            self._dirty = False
        except (OSError, PermissionError):
            # Tolerates read-only filesystem: fallback to tempfile
            try:
                fallback_path = Path(tempfile.gettempdir()) / "rxnorm_cache.json"
                fallback_path.parent.mkdir(parents=True, exist_ok=True)
                with open(fallback_path, "w", encoding="utf-8") as f:
                    json.dump(self._cache, f, separators=(",", ":"))
                self._dirty = False
            except (OSError, PermissionError) as fallback_exc:
                _LOGGER.warning(
                    "rxnorm_cache_persist_failed error_type=%s",
                    type(fallback_exc).__name__,
                )

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    @property
    def size(self) -> int:
        return len(self._cache)


# Global cache instance
_cache: DiskCache | None = None


def get_cache() -> DiskCache:
    global _cache
    if _cache is None:
        _cache = DiskCache()
    return _cache


def reset_cache(cache_obj: DiskCache | None = None):
    """Replace the global cache (useful for testing)."""
    global _cache
    _cache = cache_obj


# ===========================================================================
# HTTP helpers
# ===========================================================================

def _rate_limit():
    """Enforce rate limiting."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.monotonic()


def _api_get(url: str, retries: int = _MAX_RETRIES) -> dict | None:
    """Make a GET request with retry and exponential backoff.
    
    Returns parsed JSON dict, or None on failure.
    """
    started = time.monotonic()
    _api_metrics["requests"] += 1
    for attempt in range(retries):
        _rate_limit()
        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                _api_metrics["duration_ms"] += int((time.monotonic() - started) * 1000)
                return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                _api_metrics["duration_ms"] += int((time.monotonic() - started) * 1000)
                return None
            if attempt < retries - 1:
                wait = 2 ** attempt
                time.sleep(wait)
            else:
                _api_metrics["failures"] += 1
                _api_metrics["duration_ms"] += int((time.monotonic() - started) * 1000)
                _LOGGER.warning("rxnorm_request_failed error_type=HTTPError status=%s attempts=%d", e.code, retries)
                raise TerminologyUnavailable(f"RxNorm HTTP failure ({e.code})") from e
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                time.sleep(wait)
            else:
                _api_metrics["failures"] += 1
                _api_metrics["duration_ms"] += int((time.monotonic() - started) * 1000)
                _LOGGER.warning("rxnorm_request_failed error_type=%s attempts=%d", type(e).__name__, retries)
                raise TerminologyUnavailable(f"RxNorm transport/response failure ({type(e).__name__})") from e
    return None


# ===========================================================================
# RxNorm lookups
# ===========================================================================

def rxnorm_exact_match(drug_name: str) -> dict | None:
    """Exact match via /rxcui.json?name=<name>&search=0."""
    cache = get_cache()
    cache_key = f"exact:{drug_name.lower()}"
    if cache.contains(cache_key):
        return cache.get(cache_key)

    encoded = urllib.parse.quote(drug_name)
    url = f"{RXNORM_BASE}/rxcui.json?name={encoded}&search=0"
    data = _api_get(url)

    result = None
    if data:
        id_group = data.get("idGroup", {})
        rxnorm_ids = id_group.get("rxnormId")
        if rxnorm_ids and len(set(rxnorm_ids)) == 1:
            result = {"rxcui": rxnorm_ids[0], "match_type": "exact"}
        elif rxnorm_ids:
            result = {"match_type": "ambiguous", "candidate_rxcuis": list(dict.fromkeys(rxnorm_ids))}

    cache.set(cache_key, result)
    return result


def rxnorm_approximate_match(drug_name: str) -> dict | None:
    """Approximate match via /approximateTerm.json?term=<name>&maxEntries=3."""
    cache = get_cache()
    cache_key = f"approx:{drug_name.lower()}"
    if cache.contains(cache_key):
        return cache.get(cache_key)

    encoded = urllib.parse.quote(drug_name)
    url = f"{RXNORM_BASE}/approximateTerm.json?term={encoded}&maxEntries=3"
    data = _api_get(url)

    result = None
    if data:
        candidates = data.get("approximateGroup", {}).get("candidate", [])
        if candidates:
            best = candidates[0]
            result = {
                "rxcui": best.get("rxcui"),
                "match_type": "ambiguous" if len(candidates) > 1 else "approximate",
                "score": best.get("score"),
                "candidate_count": len(candidates),
            }

    cache.set(cache_key, result)
    return result


def get_rxcui_properties(rxcui: str) -> dict | None:
    """Get concept properties: name, tty, etc. via /rxcui/<rxcui>/properties.json."""
    cache = get_cache()
    cache_key = f"props:{rxcui}"
    if cache.contains(cache_key):
        return cache.get(cache_key)

    url = f"{RXNORM_BASE}/rxcui/{rxcui}/properties.json"
    data = _api_get(url)

    result = None
    if data:
        props = data.get("properties")
        if props:
            result = {
                "name": props.get("name"),
                "tty": props.get("tty"),
                "rxcui": props.get("rxcui"),
            }

    cache.set(cache_key, result)
    return result


def get_ingredient(rxcui: str) -> dict | None:
    """Resolve a concept to its ingredient(s) via /rxcui/<rxcui>/related.json?tty=IN.
    
    For brand names, this resolves to the generic ingredient.
    """
    cache = get_cache()
    cache_key = f"ingredient:{rxcui}"
    if cache.contains(cache_key):
        return cache.get(cache_key)

    url = f"{RXNORM_BASE}/rxcui/{rxcui}/related.json?tty=IN"
    data = _api_get(url)

    result = None
    if data:
        groups = data.get("relatedGroup", {}).get("conceptGroup", [])
        for group in groups:
            concepts = group.get("conceptProperties", [])
            if concepts:
                ing = concepts[0]
                result = {
                    "rxcui": ing.get("rxcui"),
                    "name": ing.get("name"),
                    "tty": ing.get("tty"),
                }
                break

    cache.set(cache_key, result)
    return result


def get_indications(rxcui: str) -> dict:
    """Fetch therapeutic indications from RxClass MED-RT (may_treat, may_prevent).
    
    Returns {indications: [...], indications_status: "found"|"unknown"}.
    Coverage is incomplete; when nothing is returned, status is "unknown", never "none".
    """
    cache = get_cache()
    cache_key = f"indications:{rxcui}"
    if cache.contains(cache_key):
        return cache.get(cache_key)

    indications = []

    for rela in ("may_treat", "may_prevent"):
        url = (
            f"{RXCLASS_BASE}/class/byRxcui.json"
            f"?rxcui={rxcui}&relaSource=MEDRT&relas={rela}"
        )
        data = _api_get(url)
        if data:
            entries = data.get("rxclassMinConceptList", {}).get(
                "rxclassMinConcept", []
            )
            for entry in entries:
                class_name = entry.get("className", "")
                if class_name:
                    indications.append({
                        "condition": class_name,
                        "relationship": rela,
                        "source": "MED-RT",
                    })

    result = {
        "indications": indications,
        "indications_status": "found" if indications else "unknown",
    }

    cache.set(cache_key, result)
    return result


def get_known_strengths(rxcui: str) -> list[str]:
    """Fetch known product strengths for an ingredient.
    
    Uses /rxcui/<rxcui>/related.json?tty=SCD (Semantic Clinical Drug)
    to find dosage forms and extract strengths.
    """
    cache = get_cache()
    cache_key = f"strengths:{rxcui}"
    if cache.contains(cache_key):
        return cache.get(cache_key)

    url = f"{RXNORM_BASE}/rxcui/{rxcui}/related.json?tty=SCD"
    data = _api_get(url)

    strengths: list[str] = []
    if data:
        groups = data.get("relatedGroup", {}).get("conceptGroup", [])
        for group in groups:
            concepts = group.get("conceptProperties", [])
            for concept in concepts:
                name = concept.get("name", "")
                if name:
                    strengths.append(name)

    # Deduplicate and limit
    unique_strengths = list(dict.fromkeys(strengths))[:20]

    cache.set(cache_key, unique_strengths)
    return unique_strengths


# ===========================================================================
# High-level verification
# ===========================================================================

def verify_medication(drug_name: str) -> dict:
    """Full verification pipeline for a medication name.
    
    Returns a comprehensive result dict with:
    - exists, match_type, rxcui, canonical_name, ingredient,
      indications, known_strengths
    """
    # Exact results may be verified. Approximate results are evidence for
    # review only and are never promoted to a confirmed RxNorm match.
    try:
        result = rxnorm_exact_match(drug_name)
        if result and result.get("match_type") == "ambiguous":
            return {"exists": False, "verification_status": "ambiguous", **result}
        if result is None:
            result = rxnorm_approximate_match(drug_name)
            if result is not None:
                return {"exists": False, "verification_status": "ambiguous", **result}
    except TerminologyUnavailable:
        return {
            "exists": False, "verification_status": "lookup_unavailable",
            "match_type": "unavailable", "rxcui": None,
            "canonical_name": None, "ingredient": None,
            "indications": {"indications": [], "indications_status": "unknown"},
            "known_strengths": [],
        }

    if result is None:
        return {
            "exists": False,
            "verification_status": "unknown",
            "match_type": "none",
            "rxcui": None,
            "canonical_name": None,
            "ingredient": None,
            "indications": {"indications": [], "indications_status": "unknown"},
            "known_strengths": [],
        }

    rxcui = result["rxcui"]
    match_type = result["match_type"]

    # Step 2: Get properties
    try:
        props = get_rxcui_properties(rxcui) or {}
    except TerminologyUnavailable:
        return {"exists": False, "verification_status": "lookup_unavailable",
                "match_type": "unavailable", "rxcui": None,
                "canonical_name": None, "ingredient": None,
                "indications": {"indications": [], "indications_status": "unknown"},
                "known_strengths": []}
    canonical_name = props.get("name")
    if not canonical_name or str(props.get("rxcui")) != str(rxcui):
        return {"exists": False, "verification_status": "unknown",
                "match_type": "unconfirmed", "rxcui": None,
                "canonical_name": None, "ingredient": None,
                "indications": {"indications": [], "indications_status": "unknown"},
                "known_strengths": []}
    tty = props.get("tty")

    # Optional enrichment must not erase the verified exact RxNorm identity.
    enrichment_status = "complete"
    ingredient_info = None
    ingredient_rxcui = rxcui
    indications = {"indications": [], "indications_status": "unknown"}
    known_strengths = []
    try:
        if tty == "BN":
            ing = get_ingredient(rxcui)
            if ing:
                ingredient_info = ing
                ingredient_rxcui = ing["rxcui"]
        elif tty in ("IN", "PIN"):
            ingredient_info = {"rxcui": rxcui, "name": canonical_name, "tty": tty}
        else:
            ing = get_ingredient(rxcui)
            if ing:
                ingredient_info = ing
                ingredient_rxcui = ing["rxcui"]
        indications = get_indications(ingredient_rxcui)
        known_strengths = get_known_strengths(ingredient_rxcui)
    except TerminologyUnavailable:
        enrichment_status = "partial"

    return {
        "exists": True,
        "verification_status": "verified",
        "enrichment_status": enrichment_status,
        "match_type": match_type,
        "rxcui": rxcui,
        "canonical_name": canonical_name,
        "ingredient": ingredient_info,
        "indications": indications,
        "known_strengths": known_strengths,
    }


def save_cache():
    """Persist the cache to disk."""
    cache = get_cache()
    cache.save()


# ===========================================================================
# CLI
# ===========================================================================
if __name__ == "__main__":
    test_drugs = ["Aspirin", "Metformin", "FakeDrugXYZ123", "Lisinopril", "Tylenol"]
    for drug in test_drugs:
        print(f"\n--- {drug} ---")
        result = verify_medication(drug)
        print(json.dumps(result, indent=2))
    save_cache()
    print(f"\nCache hit rate: {get_cache().hit_rate:.1%}")
    print(f"Cache entries: {get_cache().size}")
