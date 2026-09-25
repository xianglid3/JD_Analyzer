import pytest

from services import tailoring_review_trace as trace


def test_stage_call_ids_are_stable_and_attempt_scoped():
    assert trace.call_id("entry 1", "clarity") == (
        "review-stage:focused_v1:entry-1:clarity:1"
    )
    assert trace.call_id("entry 1", "clarity", 2).endswith(":clarity:2")
    with pytest.raises(ValueError, match="unknown review stage"):
        trace.call_id("entry", "editor", 1)


def test_record_is_fenced_and_completed_stages_are_recoverable(fixtures, _db):
    from services.tailoring_agent import start_run
    from db import get_cursor

    run_id = start_run(get_cursor, fixtures["user_id"], fixtures["job_id"])["run_id"]
    event = {
        "stage": "clarity", "scope_id": "entry-1", "attempt": 1,
        "status": "completed", "model": "gpt-4o-mini",
        "settings": {"response_format": "json_schema"},
        "input": {"target_bullet_ids": ["b1"]},
        "messages": [{"role": "system", "content": "private"}],
        "raw_response": '{"check":"clarity"}',
        "normalized": {"check": "clarity", "bullets": []},
        "validation": {"valid": True}, "elapsed_ms": 12,
    }
    with get_cursor(commit=True) as cur:
        trace.record(cur, run_id, event)
        trace.record(cur, run_id, event)  # idempotent replay updates one row
    with get_cursor() as cur:
        assert trace.load_completed(cur, run_id) == {
            ("entry-1", "clarity"): event["normalized"],
        }
        readable = trace.readable(cur, run_id)
    assert len(readable) == 1
    assert readable[0]["stage"] == "clarity"
    assert readable[0]["counts"] == {"bullets": 0}

    with pytest.raises(trace.LeaseLost):
        with get_cursor(commit=True) as cur:
            trace.record(cur, run_id, {**event, "attempt": 2}, token="bad-token")
