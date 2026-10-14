"""
Tests for RxNorm client against recorded fixtures (no network needed).
"""

import json
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from backend.stage_a.rxnorm_client import (
    DiskCache,
    verify_medication,
    rxnorm_exact_match,
    rxnorm_approximate_match,
    get_rxcui_properties,
    get_ingredient,
    get_indications,
    get_known_strengths,
    reset_cache,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def use_fixture_cache():
    """Replace global cache with a fixture-based cache for all tests."""
    fixture_path = FIXTURES_DIR / "rxnorm_fixture_cache.json"

    # Create fixture cache with pre-recorded responses
    fixture_data = {
        "exact:aspirin": {"rxcui": "1191", "match_type": "exact"},
        "exact:metformin": {"rxcui": "6809", "match_type": "exact"},
        "exact:fakedrug123": None,
        "exact:lisinopril": {"rxcui": "29046", "match_type": "exact"},
        "exact:tylenol": {"rxcui": "202433", "match_type": "exact"},
        "approx:fakedrug123": None,
        "props:1191": {"name": "aspirin", "tty": "IN", "rxcui": "1191"},
        "props:6809": {"name": "metformin", "tty": "IN", "rxcui": "6809"},
        "props:29046": {"name": "lisinopril", "tty": "IN", "rxcui": "29046"},
        "props:202433": {"name": "Tylenol", "tty": "BN", "rxcui": "202433"},
        "ingredient:1191": {"rxcui": "1191", "name": "aspirin", "tty": "IN"},
        "ingredient:6809": {"rxcui": "6809", "name": "metformin", "tty": "IN"},
        "ingredient:29046": {"rxcui": "29046", "name": "lisinopril", "tty": "IN"},
        "ingredient:202433": {"rxcui": "161", "name": "acetaminophen", "tty": "IN"},
        "indications:1191": {
            "indications": [
                {"condition": "Pain", "relationship": "may_treat", "source": "MED-RT"},
                {"condition": "Fever", "relationship": "may_treat", "source": "MED-RT"},
            ],
            "indications_status": "found",
        },
        "indications:6809": {
            "indications": [
                {"condition": "Diabetes Mellitus, Non-Insulin-Dependent", "relationship": "may_treat", "source": "MED-RT"},
            ],
            "indications_status": "found",
        },
        "indications:29046": {
            "indications": [],
            "indications_status": "unknown",
        },
        "strengths:1191": [
            "aspirin 81 MG Oral Tablet",
            "aspirin 325 MG Oral Tablet",
            "aspirin 500 MG Oral Tablet",
        ],
        "strengths:6809": [
            "metformin hydrochloride 500 MG Oral Tablet",
            "metformin hydrochloride 850 MG Oral Tablet",
            "metformin hydrochloride 1000 MG Oral Tablet",
        ],
        "strengths:29046": [
            "lisinopril 5 MG Oral Tablet",
            "lisinopril 10 MG Oral Tablet",
            "lisinopril 20 MG Oral Tablet",
        ],
    }

    # Write fixture
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    with open(fixture_path, "w") as f:
        json.dump(fixture_data, f)

    # Set up fixture cache
    cache = DiskCache(fixture_path)
    reset_cache(cache)
    yield
    reset_cache(None)


class TestRxNormClient:
    def test_exact_match_found(self):
        result = rxnorm_exact_match("aspirin")
        assert result is not None
        assert result["rxcui"] == "1191"
        assert result["match_type"] == "exact"

    def test_exact_match_not_found(self):
        result = rxnorm_exact_match("FakeDrug123")
        assert result is None

    def test_verify_medication_exists(self):
        result = verify_medication("aspirin")
        assert result["exists"] is True
        assert result["rxcui"] == "1191"
        assert result["canonical_name"] == "aspirin"

    def test_verify_medication_not_found(self):
        result = verify_medication("FakeDrug123")
        assert result["exists"] is False
        assert result["match_type"] == "none"

    def test_verify_medication_has_indications(self):
        result = verify_medication("aspirin")
        ind = result["indications"]
        assert ind["indications_status"] == "found"
        assert len(ind["indications"]) >= 1

    def test_verify_medication_unknown_indications(self):
        result = verify_medication("lisinopril")
        ind = result["indications"]
        assert ind["indications_status"] == "unknown"

    def test_verify_medication_has_strengths(self):
        result = verify_medication("metformin")
        assert len(result["known_strengths"]) >= 1
        assert any("500" in s for s in result["known_strengths"])

    def test_verify_brand_name_resolves_ingredient(self):
        result = verify_medication("Tylenol")
        assert result["exists"] is True
        ing = result["ingredient"]
        assert ing is not None
        assert ing["name"] == "acetaminophen"


class TestDiskCache:
    def test_hit_miss(self):
        cache = DiskCache(FIXTURES_DIR / "test_cache.json")
        assert cache.get("nonexistent") is None
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_hit_rate(self):
        cache = DiskCache(FIXTURES_DIR / "test_cache2.json")
        cache.set("a", 1)
        cache.get("a")  # hit
        cache.get("a")  # hit
        cache.get("b")  # miss
        assert cache.hit_rate == pytest.approx(2/3, rel=0.01)
