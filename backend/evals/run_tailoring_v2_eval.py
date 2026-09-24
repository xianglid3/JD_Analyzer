#!/usr/bin/env python3
"""Paid complete-resume evaluation for the question-first V2 tailoring flow.

This runs the real reviewer, Question Coordinator, editor and claim checks against a disposable
local test database. It wipes that database and spends model budget. Run it manually, never in CI:

    cd backend
    ./venv/bin/python evals/run_tailoring_v2_eval.py --repeat 3

The machine checks are intentionally narrower than the final judgment. Read every selected
question and edit in the report before enabling V2 in a deployment.
"""

import argparse
from collections import Counter, defaultdict
import itertools
import json
import os
import pathlib
import sys


EVAL_DSN = os.environ.get(
    "TAILORING_EVAL_DSN", "postgresql://postgres:postgres@localhost:5432/jd_test"
)
os.environ["SUPABASE_URL"] = EVAL_DSN
os.environ["APP_ENV"] = "test"
os.environ["SENTRY_DSN"] = ""
os.environ["TAILORING_REVIEW_V2_ENABLED"] = "1"
os.environ.setdefault("JWT_SECRET", "eval-jwt-secret-not-a-real-key-000000000")

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")  # API key only; variables above remain pinned to the test run

import psycopg2

from db import get_cursor
from evals.tailoring_v2_scoring import aggregate, evaluate_bullet
from services import bullet_review_v2, question_coordinator_v2, skill_relations, tailoring_agent
from services.openai_services import ResumeEntryExtraction, ResumeStructure
from services.resume_evidence import save_resume_evidence
from services.tailoring_agent import execute_run, load_run, resolve_detail_request, run_tailoring


CASES = pathlib.Path(__file__).parent / "tailoring_v2_cases.json"
SEQUENCE = itertools.count(1)
MAX_STEPS = 20


def refuse_unless_test_database(dsn):
    lowered = dsn.lower()
    if not ("localhost" in lowered or "127.0.0.1" in lowered or "test" in lowered):
        sys.exit(f"refusing to wipe {dsn!r}: not a local or test database")


def fresh_schema():
    connection = psycopg2.connect(EVAL_DSN)
    connection.autocommit = True
    with connection.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cur.execute((BACKEND / "schema.sql").read_text())
    connection.close()


def requirement_rows(job):
    rows = []
    for item in job.get("requirements") or []:
        if isinstance(item, dict) and item.get("any_of"):
            values = item["any_of"]
            rows.append({
                "condition": {"operator": "any_of", "minimum": 1, "items": values},
                "source_text": " or ".join(values),
                "importance": "required",
                "type": "skill",
            })
        else:
            rows.append({"skill": item, "importance": "required", "type": "skill"})
    return rows


def skill_names(job):
    names = []
    for item in job.get("requirements") or []:
        names.extend(item.get("any_of") or [] if isinstance(item, dict) else [item])
    return names


