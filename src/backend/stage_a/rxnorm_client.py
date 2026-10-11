"""
RxNorm API Client for Stage A
-------------------------------
Provides verified medication lookups using the public RxNorm REST API.
Uses only urllib.request (stdlib) — no `requests` dependency.
All responses are cached to disk for performance and politeness.

API Documentation: https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html
RxClass API: https://lhncbc.nlm.nih.gov/RxNav/APIs/RxClassAPIs.html
"""

import json
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent.parent
_CACHE_PATH = _PROJECT_ROOT / "dataset" / "cache" / "rxnorm_cache.json"

RXNORM_BASE = "https://rxnav.nlm.nih.gov/REST"
RXCLASS_BASE = "https://rxnav.nlm.nih.gov/REST/rxclass"

# Rate limiting: max ~10 req/s → at least 0.1s between requests
_MIN_REQUEST_INTERVAL = 0.1
_last_request_time = 0.0
_MAX_RETRIES = 3
_TIMEOUT = 15  # seconds


# ===========================================================================
# Disk cache
# ===========================================================================

class DiskCache:
    """Simple JSON-backed disk cache for API responses."""

    def __init__(self, path: Path | None = None):
        self.path = path or _CACHE_PATH
        self._cache: dict = {}
        self._dirty = False
        self._hits = 0
        self._misses = 0
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, encoding="utf-8") as f:
                    self._cache = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._cache = {}

    def get(self, key: str):
        if key in self._cache:
            self._hits += 1
            return self._cache[key]
        self._misses += 1
        return None

    def set(self, key: str, value):
        self._cache[key] = value
        self._dirty = True

    def save(self):
        if self._dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, separators=(",", ":"))
            self._dirty = False

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
    for attempt in range(retries):
        _rate_limit()
        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt < retries - 1:
                wait = 2 ** attempt
                time.sleep(wait)
            else:
                print(f"  [warning] HTTP {e.code} after {retries} attempts: {url}")
                return None
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                time.sleep(wait)
            else:
                print(f"  [warning] Request failed after {retries} attempts: {e}")
                return None
    return None


# ===========================================================================
# RxNorm lookups
# ===========================================================================

def rxnorm_exact_match(drug_name: str) -> dict | None:
    """Exact match via /rxcui.json?name=<name>&search=0."""
    cache = get_cache()
    cache_key = f"exact:{drug_name.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    encoded = urllib.parse.quote(drug_name)
    url = f"{RXNORM_BASE}/rxcui.json?name={encoded}&search=0"
    data = _api_get(url)

    result = None
    if data:
        id_group = data.get("idGroup", {})
        rxnorm_ids = id_group.get("rxnormId")
        if rxnorm_ids:
            result = {"rxcui": rxnorm_ids[0], "match_type": "exact"}

    cache.set(cache_key, result)
    return result


def rxnorm_approximate_match(drug_name: str) -> dict | None:
    """Approximate match via /approximateTerm.json?term=<name>&maxEntries=3."""
    cache = get_cache()
    cache_key = f"approx:{drug_name.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

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
                "match_type": "approximate",
                "score": best.get("score"),
            }

    cache.set(cache_key, result)
    return result


def get_rxcui_properties(rxcui: str) -> dict | None:
    """Get concept properties: name, tty, etc. via /rxcui/<rxcui>/properties.json."""
    cache = get_cache()
    cache_key = f"props:{rxcui}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

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
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

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
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

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
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

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
    # Step 1: Existence check (exact then approximate)
    result = rxnorm_exact_match(drug_name)
    if result is None:
        result = rxnorm_approximate_match(drug_name)

    if result is None:
        return {
            "exists": False,
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
    props = get_rxcui_properties(rxcui) or {}
    canonical_name = props.get("name", drug_name)
    tty = props.get("tty")

    # Step 3: Resolve ingredient (especially for brand names)
    ingredient_info = None
    ingredient_rxcui = rxcui  # Default to self for ingredients
    if tty == "BN":
        ing = get_ingredient(rxcui)
        if ing:
            ingredient_info = ing
            ingredient_rxcui = ing["rxcui"]
    elif tty == "IN" or tty == "PIN":
        ingredient_info = {"rxcui": rxcui, "name": canonical_name, "tty": tty}
    else:
        # Try to resolve anyway
        ing = get_ingredient(rxcui)
        if ing:
            ingredient_info = ing
            ingredient_rxcui = ing["rxcui"]

    # Step 4: Indications
    indications = get_indications(ingredient_rxcui)

    # Step 5: Known strengths
    known_strengths = get_known_strengths(ingredient_rxcui)

    return {
        "exists": True,
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
