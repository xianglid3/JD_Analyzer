# Review of JobMatcha question-generation architecture

Date: 2026-09-24. Scope: current working tree, including uncommitted V2 changes; production runtime path, supplied Google traces, both saved human-label sets, relevant tests and schema. This is an architecture review, not an implementation change.

**Verdict: the current question pipeline is not a sound endpoint for the stated product goal.** Its grounding and orchestration infrastructure is worth retaining. Its central abstraction—classify each bullet ASK, generate questions to justify that classification, then filter them—is a poor fit for discovering the most valuable missing resume evidence. There are also concrete implementation defects. We should compare a repaired current contract against a joint evidence-discovery/question-planning stage before undertaking a production migration.

This review does not establish that a replacement architecture will produce good questions. It identifies why the current design fails, specifies a smaller alternative, and defines a fair test. No live model calls or production database operations were performed.

The supplied trace establishes the run's decisions and questions. It does not contain a complete prompt snapshot or source revision hash, so exact historical request bytes cannot be reconstructed from it alone. Runtime explanations below combine the current code with the observed trace; where individual API-call attribution is inferred, it is marked as such.

## 1. Current architecture

```text
Raw posting → job analysis → stored summary, skills and structured requirements
                                  ↓
Resume evidence → fit assessment / requirement plan
                                  ↓
All experience/project bullets, capped at 30 in resume order
                                  ↓
For each bullet, with siblings and fit context:
    LLM triage: KEEP / REWRITE / ASK + facts + five gap labels
        KEEP: stop
        REWRITE: instruction for editor
        ASK: separate LLM generates up to 3 question candidates
                                  ↓
Deterministic schema / vocabulary checks and lexical warnings
                                  ↓
Coordinator:
    automatic rejection / automatic preservation
    LLM cross-bullet overlap judgment
    server chooses overlap representative by priority
    LLM select/reject remaining questions; corrective retry if malformed
                                  ↓
Selected questions mapped back to original text
                                  ↓
Server files questions; existing per-bullet candidates wait for answers
                                  ↓
Answered candidate → editor → claim checks → user-reviewed proposal
```

Key code paths:

| Responsibility | Source |
|---|---|
| Load reduced job context | `services/tailoring_agent.py:2020`, `load_job_context` |
| Review the resume independently of fit membership | `services/tailoring_agent.py:1980`, `bullets_for_review` |
| User-owned bullets, same-entry siblings, current-run answers | `services/bullet_review.py:585`, `build_tasks` |
| Requirement context | `services/bullet_review_v2.py:520`, `fit_context` |
| Triage prompt construction | `services/bullet_review_v2.py:458`, `prompt_for` |
| Actual production triage and generation | `services/bullet_review_v2.py:1217`, `_review_one` |
| Generator input and call | `services/bullet_review_v2.py:882`, `_alternative_payload`; `:907`, `request_alternatives` |
| Candidate checks and merging | `services/bullet_review_v2.py:688`, `_hard_problem`; `:939`, `expand_review` |
| Coordinator input boundary | `services/tailoring_agent.py:1807`, `_coordinator_bullets`; `services/question_coordinator_v2.py:239`, `collect_candidates` |
| Coordinator execution | `services/question_coordinator_v2.py:649`, `request_selection` |
| Production wiring | `services/tailoring_agent.py:2222` onwards |
| Question persistence and waiting | `services/tailoring_agent.py:2844`, `file_planned_questions` |

The generator is a separate LLM call, despite living in the reviewer file. The coordinator does not generate or repair wording. `complete_json` forwards the supplied messages through the API wrapper; I found no hidden replacement prompt in that path.

The reviewer does **not** receive the full raw JD. `load_job_context` returns `(title, company, summary, skills)` as the job tuple. Reviewer/generator payloads call the flat skills list `requirements`; additional structured match context comes from the assessment. The coordinator receives that reduced job tuple but loses the structured fit context entirely.

### What the real run establishes

Latest run: `ff8cfa7b-cb0e-44ed-8745-322b3231a9d9`.

