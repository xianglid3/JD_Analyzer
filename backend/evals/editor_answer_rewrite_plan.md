# Answer-to-edit improvement plan

## Measured failures

The Plaid vague-resume run recovered useful facts in all seven answers, but the editor weakened
four of the resulting bullets:

- it blended a resume-ready answer into the vague source instead of using the answer as the draft;
- it changed fact relationships (`cone validation and centroid estimation` became `validating cone
  centroids`);
- it changed number scope (a subset of backend tests read as though every backend test used the
  throwaway PostgreSQL database);
- it copied the same cone-perception inventory into two sibling bullets.

## Smallest production change

1. Make a complete answer the primary draft for an answered target. Retain source facts only when
   they add distinct, useful information.
2. Tell the editor to preserve relationships, qualifiers, and number attachment rather than merely
   preserving the same words.
3. Supply same-entry sibling text as context only. The editor may use it to avoid repetition but may
   not cite or copy it into the active bullet.
4. If the answer adds nothing distinct from a sibling, keep the active bullet instead of producing a
   redundant proposal.

These are editor judgment rules. They are not deterministic truth gates: the observed failures are
semantic, and turning them into broad regex hard blocks would reject good rewrites.

## Verification

- Add focused contract tests for the answer-first and sibling-context instructions.
- Run the focused editor and claim-check tests.
- Repeat the same vague Plaid question-answer-edit flow with the same seven answers.
- Compare every new edit with the prior edit and the paired polished resume.
- Run the full backend suite once after the stage is complete.
