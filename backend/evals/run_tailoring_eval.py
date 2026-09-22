"""End-to-end check of tailoring: planner -> recruiter review -> editor -> validation.

Every case builds a real user, resume and job, then runs the real pipeline with the REAL
OpenAI API: the review call, the editor's steps, and — when a question is asked — the
case's answer and the resumed run that uses it. What is checked is the result a user would
see: the questions actually filed and the edits actually recorded.

It needs the local test database (Docker `jd-translator-test-db`) and WIPES it, the same way
the test suite does, so don't run it alongside pytest. Costs a few cents; run by hand, never
in CI.

    cd backend && ./venv/bin/python evals/run_tailoring_eval.py
"""

import argparse
import json
import os
import pathlib
import sys
from collections import Counter

# The test database, set before anything imports `db` — `backend/.env` points at production,
# and load_dotenv never overrides a variable that is already set.
EVAL_DSN = os.environ.get("TAILORING_EVAL_DSN",
                          "postgresql://postgres:postgres@localhost:5432/jd_test")
os.environ["SUPABASE_URL"] = EVAL_DSN
os.environ["APP_ENV"] = "test"
os.environ["SENTRY_DSN"] = ""
os.environ.setdefault("JWT_SECRET", "eval-jwt-secret-not-a-real-key-000000000")

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv
load_dotenv(BACKEND / ".env")  # OPENAI_API_KEY; SUPABASE_URL stays the test database

import psycopg2

from db import get_cursor
from services import bullet_review, skill_relations
from services.bullet_review import build_tasks, payload
from services.openai_services import ResumeEntryExtraction, ResumeStructure
from services.resume_evidence import save_resume_evidence
from services.tailoring_agent import (
    bullets_by_entry, execute_run, load_job_assessment, load_run, resolve_detail_request,
    run_plan, run_tailoring,
)

CASES = pathlib.Path(__file__).parent / "tailoring_cases.json"
_SEQUENCE = __import__("itertools").count(1)
MAX_STEPS = 8


def _refuse_unless_test_database(dsn):
    lowered = dsn.lower()
    if not ("localhost" in lowered or "127.0.0.1" in lowered or "test" in lowered):
        sys.exit(f"refusing to wipe {dsn!r}: not a local or test database")


