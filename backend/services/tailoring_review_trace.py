"""Durable, fenced traces for server-owned tailoring review stages.

The editor's tool calls and the review pipeline share ``tool_calls`` so one run has one
transactional history. Review-stage rows are excluded from the editor conversation and public
step rail. Their payloads contain private resume text and answers and must not be copied into
ordinary application logs.
"""

import json
import re


TOOL_NAME = "tailoring_review_stage"
CONTRACT = "focused_v1"
PROMPT_VERSION = "focused-review-v1-2026-09-25aa"
STAGES = ("clarity", "clarity_gate", "claim_support", "opportunity", "question_generation",
          "coordinator_overlap", "coordinator_selection")


class LeaseLost(Exception):
    """The run is no longer owned by the worker trying to persist a trace."""


def _part(value):
    value = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", str(value or "").strip())
    return value[:180] or "unknown"


def call_id(scope_id, stage, attempt=1, contract=CONTRACT):
    if stage not in STAGES:
        raise ValueError(f"unknown review stage: {stage}")
    attempt = int(attempt)
    if attempt < 1:
        raise ValueError("attempt must be positive")
    return f"review-stage:{_part(contract)}:{_part(scope_id)}:{stage}:{attempt}"


def record(cur, run_id, event, token=None):
    """Persist one completed attempt. The caller owns the transaction and orchestrator thread."""
    stage = event.get("stage")
    scope_id = event.get("scope_id")
    attempt = int(event.get("attempt") or 1)
    status = event.get("status")
    if stage not in STAGES or not scope_id or status not in ("completed", "failed"):
        raise ValueError("stage trace needs a known stage, scope_id and completed/failed status")
    if token is not None:
        cur.execute(
            "SELECT 1 FROM tailoring_runs WHERE id = %s AND claim_token = %s FOR UPDATE",
            (run_id, token),
        )
        if cur.fetchone() is None:
            raise LeaseLost(f"run {run_id} was claimed by another worker")

    arguments = {
        "contract": event.get("contract") or CONTRACT,
        "prompt_version": event.get("prompt_version") or PROMPT_VERSION,
        "stage": stage,
        "scope_id": str(scope_id),
        "attempt": attempt,
        "model": event.get("model"),
        "settings": event.get("settings") or {},
        "input": event.get("input"),
        "messages": event.get("messages"),
    }
    result = {
        "raw_response": event.get("raw_response"),
        "normalized": event.get("normalized"),
        "validation": event.get("validation") or {},
        "usage": event.get("usage"),
        "elapsed_ms": event.get("elapsed_ms"),
    }
    cur.execute(
        """
        INSERT INTO tool_calls (
            run_id, step_number, call_id, tool_name, arguments, result, status, error_message
        ) VALUES (%s, 1, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        ON CONFLICT (run_id, call_id) DO UPDATE SET
            arguments = EXCLUDED.arguments,
            result = EXCLUDED.result,
            status = EXCLUDED.status,
            error_message = EXCLUDED.error_message
        """,
        (
            run_id,
            call_id(scope_id, stage, attempt, arguments["contract"]),
            TOOL_NAME,
            json.dumps(arguments, default=str),
            json.dumps(result, default=str),
            status,
            event.get("error"),
        ),
    )


def load_completed(cur, run_id, contract=CONTRACT):
    """Latest valid completed normalized result keyed by ``(scope_id, stage)``."""
    cur.execute(
        """
        SELECT arguments, result
        FROM tool_calls
        WHERE run_id = %s AND tool_name = %s AND status = 'completed'
          AND arguments ->> 'contract' = %s
        ORDER BY created_at, call_id
        """,
        (run_id, TOOL_NAME, contract),
    )
    completed = {}
    for arguments, result in cur.fetchall():
        normalized = (result or {}).get("normalized")
        if normalized is None:
            continue
        key = (str((arguments or {}).get("scope_id") or ""),
               str((arguments or {}).get("stage") or ""))
        if all(key):
            completed[key] = normalized
    return completed


def readable(cur, run_id):
    """A compact authenticated view; exact private inputs/raw output stay in the stored row."""
    cur.execute(
        """
        SELECT call_id, arguments, result, status, error_message, created_at
        FROM tool_calls
        WHERE run_id = %s AND tool_name = %s
        ORDER BY created_at, call_id
        """,
        (run_id, TOOL_NAME),
    )
    items = []
    for row in cur.fetchall():
        arguments, result = row[1] or {}, row[2] or {}
        normalized = result.get("normalized")
        counts = {}
        if isinstance(normalized, dict):
            for key in ("bullets", "decisions", "candidates", "finding_dispositions",
                        "selected_ids", "rejected"):
                if isinstance(normalized.get(key), list):
                    counts[key] = len(normalized[key])
        items.append({
            "call_id": row[0],
            "contract": arguments.get("contract"),
            "prompt_version": arguments.get("prompt_version"),
            "stage": arguments.get("stage"),
            "scope_id": arguments.get("scope_id"),
            "attempt": arguments.get("attempt"),
            "model": arguments.get("model"),
            "status": row[3],
            "error": row[4],
            "counts": counts,
            "elapsed_ms": result.get("elapsed_ms"),
            "created_at": row[5].isoformat(),
        })
    return items
