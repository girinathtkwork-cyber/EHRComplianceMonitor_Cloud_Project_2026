import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import run_end_to_end


def test_process_records_reports_mixed_stage_a_outcomes(monkeypatch, tmp_path):
    monkeypatch.setattr(run_end_to_end, "run_stage_a_batch", lambda records: [
        {"transcript_id": "ok", "verification_status": "completed"},
        {"transcript_id": "bad", "verification_status": "error", "error": {"code": "ValueError"}},
    ])
    monkeypatch.setattr(run_end_to_end, "run_stage_b_batch", lambda records, **kwargs: [
        {"transcript_id": "ok", "verification_status": "completed", "stage_b_status": "completed",
         "stage_b": {"llm_status": "skipped", "request_count": 0, "latency_ms": 0}}
    ])

    outputs, summary = run_end_to_end.process_records(
        [{"transcript_id": "ok"}, {"transcript_id": "bad"}],
        mode="stage_a_only", dry_run=False, max_calls=None, project_root=tmp_path,
    )

    assert len(outputs) == 2
    assert outputs[1]["verification_status"] == "error"
    assert summary["status"] == "partial"
    assert summary["expected_count"] == summary["processed_count"] == 2
    assert summary["stage_a_successes"] == 1
    assert summary["stage_a_failures"] == 1


def test_process_records_reports_all_stage_a_failures_as_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(run_end_to_end, "run_stage_a_batch", lambda records: [
        {"transcript_id": "bad", "verification_status": "error"}
    ])
    monkeypatch.setattr(run_end_to_end, "run_stage_b_batch", lambda *args, **kwargs: [])

    outputs, summary = run_end_to_end.process_records(
        [{"transcript_id": "bad"}], mode="stage_a_only", dry_run=False,
        max_calls=None, project_root=tmp_path,
    )
    assert len(outputs) == 1
    assert summary["status"] == "failed"
    assert summary["stage_a_failures"] == 1


def test_process_records_sends_partial_stage_a_records_to_stage_b(monkeypatch, tmp_path):
    monkeypatch.setattr(run_end_to_end, "run_stage_a_batch", lambda records: [
        {"transcript_id": "partial", "verification_status": "partial", "entities": []},
    ])
    seen = []
    def stage_b(records, **kwargs):
        seen.extend(records)
        return [{"transcript_id": "partial", "verification_status": "partial",
                 "stage_b_status": "completed", "stage_b": {"llm_status": "skipped"}}]
    monkeypatch.setattr(run_end_to_end, "run_stage_b_batch", stage_b)

    outputs, summary = run_end_to_end.process_records(
        [{"transcript_id": "partial"}], mode="stage_a_only", dry_run=False,
        max_calls=None, project_root=tmp_path,
    )
    assert len(seen) == 1
    assert summary["stage_a_successes"] == 1
    assert summary["stage_a_failures"] == 0
    assert outputs[0]["verification_status"] == "partial"


def test_end_to_end_cli_writes_only_to_requested_temporary_output(tmp_path, monkeypatch, capsys):
    input_path = tmp_path / "synthetic-input.json"
    output_path = tmp_path / "unique-output.json"
    input_path.write_text(json.dumps([{"transcript_id": "safe", "transcription": "Patient reports feeling well."}]), encoding="utf-8")
    monkeypatch.setenv("STAGE_B_MODE", "stage_a_only")
    monkeypatch.setattr(sys, "argv", [
        "run_end_to_end.py", "--input", str(input_path), "--output", str(output_path),
        "--mode", "stage_a_only",
    ])

    assert run_end_to_end.main() == 0
    summary = json.loads(capsys.readouterr().out)
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert summary["status"] == "completed"
    assert summary["expected_count"] == summary["processed_count"] == 1
    assert summary["total_duration_ms"] >= summary["stage_a_duration_ms"]
    assert "terminology_request_count" in summary
    assert "gemini_request_count" in summary
    assert len(saved) == 1
    assert saved[0]["verification_status"] == "completed"