def fresh_schema():
    conn = psycopg2.connect(EVAL_DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cur.execute((BACKEND / "schema.sql").read_text())
    conn.close()


def _requirements(job):
    out = []
    for item in job["requirements"]:
        if isinstance(item, dict) and "any_of" in item:
            out.append({"condition": {"operator": "any_of", "minimum": 1, "items": item["any_of"]},
                        "source_text": " or ".join(item["any_of"]),
                        "importance": "required", "type": "skill"})
        else:
            out.append({"skill": item, "importance": "required", "type": "skill"})
    return out


def _skills(job):
    names = []
    for item in job["requirements"]:
        names.extend(item["any_of"] if isinstance(item, dict) else [item])
    return names


def build_case(case, default_job, requirements_order=None):
    """A user, their resume entry, a job, and any earlier answers. Returns ids."""
    job = case.get("job") or default_job
    requirements = _requirements(job)
    if requirements_order == "reversed":
        requirements = list(reversed(requirements))
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, 'x') RETURNING id",
            (f"eval_{next(_SEQUENCE)}_{case['id']}"[:60],),
        )
        user_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO jobs (user_id, raw_description, title, company_name, summary, skills, requirements)
            VALUES (%s, 'eval', %s, %s, %s, %s::jsonb, %s::jsonb) RETURNING id
            """,
            (user_id, job["title"], job["company"], job["summary"],
             json.dumps(_skills(job)), json.dumps(requirements)),
        )
        job_id = cur.fetchone()[0]
        save_resume_evidence(cur, user_id, ResumeStructure(entries=[ResumeEntryExtraction(
            kind="experience", organization=case["entry"]["organization"],
            title=case["entry"]["title"], bullets=[case["text"], *case.get("siblings", [])],
        )]))
        cur.execute("SELECT id FROM resume_bullets WHERE user_id = %s AND text = %s",
                    (user_id, case["text"]))
        bullet_id = cur.fetchone()[0]

        for specific, general in case.get("learned", []):
            cur.execute(
                "INSERT INTO skill_relations (specific, general) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (specific, general),
            )
        skill_relations.reset()
        skill_relations.load(cur)

        if case.get("answers"):
            # an earlier, finished run whose question was answered
            cur.execute(
                """
                INSERT INTO tailoring_runs (user_id, job_id, model, max_steps, status, completed_at)
                VALUES (%s, %s, 'gpt-4o-mini', 8, 'completed', now()) RETURNING id
                """,
                (user_id, job_id),
            )
            old_run = cur.fetchone()[0]
            for index, text in enumerate(case["answers"]):
                cur.execute(
                    """
                    INSERT INTO tailoring_detail_requests (
                        run_id, user_id, bullet_id, requirement, question, answer, status,
                        intent, resolved_at
                    )
                    VALUES (%s, %s, %s, 'backend', %s, %s, 'answered', 'implementation', now())
                    """,
                    (old_run, user_id, bullet_id, f"earlier question {index}", text),
                )
    return user_id, job_id, str(bullet_id)


def plan_owners(user_id, job_id):
    with get_cursor() as cur:
        _job, assessment = load_job_assessment(cur, user_id, job_id)
        plan = run_plan(cur, user_id, assessment, bullets_by_entry(cur, user_id))
    return sorted(
        (item["agent_label"], item["action"], tuple(sorted(t["bullet_id"] for t in item["targets"])))
        for item in plan
    ), assessment, plan


def check_order_independence(case, default_job):
    """Planner and review input under both requirement orders — no model call needed."""
    results = []
    for order in (None, "reversed"):
        user_id, job_id, bullet_id = build_case(case, default_job, order)
        owners, assessment, _plan = plan_owners(user_id, job_id)
        with get_cursor() as cur:
            tasks = build_tasks(cur, user_id, None, assessment, [bullet_id])
        job = case.get("job") or default_job
        shown = json.dumps(payload((job["title"], job["company"], job["summary"], _skills(job)),
                                   [{**t, "bullet_id": "x"} for t in tasks]))
        results.append(([(label, action, len(targets)) for label, action, targets in owners], shown))
    return results[0] == results[1]


def quotes_the_bullet(question, bullet):
    """Every question the server stores is the review's, and a review's question has to repeat
    the bullet's own words. Checked here too, on what actually reached the user."""
    from services.bullet_review import quotes_bullet

    return quotes_bullet(question, bullet)