| Measure | Observed |
|---|---:|
| Human bullet labels | 10 KEEP, 2 ASK |
| Model decisions | 1 KEEP, 11 ASK |
| Bullet decisions matching labels | 3/12 |
| False ASK among human KEEP bullets | 9/10 |
| Surviving generated questions labelled | 20 |
| Labels on those questions | 7 duplicate/already answered, 9 low value, 2 unsupported premise, 2 useful but reword |
| Selected questions | 9 |
| Selected labels | 4 duplicate/already answered, 3 low value, 2 useful but reword |
| Selected questions useful as written | 0/9 under these labels |
| Coordinator omissions after retry | 5 |
| Reported tailoring calls | 26 |
| Reported tokens / latency | 71,557 / 92.9 seconds |

The 26 calls are consistent with 12 triages + 11 generators + overlap + selection + selection retry. This attribution is inferred from code and the log; the pasted output does not individually enumerate every API call. JD analysis is separate from the reported tailoring count. Summed provider latency is not necessarily wall-clock time because review calls run concurrently.

The prior labelled run matched 6/12 decisions but had three unavailable reviews and never reached coordination. The latest run fixed that observed transport symptom while question judgment remained poor. Two different runs do not isolate the causal effect of the prompt change.

The human labels are useful product judgments, **not independent gold truth**: the assistant recommended labels during the session. Some require reconsideration, particularly blanket treatment of architecture questions and the claim that automation is irrelevant to a Google posting that explicitly mentions automation.

## 2. What is working

- **Fit no longer controls which bullets are read.** Preserve this. The earlier requirement-owned pool hid vague bullets; a pure requirement-retrieval replacement must not recreate that failure.
- **Evidence provenance infrastructure exists.** Bullet IDs, user ownership, current-run answers, citations, and stored reviews provide a foundation for safe editing.
- **Explicit versus inferred fit is represented.** Keep the distinction, but do not confuse lexical support with quality or demonstrated depth.
- **A missing review is not silently labelled KEEP.** Unavailable is an honest state. Its current run-wide consequences are too broad, but the distinction is correct.
- **Selected text resolves from server-owned candidate records.** The coordinator cannot silently invent text or authorize an unknown candidate ID.
- **Question filing is orchestrated, and answers remain attached to their run and bullet.** This is useful state machinery. Changing the planner need not require replacing the worker or editor.
- **Multiple complementary questions are possible.** Preserve the ability to discover several facts; do not require a number of questions per bullet.
- **The interactive real-resume evaluation exposes actual failures.** It is much more informative than synthetic keyword assertions.

Verification performed for this review: 106 focused reviewer, coordinator and human-review tests passed. Additional in-memory probes below used mocked model calls. No full DB suite or paid semantic eval was run; passing these tests does not prove model judgment or database integration.

## 3. What is not working

### 3.1 Highest impact: ASK commits to work before the missing information is precise

**Current behavior.** Triage returns a decision, free-text reason, facts, strength assessment, and five `ask|settled|not_material` categories. The generator is told: “The first review already decided ASK.” It is then asked to create questions.

**Why it fails.** On the worker bullet, the reviewer claimed the mechanisms were missing, even though leases, fencing tokens, checkpoints and a recovery experiment were explicitly present. Generation converted that false claim into “What specific mechanisms or technologies…?” The coordinator selected it. No validation rejection caused this failure.

**Root cause.** A weak semantic contract plus a premature gate. `contribution=ask` is not an exact uncertainty. The generator receives the original evidence, so this is not simply missing input; it is being anchored on an already-approved gap and asked to explain it. It cannot return a revised KEEP through this path. If it correctly generates no worthwhile question, `_review_one` replaces the review with unavailable, and the orchestrator fails the whole V2 run before coordination.

There is also a concrete prompt-construction defect: `prompt_for(triage_only=True)` cuts `REVIEW_PROMPT` at `QUESTION CANDIDATES`. The definitions of the five gap categories occur after that cut. Triage therefore receives the category names without their full definitions. Its prefix still says it proposes questions, despite the later triage-only instruction.

**Proposed change.** Make an evidence opportunity—not ASK—the planning unit. Generate the exact uncertainty, source evidence, answer use and proposed question together. Allow no opportunity as a normal result. Derive ASK only after an opportunity has been selected. In a repaired-current baseline, at minimum pass explicit uncertainty objects, preserve category definitions, and allow the generator to report that the claimed gap is already settled.