def build_resume_case(case):
    """Create one complete resume and return its stable fixture-key to bullet-id map."""
    job = case["job"]
    flat = [bullet for entry in case["entries"] for bullet in entry["bullets"]]
    entries = [
        ResumeEntryExtraction(
            kind=entry["kind"], organization=entry.get("organization"),
            title=entry.get("title"), bullets=[bullet["text"] for bullet in entry["bullets"]],
        )
        for entry in case["entries"]
    ]
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, 'x') RETURNING id",
            (f"v2_eval_{next(SEQUENCE)}_{case['id']}"[:60],),
        )
        user_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO jobs (
                user_id, raw_description, title, company_name, summary, skills, requirements
            ) VALUES (%s, 'v2 eval', %s, %s, %s, %s::jsonb, %s::jsonb)
            RETURNING id
            """,
            (
                user_id, job["title"], job["company"], job["summary"],
                json.dumps(skill_names(job)), json.dumps(requirement_rows(job)),
            ),
        )
        job_id = cur.fetchone()[0]
        save_resume_evidence(cur, user_id, ResumeStructure(entries=entries))
        cur.execute(
            """
            SELECT b.id, b.text
            FROM resume_bullets AS b
            JOIN resume_entries AS e ON e.id = b.entry_id
            WHERE b.user_id = %s
            ORDER BY e.sort_order, b.sort_order
            """,
            (user_id,),
        )
        stored = cur.fetchall()
        if len(stored) != len(flat):
            raise RuntimeError("saved resume does not have the fixture's bullet count")
        bullet_ids = {item["key"]: str(row[0]) for item, row in zip(flat, stored)}

        for specific, general in case.get("learned") or []:
            cur.execute(
                """
                INSERT INTO skill_relations (specific, general, source)
                VALUES (%s, %s, 'model') ON CONFLICT DO NOTHING
                """,
                (specific, general),
            )
        skill_relations.reset()
        skill_relations.load(cur)
    return str(user_id), str(job_id), bullet_ids, {item["key"]: item for item in flat}


def answer_batch(user_id, job_id, run, fixture_by_key, key_by_bullet):
    """Answer every filed question, then resume exactly once after the final resolution."""
    indexes = defaultdict(int)
    last = None
    for question in run.get("detail_requests") or []:
        if question["status"] != "pending":
            continue
        key = key_by_bullet.get(question.get("bullet_id"))
        fixture = fixture_by_key.get(key) or {}
        answers = fixture.get("answers") or []
        index = indexes[key]
        indexes[key] += 1
        if index < len(answers):
            last = resolve_detail_request(
                get_cursor, user_id, question["id"], answer=answers[index],
            )
        else:
            last = resolve_detail_request(get_cursor, user_id, question["id"], dismiss=True)
    if last and last.get("resume"):
        execute_run(
            get_cursor, user_id, job_id, run["id"], max_steps=MAX_STEPS,
            resume_from=last["steps_used"],
        )


def coordinator_trace(run_id):
    with get_cursor() as cur:
        cur.execute(
            "SELECT result FROM tool_calls WHERE run_id = %s AND call_id = %s",
            (run_id, tailoring_agent.QUESTION_COORDINATOR_CALL),
        )
        row = cur.fetchone()
    return row[0] if row else {"selected_ids": [], "rejected": []}


def call_metrics(run_id):
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT count(*), COALESCE(sum(prompt_tokens), 0),
                   COALESCE(sum(completion_tokens), 0), COALESCE(sum(cost_usd), 0),
                   COALESCE(sum(latency_ms), 0)
            FROM llm_calls WHERE run_id = %s
            """,
            (run_id,),
        )
        calls, prompt, completion, cost, latency = cur.fetchone()
    return {
        "calls": calls, "prompt_tokens": prompt, "completion_tokens": completion,
        "cost": float(cost), "latency_ms": latency,
    }


def validation_metrics(run_id):
    """Counts from the stored validator record; no prose classifier is involved."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT
              count(*) FILTER (
                WHERE status = 'failed'
                  AND jsonb_array_length(coalesce(result -> 'validation' -> 'hard_blocks', '[]'::jsonb)) > 0
              ),
              count(*) FILTER (
                WHERE status = 'failed'
                  AND jsonb_array_length(coalesce(result -> 'validation' -> 'repair_requests', '[]'::jsonb)) > 0
                  AND jsonb_array_length(coalesce(result -> 'validation' -> 'hard_blocks', '[]'::jsonb)) = 0
              ),
              count(*) FILTER (
                WHERE status = 'completed'
                  AND jsonb_array_length(coalesce(result -> 'validation_warnings', '[]'::jsonb)) > 0
              )
            FROM tool_calls
            WHERE run_id = %s AND tool_name = 'propose_edit'
            """,
            (run_id,),
        )
        hard_blocks, repair_requests, warned_edits = cur.fetchone()
    return {
        "hard_blocks": hard_blocks,
        "repair_requests": repair_requests,
        "repair_recovered_with_warning": warned_edits,
    }


