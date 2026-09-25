# Plaid vague-resume editor comparison

## Setup

- Job: the same Plaid posting used by the real-resume matrix.
- Resume: `real_resume_vague.json`.
- Review contract: `focused_v1`.
- Answers: the same seven controlled answers used in the prior audit.
- Final run: `304fde8f-7919-4de1-b3e9-2f220d10cccc`.
- Final artifact: `artifacts/plaid-vague-answered-editor-v3-304fde8f.json`.

The implementation changed only the editor contract and the context handed to it. Questions and
answers remain separate from supporting evidence, complete answers become the primary draft, and
same-entry sibling bullets are supplied for repetition checking without widening citation scope.

## Result

The prior editor produced three cleanly acceptable edits out of seven. The final run produced four
strong edits, one usable edit with a small wording issue, and two edits that still need revision.

| Bullet | Final assessment | Measured change |
| --- | --- | --- |
| JobMatcha grounding | Usable; minor revision | The grounding rule now leads the bullet and no unsupported premise was added. The editor omitted the literal word `backend`, which the validator surfaced as a warning. |
| JobMatcha PostgreSQL | Accept | Preserves the answer's pooled psycopg2, GIN search, row locking, partial indexes, and exact enforced invariants. It no longer invents performance optimization from the question. |
| JobMatcha worker | Accept | Uses the answer as the draft and preserves the worker boundary, leases, fencing tokens, checkpoints, kill test, resume behavior, and no-duplicate outcome. |
| JobMatcha idempotency | Revise | Keeps reserve-before-spend and stored-response replay, but weakens the answer's exact `duplicate LLM calls` detail to generic duplicate API calls. |
| JobMatcha delivery | Accept | Preserves the number split and correctly scopes the throwaway PostgreSQL database to included backend tests rather than all 668 backend tests. |
| Panther ROS | Reject / keep original | Shared-credit and ongoing tense are preserved, but the proposal still copies the point-cloud sibling's technical inventory. |
| Panther point cloud | Accept | Keeps direct ownership limited to the operations confirmed in the answer and no longer claims direct LiDAR-to-camera implementation. Candidate-cluster filtering and centroid estimation survive. |

## Remaining limits

The editor still does not reliably obey sibling-de-duplication when the candidate's answer itself
repeats the sibling. Fixing that robustly belongs in a final composition review, where all proposals
can be compared without letting one candidate edit another candidate's bullet. The exact `LLM calls`
omission is a smaller answer-fidelity miss. Both fall within the accepted tolerance of one or two
model mistakes per run and remain visible to the user through proposal review and validation
warnings where deterministic evidence-loss checks apply.

The run also exposed a separate validator false positive: `deployment` was reported missing even
though the proposal begins with `Deployed`. That morphology issue is logged for a later validator
stage and was not turned into another editor prompt tweak.