**Why this is better.** The planner must demonstrate a concrete question and a distinct resume benefit in the same decision. A generic ownership category is insufficient to trigger user work.

**Cost / tradeoff.** Joint planning may again bias discovery toward asking. The empty result must remain legitimate, and the comparison must measure over-asking. This is a hypothesis to test, not a guaranteed benefit from combining calls.

### 3.2 High impact: per-bullet planning splits a project into artificial gaps

**Current behavior.** Every bullet gets an independent review and generator. Same-entry sibling text is included, but opportunities and questions are owned by a bullet from the beginning. Siblings have no evidence IDs in the model payload. The coordinator sees only candidates from ASK bullets, with sibling text repeated.

**Why it fails.** The ROS overview produces an algorithms question; the adjacent point-cloud bullet already lists techniques and has an ownership uncertainty about the same work. Another ROS question assumes personal integration ownership. Those are not solved by producing more variants of each isolated question.

**Root cause.** The planning scope and storage scope have been conflated. A bullet is a useful edit target, but a project is often the right unit for understanding an accomplishment and its missing facts. Same-project facts do not automatically prove the same ownership; that relationship needs semantic interpretation.

**Proposed change.** Plan opportunities over an entire project/experience entry, with an indexed view of the rest of the resume. Keep source bullet IDs and choose a primary target only once the missing fact is understood. Rank distinct opportunities across entries. Begin with one target bullet for each selected question to retain existing answer/edit safety.

**Why this is better.** It can distinguish “which perception stages did you implement?” from “what did perception supply to the larger stack?” before producing competing broad ownership questions.

**Cost / tradeoff.** Entry size varies; the five-bullet JobMatcha entry is substantially larger than one bullet. Earlier multi-bullet experiments showed quality degradation, so grouping must be measured, not assumed safe. Output size should depend on useful opportunities, not a mandatory review object for every bullet. Split large entries by workstream with shared context if measured limits require it.

### 3.3 High impact: the JD is reduced to keywords, then barred from guiding legitimate discovery

**Current behavior.** The job tuple omits raw JD text. Some structured fit records include importance, but they reduce requirements to labels; a candidate may reference only requirements already explicitly or inferentially associated with its bullet. The coordinator receives `requirement_reference` without the map needed to interpret that ID. It receives neither the review's established facts nor its strength assessment or fit states.

**Why it fails.** ChatRoom was criticized for missing data structures and algorithms rather than evaluated for its security evidence. The original Google text says “data structures **or** algorithms,” whereas this run's printed extraction lists both separately as required. That is an upstream extraction discrepancy visible in the raw fixture and trace, not evidence that each bullet should explain both. The data flow makes keyword pressure easy and nuanced prioritization difficult.

The opposite failure is also built into the policy: “fit may only rank a weakness found without the JD” is too restrictive. A strong general bullet may omit scale, operating constraints or decisions that are unusually valuable for a particular role. Such an opportunity need not mean that the original bullet is bad.

**Root cause.** Fit score, writing quality and missing role-relevant evidence are different dimensions, but the pipeline tries to express them through one bullet-strength decision. Keyword matching is being used as a substitute for a source-backed role brief.

**Proposed change.** Assemble a role brief with source excerpts, responsibilities, required/preferred importance, and preserved alternative/minimum conditions. Retain matching as an index, not an eligibility gate. Give the planner all project evidence for a short resume. It should ask whether an unknown fact would materially improve demonstrated role fit, without treating unknown as absent or treating a JD term as a candidate fact.

**Why this is better.** It can ask an open question about a relevant mechanism without demanding a keyword everywhere or missing vague unmatched work. The coordinator can compare opportunities against actual job priorities.

**Cost / tradeoff.** More context and source mapping; extraction errors still need checks. Reuse stored requirements where accurate and supply selected source excerpts. Do not introduce another mandatory LLM just to summarize an existing summary.

### 3.4 High impact: downstream filtering is asked to rescue an ungrounded gap claim

**Current behavior.** The coordinator deduplicates, chooses and rejects question strings. It cannot revise them. `_coordinator_bullets` drops the triage findings and requirement context; `collect_candidates` carries the generator's self-authored missing fact, benefit and priority. Questions on KEEP entries never enter global comparison except as sibling context for another candidate in that entry.

