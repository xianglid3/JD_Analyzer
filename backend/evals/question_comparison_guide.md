# Real-input question comparison

## What changed

The current reviewer now has a complete triage contract and must describe each uncertainty
before generation. A question must map to that uncertainty. The coordinator sees the findings and
fit context, in resume order. Wording heuristics no longer force acceptance or rejection.

The experimental planner now uses two enforced phases. It first audits the entire entry without a
job description, then supplies only confirmed weakness IDs to a job-relevance/question pass. It
never enters the deployed run manager/editor path. Multiple questions require multiple distinct
audited weaknesses; no question-count target is treated as evidence of quality.

## Run once, review without rerunning

From `backend`, generate the repaired baseline with the actual resume and raw Google posting:

```bash
./venv/bin/python evals/run_real_resume_v2_eval.py --only google --no-human-review
```

This paid command uses the existing **test database** guard and resets that test schema. It saves
an artifact path before any ratings. The snapshot contains the raw posting, analyzed requirements,
resume, stored decisions, selected questions, source hashes and JSON-call requests/responses.
JSON calls are also journaled as they finish, including exceptions. Analyzer provider output is
not captured by that journal: its parsed output and raw posting are in the snapshot.

Use the printed snapshot path for the comparison:

```bash
./venv/bin/python evals/run_question_comparison.py --snapshot /path/to/saved-run.json
```

Only the project arm makes new paid calls: one per entry, then coordination. It uses the SAME
recorded model, target text, answers, extracted job representation and fit context. The baseline
is reused, not regenerated. There is no database access or schema reset in this command.
Partial results and provider responses are saved even if a project fails.

After changing only the experimental planner, reuse the already-paid recorded baseline rather than
rerunning it:

```bash
./venv/bin/python evals/run_question_comparison.py \
  --iterate evals/artifacts/comparison-dc0116933336/comparison.json
```

This intentionally permits the frozen baseline's older source hashes and records both versions plus
the parent comparison id. It pays only for the new evidence-first planner and its coordinator.

The command prints the exact offline review command:

```bash
./venv/bin/python evals/run_question_comparison.py --review /path/to/comparison.json
```

Sets A/B are randomly assigned. Read the posting and resume context, then label each bullet and
question. Model decisions, explanations and selection are hidden while rating. Labels save after
every answer and are keyed by comparison run, bullet and candidate. Review reopens all questions
so you can revise ratings; it does not silently reuse ratings from another run.

After rating both sets:

```bash
./venv/bin/python evals/run_question_comparison.py --report /path/to/comparison.json
```

This reveals the arms and counts labels for all generated versus selected questions. Useful-but-
reworded stays separate from useful; unavailable/unrated outputs are not successes. No synthetic
PASS is generated. Artifacts are gitignored because they contain resume data. Existing historical
labels are retained unchanged and are AI-assisted calibration evidence, not independent gold.

## What this does and does not establish

Judge whether a question can add a distinct, job-relevant resume fact. Penalize repeated known
facts, generic interview prompts and unsupported premises. A conditional exploration of a missing
fact is not itself a claim that the applicant did it. The planner validates identity, source quotes,
shape and references; human review evaluates semantics. It does not assert any new resume facts.

This compares two packages: repaired per-bullet planning with existing question guards versus joint
project planning with structural guards. It does **not** isolate architecture from every validation
choice. Provider traces expose rejected output so those causes can be separated in a subsequent
controlled experiment. The raw JD is preserved for humans; both model arms deliberately receive
the same extracted representation. JD extraction quality remains a separate check.

One paired run is a pilot, not proof of stability. After reviewing Google, repeat and then run
held-out postings. Answers, final edits, grounding, production DB behavior and deployment remain
separate validation gates. Do not deploy the experimental planner on the strength of unit tests.

Current production ceilings are 20 generated candidates and 10 selected questions per bullet.
The isolated reviewer eval can deliberately use a smaller --ceiling; it now applies to generation
as well. More capacity is not a quality guarantee, and the larger ceiling's runtime needs measuring.