def run_case(case, default_job):
    user_id, job_id, bullet_id = build_case(case, default_job)
    result = run_tailoring(get_cursor, user_id, job_id, max_steps=MAX_STEPS)
    if not result or "run_id" not in result:
        # not a keep: nothing ran, so nothing was decided
        return {"outcome": "no_run", "questions": [], "edits": [], "review": None,
                "status": None, "refusals": [], "note": f"no run: {result}"}
    run_id = result["run_id"]

    with get_cursor() as cur:
        run = load_run(cur, user_id, run_id)
    asked = [q for q in run["detail_requests"]]
    if asked and run["status"] == "waiting_for_user":
        question = asked[0]
        answer = case.get("answer")
        if answer:
            resumed = resolve_detail_request(
                get_cursor, user_id, question["id"], answer=answer,
            )
            if resumed and resumed.get("resume"):
                execute_run(get_cursor, user_id, job_id, run_id, max_steps=MAX_STEPS,
                            resume_from=resumed["steps_used"])
        with get_cursor() as cur:
            run = load_run(cur, user_id, run_id)

    questions = [(q["question"], q.get("intent")) for q in run["detail_requests"]]
    edits = [(e.get("original_text"), e["proposed_text"]) for e in run["edits"]]
    review = (run.get("reviews") or {}).get(bullet_id)
    refusals = [f"{item['tool']}: {item['error']}" for item in run.get("trace") or []
                if item["status"] == "failed"]
    if questions:
        outcome = "ask"
    elif edits:
        outcome = "rewrite"
    else:
        outcome = "keep"
    return {"outcome": outcome, "questions": questions, "edits": edits, "review": review,
            "status": run["status"], "refusals": refusals, "note": None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1,
                        help="how many times to run each case whose outcome is a judgment")
    parser.add_argument("--only", default=None, help="substring of a case id")
    args = parser.parse_args()

    _refuse_unless_test_database(EVAL_DSN)
    print(f"review model: {bullet_review.MODEL}\n")
    fresh_schema()
    data = json.loads(CASES.read_text())
    cases = [c for c in data["cases"] if not args.only or args.only in c["id"]]
    stable, flaky, failed = [], [], []

    for case in cases:
        # every case, not only the ones with an answer step: a supposedly simple KEEP moved
        # between runs once, which is exactly what repeats are for
        judgment = args.repeat
        outcomes, problems = [], []
        if case.get("check_order_independence") and not check_order_independence(case, data["default_job"]):
            problems.append("requirement order changed the plan or the review input")

        for attempt in range(judgment):
            got = run_case(case, data["default_job"])
            outcomes.append(got["outcome"])
            attempt_problems = []
            if got["outcome"] not in case["expect"]:
                attempt_problems.append(
                    f"ended as {got['outcome']}, expected {' or '.join(case['expect'])}")
            wanted = (got["review"] or {}).get("decision")
            if wanted not in case.get("expect_review", [wanted]):
                attempt_problems.append(
                    f"review decided {wanted}, expected "
                    f"{' or '.join(str(d) for d in case['expect_review'])}")
            if got["status"] != "completed":
                attempt_problems.append(f"run ended {got['status']}, not completed")
            asked_questions = [question for question, _intent in got["questions"]]
            if len(asked_questions) > 1:
                attempt_problems.append(f"{len(asked_questions)} questions on one bullet")
            for question in asked_questions:
                if not quotes_the_bullet(question, case["text"]):
                    attempt_problems.append(f"question does not quote the bullet: {question!r}")
            # A sibling naming concrete work does not silence a vague bullet — that rule once
            # silenced 14 of 18 — but a question that invites the answer the sibling already
            # gave wastes the ask. Naming the sibling's work is fine only to exclude it.
            beyond = case.get("question_must_go_beyond_sibling")
            for question in asked_questions if beyond else []:
                lowered = question.lower()
                repeats = [term for term in beyond if term in lowered]
                excludes = any(word in lowered for word in
                               ("besides", "other than", "apart from", "aside from", "in addition to",
                                "other part", "else"))
                if repeats and not excludes:
                    attempt_problems.append(
                        f"the question asks again for what the sibling gives: {', '.join(repeats)}")
            wanted_words = case.get("question_should_mention_any")
            if got["outcome"] == "ask" and wanted_words and not any(
                w in asked_questions[0].lower() for w in wanted_words
            ):
                attempt_problems.append("question does not target the expected fact")
            if (got["outcome"] == "ask" and case.get("answer") and not got["edits"]
                    and not case.get("allow_no_edit")):
                attempt_problems.append("answered, but no edit followed")

            label = f"{attempt + 1}. " if judgment > 1 else ""
            review = got["review"] or {}
            print(f"        {label}{got['outcome']} "
                  f"(review {review.get('decision', '-')}/{review.get('doubt_type') or '-'})")
            if review.get("downgraded"):
                print(f"           downgraded: {review['downgraded']}")
            for question, _intent in got["questions"]:
                print(f"           Q: {question}")
            for original, proposed in got["edits"]:
                print(f"           - {original}\n           + {proposed}")
            for refusal in got.get("refusals") or []:
                print(f"           refused {refusal[:200]}")
            for problem in attempt_problems:
                print(f"           ✗ {problem}")
            problems.extend(attempt_problems)

        mark = "PASS" if not problems else ("FLAKY" if len(set(outcomes)) > 1 else "FAIL")
        (stable if mark == "PASS" else flaky if mark == "FLAKY" else failed).append(case["id"])
        counts = ", ".join(f"{o}×{n}" for o, n in Counter(outcomes).most_common())
        print(f"[{mark}] {case['id']:<30} {counts:<22} expected {' or '.join(case['expect'])}")
        print(f"        {case['why']}\n")

    total = len(cases)
    print(f"{len(stable)}/{total} stable, {len(flaky)} flaky, {len(failed)} failed (n={total}).")
    if flaky:
        print("flaky: " + ", ".join(flaky))
    if failed:
        print("failed: " + ", ".join(failed))
    print("\nNow read them: is each question about work the bullet claims? Is each edit "
          "faithful and actually better?")
    sys.exit(1 if failed or flaky else 0)


if __name__ == "__main__":
    main()