**Why it fails.** It selected role questions for “Built…” bullets, selected a generic FastAPI challenges question, and rejected a duplicate functionality question as the same gap as that challenge story. Five omissions after retry became `not_selected`; those omissions must not be credited as semantic filtering success.

Two selected questions were labelled useful-but-reword. A selector restricted to original strings cannot turn the ROS integration premise into a neutral system-role question.

**Root cause.** The downstream stage repeats the same utility judgment using weaker context. The generator supplies generic justifications such as “demonstrates problem-solving”; no exact answer destination is required.

**Proposed change.** Rank structured opportunities after joint evidence discovery. Provide source facts and actual requirement context. Prefer a selector that chooses the smallest complementary set. If wording repair is supported, tie it to the same immutable uncertainty and source IDs, record the revision, and validate it; do not give the selector an unrestricted mandate to invent new gaps.

**Why this is better.** Deduplication compares missing information and likely answer use, rather than category labels and question wording. A repair cannot silently change the task.

**Cost / tradeoff.** Semantic ranking remains fallible. Rewording adds another place to introduce assumptions, so initially make the joint planner produce acceptable text and add coordinator repair only if measured residual failures justify it.

### 3.5 Important: heuristics have become correctness rules in both directions

**Current behavior.** The coordinator has automatic ownership rejection and automatic result-question preservation. The reviewer blocks named technologies not in the target/answers. Different recruiter-doubt categories are treated as unable to represent the same gap when interpreting rejection reasons.

**Why it fails: reproduced without model calls.**

| Probe | Actual current behavior | Problem |
|---|---|---|
| Actual “What specific role did you play…” on the detailed grounding bullet | Automatic rejection returns `None` | The regex recognizes some “which parts/features” questions, not this paraphrase. The LLM may still reject it, but did not in the trace. |
| “Built an ordering platform with a three-person team: Python services, PostgreSQL storage, and a React interface.” → “Which parts did you personally implement…?” | Automatically rejected as `low_value` | Shared ownership remains a legitimate uncertainty despite the leading verb and punctuation. |
| A recovery bullet already explains its kill-and-resume test, with the same observation in its answer → “What did you observe that showed recovery worked…?” | Automatically preserved and selected without a model call | Priority + result vocabulary overrides already-known facts. Protected questions bypass overlap and usefulness checks. |
| “For background processing, did you use Redis, PostgreSQL, or another mechanism, if any?” on a vague processing bullet | Hard rejection for unsupported technology names | This is a conditional inquiry, not an assertion of tool use. Its utility still requires judgment. |
| “How did you scale background processing to one million users?” on the same bullet | `_hard_problem` returns `None` | Unsupported scope is not reliably detected by vocabulary checks. This only demonstrates that this check allows it, not that a later coordinator necessarily would. |
| Triage marks `result_validation=not_material`; generator returns a result question | `expand_review` accepts it and changes that lens to `ask` | The generator can override triage's materiality judgment without an explicit reconsideration decision. |

**Root cause.** Lexical heuristics stand in for semantic certainty. More ownership verbs, regex templates or hard blocks would fix examples while creating new false negatives. I retract the earlier recommendation that deterministic prechecks alone are the next answer.

**Proposed change.** Keep schema, identity, ownership, current-run scope and exact-repeat checks deterministic. Treat usefulness, semantic answer coverage, collaborative ownership, and presupposition as semantic judgments. Explicitly permit a truthful negative answer and distinguish suggested possibilities from assertions. Unmentioned tool names alone should not make a question invalid; adding an unconfirmed tool to the resume must still be forbidden.

**Why this is better.** It allows discovery without weakening claim validation. Evidence for asking and evidence for asserting a resume claim have different requirements.

**Cost / tradeoff.** Presupposition cannot be perfectly determined by code. Avoid forcing the model's opinions into fake certainty. Adversarial question tests and final human review remain necessary.

### 3.6 Important: evaluation measures contract compliance more reliably than usefulness

**Current behavior.** Many unit tests mock model outputs or assert prompt strings. Synthetic evals specify action/count/word expectations. Real labels now exist, but the display presents model decisions/reasons and coordinator selection before the human labels. Labels were discussed with the same assistant proposing changes.

