# Focused review assessment

## What the code currently does

- Production V2: `bullet_review_v2.prompt_for(triage_only=True)` constructs a 1,697-word system
  prompt before input data. A single call assesses five lenses, relevance, and KEEP/REWRITE/ASK.
  `_review_one` then calls question generation for ASK findings. The generator may reconsider
  usefulness, followed by overlap review and a global coordinator (1,017-word system prompt).
- Experimental review: `project_question_planner.plan_project` first makes a 913-word evidence-only
  assessment, then a 266-word relevance/question call for UNCERTAIN targets, then coordination.
  It isolates the JD but does not independently inspect different kinds of omissions.
- Question answers receive text/length/control-character checks in
  `routes/tailoring.resolve_tailoring_question`. `resolve_detail_request` stores the answer and
  resumes after pending questions settle. `tailoring_candidates.make_answerable` checks answer
  presence/status, not whether the requested fact was actually supplied. The editor then consumes
  answers. There is no dedicated answer-clarification stage in this path.

Word counts are whitespace-delimited system-prompt counts, not token counts. Length alone is not
proven to cause the failures. The repeated policy judgments and ambiguous definitions are observable;
their individual causal effects have not been isolated.

## Define vague before choosing an action

A claim is materially vague when, in its entry context, a reader cannot identify the work or change
being claimed because an essential referent, action, scope, or supporting detail is missing.
"Improved reliability" without a change says less than "resumed interrupted work from checkpoints."
"Implemented caching" can identify a technique but leave its application unclear. A short bullet
is not automatically vague; no metric or implementation source code is required.

Record the exact ambiguous phrase, what is already known, the particular unknown, and what a
possible answer would add. Detecting vagueness is separate from deciding whether asking is useful.
Some questions are enrichment opportunities rather than repairs: an already-clear bullet can still
benefit from relevant experience the user has not thought to mention. Do not make every good question
pretend that the source bullet is defective.

Vague does not automatically mean ASK:
- Evidence/answers already settle the issue: a supported rewrite may suffice.
- Unknown matters to the target role and a useful answer could change the resume: ASK.
- Unknown adds little value, repeats another question, or the user cannot supply it: leave it.

## Product heuristics currently mixed with correctness

1. Verb-based contribution judgments: "Built" is a claim of personal work, not proof of scope;
   "Worked on" is a signal to inspect context, not an automatic failure.
2. "Strong stays KEEP" / "unknown isn't material": useful noise controls, but too broad as gates
   against all new, role-relevant information.
3. "JD can only rank an existing weakness": prevents keyword fishing but blocks a separate,
   non-presumptive exploration of relevant experience on otherwise sufficient bullets.
4. Generic technology, impact, and challenge prohibitions: should distinguish low-value wording
   from a fact worth discovering. Asking whether experience exists is different from asserting it.
   Production question-term checks also require a separate pass/fail audit before changing them.
5. The five doubt types are our taxonomy; requiring complete scans and category preservation
   does not establish whether a question helps. Category agreement is not a truth invariant.
6. "Irrelevant stays KEEP", sibling overlap preferences, and one-question-per-weakness decisions
   are product choices, not proof of correctness. Test their effects rather than accumulating rules.

Coordinator lexical ownership/result signals are currently hints, not automatic rejections or
selections. Do not misdiagnose them as current deterministic hard blocks.

## Proposed bounded experiment

Replace the monolithic experimental evidence judgment with narrow assessments; do not simply append
another judge after KEEP, which would prevent it from inspecting missed gaps.

1. Shared factual extraction: target and sibling facts with provenance. No quality decision.
2. Independent checks over every bullet in an entry: (a) vague action/implementation/scope,
   (b) claimed-result support and contradictions, (c) relevant enrichment opportunities given the JD.
   These report findings, not KEEP/ASK and not question text. They may share batched entry input and
   run independently; per-bullet responsibility does not require one API call per bullet per check.
3. A question planner weighs those findings, removes overlaps/already-known facts, and writes the
   useful batch. Every question names the information sought and the change it could support.
   Conditional experience discovery must allow "no" without creating resume evidence.
4. Answer clarification compares each response to the requested fact. Use clear details directly;
   preserve the useful part of partial answers; ask a focused follow-up only for a consequential
   unresolved ambiguity or contradiction. Do not reject typos, shorthand, or unusual but clear facts.
   Propose one follow-up round initially as a UX limit, not a factual requirement. Skip/unknown ends
   questioning without invention. Further work requires explicit state, question-parent IDs,
   idempotency and run-lease tests; it is not an editor retry disguised as a question.
5. Existing grounded editing follows settled details. Ownership/citations/run provenance stay hard
   invariants regardless of the softer review policies.

The opportunity check is an explicit proposed change from the current evidence-only gate. It needs
positive examples of useful discovery and negative examples of forced JD keywords; do not silently
introduce it as another prompt patch.

## Evaluation before integration

Freeze expected missing facts separately from desired actions. Include vague and clear paraphrases,
same work under weak/strong verbs, sibling-resolved gaps, clear bullets with worthwhile enrichment,
irrelevant vague bullets, incomplete answers, contradictions, and "I don't know" responses.
Use the supplied polished/vague resume pair for development and other real JDs for held-out checks.
Rate whether each stage found the right unknown, whether its question helps, and whether an answer
produces a useful grounded edit. Keep model ratings explicitly separate from owner labels.

Compare current and focused designs on the same frozen inputs; retain the individual check outputs
so misses and false questions can be attributed. Measure latency/cost as well as utility. Existing
logs establish failures, not that adding calls necessarily solves them. No production change or
additional paid run was made during this assessment.
