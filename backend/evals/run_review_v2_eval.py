"""Stage 1: the question-generating reviewer, measured alone.

Runs `services/bullet_review_v2.py` over `review_v2_cases.json` with the real model and prints
every candidate question it proposes, so the judgment can be read before any of it is wired into a
run. No planner, no editor, no database — and nothing in the app imports the module under test.

    cd backend && ./venv/bin/python evals/run_review_v2_eval.py --repeat 3

Nothing passes or fails. `run_review_eval.py` is the pass/fail check on the contract production
uses; this one exists to answer questions a pass/fail number cannot: are the doubts technically
useful, would an answer change the bullet, is the premise supported, are they distinct, and does a
strong bullet produce nothing.

Two things to read the output for specifically:

* the paired `same_bullet_high_fit` / `same_bullet_low_fit` cases print their strength assessments
  side by side. They must be the same sentence in substance. Strength drifting between them means
  the reviewer is still reading the job before the bullet.
* `repetitive_temptation` has one real gap phrasable ten ways. Filling the ceiling with rewordings
  is the failure; stopping at one or two is the reviewer knowing the difference.
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

from services import bullet_review_v2 as v2

CASES = pathlib.Path(__file__).parent / "review_v2_cases.json"
FIT_KEYS = ("supported_explicit", "related_inferred", "claimed_not_demonstrated", "resume_gaps")


def task_for(case):
    """The task `build_v2_tasks` would build, without a database.

    `requirements` is v1's field and is kept deliberately: `unsupported_requirement_terms` reads it
    to catch a question that asks the posting's own words back, and the labels come from the same
    fit context rather than being invented here.
    """
    fit = case.get("fit") or {}
    labels = [
        {"requirement": item["label"], "match": key, "importance": item.get("importance", "required")}
        for key in FIT_KEYS for item in fit.get(key) or []
    ]
    return {
        "bullet_id": case["id"],
        "text": case["text"],
        "entry": case.get("entry", ""),
        "siblings": case.get("siblings", []),
        "answers": case.get("answers", []),
        "requirements": labels,
        **{key: fit.get(key) or [] for key in FIT_KEYS},
    }


def job_tuple(job):
    return (job["title"], job["company"], job["summary"], job["requirements"])


def show(result, indent="      "):
    lines = []
    if result.get("unavailable_reason"):
        return [f"{indent}NOT REVIEWED: {result['unavailable_reason']}"]
    lines.append(f"{indent}strength: {result.get('strength_assessment')}")
    for fact in result.get("established_facts") or []:
        lines.append(f"{indent}  established: {fact}")
    if result.get("rewrite_from_existing_evidence"):
        lines.append(f"{indent}rewrite: {result['rewrite_from_existing_evidence']}")
    for candidate in result.get("question_candidates") or []:
        reference = candidate.get("requirement_reference") or "—"
        refused = "  (reference refused)" if candidate.get("reference_refused") else ""
        lines.append(
            f"{indent}[{candidate['id']}] {candidate.get('priority') or 'unranked'} · "
            f"{candidate.get('recruiter_doubt_type')} · ref {reference}{refused}"
        )
        lines.append(f"{indent}  Q: {candidate['question']}")
        lines.append(f"{indent}     missing: {candidate.get('missing_fact')}")
        lines.append(f"{indent}     matters: {candidate.get('why_it_matters_for_this_job')}")
        lines.append(f"{indent}     would let the bullet: {candidate.get('expected_resume_change')}")
    for drop in result.get("dropped") or []:
        lines.append(f"{indent}dropped {drop.get('id') or '?'}: {drop['why']}")
    if not result.get("question_candidates") and not result.get("rewrite_from_existing_evidence"):
        lines.append(f"{indent}(nothing proposed)")
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--model", default=None, help="overrides TAILORING_REVIEW_MODEL")
    parser.add_argument("--only", default=None, help="substring of a case id")
    parser.add_argument("--ceiling", type=int, default=v2.STAGE_1_EVAL_CEILING)
    args = parser.parse_args()

    data = json.loads(CASES.read_text())
    cases = [c for c in data["cases"] if not args.only or args.only in c["id"]]
    model = args.model or v2.MODEL
    print(f"review model: {model}   cases: {len(cases)}   repeat: {args.repeat}   "
          f"candidate ceiling: {args.ceiling} (validator accepts {v2.MAX_QUESTION_CANDIDATES})")
    print(f"calls: {len(cases) * args.repeat}\n")

    runs = {}
    for case in cases:
        job = job_tuple(data["jobs"][case["job"]])
        task = task_for(case)
        allowed = v2.referenceable(task)
        print(f"── {case['id']}   ({case['job']})")
        print(f"   {case['text']}")
        print(f"   referenceable: {', '.join(sorted(allowed)) or '— none'}")
        gap_ids = {item['id'] for item in task['resume_gaps']} | {
            item['id'] for item in task['claimed_not_demonstrated']}
        if gap_ids:
            print(f"   context only, must not be referenced: {', '.join(sorted(gap_ids))}")
        print(f"   expect: {case['expect']}")

        results = []
        for attempt in range(args.repeat):
            try:
                raw = (v2.request_review(job, [task], model=model, ceiling=args.ceiling) or [{}])[0]
            except v2.ReviewUnavailable as exc:
                print(f"      {attempt + 1}. ERROR: {exc}")
                results.append(None)
                continue
            try:
                result = v2.validate(raw, task)
            except v2.ContractViolation as exc:
                # The one thing that is not a bad candidate but two incompatible claims. In a run
                # this retries and then abandons the bullet; here it is printed, because a reviewer
                # that does it often is telling us the contract is unclear.
                print(f"      {attempt + 1}. CONTRACT VIOLATION: {exc}")
                results.append(None)
                continue
            results.append(result)
            prefix = f"      {attempt + 1}." if args.repeat > 1 else "     "
            for line in show(result, indent=prefix + " "):
                print(line)
        runs[case["id"]] = results
        print()

    # ── what the numbers are for: whether ten was the right first ceiling ──
    counts, referenced, dropped, unavailable = [], 0, 0, 0
    for case_id, results in runs.items():
        for result in results:
            if result is None:
                unavailable += 1
                continue
            candidates = result["question_candidates"]
            counts.append(len(candidates))
            referenced += sum(1 for c in candidates if c["requirement_reference"])
            dropped += len(result["dropped"])
    total = sum(counts)
    print("── totals")
    print(f"   candidates: {total} across {len(counts)} reviews "
          f"(mean {total / max(len(counts), 1):.1f}, max {max(counts, default=0)})")
    print("   distribution: " + ", ".join(
        f"{n}×{count}" for n, count in sorted(Counter(counts).items())
    ))
    print(f"   at the ceiling ({args.ceiling}): {sum(1 for n in counts if n >= args.ceiling)} reviews")
    print(f"   with a requirement reference: {referenced} of {total}")
    print(f"   candidates dropped by the server: {dropped}")
    print(f"   reviews unavailable: {unavailable}")

    pair = [runs.get("same_bullet_high_fit") or [], runs.get("same_bullet_low_fit") or []]
    if all(pair):
        print("\n── the paired bullet: strength must not move with the job")
        for label, results in zip(("high fit", " low fit"), pair):
            for attempt, result in enumerate(results, start=1):
                if result:
                    print(f"   {label} {attempt}: {len(result['question_candidates'])} candidates"
                          f" · {result['strength_assessment']}")

    print("\nNow read them: is each question about work the bullet already claims, answerable in "
          "one sentence, distinct from its siblings, and worth the candidate's time? Did any "
          "strong bullet produce a question it should not have?")


if __name__ == "__main__":
    main()
