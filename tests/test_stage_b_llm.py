import io
import json
import sys
import tempfile
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from backend.stage_b.llm_client import GeminiClient, JsonlCache
from backend.stage_b.prompts import build_prompt
from backend.stage_b.schema import validate_response
from backend.stage_b.stage_b import run_stage_b


def _record():
    return {
        "transcript_id": "case-llm",
        "transcription": "Diagnosis: otitis media. Medication: amoxicillin 750mg.",
        "ground_truth_label": {"is_error": True, "error_type": "wrong_dosage", "injected_entity": "750mg", "correct_value": "75mg"},
        "entities": [
            {
                "entity_type": "diagnosis",
                "text": "otitis media",
                "span": [11, 23],
                "routing": "verified_needs_context_check",
                "retrieval_context": {"evidence_text": "[ICD-10-CM] otitis media"},
            },
            {
                "entity_type": "medication",
                "text": "amoxicillin",
                "span": [37, 48],
                "routing": "verified_needs_context_check",
                "retrieval_context": {"evidence_text": "[RxNorm] amoxicillin exists"},
            },
        ],
    }


class FakeClient:
    model = "fake-model"

    def __init__(self, payload=None, fail=False):
        self.payload = payload if payload is not None else {
            "entity_verdicts": [
                {"entity_id": "E2", "verdict": "suspicious", "reason": "not supported", "evidence_used": ["F2"], "severity": "high"}
            ],
            "additional_concerns": [],
            "source_discrepancies": [],
        }
        self.fail = fail
        self.calls_made = 0

    def generate_json(self, prompt, dry_run=False):
        self.calls_made += 0 if dry_run else 1
        if self.fail:
            raise RuntimeError("boom")
        return {"json": self.payload, "usage": {"prompt_tokens": 10, "output_tokens": 5}, "latency_ms": 1, "cache_hit": False}


def test_ground_truth_not_in_prompt():
    prompt, _, _ = build_prompt(_record(), mode="llm_rag")
    assert "ground_truth_label" not in prompt
    assert "injected_entity" not in prompt
    assert "correct_value" not in prompt
    assert "Trusted source transcript:" in prompt
    assert "[Not provided]" in prompt


def test_rag_uncited_suspicious_is_downgraded():
    data, stats = validate_response({
        "entity_verdicts": [
            {"entity_id": "E1", "verdict": "suspicious", "reason": "guess", "evidence_used": [], "severity": "high"}
        ],
        "additional_concerns": [],
        "source_discrepancies": [],
    }, mode="llm_rag", valid_fact_ids={"F1"})
    assert data["entity_verdicts"][0]["verdict"] == "insufficient_evidence"
    assert data["entity_verdicts"][0]["severity"] == "none"
    assert stats["rag_uncited_suspicious_downgrades"] == 1


def test_run_stage_b_does_not_persist_prompt_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("PERSIST_STAGE_B_PROMPTS", raising=False)
    out = run_stage_b(_record(), mode="llm_rag", client=FakeClient(), project_root=tmp_path)
    assert out["stage_b"]["llm_status"] == "ok"
    assert out["stage_b_summary"]["n_findings"] == 1
    finding = out["stage_b_findings"][0]
    assert finding["detection_stage"] == "stage_b_llm"
    assert finding["provenance"] == "gemini_validated_response"
    assert finding["confidence"] == "not_reported"
    assert not (tmp_path / "results" / "prompts" / "llm_rag" / "case-llm.txt").exists()
    assert out["stage_b"]["prompt_path"] is None
    assert out["stage_b"]["request_count"] == 1
    assert out["stage_b"]["request_failures"] == 0
    assert out["stage_b"]["duration_ms"] >= 0


