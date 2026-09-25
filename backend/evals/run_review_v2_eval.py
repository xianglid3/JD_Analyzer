"""Stage 1: the question-generating reviewer, measured alone.

Runs `services/bullet_review_v2.py` over `review_v2_cases.json` with the real model and prints
every candidate question it proposes, so the judgment can be read before any of it is wired into a
run. No planner, no editor, no database — and nothing in the app imports the module under test.

    cd backend && ./venv/bin/python evals/run_review_v2_eval.py --repeat 3

Each case has executable expectations for decisions, question counts, forbidden premises and the
concepts a useful question must cover. Those checks make the command fail loudly. The complete
output is still printed because semantic quality cannot be reduced to the score: a reader must
still decide whether each surviving doubt is technically useful and worth the candidate's time.

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
FIT_KEYS = (
    "supported_explicit", "related_inferred", "claimed_not_demonstrated",
    "related_partial", "resume_gaps",
)


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
    if result.get("decision_claimed"):
        lines.append(f"{indent}decision: {result['decision_claimed']} — {result.get('decision_reason')}")
    if result.get("gap_scan"):
        lines.append(f"{indent}gap scan: " + ", ".join(
            f"{key}={value}" for key, value in result["gap_scan"].items()
        ))
    lines.append(f"{indent}strength: {result.get('strength_assessment')}")
    for fact in result.get("established_facts") or []:
        lines.append(f"{indent}  established: {fact}")
    if result.get("rewrite_from_existing_evidence"):
        lines.append(f"{indent}rewrite: {result['rewrite_from_existing_evidence']}")
    for candidate in result.get("question_candidates") or []:
        reference = candidate.get("requirement_reference") or "—"
        lines.append(
            f"{indent}[{candidate['id']}] {candidate.get('priority') or 'unranked'} · "
            f"{candidate.get('recruiter_doubt_type')} · ref {reference}"
        )
        lines.append(f"{indent}  Q: {candidate['question']}")
        lines.append(f"{indent}     missing: {candidate.get('missing_fact')}")
        lines.append(f"{indent}     matters: {candidate.get('why_it_matters_for_this_job')}")
        lines.append(f"{indent}     would let the bullet: {candidate.get('expected_resume_change')}")
        for warning in candidate.get("warnings") or []:
            lines.append(f"{indent}     ! {warning}")
    for drop in result.get("hard_rejected") or []:
        lines.append(f"{indent}REJECTED {drop.get('id') or '?'}: {drop['why']}")
    if not result.get("question_candidates") and not result.get("rewrite_from_existing_evidence"):
        lines.append(f"{indent}(nothing proposed)")
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--model", default=None, help="overrides TAILORING_REVIEW_MODEL")
    parser.add_argument("--only", default=None,
                        help="comma-separated case ids or substrings")
    parser.add_argument("--ceiling", type=int, default=v2.STAGE_1_EVAL_CEILING)
    # The two experiments, each moving exactly one variable. Default off, so the baseline the
    # first measurement produced stays reproducible.
    # The decision is the contract now. The flag survives inverted, so the silent baseline the
    # first measurement produced stays reproducible for comparison.
    parser.add_argument("--no-decision", action="store_true",
                        help="reproduce the original silent contract, with no KEEP/REWRITE/ASK")
    args = parser.parse_args()

    data = json.loads(CASES.read_text())
    wanted = [part.strip() for part in (args.only or "").split(",") if part.strip()]
    cases = [c for c in data["cases"]
             if not wanted or any(part in c["id"] for part in wanted)]
    model = args.model or v2.MODEL
    variant = "silent (no decision)" if args.no_decision else "decision contract"
    print(f"review model: {model}   cases: {len(cases)}   repeat: {args.repeat}   "
          f"candidate ceiling: {args.ceiling} (validator accepts {v2.MAX_QUESTION_CANDIDATES})")
    print(f"contract: {variant}"
          + (" + separate question generation" if not args.no_decision else ""))
    print(f"evaluation attempts: {len(cases) * args.repeat} "
          "(an ASK gets one separate question-generation call)\n")

    runs = {}
    stable, flaky, failed = [], [], []
    for case in cases:
        job = job_tuple(data["jobs"][case["job"]])
        task = task_for(case)
        allowed = v2.referenceable(task)
        print(f"── {case['id']}   ({case['job']})")
        print(f"   {case['text']}")
        print(f"   referenceable: {', '.join(sorted(allowed)) or '— none'}")
        gap_ids = {
            item["id"]
            for key in ("claimed_not_demonstrated", "related_partial", "resume_gaps")
            for item in task[key]
        }
        if gap_ids:
            print(f"   context only, must not be referenced: {', '.join(sorted(gap_ids))}")
        print(f"   expect: {case['expect']}")

        results = []
        judged = []
        for attempt in range(args.repeat):
            try:
                raw = (v2.request_review(job, [task], model=model, ceiling=args.ceiling,
                                         decision=not args.no_decision,
                                         triage_only=not args.no_decision) or [{}])[0]
            except v2.ReviewUnavailable as exc:
                print(f"      {attempt + 1}. ERROR: {exc}")
                results.append(None)
                judged.append([str(exc)])
                continue
            # What the model proposed, before the server had an opinion. Printed because "the
            # validator is deleting good questions" and "the model only wrote one" are different
            # diagnoses and the survivor count alone cannot tell them apart.
            offered = [c for c in (raw.get("question_candidates") or []) if isinstance(c, dict)]
            print(f"      {attempt + 1}. triage: decision {raw.get('decision')}"
                  + (f" · {len(offered)} candidate(s)" if args.no_decision else " · no question text"))
            for entry in offered:
                print(f"         raw[{entry.get('id')}] missing={entry.get('missing_fact')!r}")
                print(f"              {entry.get('question')}")
            try:
                result = v2.validate(
                    raw, task,
                    require_decision=not args.no_decision,
                    triage_only=not args.no_decision,
                )
            except v2.ContractViolation as exc:
                # The one thing that is not a bad candidate but two incompatible claims. In a run
                # this retries and then abandons the bullet; here it is printed, because a reviewer
                # that does it often is telling us the contract is unclear.
                print(f"      {attempt + 1}. CONTRACT VIOLATION: {exc}")
                results.append(None)
                judged.append([str(exc)])
                continue
            primary_count = len(result.get("question_candidates") or [])
            if result.get("decision_claimed") == "ASK" and primary_count < 2:
                try:
                    result = v2.expand_review(job, task, result, model=model, limit=args.ceiling)
                except v2.ReviewUnavailable as exc:
                    print(f"      {attempt + 1}. ALTERNATIVE ERROR: {exc}")
                    results.append(None)
                    judged.append([str(exc)])
                    continue
                added = len(result.get("question_candidates") or []) - primary_count
                print(f"      {attempt + 1}. question generation added {added} candidate(s)")
            results.append(result)
            problems = v2.evaluation_problems(result, case.get("expected"))
            judged.append(problems)
            prefix = f"      {attempt + 1}." if args.repeat > 1 else "     "
            for line in show(result, indent=prefix + " "):
                print(line)
            for problem in problems:
                print(f"{prefix} FAIL: {problem}")
        runs[case["id"]] = results
        passed = sum(not problems for problems in judged)
        if passed == len(judged):
            mark, bucket = "PASS", stable
        elif passed:
            mark, bucket = "FLAKY", flaky
        else:
            mark, bucket = "FAIL", failed
        bucket.append(case["id"])
        print(f"   [{mark}] {passed}/{len(judged)} attempts met the executable expectations")
        print()

    # ── what the numbers are for: whether ten was the right first ceiling ──
    counts, offered, referenced, rejected, warned, unavailable = [], 0, 0, 0, 0, 0
    for case_id, results in runs.items():
        for result in results:
            if result is None:
                unavailable += 1
                continue
            candidates = result["question_candidates"]
            counts.append(len(candidates))
            offered += result.get("offered", 0)
            referenced += sum(1 for c in candidates if c["requirement_reference"])
            rejected += len(result["hard_rejected"])
            warned += sum(1 for c in candidates if c.get("warnings"))
    total = sum(counts)
    print("── totals")
    if not args.no_decision:
        claimed = Counter(
            (result or {}).get("decision_claimed") or "—"
            for results in runs.values() for result in results
        )
        print("   decisions claimed: " + ", ".join(f"{k} {v}" for k, v in claimed.most_common()))
        contradictions = sum(
            1 for results in runs.values() for result in results
            if result and result.get("decision_claimed") == "KEEP"
            and (result["question_candidates"] or result.get("offered"))
        )
        print(f"   claimed KEEP but proposed questions anyway: {contradictions}")
    print(f"   candidates: {total} across {len(counts)} reviews "
          f"(mean {total / max(len(counts), 1):.1f}, max {max(counts, default=0)})")
    print("   distribution: " + ", ".join(
        f"{n}×{count}" for n, count in sorted(Counter(counts).items())
    ))
    print(f"   at the ceiling ({args.ceiling}): {sum(1 for n in counts if n >= args.ceiling)} reviews")
    print(f"   with a requirement reference: {referenced} of {total}")
    print(f"   raw offered by the model: {offered}   accepted: {total}   "
          f"hard-rejected: {rejected}")
    print(f"   accepted but carrying a warning: {warned}")
    print(f"   reviews unavailable: {unavailable}")

    pair = [runs.get("same_bullet_high_fit") or [], runs.get("same_bullet_low_fit") or []]
    if all(pair):
        print("\n── the paired bullet: strength must not move with the job")
        for label, results in zip(("high fit", " low fit"), pair):
            for attempt, result in enumerate(results, start=1):
                if result:
                    print(f"   {label} {attempt}: {len(result['question_candidates'])} candidates"
                          f" · {result['strength_assessment']}")

    print(f"\n── verdict\n   {len(stable)}/{len(cases)} stable, {len(flaky)} flaky, "
          f"{len(failed)} failed")
    if flaky:
        print("   flaky: " + ", ".join(flaky))
    if failed:
        print("   failed: " + ", ".join(failed))
    print("\nNow read them: is each question about work the bullet already claims, answerable in "
          "one sentence, distinct from its siblings, and worth the candidate's time? Did any "
          "strong bullet produce a question it should not have?")
    sys.exit(1 if failed or flaky else 0)


if __name__ == "__main__":
    main()