def observe(case):
    user_id, job_id, bullet_ids, fixtures = build_resume_case(case)
    key_by_bullet = {bullet_id: key for key, bullet_id in bullet_ids.items()}
    started = run_tailoring(get_cursor, user_id, job_id, max_steps=MAX_STEPS)
    with get_cursor() as cur:
        run = load_run(cur, user_id, started["run_id"])
    answer_batch(user_id, job_id, run, fixtures, key_by_bullet)
    with get_cursor() as cur:
        run = load_run(cur, user_id, started["run_id"])

    questions_by_bullet = defaultdict(list)
    answered_by_bullet = defaultdict(bool)
    for question in run.get("detail_requests") or []:
        key = key_by_bullet.get(question.get("bullet_id"))
        if key:
            questions_by_bullet[key].append(question["question"])
            answered_by_bullet[key] |= question["status"] == "answered"
    edits_by_bullet = defaultdict(list)
    warnings_by_bullet = defaultdict(list)
    for edit in run.get("edits") or []:
        key = key_by_bullet.get(edit.get("bullet_id"))
        if key:
            edits_by_bullet[key].append(edit["proposed_text"])
            warnings_by_bullet[key].extend(edit.get("validation_warnings") or [])

    records = []
    reviews = run.get("reviews") or {}
    for key, fixture in fixtures.items():
        questions = questions_by_bullet[key]
        edits = edits_by_bullet[key]
        action = "ask" if questions else "rewrite" if edits else "keep"
        actual = {
            "action": action, "questions": questions, "edits": edits,
            "answered": answered_by_bullet[key],
            "review": reviews.get(bullet_ids[key]) or {},
            "validation_warnings": warnings_by_bullet[key],
        }
        records.append({
            "resume": case["id"], "key": key, "text": fixture["text"],
            "expected": fixture["expected"], "actual": actual,
            "problems": evaluate_bullet(actual, fixture["expected"]),
        })
    refusals = [
        f"{step['tool']}: {step['error']}" for step in run.get("trace") or []
        if step["status"] == "failed"
    ]
    return {
        "run": run, "records": records, "coordinator": coordinator_trace(run["id"]),
        "refusals": refusals, "metrics": call_metrics(run["id"]),
        "validation": validation_metrics(run["id"]),
    }