def test_run_stage_b_can_persist_prompt_when_explicitly_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSIST_STAGE_B_PROMPTS", "true")
    out = run_stage_b(_record(), mode="llm_rag", client=FakeClient(), project_root=tmp_path)
    prompt_file = tmp_path / "results" / "prompts" / "llm_rag" / "case-llm.txt"
    assert prompt_file.exists()
    assert "Candidate transcript:" in prompt_file.read_text(encoding="utf-8")
    assert out["stage_b"]["prompt_path"] == str(Path("results") / "prompts" / "llm_rag" / "case-llm.txt")


def test_stage_b_compares_trusted_source_and_reports_exact_quotes(tmp_path):
    record = _record()
    record["source_transcript"] = "Diagnosis: otitis media. Medication: amoxicillin 75mg."
    discrepancy = {
        "type": "contradiction",
        "candidate_excerpt": "amoxicillin 750mg",
        "source_excerpt": "amoxicillin 75mg",
        "reason": "The candidate and trusted source specify different medication strengths.",
    }
    client = FakeClient(payload={
        "entity_verdicts": [],
        "additional_concerns": [],
        "source_discrepancies": [discrepancy],
    })

    out = run_stage_b(record, mode="llm_rag", client=client, project_root=tmp_path)

    assert out["stage_b_summary"]["source_comparison"] is True
    assert out["stage_b"]["source_comparison"] == {
        "status": "compared",
        "discrepancies": [discrepancy],
    }


def test_source_discrepancy_quotes_must_match_both_transcripts():
    data = {
        "entity_verdicts": [],
        "additional_concerns": [],
        "source_discrepancies": [{
            "type": "contradiction",
            "candidate_excerpt": "invented dose",
            "source_excerpt": "amoxicillin 75mg",
            "reason": "The dose differs.",
        }],
    }

    try:
        validate_response(
            data,
            mode="llm_rag",
            valid_fact_ids=set(),
            candidate_transcript="Medication: amoxicillin 750mg.",
            source_transcript="Medication: amoxicillin 75mg.",
        )
    except ValueError as exc:
        assert "quotes must match" in str(exc)
    else:
        raise AssertionError("Fabricated comparison excerpts must be rejected.")


def test_stage_b_fallback_does_not_claim_source_comparison(tmp_path):
    record = _record()
    record["source_transcript"] = "Diagnosis: otitis media."

    out = run_stage_b(record, mode="llm_rag", client=FakeClient(fail=True), project_root=tmp_path)

    assert out["stage_b"]["llm_status"] == "fallback"
    assert out["stage_b"]["request_count"] == 1
    assert out["stage_b"]["request_failures"] == 1
    assert out["stage_b"]["source_comparison"]["status"] == "not_performed"
    assert out["stage_b_summary"]["source_comparison"] is False


def test_lambda_rag_uses_tmp_when_deployment_root_is_read_only(tmp_path, monkeypatch):
    deployment_root = tmp_path / "var" / "task"
    deployment_root.mkdir(parents=True)
    lambda_tmp = tmp_path / "lambda-tmp"
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "synthetic-test")
    monkeypatch.setenv("PERSIST_STAGE_B_PROMPTS", "true")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(lambda_tmp))

    original_mkdir = Path.mkdir

    def deny_deployment_writes(path, *args, **kwargs):
        try:
            path.relative_to(deployment_root)
        except ValueError:
            return original_mkdir(path, *args, **kwargs)
        raise PermissionError("simulated read-only Lambda deployment directory")

    monkeypatch.setattr(Path, "mkdir", deny_deployment_writes)
    output = run_stage_b(
        _record(),
        mode="llm_rag",
        client=FakeClient(),
        project_root=deployment_root,
    )

    assert output["stage_b"]["llm_status"] == "ok"
    assert output["stage_b"]["mode"] == "llm_rag"
    assert (lambda_tmp / "ehr-compliance-monitor" / "results" / "prompts" / "llm_rag" / "case-llm.txt").exists()
    assert not (deployment_root / "results").exists()