**Why it fails.** A passing ownership-boundary fixture did not prevent nine false ASK decisions here. The previous “bigger model is not the issue” conclusion was based on a small, narrow experiment, not a sufficient architecture/model comparison. The historical API request/response shape errors also obscured semantic results.

**Root cause.** Evaluation distribution mismatch, label anchoring, and insufficient immutable run records. `interactive_review` upserts bullet labels by `(job, bullet_key)` and questions by `(job, bullet_key, question_hash)`, excluding run ID. Repeated runs with the same job key can replace prior ratings; labels with changed wording accumulate. `--review-run` derives a display job name differently from `--only`, allowing separate names for the same posting. The printed counts aggregate by job, not the current run. There is no complete prompt/input/version archive in these label rows.

**Proposed change.** Store immutable run snapshots and annotations keyed by run/variant. Separate canonical acceptable fact targets from ratings of individual outputs. Use blind side-by-side ratings on real resume/JD pairs and controlled variants. Preserve raw rejected questions as well as survivors.

**Why this is better.** We can distinguish missed discovery, generation errors, validator false rejection, coordinator loss, and irrelevant questions instead of repeatedly tuning one case until it passes.

**Cost / tradeoff.** Better measurement requires owner time. Reduce it by reusing frozen inputs and showing only new outputs, not by asking the model to certify its own quality.

## 4. Root cause

The dominant problem is **stage responsibilities and data contracts**, reinforced by an architecture that treats isolated bullet weakness as the prerequisite for evidence discovery. Prompt contradictions and real implementation defects worsen it. Evaluation initially hid the mismatch.

This is not principally the final edit validator rejecting otherwise excellent Google questions. Bad questions were visible in the surviving generation pool and were selected. Nor does the trace exonerate question validation generally; the adversarial probes show both false passes and false rejections.

There is no exact uncertainty entity between triage and generation. The generator receives substantial original context, but the structured review supplies only broad categories and free prose. There is no explicit answer type, exact supporting-span map, disconfirming branch or requirement-to-benefit mapping. The coordinator then loses more of that context.

The following shortcuts would be mistakes:

- `Built/Implemented → KEEP` as a universal hard rule. A collaborative project or vague object can still warrant a question.
- `No missing keyword → no useful question`. Relevant evidence can be weak despite lexical coverage.
- `Missing keyword → ask its nearest bullet`. This recreates the original confirmation failure.
- `Generic challenges → always forbidden`. Usually poor as a broad prompt, but a specific missing technical constraint and response can be valuable.
- `10 KEEP / 2 ASK` as a permanent truth for every JD. It is a working judgment on this resume/job pair, not the product objective.

## 5. Recommended architecture

Use **joint evidence discovery and question planning**, grouped by project/experience when the resume is too large for one measured call. Keep role-aware prioritization global.

```text
Resume source records + current-run answers + source-backed role brief
                                ↓
Code assembles project/experience packets and a full-resume evidence index
                                ↓
One LLM planning call per packet:
  known evidence → precise uncertainty → why worthwhile → question + answer use
  returns zero or more evidence opportunities; no separate ASK gate
                                ↓
Code verifies source IDs, schema, ownership, run scope and exact repeats
                                ↓
One global semantic selection pass, only when comparison is needed:
  reject already-known facts; consolidate same-answer opportunities;
  choose the smallest complementary set with meaningful expected benefit
                                ↓
Existing server-owned questions / waiting / answer / editor path
```

For this fixture there are six entries, so an entry-based prototype targets six joint calls plus one selection call, excluding retries and JD analysis. It is not a claim of seven-call semantic success. A whole-resume call is an experimental alternative, not an assumed improvement; earlier output-pressure failures justify measuring it carefully.

### Opportunity contract

Each opportunity should carry:

```json
{
  "id": "server-assigned",
  "entry_id": "provided entry ID",
  "source_bullet_ids": ["provided source IDs"],
  "primary_target_bullet_id": "provided edit target",
  "job_requirement_ids": ["provided relevant requirement IDs"],
  "known_evidence": [
    {"bullet_id": "source ID", "quote": "exact source span"}
  ],
  "uncertainty": "Which listed perception stages the candidate personally changed",
  "why_existing_evidence_does_not_answer": "Working on lists stages without allocating ownership",
  "answer_type": "implemented stages plus concrete changes; none is valid",
  "expected_resume_change": "Replace broad participation with the confirmed owned stage and change",
  "question": "Which of the listed perception stages did you personally implement or change?",
  "negative_answer_action": "Keep the current contribution level; add no implementation claim",
  "priority": "high",
  "priority_reason": "Clarifies the candidate's C++ project contribution"
}
```

