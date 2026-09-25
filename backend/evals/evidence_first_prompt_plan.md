# Evidence-first reviewer prompt plan

## Measured problem

The blind Google comparison found that the per-bullet reviewer marked 10 of 12 bullets ASK and the
project planner marked all 12 ASK. Human review found only the ROS and point-cloud bullets worth
asking about. The project planner produced more candidates but lower precision. Both prompts used
job requirements while deciding whether a bullet was weak, so `algorithms`, `data structures`,
technologies, challenges, metrics and role restatements became manufactured gaps.

The coordinator removed some noise but could not replace a poor candidate with a useful fact that
the reviewer never proposed. This places the primary defect before coordination.

## Current production flow

1. Fit assessment supplies explicit, inferred, partial and missing requirements.
2. The V2 reviewer performs triage per bullet while seeing the job and fit context.
3. A second reviewer call generates questions for triaged uncertainties.
4. The coordinator compares all generated candidates and selects a resume-level set.
5. The run manager files selected questions. Answers later reach the editor and claim validator.

The prompts say that fit cannot create weakness, but the same model sees the job while creating the
weakness. The measured output shows that instruction is not an effective boundary.

## Proposed production contract

### 1. Entry evidence audit — no job input

Call once per project or employment entry so sibling bullets can settle ambiguity. Supply only:

- target bullets and stable IDs;
- sibling text within the entry;
- answers already supplied for those bullets.

Return `SUFFICIENT`, `REWRITE`, or `UNCERTAIN` for every bullet. `UNCERTAIN` must include one or
more weakness objects with:

- a stable weakness ID;
- an exact quote from the target containing the ambiguity;
- the precise factual clause a truthful answer could add;
- why target, siblings and answers do not settle it;
- one recruiter-doubt type.

The audit must never receive a job description, requirement list, fit state, taxonomy, or skill
graph result. This makes bullet sufficiency stable across postings and prevents keyword matching
from creating resume defects.

### 2. Job relevance and question wording — audited weaknesses only

Call only for entries containing `UNCERTAIN` bullets. Supply the fixed weakness objects, evidence
context, job summary, and only the explicit/inferred requirement IDs belonging to each target.
Resume-wide gaps, keyword-only skills and partial matches are excluded.

For every weakness return `ASK` or `NOT_MATERIAL`. `ASK` writes exactly one focused question for
the supplied missing clause and assigns priority. It cannot create, split, broaden or rename a
weakness. Several questions are possible only when the evidence audit identified several distinct
missing clauses. This preserves the many-question design without generating paraphrase padding.

### 3. Resume-level coordinator

Keep the coordinator as a separate global judgment. It receives source quote, missing clause,
question, siblings, answers and fit ranking. It may select or reject supplied questions, remove
cross-bullet overlap, and keep a distinct replacement candidate. It cannot write questions or
invent gaps.

The coordinator prompt should be shorter after cutover:

- select only when the question actually seeks its audited missing clause;
- reject answered, overlapping, irrelevant, unsupported or low-value questions;
- use priority and fit only after usefulness is established;
- select zero when no candidate is worth asking.

### 4. Deterministic validation boundary

Hard validation should enforce facts the server can prove:

- exact target, bullet and weakness identities;
- exact source quote belongs to the target;
- all returned weakness IDs were supplied and accounted for once;
- only local referenceable requirement IDs are used;
- question/rewrite/action shapes are mutually consistent;
- one question per audited weakness and bounded arrays.

Semantic usefulness, whether wording is overly broad, and whether two paraphrases would receive the
same answer remain model/human judgments. Do not turn keyword heuristics into hard grounding rules.

### 5. Run-manager and editor mapping

- `SUFFICIENT` or all weaknesses `NOT_MATERIAL` creates no candidate and costs no editor step.
- `REWRITE` creates the existing bullet-owned rewrite candidate.
- Any selected `ASK` creates the existing question rows; unanswered questions cost no editor step.
- Answered questions return to the existing bullet-owned editor brief with their audited missing
  clause and answer.
- The claim validator continues to block fabricated facts. Prompt quality changes do not loosen
  ownership, citation, or run-scoped evidence invariants.

## Experimental implementation

`project_question_planner.py` now implements this two-call contract in the eval path. The evidence
payload contains no job or fit data. The relevance validator accepts only weakness IDs returned by
the audit. Exact source quotes and missing clauses survive into coordinator candidates. Strong
entries skip the second call entirely.

## Evaluation gate

Do not replace production from mocked tests. Run the polished and vague Google snapshots with the
same experimental prompt. The saved Google contexts differ between the two original runs, so each
new arm must reuse its own frozen context for the before/after comparison. Require:

- close to the human 10 KEEP / 2 ASK action distribution;
- no algorithms/data-structures questions unless an evidence-only weakness explicitly seeks them;
- no generic role, technology, metric or challenge questions on strong bullets;
- recovery of missing implementation detail on vague bullets, especially PostgreSQL and worker
  recovery, without making every shortened bullet automatically ASK;
- a useful ownership question covering the ROS/point-cloud work without asking the same thing twice;
- no increase in unavailable reviews;
- recorded call count, latency and cost.

Then repeat on the other four real postings. A production cutover needs stable semantic improvement,
not merely valid JSON or more generated questions.

## Reviewer prompt correction after the vague-resume run

The first evidence-only audit protected concrete bullets but accepted four of five weakened
JobMatcha bullets. Ownership verbs and intended benefits were being mistaken for sufficient
implementation detail. The revised prompt separates ownership, implementation specificity, and
scope/verification as reading lenses, not required fields. A missing mechanism behind a claimed
improvement can warrant a question even when ownership is settled.

Contrast examples use exports, caching, monitoring, and booking APIs rather than reproducing the
test resume's answers. A stated purpose alone does not explain an implementation. Conversely,
concrete functionality can be sufficient without metrics, algorithm names, challenge stories, or
every internal design choice. Sibling evidence can settle a gap and must prevent redundant questions.
REWRITE must identify an actual wording problem expressible from existing facts.

This iteration changes only `EVIDENCE_PROMPT`. The relevance/question prompt, coordinator, schema,
and production reviewer stay fixed to isolate the experiment. Multiple independent missing clauses
remain supported. Automated schema tests cannot establish question quality: inspect paid outputs
and record Codex ratings explicitly as model judgments, not independent human ground truth.
