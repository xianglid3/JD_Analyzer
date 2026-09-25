# Question quality: repair, then compare

This implements the 2026-09-24 architecture review. No paid calls are run automatically.

## Stage 1 — trustworthy measurements

- Preserve existing labels; new annotations are keyed by run and bullet/question.
- Canonicalize posting identity when reopening runs.
- Show job/project context before ratings, and hide model explanations/selection until rated.
- Snapshot each real run before any human interaction, including input, normalized output and
  model requests/responses when captured. Reopen snapshots without a database or another payment.
- Preserve the two historical Google labelled runs as baseline evidence; never invent missing raw
  historical responses.
- Verify repeated-run history, offline review and blind display; then run the full backend suite.

## Stage 2 — repair current contracts

- Put gap definitions in the actual triage prompt; eliminate question-writing contradictions.
- Carry precise uncertainties from reviewer through generation and selection, with source quotes,
  missing fact, expected answer and resume benefit. Legacy stored reviews remain readable.
- Do not let generation silently turn a settled lens into an ask. Allow an explicit, reasoned
  reconsideration instead of treating legitimate abstention as a failed transport call.
- Carry known facts and requirement context to coordination in stable resume order.
- Remove automatic semantic selection/rejection shortcuts that bypass evidence judgment. Keep
  structural/identity constraints and exact-repeat checks.
- Verify bad outputs rejected and good outputs preserved; then full backend suite.

## Stage 3 — eval-only architecture comparison

- Add a project-level opportunity planner with source-checked output and concrete answer use.
- Use frozen resume/role input for both the repaired per-bullet baseline and project planner.
- Preserve full role text/structured requirements, all source bullets and current-run answers.
- Record each call, failure and stage, and save partial progress if interrupted.
- Compare generated and selected questions with offline, blind human ratings; do not manufacture
  a semantic PASS from question counts or vocabulary.
- No production replacement, new database schema, or answer-ownership change in this experiment.
- Run meaningful mocked boundary tests and the full backend suite after implementation.

## Paid validation gate

Run one paired Google experiment first. Inspect usefulness, repeated-known-fact rate,
unsupported premises, coverage of worthwhile uncertainties, cost and latency. Then use held-out
postings. Only the measured winner should reach the existing answer/editor path. No claim of
improved question quality is justified by mocked tests alone.

## Implementation checkpoint

All three implementation stages are complete. Stage 1: 567 passed / 409 skipped. Stage 2:
573 passed / 409 skipped. Final Stage 3 plus boundary corrections: 581 passed / 409 skipped.
The skips are DB-dependent checks unavailable locally. No paid measurement or deployment yet.
See `question_comparison_guide.md` for the owner-run pilot and its limitations. Production now
uses the documented 20 raw / 10 selected ceilings rather than a hidden generation cap of three.