Do not force long prose or all five doubt categories for every bullet. Verify quotations mechanically; their sufficiency and interpretation remain semantic. A `known_evidence` list containing only true statements does not itself prove a worthwhile uncertainty.

### Requirement-first versus bullet-first

Pure requirement-first retrieval would address global gaps but risks restoring the original invisible-bullet failure. Pure bullet-first review misses global coverage and creates repeated questions. Use both: a source-backed role brief ranks opportunities, and complete entry coverage ensures vague unmatched work is examined.

For a requirement with no supporting experience, a future resume-level inquiry could ask whether any relevant example exists, with “none” acceptable. Do not attach that question to an arbitrary bullet. Existing storage requires a bullet target, so defer that capability until the experiment proves its value and a distinct question scope has an explicit migration. The first prototype improves anchored project questions without pretending it solves all absent-evidence discovery.

### Ownership of decisions

| Decision | Owner |
|---|---|
| User/run ownership, allowed IDs, schema, question count ceiling | Code/DB |
| Source quotes exist; answer is from this run | Code |
| Exact repeated question already asked | Code/DB |
| Candidate technology explicitly named | Code can detect presence; cannot infer materiality |
| Retrieval for large resumes | Ranking/retrieval with coverage audit; never an opaque eligibility cutoff |
| Semantic relevance, exact uncertainty, sufficient evidence | LLM with source context |
| Whether two differently worded questions seek the same fact | Semantic judgment; exact equality alone is deterministic |
| Question wording and optional examples | LLM; negative answers remain valid |
| Selection by marginal value after other selected questions | Global semantic planner, bounded by user workload preference |
| Writing confirmed answers as claims | Existing editor and claim/evidence checks |

The purpose is to improve what the resume can truthfully demonstrate, not interrogate the applicant to verify that they are honest or exhaustively test their technical competence.

## 6. Current vs proposed

| Dimension | Current | Proposed hypothesis |
|---|---|---|
| LLM calls | `N bullets + A ASK bullets + overlap + selector`, plus retries; 26 reported here | `G entry packets + optional global selector`; target 7 on this fixture, plus retries |
| Primary unit | Bullet action classification | Missing fact worth discovering |
| Generation | Asked to fill an already-approved gap category | Jointly justify uncertainty, question and answer use |
| Role context | Summary, flat skills, uneven match labels | Source-backed requirements/responsibilities with importance and alternatives |
| Scope | Independent bullets; later overlap repair | Related project evidence assessed together; global opportunity ranking |
| Failure modes | False ASK cascades; generic padding; useful empty generation fails run | Planner may still invent gaps or miss opportunities; larger inputs may reduce attention |
| Deterministic safeguards | Structural rules plus semantic regex shortcuts | Structural rules; semantic decisions tested as judgments |
| Testability | Mostly decision/count/word and schema tests | Source/uncertainty/answer-use contract plus blinded quality and actual edit benefit |
| Question quality | Measured poor on this real case | Unknown until controlled evaluation; explicit mechanism for improvement |
| Complexity | Four semantic responsibilities and duplicated rules | Joint planner + global selector; retain proven worker/editor machinery |

The separate generator has no demonstrated independent benefit on the current real baseline. A global comparison stage does have a distinct job: limited attention and shared evidence require choosing among opportunities. The separate overlap call may be removable, but its previous isolated successes mean this should be an ablation, not an aesthetic deletion.

## 7. Migration plan

