"""Accuracy harness for the JD extractor.

Runs analyze_job_description() over a labeled dataset and reports per-field
accuracy. Uses the REAL OpenAI API (small cost, ~$0.0002/JD), so it's run by
hand — never in CI.

    cd backend && python evals/run_eval.py

Dataset: evals/dataset.jsonl — one JSON object per line:
    {"description": "<full JD text>", "expected": {title, company_name,
     location, work_type, skills: [...]}}
"""

import sys
import json
import pathlib

# make backend/ importable no matter where this is run from
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()  # OPENAI_API_KEY from backend/.env

from services.openai_services import analyze_job_description
from services.match import normalize_skill

DATASET = pathlib.Path(__file__).parent / "dataset.jsonl"
SCALAR_FIELDS = ["title", "company_name", "location", "work_type"]


def prf(e, a):
    """precision, recall, F1 of two sets of already-normalized skills."""
    if not e and not a:
        return 1.0, 1.0, 1.0
    tp = len(e & a)
    precision = tp / len(a) if a else 0.0
    recall = tp / len(e) if e else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def norm(v):
    return str(v).strip().lower() if v is not None else None


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    rows = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
    if not rows:
        print("evals/dataset.jsonl is empty — add labeled JDs first.")
        return

    scalar_hits = {f: 0 for f in SCALAR_FIELDS}
    prfs = []
    for i, row in enumerate(rows, 1):
        if not verbose:
            print(f"  [{i}/{len(rows)}] analyzing…", end="\r")
        job = analyze_job_description(row["description"])
        exp = row["expected"]

        for f in SCALAR_FIELDS:
            if norm(getattr(job, f)) == norm(exp.get(f)):
                scalar_hits[f] += 1

        exp_sk = {normalize_skill(s) for s in exp.get("skills", [])}
        got_sk = {normalize_skill(s) for s in job.skills}
        p, r, f1 = prf(exp_sk, got_sk)
        prfs.append((p, r, f1))

        if verbose:
            print(f"\n[{i}] {exp.get('title')}   (P={p:.2f} R={r:.2f} F1={f1:.2f})")
            for f in SCALAR_FIELDS:
                got, want = getattr(job, f), exp.get(f)
                if norm(got) != norm(want):
                    print(f"    {f}: got {got!r}  expected {want!r}")
            missed, extra = sorted(exp_sk - got_sk), sorted(got_sk - exp_sk)
            if missed:
                print(f"    skills MISSED (label, not extracted): {missed}")
            if extra:
                print(f"    skills EXTRA  (extracted, not in label): {extra}")

    n = len(rows)
    print(f"\n\nn = {n}\n")
    for f in SCALAR_FIELDS:
        print(f"{f:14} {scalar_hits[f]}/{n} = {scalar_hits[f] / n:.0%}")
    ap = sum(x[0] for x in prfs) / n
    ar = sum(x[1] for x in prfs) / n
    af = sum(x[2] for x in prfs) / n
    print(f"{'skills':14} precision={ap:.2f}  recall={ar:.2f}  F1={af:.2f}")


if __name__ == "__main__":
    main()
