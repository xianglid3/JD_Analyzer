#!/usr/bin/env python3
"""Paid audit of the actual resume against one or more real job descriptions.

Unlike ``run_tailoring_v2_eval.py``, this script does not substitute a hand-written job summary or
requirement list. It runs each raw posting through the production job analyzer, saves the exact
resume fixture, then runs fit -> review -> coordination and stops at the user-question boundary.

There are intentionally no automatic PASS/FAIL labels yet. Read and label the selected questions
before turning this audit into a scored benchmark. Run one job at a time unless ``--all`` is
deliberate; each job reviews the complete resume and spends model budget.

    cd backend
    ./venv/bin/python evals/run_real_resume_v2_eval.py --only google --contract focused_v1
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))
# The production default is off. This real-input audit needs the original provider JSON when the
# reviewer violates its one-target contract; normalized tool-call rows cannot reconstruct it.
os.environ["TAILORING_REVIEW_V2_LOG_RAW"] = "1"

from evals.run_tailoring_v2_eval import (
    EVAL_DSN,
    build_resume_case,
    call_metrics,
    coordinator_trace,
    fresh_schema,
    refuse_unless_test_database,
)
from db import get_cursor
from services.jd_preprocess import preprocess_text
from services.bullet_review import is_rate_limit
from services.openai_services import analyze_job_description
from services.tailoring_agent import load_run, run_tailoring


RESUME = HERE / "real_resume.json"
POSTINGS = HERE / "real_jd.md"
MAX_STEPS = 20
DEFAULT_LABELS = HERE / "real_resume_human_labels.json"
JOBS = {
    "1": ("google_swe_sre", "Google"),
    "2": ("palantir_swe", "Palantir"),
    "3": ("aerotech_software", "Aerotech"),
    "4": ("plaid_swe", "Plaid"),
    "5": ("tiktok_ads_interface", "TikTok"),
}


def analyze_with_retries(raw, attempts=3, sleep=time.sleep):
    """Keep a paid matrix run alive across transient provider and malformed-JSON failures."""
    prepared = preprocess_text(raw)
    for attempt in range(1, attempts + 1):
        try:
            return analyze_job_description(prepared)
        except Exception as exc:
            retryable = is_rate_limit(exc) or isinstance(exc, ValueError)
            if not retryable or attempt == attempts:
                raise
            delay = 2 ** (attempt - 1)
            print(
                f"Job analysis attempt {attempt}/{attempts} failed; retrying in {delay}s: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            sleep(delay)


def select_jobs(only=None, run_all=False):
    needles = [item.strip().lower() for item in (only or "").split(",") if item.strip()]
    return [
        (number, name, company) for number, (name, company) in JOBS.items()
        if run_all or any(needle in name.lower() for needle in needles)
    ]


def configure_contract(contract):
    """Choose the fresh-run contract explicitly; stored runs remain pinned by production."""
    if contract not in {"v2", "focused_v1"}:
        raise ValueError(f"unknown review contract: {contract}")
    os.environ["TAILORING_FOCUSED_REVIEW_ENABLED"] = "1" if contract == "focused_v1" else "0"
    os.environ["TAILORING_FOCUSED_REVIEW_USERS"] = ""
    os.environ["TAILORING_FOCUSED_REVIEW_PERCENT"] = "0"
    os.environ["TAILORING_REVIEW_V2_ENABLED"] = "1" if contract == "v2" else "0"
    os.environ["TAILORING_REVIEW_V2_USERS"] = ""
    os.environ["TAILORING_REVIEW_V2_PERCENT"] = "0"


def raw_postings():
    text = POSTINGS.read_text()
    matches = list(re.finditer(r"(?m)^#([1-5])\s*$", text))
    found = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        found[match.group(1)] = text[match.end():end].strip()
    missing = set(JOBS) - set(found)
    if missing:
        raise RuntimeError("real_jd.md is missing section(s): " + ", ".join(sorted(missing)))
    return found


def permissive_entries(fixture):
    """Attach no fake gold labels; this first pass is for human semantic review."""
    entries = json.loads(json.dumps(fixture["entries"]))
    for entry in entries:
        for bullet in entry["bullets"]:
            bullet["expected"] = {
                "actions": ["keep", "ask", "rewrite"],
                "questions": {"min": 0, "max": 10},
            }
    return entries


def case_from_analysis(job_id, company, raw, analysis, resume):
    return {
        "id": job_id,
        "skills": resume.get("skills") or [],
        "entries": permissive_entries(resume),
        "job": {
            "title": analysis.title or job_id,
            "company": analysis.company_name or company,
            "summary": analysis.summary or "",
            "raw_description": raw,
            "skills": analysis.skills,
            "stored_requirements": [item.model_dump(mode="json") for item in analysis.requirements],
            # Kept for compatibility with the shared fixture builder; stored_requirements wins.
            "requirements": [],
        },
    }


def expected_action_problems(job_name, resume, run, bullet_ids):
    """Score only owner-authored fixture controls; never let the model grade itself."""
    expected = (resume.get("expected_actions_by_job") or {}).get(job_name) or {}
    if not expected:
        return []
    selected_bullets = {
        str(item.get("bullet_id"))
        for item in run.get("detail_requests") or []
        if item.get("bullet_id")
    }
    reviews = run.get("reviews") or {}
    problems = []
    for key, wanted in expected.items():
        bullet_id = bullet_ids.get(key)
        if bullet_id is None:
            problems.append(f"expected action names unknown bullet {key!r}")
            continue
        if bullet_id in selected_bullets:
            actual = "ask"
        else:
            review = reviews.get(bullet_id) or {}
            decision = (review.get("decision_claimed") or review.get("decision") or "").lower()
            actual = "rewrite" if decision == "rewrite" else "keep"
        if actual != wanted:
            problems.append(f"{key}: expected {wanted}, got {actual}")
    return problems


def print_analysis(name, analysis):
    print(f"\n## {name}")
    print(f"job analysis: {analysis.title or '-'} at {analysis.company_name or '-'}")
    print("summary:", analysis.summary or "-")
    print("skills:", ", ".join(analysis.skills) or "none")
    print("requirements:")
    for requirement in analysis.requirements:
        condition = requirement.condition
        label = requirement.skill
        if condition is not None:
            label = f"{condition.operator}({', '.join(condition.items)})"
        print(f"  - {requirement.importance}: {label} [{requirement.type}]")


def full_review_stage_trace(run_id):
    """Load the exact private stage records for a local eval artifact."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT call_id, arguments, result, status, error_message, created_at
            FROM tool_calls
            WHERE run_id = %s AND tool_name = 'tailoring_review_stage'
            ORDER BY created_at, call_id
            """,
            (run_id,),
        )
        rows = cur.fetchall()
    return [{
        "call_id": row[0], "arguments": row[1], "result": row[2],
        "status": row[3], "error": row[4], "created_at": row[5].isoformat(),
    } for row in rows]


def print_review_stage_trace(records, print_fn=print):
    if not records:
        return
    print_fn("\nfocused review stages:")
    for record in records:
        arguments, result = record.get("arguments") or {}, record.get("result") or {}
        normalized = result.get("normalized") or {}
        counts = {
            key: len(normalized.get(key) or [])
            for key in ("bullets", "candidates", "finding_dispositions", "selected_ids", "rejected")
            if isinstance(normalized.get(key), list)
        }
        count_text = " ".join(f"{key}={value}" for key, value in counts.items())
        print_fn(
            f"  {arguments.get('stage')} scope={arguments.get('scope_id')} "
            f"attempt={arguments.get('attempt')} {record.get('status')} {count_text}".rstrip()
        )


def audit_once(case, *, show_trace=True):
    user_id, job_id, bullet_ids, fixtures = build_resume_case(case)
    started = run_tailoring(get_cursor, user_id, job_id, max_steps=MAX_STEPS)
    with get_cursor() as cur:
        run = load_run(cur, user_id, started["run_id"])
    trace = coordinator_trace(run["id"])
    questions = {}
    for item in run.get("detail_requests") or []:
        questions.setdefault(item.get("bullet_id"), []).append(item["question"])

    if show_trace:
        print(f"run: {run['status']}  id={run['id']}  contract={run.get('review_contract')}")
        print_review_stage_trace(full_review_stage_trace(run["id"]))
        selected = trace.get("selected_ids") or []
        print("coordinator selected:", ", ".join(selected) or "none")
        reviews = run.get("reviews") or {}
        for key, fixture in fixtures.items():
            bullet_id = bullet_ids[key]
            review = reviews.get(bullet_id) or {}
            decision = review.get("decision_claimed") or review.get("decision") or "-"
            print(f"\n[{decision}] {key}")
            print(fixture["text"])
            if review.get("decision_reason"):
                print("reason:", review["decision_reason"])
            if review.get("unavailable_reason"):
                print("unavailable:", review["unavailable_reason"])
            for candidate in review.get("question_candidates") or []:
                print(f"raw {candidate.get('id')}: {candidate.get('question')}")
            for question in questions.get(bullet_id) or []:
                print("SELECTED:", question)
        for rejected in trace.get("rejected") or []:
            suffix = f" -> {rejected.get('duplicate_of')}" if rejected.get("duplicate_of") else ""
            print(f"\nDROP {rejected.get('id')}: {rejected.get('reason')}{suffix}")
        metrics = call_metrics(run["id"])
        print(
            f"\ntailoring calls={metrics['calls']} "
            f"tokens={metrics['prompt_tokens'] + metrics['completion_tokens']} "
            f"cost=${metrics['cost']:.5f} latency={metrics['latency_ms'] / 1000:.1f}s"
        )
    return run


ACTION_LABELS = {
    "k": ("keep", "KEEP — the bullet needs no work for this job"),
    "a": ("ask", "ASK — a truthful answer could materially improve it"),
    "r": ("rewrite", "REWRITE — existing evidence is enough"),
    "u": ("unsure", "UNSURE"),
}
QUESTION_LABELS = {
    "u": ("useful", "USEFUL — distinct answer would materially improve the resume"),
    "d": ("duplicate_or_answered", "DUPLICATE / already answered by this or a sibling bullet"),
    "l": ("low_value", "LOW VALUE — generic, interview-only, or not worth asking"),
    "p": ("unsupported_premise", "UNSUPPORTED PREMISE — assumes a fact not in evidence"),
    "w": ("useful_but_reword", "USEFUL FACT, but the question needs different wording"),
    "s": ("skip", "SKIP / unsure"),
}


def _choice(prompt, choices, input_fn=input, print_fn=print):
    print_fn(prompt)
    for key, (_value, description) in choices.items():
        print_fn(f"  [{key}] {description}")
    while True:
        answer = input_fn("> ").strip().lower()
        if answer in choices:
            return choices[answer][0]
        print_fn("Choose one of: " + ", ".join(choices))


def _load_labels(path):
    if not path.exists():
        return {
            "_about": "Human labels for the real-resume / real-JD tailoring evaluation.",
            "bullet_reviews": [],
            "question_reviews": [],
        }
    data = json.loads(path.read_text())
    data.setdefault("bullet_reviews", [])
    data.setdefault("question_reviews", [])
    return data


def _save_labels(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def _upsert(items, keys, record):
    for index, existing in enumerate(items):
        if all(existing.get(key) == record.get(key) for key in keys):
            items[index] = record
            return
    items.append(record)


def interactive_review(job_name, run, fixtures, bullet_ids, labels_path,
                       coordinator=None, input_fn=input, print_fn=print, job_context=None):
    """Pause for the owner's label on every bullet and every surviving generated question."""
    data = _load_labels(labels_path)
    coordinator = coordinator if coordinator is not None else coordinator_trace(run["id"])
    selected = set(coordinator.get("selected_ids") or [])
    rejected = {
        item.get("id"): item for item in coordinator.get("rejected") or [] if item.get("id")
    }
    reviews = run.get("reviews") or {}
    now = datetime.now(timezone.utc).isoformat()

    print_fn("\n" + "=" * 78)
    print_fn("HUMAN REVIEW — your labels become the benchmark; the model does not grade itself")
    print_fn("=" * 78)
    print_fn(f"Job: {job_name}   Run: {run['id']}   Save file: {labels_path}")

    if job_context:
        print_fn("Job context:")
        print_fn(job_context.get("raw_description") or job_context.get("summary") or str(job_context))
    print_fn("Resume context:")
    for key, fixture in fixtures.items():
        print_fn(f"  {key}: {fixture['text']}")

    for position, (key, fixture) in enumerate(fixtures.items(), start=1):
        bullet_id = bullet_ids[key]
        review = reviews.get(bullet_id) or {}
        decision = review.get("decision_claimed") or review.get("decision") or "UNAVAILABLE"
        candidates = review.get("question_candidates") or []

        print_fn("\n" + "-" * 78)
        print_fn(f"BULLET {position}/{len(fixtures)} — {key}")
        print_fn(fixture["text"])
        expected_action = _choice(
            "What should happen to this bullet for this job?", ACTION_LABELS,
            input_fn=input_fn, print_fn=print_fn,
        )
        _upsert(data["bullet_reviews"], ("run_id", "bullet_key"), {
            "job": job_name,
            "run_id": run["id"],
            "bullet_key": key,
            "bullet": fixture["text"],
            "model_decision": decision,
            "human_action": expected_action,
            "reviewed_at": now,
        })
        _save_labels(labels_path, data)

        if not candidates:
            print_fn("No surviving generated questions for this bullet.")
            continue
        for question_position, candidate in enumerate(candidates, start=1):
            opaque_id = f"{bullet_id}:{candidate.get('id')}"
            coordination = (
                "selected" if opaque_id in selected else
                f"rejected:{rejected[opaque_id].get('reason')}" if opaque_id in rejected else
                "not_run"
            )
            print_fn("\n" + "." * 78)
            print_fn(
                f"QUESTION {question_position}/{len(candidates)} for {key}"
            )
            print_fn(candidate.get("question") or "<missing question text>")
            label = _choice(
                "Your label for this question:", QUESTION_LABELS,
                input_fn=input_fn, print_fn=print_fn,
            )
            digest = hashlib.sha256(
                (job_name + "\n" + key + "\n" + (candidate.get("question") or ""))
                .encode("utf-8")
            ).hexdigest()[:16]
            _upsert(data["question_reviews"], ("run_id", "bullet_key", "candidate_id", "question_hash"), {
                "job": job_name,
                "run_id": run["id"],
                "bullet_key": key,
                "bullet": fixture["text"],
                "question_hash": digest,
                "candidate_id": candidate.get("id"),
                "question": candidate.get("question"),
                "coordination": coordination,
                "human_label": label,
                "reviewed_at": now,
            })
            _save_labels(labels_path, data)

    counts = {}
    for item in data["question_reviews"]:
        if item.get("run_id") == run["id"]:
            label = item.get("human_label")
            counts[label] = counts.get(label, 0) + 1
    print_fn("\nSaved human review to " + str(labels_path))
    print_fn("Question labels for this run: " + json.dumps(counts, sort_keys=True))
    return data