1. **Freeze evidence first.** Archive both Google runs, full JD analysis, exact resume, raw/validated candidates, rejection reasons, prompts/model settings and labels. Fix run-keyed annotation storage in the eval, retaining current labels as historical records.
2. **Create an eval-only competing planner.** No schema/worker rewrite. It consumes the same frozen inputs and emits opportunity objects. Add an adapter to the existing coordinator candidate shape for comparison only; keep the richer opportunity record for inspection.
3. **Compare a repaired current pipeline fairly.** Repair missing triage definitions and pass exact uncertainty/source/answer-use objects to generation and selection. This is the minimal-contract arm. Do not add a new stack of ownership regexes.
4. **Compare joint planning and selection.** Use the same model initially. Test per-entry planning against the repaired per-bullet baseline. Separately measure whether the extra overlap call improves precision enough to justify its latency and failures. Only then test a stronger model on the same held-out inputs if needed; previous tiny experiments do not rule out a model effect.
5. **Run answer-to-edit checks on approved questions.** Use truthful specific, partial, negative and unknown answers. Preserve one primary target and current-run grounding; no automatic spreading of an answer across sibling edits.
6. **Integrate only the measured winner.** Run-pin the new contract and source snapshot, translate selected questions into existing candidates, preserve waiting/answer_ready states, idempotent filing and lease fencing. Update progress to match the chosen packet count. Run full integration tests, including database-backed tests, at this completed stage before production rollout.

Do not implement a new orchestration framework or migrate multi-bullet answer ownership to evaluate this idea. The most valuable next implementation is a controlled experiment with a better semantic contract.

## 8. Evaluation plan

### Inputs and controls

- Use the actual 12-bullet resume and all five supplied JDs. Freeze each source and analyzed role brief for paired comparisons. Measure live JD extraction separately so two architecture variants do not receive different jobs.
- Use Google for development, reserve at least two postings for held-out comparison, and rotate which postings are held out in later iterations. One resume alone cannot establish generalization; add other independently labelled resumes before broader rollout.
- Include deliberate weak/strong counterfactual pairs: “worked on processing” versus a concrete worker mechanism and validation; solo versus team ownership; stated versus missing technology; tested versus unsupported result; overlapping versus separate sibling work.
- Preserve open discovery. A strong bullet may legitimately yield a useful new question on another JD. Assess the missing fact, not whether a historical fixture demanded KEEP.

### Human review protocol

First show the JD context and project bullets without model explanations or a selected/rejected badge. Let the human identify worthwhile unknowns, including “none.” Then present randomized candidate questions with sources. Rate them before revealing the model's rationale. Record uncertainty and allow multiple labels; duplicate and low-value are not always mutually exclusive. Ask the owner to adjudicate disputed labels without treating this assistant's earlier recommendations as independent validation.

Suggested dimensions, each rated explicitly:

| Dimension | What the reader judges |
|---|---|
| Evidence-discovery value | Does the answer supply a new material fact? |
| JD relevance | Which actual responsibility/requirement benefits, and how much? |
| Specificity | Is the unknown clear and answerable without a broad interview story? |
| Redundancy | Is it answered already or likely to repeat another selected answer? |
| Answerability | Can the candidate give a short truthful answer, including none/unknown? |
| Premise validity | Does it assume unclaimed work, scale or ownership? |
| Expected resume improvement | What concrete clause or selection decision could change? |
| Observed resume improvement | After a real answer, is the resulting edit faithful and better? |

### Metrics and attribution

- Accepted-as-written precision among shown questions. Count useful-but-reword separately; it is not a finished question.
- Unique useful unknowns covered, against human-identified opportunities. Coverage prevents an empty output from winning.
- Already-known-information rate; cross-question redundancy rate; unsupported-premise rate.
- Useful generation lost at validation or selection, including omissions. `not_selected` after malformed output is not a correct semantic rejection.
- False ASK on judged-sufficient evidence as a diagnostic, not the ultimate product metric.
- Faithful improved edits per answered question, plus preservation on negative/unknown answers.
- Review failure rate, time to first questions, total wall-clock latency, calls/tokens/cost and question burden.

Use three paired attempts as an initial variability screen, not statistical proof. Compare the same frozen inputs and model across architectures. Require zero unsupported-premise questions in the small release set, no repeated requests for explicitly supplied facts, and coverage of the agreed worthwhile unknowns. Choose broader numerical acceptance thresholds with the owner before inspecting held-out results. Do not make the expected number of questions the target.

**Recommended next step:** an eval-only comparison of the minimal repaired contract versus joint project-level opportunity planning. The Google trace justifies that experiment; it does not justify another production rewrite on faith.
