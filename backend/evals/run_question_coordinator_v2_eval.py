#!/usr/bin/env python3
"""Stage 2: measure the Question Coordinator without changing production.

The fixture supplies immutable reviewer candidates. The coordinator may return their ids only.
Every selected question and rejection is printed, executable expectations fail loudly, and the
reader still judges whether the surviving set is the smallest useful one.

    cd backend
    ./venv/bin/python evals/run_question_coordinator_v2_eval.py --repeat 3
"""

import argparse
from collections import Counter
import json
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")

from services import question_coordinator_v2 as coordinator


CASES = pathlib.Path(__file__).with_name("question_coordinator_v2_cases.json")


def job_tuple(job):
    return job["title"], job["company"], job["summary"], job.get("requirements") or []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--model", default=None, help="overrides TAILORING_REVIEW_MODEL")
    parser.add_argument("--only", default=None, help="comma-separated case ids or substrings")
    args = parser.parse_args()

    data = json.loads(CASES.read_text())
    wanted = [part.strip() for part in (args.only or "").split(",") if part.strip()]
    cases = [
        case for case in data["cases"]
        if not wanted or any(part in case["id"] for part in wanted)
    ]
    model = args.model or coordinator.MODEL
    print(f"coordinator model: {model}   cases: {len(cases)}   repeat: {args.repeat}")
    print(f"evaluation attempts: {len(cases) * args.repeat} "
          "(an attempt may call overlap review, selection, and a corrective retry)\n")

    stable, flaky, failed = [], [], []
    totals = Counter()
    for case in cases:
        candidates = coordinator.collect_candidates(case["bullets"])
        lookup = {item["id"]: item for item in candidates}
        print(f"── {case['id']}   ({case['job']})")
        print(f"   expect: {case['expect']}")
        for item in candidates:
            print(f"   input {item['id']} · {item['priority']} · {item['missing_fact']}")
            print(f"      {item['question']}")

        judged = []
        for attempt in range(1, args.repeat + 1):
            try:
                raw = coordinator.request_selection(
                    job_tuple(data["jobs"][case["job"]]), candidates, model=model,
                )
                result = coordinator.validate(raw, candidates)
                problems = coordinator.evaluation_problems(result, case.get("expected"))
            except (coordinator.ReviewUnavailable, coordinator.ContractViolation) as exc:
                result, problems = None, [str(exc)]
            judged.append(problems)
            prefix = f"   {attempt}."
            if result is None:
                print(f"{prefix} ERROR: {problems[0]}")
                totals["unavailable"] += 1
                continue
            totals["selected"] += len(result["selected_ids"])
            totals["rejected"] += len(result["rejected"])
            print(f"{prefix} selected {len(result['selected_ids'])}")
            for key in result["selected_ids"]:
                print(f"      KEEP {key}: {lookup[key]['question']}")
            for rejection in result["rejected"]:
                suffix = (
                    f" -> {rejection['duplicate_of']}" if rejection.get("duplicate_of") else ""
                )
                print(f"      DROP {rejection['id']}: {rejection['reason']}{suffix}")
            for problem in problems:
                print(f"      FAIL: {problem}")

        passed = sum(not problems for problems in judged)
        if passed == len(judged):
            mark, bucket = "PASS", stable
        elif passed:
            mark, bucket = "FLAKY", flaky
        else:
            mark, bucket = "FAIL", failed
        bucket.append(case["id"])
        print(f"   [{mark}] {passed}/{len(judged)} attempts met expectations\n")

    print("── totals")
    print(f"   selected: {totals['selected']}   rejected: {totals['rejected']}   "
          f"unavailable: {totals['unavailable']}")
    print(f"\n── verdict\n   {len(stable)}/{len(cases)} stable, {len(flaky)} flaky, "
          f"{len(failed)} failed")
    if flaky:
        print("   flaky: " + ", ".join(flaky))
    if failed:
        print("   failed: " + ", ".join(failed))
    print("\nNow read the selected sets: did the coordinator remove paraphrases without deleting "
          "questions whose answers would add genuinely different facts?")
    return 1 if failed or flaky else 0


if __name__ == "__main__":
    raise SystemExit(main())