def load_existing_run(run_id, resume):
    """Open a paid run without resetting the database or making another model call."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT r.user_id, coalesce(j.company_name, ''), coalesce(j.title, ''),
                   j.raw_description, j.id
            FROM tailoring_runs AS r
            JOIN jobs AS j ON j.id = r.job_id
            WHERE r.id = %s
            """,
            (run_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"tailoring run not found: {run_id}")
        user_id, company, title, raw_description, job_id = row
        run = load_run(cur, str(user_id), run_id)
        cur.execute(
            """
            SELECT b.id, b.text
            FROM resume_bullets AS b
            JOIN resume_entries AS e ON e.id = b.entry_id
            WHERE b.user_id = %s AND e.kind IN ('experience', 'project')
            """,
            (user_id,),
        )
        stored = cur.fetchall()

    fixtures = {
        bullet["key"]: bullet
        for entry in permissive_entries(resume)
        for bullet in entry["bullets"]
    }
    by_text = {" ".join(text.split()): str(bullet_id) for bullet_id, text in stored}
    bullet_ids = {}
    for key, fixture in fixtures.items():
        bullet_id = by_text.get(" ".join(fixture["text"].split()))
        if bullet_id is None:
            raise RuntimeError(f"run resume is missing fixture bullet {key}")
        bullet_ids[key] = bullet_id
    name = next((JOBS[number][0] for number, raw in raw_postings().items()
                 if " ".join(raw.split()) == " ".join((raw_description or "").split())),
                f"job:{job_id}")
    return name, run, fixtures, bullet_ids


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--only",
        help="job id substring, or comma-separated substrings such as palantir,aerotech",
    )
    source.add_argument("--all", action="store_true", help="explicitly run all five paid audits")
    source.add_argument("--review-snapshot", type=pathlib.Path,
                        help="review a saved artifact offline; no DB or model calls")
    source.add_argument("--review-run", help="label an existing run without another paid call")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--contract", choices=("v2", "focused_v1"), default="v2",
                        help="fresh-run reviewer contract; defaults to current V2")
    parser.add_argument("--resume-fixture", type=pathlib.Path, default=RESUME,
                        help="resume JSON fixture; defaults to evals/real_resume.json")
    parser.add_argument("--labels", type=pathlib.Path, default=DEFAULT_LABELS)
    parser.add_argument("--no-human-review", action="store_true")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")

    if args.review_snapshot:
        artifact = json.loads(args.review_snapshot.read_text())
        interactive_review(
            artifact["job_name"], artifact["run"], artifact["fixtures"],
            artifact["bullet_ids"], args.labels, coordinator=artifact["coordinator"],
            job_context=artifact.get("job"),
        )
        return 0

    configure_contract(args.contract)

    refuse_unless_test_database(EVAL_DSN)
    resume = json.loads(args.resume_fixture.read_text())
    if args.review_run:
        if args.no_human_review:
            parser.error("--review-run requires human review")
        name, run, fixtures, bullet_ids = load_existing_run(args.review_run, resume)
        interactive_review(name, run, fixtures, bullet_ids, args.labels)
        sys.stdout.write("\a")
        sys.stdout.flush()
        return 0

    postings = raw_postings()
    chosen = select_jobs(args.only, args.all)
    if not chosen:
        parser.error(f"no job matched {args.only!r}")

    fresh_schema()
    bad = []
    for number, name, company in chosen:
        raw = postings[number]
        print(f"\nAnalyzing the raw {name} posting...", flush=True)
        analysis = analyze_with_retries(raw)
        print_analysis(name, analysis)
        case = case_from_analysis(name, company, raw, analysis, resume)
        for attempt in range(args.repeat):
            print(f"\n-- attempt {attempt + 1}/{args.repeat} --", flush=True)
            from evals.question_artifacts import capture_calls, save_artifact
            journal = HERE / "artifacts" / f"{name}-{uuid.uuid4().hex}-calls.json"
            with capture_calls(journal) as calls:
                run = audit_once(case, show_trace=args.no_human_review)
            _name, _run, fixtures, bullet_ids = load_existing_run(run["id"], resume)
            trace = coordinator_trace(run["id"])
            control_problems = expected_action_problems(name, resume, _run, bullet_ids)
            if (resume.get("expected_actions_by_job") or {}).get(name):
                if control_problems:
                    print("CONTROL FAIL:")
                    for problem in control_problems:
                        print("  - " + problem)
                else:
                    print("CONTROL PASS: every owner-authored bullet action matched")
            artifact_path = save_artifact(
                name, run, fixtures, bullet_ids, trace, case, calls,
                review_stage_trace=full_review_stage_trace(run["id"]),
            )
            print(f"Saved run: {artifact_path}", flush=True)
            if not args.no_human_review:
                fixture_by_key = {
                    bullet["key"]: bullet
                    for entry in case["entries"] for bullet in entry["bullets"]
                }
                # Resolve the ids from the stored run, without another call or schema reset.
                _stored_name, _stored_run, fixtures, bullet_ids = load_existing_run(
                    run["id"], resume,
                )
                interactive_review(
                    name, _stored_run, fixtures, bullet_ids, args.labels,
                    coordinator=trace, job_context=case["job"],
                )
            if run["status"] not in {"waiting_for_user", "completed"}:
                bad.append(f"{name}:{attempt + 1}:{run['status']}")
            elif control_problems:
                bad.append(f"{name}:{attempt + 1}:control")

    if args.no_human_review:
        print("\nHuman review was skipped by --no-human-review.")
    else:
        print("\nHuman labels were saved after every answer.")
    print("Only fixture controls explicitly authored by the owner are scored.")
    sys.stdout.write("\a")
    sys.stdout.flush()
    if bad:
        print("incomplete runs: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
