#!/usr/bin/env python3
"""Resume a saved real-resume eval with fixture answers and print its proposed edits.

This keeps answer-to-edit comparisons reproducible: the question run is paid once, answers live in
the resume fixture, and later editor revisions can be compared using a fresh question run plus this
small resume step.

    ./venv/bin/python evals/run_answered_edit_eval.py \
      --run-id <uuid> --resume-fixture evals/real_resume_vague.json
"""

import argparse
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

# This CLI resumes rows from the disposable eval database. Keep that configuration local to
# execution; importing an eval helper must never alter a production or pytest process.
if __name__ == "__main__":
    os.environ["SUPABASE_URL"] = os.environ.get(
        "TAILORING_EVAL_DSN", "postgresql://postgres:postgres@localhost:5432/jd_test"
    )
    os.environ["APP_ENV"] = "test"
    os.environ["SENTRY_DSN"] = ""
    os.environ.setdefault("JWT_SECRET", "eval-jwt-secret-not-a-real-key-000000000")

from evals.run_tailoring_v2_eval import answer_batch
from evals.run_real_resume_v2_eval import load_existing_run
from db import get_cursor
from services.tailoring_agent import load_run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-fixture", required=True, type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()

    resume = json.loads(args.resume_fixture.read_text())
    _name, run, fixtures, bullet_ids = load_existing_run(args.run_id, resume)
    with get_cursor() as cur:
        cur.execute("SELECT user_id, job_id FROM tailoring_runs WHERE id = %s", (args.run_id,))
        row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"tailoring run not found: {args.run_id}")
    user_id, job_id = map(str, row)
    key_by_bullet = {bullet_id: key for key, bullet_id in bullet_ids.items()}

    answer_batch(user_id, job_id, run, fixtures, key_by_bullet)
    with get_cursor() as cur:
        completed = load_run(cur, user_id, args.run_id)

    edits_by_bullet = {}
    for edit in completed.get("edits") or []:
        key = key_by_bullet.get(str(edit.get("bullet_id")))
        if key:
            edits_by_bullet[key] = edit

    print(
        f"run: {completed['status']}  steps={completed['steps_used']}  "
        f"edits={len(edits_by_bullet)}  error={completed.get('error_code') or '-'}"
    )
    for key, fixture in fixtures.items():
        edit = edits_by_bullet.get(key)
        if not edit:
            continue
        print(f"\n## {key}\nSOURCE: {fixture['text']}\nEDIT: {edit['proposed_text']}")
        warnings = edit.get("validation_warnings") or []
        if warnings:
            print("WARNINGS: " + json.dumps(warnings, ensure_ascii=False))

    output = args.output or (
        pathlib.Path("evals/artifacts") / f"answered-edit-{args.run_id}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "run_id": args.run_id,
        "resume_fixture": str(args.resume_fixture),
        "run": completed,
        "edits_by_key": edits_by_bullet,
    }, indent=2, ensure_ascii=False, default=str) + "\n")
    print(f"\nSaved answered edit run: {output}")
    return 0 if completed["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
