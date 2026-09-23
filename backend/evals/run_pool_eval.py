"""Stage 1: the complete-pool review, measured on its own.

Reviews a whole resume the way the planner will — every experience and project bullet, in
chunks, with no requirement deciding who gets read — and prints all of it for a human. No
planner, no editor, no database, so nothing here can reach a resume.

    cd backend && ./venv/bin/python evals/run_pool_eval.py

Nothing passes or fails. `run_review_eval.py` is the pass/fail check on judgment the reviewer
has already been tuned against; this one exists to show the decisions on the bullets it has
never seen, because those are the ones the old requirement-driven pool hid. Read every one.

The ranking at the end is what the question cap would keep if this ran for real. It is printed
so the cap can be judged before it is built, not because anything enforces it yet.
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

CASES = pathlib.Path(__file__).parent / "pool_cases.json"
QUESTION_CAP = 8
# unranked sorts after `low`: a review that said nothing about value must not outrank one that
# honestly said the change barely matters
LEVEL_RANK = {"high": 0, "medium": 1, "low": 2}
UNRANKED = 3


def build_tasks(data):
    """The pool, in resume order, each bullet carrying its entry's other bullets.

    This is `services/bullet_review.build_tasks` without a database: same shape, same sibling
    rule, same per-bullet match findings.
    """
    tasks = []
    for entry in data["entries"]:
        for bullet in entry["bullets"]:
            tasks.append({
                "bullet_id": bullet["id"],
                "text": bullet["text"],
                "entry": entry["name"],
                "siblings": [b["text"] for b in entry["bullets"] if b["id"] != bullet["id"]],
                "answers": [],
                "requirements": bullet.get("match_findings", []),
            })
    return tasks


def job_tuple(job):
    return (job["title"], job["company"], job["summary"], job["requirements"])


def relevance_rank(task):
    """Job relevance as the cap would read it: does any requirement cite this bullet, and how
    important is it. Second to expected improvement, never first — ranking by importance alone
    is what would push an unmatched vague bullet out again."""
    order = {"required": 0, "preferred": 1, "nice_to_have": 2}
    return min((order.get(f.get("importance"), 3) for f in task["requirements"]), default=3)


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
        lines.append(f"   keep: {', '.join(result['facts_to_preserve']) or '-'}")
    if result["downgraded"]:
        lines.append(f"downgraded: {result['downgraded']}")
    if result["decision"] == bullet_review.KEEP and result["decision_reason"]:
        lines.append(f"kept because: {result['decision_reason']}")
    if result["decision"] == bullet_review.REVIEW_UNAVAILABLE:
        lines.append(f"NOT REVIEWED: {result.get('unavailable_reason')}")
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--model", default=None, help="overrides TAILORING_REVIEW_MODEL")
    parser.add_argument("--size", type=int, default=bullet_review.CHUNK_SIZE)
    args = parser.parse_args()

    data = json.loads(CASES.read_text())
    job = job_tuple(data["job"])
    tasks = build_tasks(data)
    by_id = {task["bullet_id"]: task for task in tasks}
    calls = len(bullet_review.chunks(tasks, args.size))
    model = args.model or bullet_review.MODEL

    print(f"review model: {model}   bullets: {len(tasks)}   chunk size: {args.size}   "
          f"calls per run: {calls}   repeat: {args.repeat}")
    print(f"job: {data['job']['title']} at {data['job']['company']}\n")

    runs = []
    for attempt in range(args.repeat):
        def on_chunk(index, chunk, reviews, attempt=attempt):
            counts = Counter(r["decision"] for r in reviews.values())
            print(f"  run {attempt + 1} chunk {index}/{calls}: "
                  + ", ".join(f"{d} {n}" for d, n in counts.most_common()))
        runs.append(bullet_review.review_bullets(
            job, tasks, model=model, size=args.size, on_chunk=on_chunk,
        ))
    print()

    for entry in data["entries"]:
        print(f"── {entry['name']}  ({entry['kind']})")
        for bullet in entry["bullets"]:
            decisions = [run[bullet["id"]]["decision"] for run in runs]
            counts = ", ".join(f"{d}×{n}" for d, n in Counter(decisions).most_common())
            agreed = "" if len(set(decisions)) == 1 else "   << runs disagree"
            matched = ", ".join(f["requirement"] for f in by_id[bullet["id"]]["requirements"])
            print(f"   [{counts}]{agreed}")
            print(f"   {bullet['text']}")
            print(f"   matched: {matched or '— nothing (invisible to the old pool)'}")
            for index, run in enumerate(runs, start=1):
                for line in show(run[bullet["id"]]):
                    print(f"      {index}. {line}" if args.repeat > 1 else f"      {line}")
            print()

    first = runs[0]
    counts = Counter(result["decision"] for result in first.values())
    print("run 1 totals: " + ", ".join(f"{d} {n}" for d, n in counts.most_common()))

    asks = sorted(
        (bullet_id for bullet_id, r in first.items() if r["decision"] == bullet_review.ASK),
        key=lambda bullet_id: (
            LEVEL_RANK.get(first[bullet_id]["improvement_level"], UNRANKED),
            relevance_rank(by_id[bullet_id]),
            [t["bullet_id"] for t in tasks].index(bullet_id),
        ),
    )
    print(f"\nquestions ranked by expected improvement, then relevance, then resume order "
          f"(cap {QUESTION_CAP}):")
    for position, bullet_id in enumerate(asks, start=1):
        over = "  (over the cap)" if position > QUESTION_CAP else ""
        level = first[bullet_id]["improvement_level"] or "unranked"
        print(f"  {position}. [{level}]{over} {first[bullet_id]['question']}")
    if not asks:
        print("  none")

    print("\nNow read them: is each question about work the bullet already claims, answerable "
          "in one sentence, and worth the candidate's time? Is every KEEP a bullet a recruiter "
          "would genuinely not ask about?")


if __name__ == "__main__":
    main()
