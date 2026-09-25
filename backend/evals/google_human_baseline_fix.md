# Google human-baseline fix

## Evidence

The first labelled run used the submitted 12-bullet resume and the real Google SWE internship.
The owner marked 10 bullets KEEP and two Panther Racing bullets ASK. The reviewer agreed on 6/12.
Of 12 surviving questions, none was acceptable as written: five repeated evidence, four were
low-value, two assumed unsupported premises, and one useful ownership question needed rewording.
The run failed before coordination because single-target calls returned sibling reviews.

## Stage 1 — reviewer boundary

- A one-bullet call sends one top-level `target`, without model-owned bullet keys.
- It requests one direct review object, without a `reviews` array.
- Siblings remain context only.
- Retry instructions preserve the same direct contract.
- The strength decision explicitly treats concrete `Built`, `Made`, `Implemented`, `Designed` and
  `Deployed` statements as owned contributions.
- Fit ranks a weakness found from resume evidence; it cannot create an algorithms, data structures,
  distributed-systems or scale gap inside an otherwise complete bullet.
- Candidate generation excludes interview stories, already-named tools, generic challenges,
  redundant role questions and unclaimed scalability.

Acceptance: focused tests and the full backend suite pass. Then rerun only the Google real-input
audit and label it interactively. Do not run another JD yet.

## Stage 2 — resume-level selection

Begin only after Stage 1 produces a complete Google review. The coordinator must see the full
candidate pool and project siblings, reject same-answer questions, keep the most specific ownership
question, and preserve a distinct system-role question when it would change a different fact.

Acceptance: the owner labels every shown question useful or useful-but-reword; no KEEP bullet gets
a shown question.

## Stage 3 — answer and edit

Supply truthful answers only to the selected Panther questions. Verify that the editor changes the
correct bullet, preserves supported technologies and scope, introduces no unsupported claims, and
produces a materially stronger bullet.

Only after the Google case passes all three stages should the same resume run against Palantir,
Aerotech, Plaid and TikTok.