def print_observation(observation):
    coordinator = observation["coordinator"]
    print("  coordinator selected:", ", ".join(coordinator.get("selected_ids") or []) or "none")
    for item in coordinator.get("rejected") or []:
        suffix = f" -> {item.get('duplicate_of')}" if item.get("duplicate_of") else ""
        print(f"  coordinator rejected {item.get('id')}: {item.get('reason')}{suffix}")
    for record in observation["records"]:
        actual, review = record["actual"], record["actual"]["review"]
        print(
            f"  [{actual['action'].upper():7}] {record['key']} "
            f"(review {review.get('decision_claimed') or review.get('decision') or '-'})"
        )
        print(f"           reason: {review.get('decision_reason') or '-'}")
        if review.get("unavailable_reason"):
            print(f"           unavailable: {review['unavailable_reason']}")
        for candidate in review.get("question_candidates") or []:
            warnings = "; ".join(candidate.get("warnings") or [])
            print(f"           raw {candidate.get('id')}: {candidate.get('question')}")
            if warnings:
                print(f"             warnings: {warnings}")
        for rejected in review.get("hard_rejected") or []:
            print(f"           hard rejected {rejected.get('id')}: {rejected.get('why')}")
        for question in actual["questions"]:
            print(f"           Q: {question}")
        for edit in actual["edits"]:
            print(f"           EDIT: {edit}")
        for warning in actual.get("validation_warnings") or []:
            print(
                f"           WARNING {warning.get('code')}: {warning.get('message')} "
                f"[{warning.get('evidence')}]"
            )
        for problem in record["problems"]:
            print(f"           ✗ {problem}")
    for refusal in observation["refusals"]:
        print(f"  validator refusal: {refusal[:240]}")
    print("  validation:", json.dumps(observation["validation"], sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--only", help="substring of a complete-resume case id")
    parser.add_argument("--model", help="override reviewer, coordinator, and editor model")
    args = parser.parse_args()
    refuse_unless_test_database(EVAL_DSN)
    if args.model:
        bullet_review_v2.MODEL = args.model
        question_coordinator_v2.MODEL = args.model
        tailoring_agent.MODEL = args.model

    data = json.loads(CASES.read_text())
    cases = [
        case for case in data["resumes"]
        if not args.only or args.only in case["id"]
    ]
    if not cases:
        sys.exit("no complete-resume cases matched")
    fresh_schema()
    all_records, failed, flaky = [], [], []
    totals = Counter()

    for case in cases:
        signatures, case_failed = [], False
        print(f"\n## {case['id']}")
        for attempt in range(args.repeat):
            observation = observe(case)
            print(f"\n attempt {attempt + 1}: {observation['run']['status']}")
            print_observation(observation)
            all_records.extend(observation["records"])
            totals.update({
                "calls": observation["metrics"]["calls"],
                "prompt_tokens": observation["metrics"]["prompt_tokens"],
                "completion_tokens": observation["metrics"]["completion_tokens"],
                "latency_ms": observation["metrics"]["latency_ms"],
            })
            totals["cost_microusd"] += round(observation["metrics"]["cost"] * 1_000_000)
            totals["selected_questions"] += len(
                observation["coordinator"].get("selected_ids") or []
            )
            for rejected in observation["coordinator"].get("rejected") or []:
                totals[f"coordinator_rejected:{rejected.get('reason')}"] += 1
            totals["reviewer_hard_rejections"] += sum(
                len((record["actual"].get("review") or {}).get("hard_rejected") or [])
                for record in observation["records"]
            )
            problems = [
                problem for record in observation["records"] for problem in record["problems"]
            ]
            if observation["run"]["status"] != "completed":
                problems.append(f"run ended {observation['run']['status']}")
            case_failed |= bool(problems)
            signatures.append(tuple(
                (record["key"], record["actual"]["action"], len(record["actual"]["questions"]))
                for record in observation["records"]
            ))
        unstable = len(set(signatures)) > 1
        if unstable:
            flaky.append(case["id"])
        if case_failed:
            failed.append(case["id"])
        print(f"\n {'FAIL' if case_failed else 'PASS'}"
              f"{' / FLAKY' if unstable else ''}: {case['id']}")

    metrics = aggregate(all_records)
    print("\n## Aggregate")
    for name, value in metrics.items():
        print(f"{name}: {value}")
    print(
        f"calls={totals['calls']} tokens={totals['prompt_tokens'] + totals['completion_tokens']} "
        f"cost=${totals['cost_microusd'] / 1_000_000:.5f} "
        f"latency={totals['latency_ms'] / 1000:.1f}s"
    )
    print(
        "coordinator rejections: "
        + ", ".join(
            f"{key.removeprefix('coordinator_rejected:')}={value}"
            for key, value in sorted(totals.items())
            if key.startswith("coordinator_rejected:")
        )
        + f"; reviewer hard rejections={totals['reviewer_hard_rejections']}"
    )
    print("\nRead every question and edit above before enabling V2; a green score is not a semantic guarantee.")
    if failed:
        print("failed: " + ", ".join(sorted(set(failed))))
    if flaky:
        print("flaky: " + ", ".join(sorted(set(flaky))))
    sys.exit(1 if failed or flaky else 0)


if __name__ == "__main__":
    main()
