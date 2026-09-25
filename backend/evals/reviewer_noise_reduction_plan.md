# Focused reviewer noise-reduction plan

## Problem

The focused pipeline is contract-reliable, but it treats facts an interviewer could explore as
material resume gaps. In the approved five-JD matrix it selected 45 questions for the same 12
polished bullets. Human labels for the Google case say 10 bullets should remain unchanged and only
`panther_ros` and `panther_point_cloud` should be questioned.

The failure begins in clarity and claim-support review. The generator faithfully turns those false
findings into questions, and the coordinator removes only some of them. This stage changes the
review boundary and adds later defensive checks; it does not add lexical ownership rules or weaken
evidence grounding.

## Decision boundary

A question is justified only when its answer would add or replace a concrete resume clause and
materially change what a recruiter can infer. More implementation depth, design rationale,
interview discussion, or a generic result is not enough.

A bullet is resume-ready when it already communicates:

- a concrete action or change;
- the object or system scope; and
- at least one useful differentiator such as a mechanism, boundary, behavior, verification, or
  stated result.

It need not contain every implementation detail or every possible result. Claim support activates
only for an actual comparative result, quantified result, guarantee, material scope claim, or
conflict. A mechanism's purpose and an architecture boundary are not unsupported results.

## Implementation

1. Add a structured `materiality_check` to focused review output. It records whether the answer
   would change the resume, the kind of missing core fact, the evidence already present, and the
   proposed resume delta.
2. Enforce stage-specific consistency in validation. Clarity may open only action/scope/behavior
   gaps; claim support may open only stated-claim/conflict gaps; opportunity may open only a
   supplied job-linked gap. Contradictory structured judgments fail and retry.
3. Rewrite clarity and claim-support prompts around resume sufficiency, using the measured polished
   and vague bullets as boundary examples. Do not reintroduce custom ownership-word heuristics.
4. Run a separate, terse clarity-sufficiency gate only when fresh clarity review proposes a material
   gap. The gate independently judges the target and prior answers without seeing the first finding
   or siblings, preventing both confirmation bias and a vague sibling from excusing a vague target.
   It must quote the controlling evidence and identify either a concrete resume clause or one exact
   `[unknown]` clause. Generation and coordination retain the whole entry for sibling coverage and
   deduplication. Persisted legacy per-finding gate rows remain resumable.
5. Require the question generator to perform an evidence-answerability and resume-delta test. It
   must mark findings covered or dismissed when the answer would only restate the bullet.
6. Give the coordinator the same polished-bullet veto as defense in depth while preserving its
   existing whole-resume deduplication role.
7. Preserve old stored normalized stages: missing `materiality_check` remains readable for recovery,
   while all fresh model calls receive the new required schema.

## Verification

- Contract tests cover valid and contradictory materiality judgments, legacy stored rows, quote
  grounding/repair, and generator/coordinator prompt boundaries.
- The final targeted set passed `120` tests with `11` environment-dependent skips before the last
  quote-format guard; its directly affected focused/eval subset then passed `50` tests.
- The final real matrix reached `waiting_for_user` for all five JDs against both fixtures. Every
  polished run selected only `panther_ros` and `panther_point_cloud` (2 questions). Every vague run
  selected those two plus all five intentionally weakened JobMatcha bullets (7 questions). Google
  matched every owner-authored action control in both fixtures.
- Exact run ids and selected wording are saved in
  `evals/artifacts/focused_v1_5jd_2resume_questions.md`.
- The accepted operating tolerance is one or two questionable questions per run; prompt tuning stops
  here rather than treating stochastic wording variation as a release blocker.
- Run the full backend suite once after implementation and record the result in the worklog.

No production flag, database schema, state transition, editor grounding rule, commit, push, or
deployment is part of this stage.
