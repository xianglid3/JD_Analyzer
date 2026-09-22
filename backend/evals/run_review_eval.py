"""Stage 1: the recruiter review, measured on its own.

Runs `services/bullet_review.py` over `review_cases.json` with the REAL model and prints every
decision, doubt, question and rewrite instruction for a human to read. No planner, no editor, no
database — so it cannot collide with the test suite, and a bad decision cannot reach a resume.

    cd backend && ./venv/bin/python evals/run_review_eval.py --repeat 3

A case passes when every run lands on a decision the case allows. Runs that disagree are FLAKY,
which is the thing worth knowing: a judgment that moves between runs cannot be trusted by the
planner yet.
"""

import argparse
import json
import os
import pathlib
import sys
from collections import Counter

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("SUPABASE_URL", "postgresql://postgres:postgres@localhost:5432/jd_test")
os.environ.setdefault("JWT_SECRET", "eval-jwt-secret-not-a-real-key-000000000")
os.environ["APP_ENV"] = "test"
os.environ["SENTRY_DSN"] = ""

from dotenv import load_dotenv
load_dotenv(BACKEND / ".env")          # OPENAI_API_KEY

from services import bullet_review

CASES = pathlib.Path(__file__).parent / "review_cases.json"


def task_for(case):
    return {
        "bullet_id": case["id"],
        "text": case["text"],
        "entry": case.get("entry", ""),
        "siblings": case.get("siblings", []),
        "answers": case.get("answers", []),
        "requirements": case.get("match_findings", []),
    }


def job_tuple(job):
    return (job["title"], job["company"], job["summary"], job["requirements"])


def show(result):
    lines = []
    if result["doubt_type"] or result["specific_problem"]:
        lines.append(f"doubt: {result['doubt_type']} — {result['specific_problem']}")
    if result["question"]:
        lines.append(f"Q: {result['question']}")
        lines.append(f"   anchor: {result['anchor']!r}  missing: {result['missing_fact']}")
        lines.append(f"   would let the bullet: {result['expected_resume_improvement']}")
    if result["rewrite_instruction"]:
        lines.append(f"rewrite: {result['rewrite_instruction']}")
        lines.append(f"   anchor: {result['anchor']!r}  keep: {', '.join(result['facts_to_preserve']) or '-'}")
    if result["downgraded"]:
        lines.append(f"downgraded: {result['downgraded']}")
    if result["decision"] == bullet_review.KEEP and result["decision_reason"]:
        lines.append(f"kept because: {result['decision_reason']}")
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--model", default=None, help="overrides TAILORING_REVIEW_MODEL")
    parser.add_argument("--only", default=None, help="substring of a case id")
    args = parser.parse_args()

    data = json.loads(CASES.read_text())
    cases = [c for c in data["cases"] if not args.only or args.only in c["id"]]
    model = args.model or bullet_review.MODEL
    print(f"review model: {model}   cases: {len(cases)}   repeat: {args.repeat}\n")

    stable, flaky, failed = [], [], []
    for case in cases:
        job = job_tuple(data["jobs"][case["job"]])
        task = task_for(case)
        decisions, shown, errors = [], [], []
        for attempt in range(args.repeat):
            try:
                raw = (bullet_review.request_review(job, [task], model=model) or [{}])[0]
            except bullet_review.ReviewUnavailable as exc:
                # never a KEEP: a provider failure must not read as an editorial decision
                decisions.append("ERROR")
                errors.append(f"{attempt + 1}. ERROR: {exc}")
                continue
            result = bullet_review.validate(raw, task)
            decisions.append(result["decision"])
            shown.append((attempt + 1, result, raw))

        allowed = [] if errors else [d for d in decisions if d in case["expect"]]
        if len(allowed) == len(decisions):
            mark, bucket = "PASS", stable
        elif allowed:
            mark, bucket = "FLAKY", flaky
        else:
            mark, bucket = "FAIL", failed
        bucket.append(case["id"])

        counts = ", ".join(f"{d}×{n}" for d, n in Counter(decisions).most_common())
        print(f"[{mark}] {case['id']:<32} {counts:<24} expected {' or '.join(case['expect'])}")
        print(f"        {case['text'][:100]}")
        print(f"        ({case['why']})")
        for line in errors:
            print(f"        {line}")
        for attempt, result, raw in shown:
            for line in show(result):
                print(f"        {attempt}. {line}" if args.repeat > 1 else f"        {line}")
            if result["downgraded"]:
                # what the model actually said, so a refusal can be read as a schema problem
                # or a judgment problem
                print(f"        {attempt}. raw: {json.dumps({k: raw.get(k) for k in ('anchor', 'missing_fact', 'question', 'expected_resume_improvement')})[:400]}")
        print()

    print(f"{len(stable)}/{len(cases)} stable, {len(flaky)} flaky, {len(failed)} failed "
          f"(n={len(cases)}, {args.repeat} run(s) each).")
    if flaky:
        print("flaky: " + ", ".join(flaky))
    if failed:
        print("failed: " + ", ".join(failed))
    print("\nNow read them: is each question about work the bullet already claims, answerable in "
          "one sentence, and worth the candidate's time?")
    sys.exit(1 if failed or flaky else 0)


if __name__ == "__main__":
    main()