def test_invalid_json_failure_falls_back(tmp_path):
    out = run_stage_b(_record(), mode="llm_plain", client=FakeClient(payload=[]), project_root=tmp_path)
    assert out["stage_b"]["llm_status"] == "fallback"
    assert "LLM response" in out["stage_b"]["fallback_reason"]


def test_gemini_request_failure_returns_deterministic_fallback(tmp_path):
    out = run_stage_b(_record(), mode="llm_rag", client=FakeClient(fail=True), project_root=tmp_path)
    assert out["stage_b"]["llm_status"] == "fallback"
    assert out["stage_b"]["fallback_reason"] == "boom"
    assert out["stage_b_status"] == "completed"


def test_dry_run_makes_zero_calls(tmp_path):
    client = FakeClient()
    out = run_stage_b(_record(), mode="llm_plain", client=client, dry_run=True, project_root=tmp_path)
    assert client.calls_made == 0
    assert out["stage_b"]["llm_status"] == "skipped"


def test_jsonl_cache_hit(tmp_path):
    cache = JsonlCache(tmp_path / "cache.jsonl")
    cache.set("abc", {"json": {"ok": True}})
    cache2 = JsonlCache(tmp_path / "cache.jsonl")
    assert cache2.get("abc") == {"json": {"ok": True}}


def test_gemini_http_error_includes_provider_message_without_api_key(tmp_path, monkeypatch):
    api_key = "synthetic-api-key"
    error_body = json.dumps({
        "error": {
            "message": f"Invalid model request; do not echo {api_key}",
            "status": "INVALID_ARGUMENT",
        },
    }).encode()

    def reject_request(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://generativelanguage.googleapis.com/test",
            400,
            "Bad Request",
            {},
            io.BytesIO(error_body),
        )

    monkeypatch.setattr("backend.stage_b.llm_client.urllib.request.urlopen", reject_request)
    client = GeminiClient(api_key=api_key, cache=JsonlCache(tmp_path / "cache.jsonl"), rpm=1000)

    try:
        client._post_json("https://generativelanguage.googleapis.com/test", {})
    except RuntimeError as exc:
        assert "HTTP 400" in str(exc)
        assert "Invalid model request" in str(exc)
        assert api_key not in str(exc)
        assert "[REDACTED]" in str(exc)
    else:
        raise AssertionError("Gemini HTTP 400 must remain an error.")


def test_gemini_json_request_uses_valid_response_format_enum(tmp_path, monkeypatch):
    request_payloads = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return json.dumps({
                "candidates": [{
                    "content": {
                        "parts": [{
                            "text": json.dumps({
                                "entity_verdicts": [],
                                "additional_concerns": [],
                                "source_discrepancies": [],
                            }),
                        }],
                    },
                }],
            }).encode()

    def accept_request(request, timeout):
        request_payloads.append(json.loads(request.data.decode("utf-8")))
        return _Response()

    monkeypatch.setattr("backend.stage_b.llm_client.urllib.request.urlopen", accept_request)
    client = GeminiClient(
        api_key="synthetic-api-key",
        cache=JsonlCache(tmp_path / "cache.jsonl"),
        rpm=1000,
    )
    result = client.generate_json("Synthetic prompt")

    assert result["json"] == {
        "entity_verdicts": [],
        "additional_concerns": [],
        "source_discrepancies": [],
    }
    assert len(request_payloads) == 1
    generation_config = request_payloads[0]["generationConfig"]
    assert generation_config["responseFormat"]["text"]["mimeType"] == "APPLICATION_JSON"
    assert generation_config["responseMimeType"] == "application/json"


def test_plain_and_rag_prompts_differ_only_by_evidence_rule_and_facts():
    plain, _, _ = build_prompt(_record(), mode="llm_plain")
    rag, _, _ = build_prompt(_record(), mode="llm_rag")
    assert "use your own medical knowledge" in plain
    assert "base verdicts on the numbered retrieved facts" in rag
    assert "[RxNorm] amoxicillin exists" not in plain
    assert "[RxNorm] amoxicillin exists" in rag
