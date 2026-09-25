# Tailoring question benchmark — human review draft

This sheet turns the paid eval logs through 2026-09-24 into a proposed quality standard. It is
not a report card for one code version: the prompts, contracts, validators and coordinator changed
between runs. A repeated failure across versions is stronger evidence than a single output.

The earlier complete-flow fixture used synthetic companies, hand-written summaries and hand-written
requirement lists. Its runner inserted those derived fields directly and therefore never tested the
real job-description interpretation path. It remains useful as a regression suite, but it is not the
product-quality benchmark.

The product-quality benchmark now starts from:

- `real_resume.json`, transcribed from the resume the owner would submit;
- `real_jd.md`, containing five real postings: Google SWE/SRE, Palantir SWE, Aerotech software,
  Plaid SWE and TikTok Ads Interface;
- the production job-description interpreter, fit assessment, reviewer, coordinator, editor and
  validation path;
- human labels made separately for each resume/JD pair after reading the full posting.

The five jobs intentionally test different readings of the same resume. Google emphasizes general
CS breadth and scalable systems; Palantir emphasizes ownership, users and data products; Aerotech
emphasizes enterprise/database work, testing and motion-control applications; Plaid emphasizes
production ownership, APIs and distributed services; TikTok emphasizes product development,
distributed scheduling and AI-assisted work. A useful question for one posting is not automatically
useful for the other four.

The owner should review the proposed labels, especially rows marked **judgment needed**. After
that, stable examples can become held-out eval fixtures. Exact wording is not the target; the
missing fact and the resume change it enables are.

## Proposed standard

A question earns a place when its answer could add a specific, job-relevant fact to the target
bullet. That fact should be one of: the candidate's owned work, a material mechanism or decision,
the component's meaningful scope, or concrete support for a result the bullet already claims.

A question should be rejected when it asks the candidate to restate ownership already asserted,
asks for generic impact/challenges, asks which tools were used when the tools are named, imports a
premise from the job, or would produce substantially the same answer as another selected question.

Question count is an output, not a target. Generate alternatives broadly enough to expose distinct
facts, then show only the smallest useful set. A strong bullet may receive a question only when the
missing fact would materially change the resume bullet, not merely make a good interview story.

## Benchmark bullets

### JobMatcha — backend posting

| Bullet | Proposed action | Valuable missing fact | Acceptable question meaning | Reject |
|---|---|---|---|---|
| LLM platform with server-enforced grounding | KEEP | None required | **Judgment needed:** a narrow fact beyond the stated grounding mechanism only if it would materially change the bullet | Broad personal role/features; generic impact; challenges |
| FastAPI/Supabase/React/Mapbox calendar | KEEP | None required | At most a narrow unresolved architecture fact with clear resume value | What features were designed; seamless integration; performance impact; challenges |
| Worked on PostgreSQL backend with psycopg2/search/locking/indexes | ASK | Owned database functionality | What functionality, features, tasks or responsibilities did you personally implement or change? | Which technology; generic impact; testing imported from the JD |
| Improved background processing reliability and recovery | ASK | Concrete change or mechanism | What did you change or implement? A second failure-mode question is acceptable only if it would add a distinct fact | Generic impact, metrics, “how successful,” challenges |
| Reserve-before-spend idempotency with response replay | KEEP | None required | **Judgment needed:** testing evidence only if the resume needs it more than another bullet | What part did you implement; which technology; generic impact; challenges |
| Retail/customer-service work for backend role | KEEP | None worth pursuing for this role | None | Technical questions attached solely because the resume has backend gaps |

Observed across the logs:

- Strong LLM, FastAPI and idempotency bullets were repeatedly over-asked by combined
  decision/question prompts. Triage-only runs kept all three 3/3, but a later complete run again
  asked the LLM bullet for its role in grounding.
- PostgreSQL was consistently recognized as vague. Useful variants asked about functionality,
  tasks, responsibilities, features or improvements. Several eval failures were exact-wording
  failures rather than bad questions.
- Background processing initially received generic “what improvements?” questions, was then kept
  despite lacking a mechanism, and later produced useful mechanism/failure-mode alternatives plus
  occasional generic impact filler.
- The latest complete flow selected one PostgreSQL question and one background-processing
  mechanism question and produced answer-based edits. The PostgreSQL edit added vague outcome
  language; the worker edit was concrete but long.

### Panther Racing — robotics posting

| Bullet | Proposed action | Valuable missing fact | Acceptable question meaning | Reject |
|---|---|---|---|---|
| Contributing to ROS 2 stack focused on cone perception | ASK | Owned subsystem work; optionally the subsystem's role | What did you personally build/change? What role did cone perception play in the stack? | Broad ownership if the point-cloud sibling will receive the same answer |
| Working on VLP-16 filtering, clustering, validation and projection | ASK | Exact stages personally implemented | Which filtering/perception stages did you personally implement? | Tool confirmation; broad project contribution |
| C++ steering controller converting curvature to rack commands | KEEP | None required | A narrow validation fact only if materially valuable | Restating ownership; missing metric |
| LiDAR-camera calibration verified against labelled images | KEEP | None required | None required | Generic impact/challenges |
| Helped develop autonomous-vehicle software with C++/ROS 2 | ASK | Concrete component or behavior owned | What software component did you personally build/change? | Which tools were used |
| Merchandise sales for robotics role | KEEP | None worth pursuing | None | Technical gap questions |

Project-level selection proposed from the coordinator logs:

- Keep the point-cloud ownership question.
- Drop broad ROS-stack ownership if it would receive the same perception-pipeline answer.
- Keep the ROS system-role question because it changes a different fact.
- This curated three-candidate pool was selected correctly 3/3 after overlap normalization. Earlier
  coordinator versions collapsed all three or kept the wrong representative.

### Web/backend role

| Bullet | Proposed action | Valuable missing fact | Acceptable question meaning | Reject |
|---|---|---|---|---|
| Verbose REST/Flask account-authentication bullet | REWRITE | None | None | Asking for facts already stated |
| Helped improve checkout flow | ASK | Exact backend change; optional support for “improved” | Which part did you change? What did you observe, if that claim needs support? | Generic impact, revenue/conversion assumptions |
| Created backend functionality for users/messages/channels | ASK | Concrete functionality personally built | What did you build to manage users, messages and channels? | Invented API/endpoint terminology |
| Designed PostgreSQL schema with owner-scoped indexes | KEEP | None required | None | Restating ownership |
| Integration tests for lease expiry and killed-worker recovery | KEEP | None required | None | Generic testing/impact follow-up |
| Frontend styling for backend role | KEEP | None worth pursuing | None | Backend-gap questions |

The checkout question and answer-to-edit path worked repeatedly. The strongest logged edit was:
“Rewrote the address-validation step and its API endpoint in Python to improve the checkout flow
for the web store backend.” It is grounded in the supplied answer and retains the original scope.

### Fit-edge and sibling cases

| Bullet | Proposed action | Valuable missing fact | Acceptable question meaning | Reject |
|---|---|---|---|---|
| React/Flask encrypted messaging with browser-side cryptography | KEEP | None required | None | Did you use front-end frameworks/data structures; broad ownership |
| Celery workers with bounded export retry | KEEP | None required | None | What reports, unless naming report scope materially helps this JD |
| Worked on Python data pipeline | ASK | Owned pipeline component | What component did you build/change? | Which technologies; scale fishing without an existing scale claim |
| Vague payments backend beside concrete refund-service sibling | ASK | Work distinct from refunds | Which part besides the refund service did you build/change? | Asking for the sibling's already stated work |
| “I don't remember” answer | KEEP after answer | None available | None | Re-asking or inventing |

## Curated multi-question pools

These cases test the original many-question idea directly: generation may expose several facts,
and coordination must retain distinct value rather than merely one grammatical template.

### Reporting migration

Target: migrated a reporting service to a new database and updated dashboards.

- **Select:** personal migration contribution.
- **Select:** dashboard changes; this is a separate claimed workstream.
- **Optional / judgment needed:** database product. It adds a job-relevant technology and does not
  assume the answer, but should not displace the two workstream questions.
- **Reject:** generic performance/reliability impact without a claimed result.

The final coordinator selected the first three and rejected generic impact 3/3. Earlier reviewer
runs exposed only one contribution question, demonstrating why a useful candidate pool matters.

### Checkout and payments

Target: helped improve checkout and payments flow.

- **Select:** concrete personal change.
- **Select when the improvement claim remains:** what was observed that showed improvement.
- **Reject:** role in the “overall architecture,” which the bullet never claims.

The final coordinator made this selection 3/3. Earlier reviewer runs returned only the contribution
template, so the useful result-validation alternative was unavailable downstream.

## Answer-to-edit expectations

| Target | Supplied answer facts | Expected edit behavior | Logged risk |
|---|---|---|---|
| PostgreSQL | storage/queries with psycopg2; resume/job/tailoring data; normalized skill lookups; full-text search; composite indexes; claim/lease fields | Replace “worked on” with owned database work; retain PostgreSQL and psycopg2; use only supplied scope | “Enhancing backend database functionality” added vague result language; technology-preservation repair fired in earlier runs |
| Background worker | separate worker service; job claiming; lease expiry; retry/recovery; idempotency; no duplicate LLM work | State the architecture change and main reliability mechanism concisely | Long list-like edits; duplicated reliability language |
| Checkout | address-validation step and API endpoint in Python | Name the exact change and retain checkout/backend scope | Generally successful |
| ROS stack | point-cloud processing, ground removal, clustering, validation, centroiding, projection/classification | Clarify owned perception work without making the project-wide and point-cloud bullets duplicates | Earlier editor kept both bullets despite detailed answers |
| Point cloud | ground removal, clustering, candidate filtering, centroid estimation | Replace “working on” with the stages implemented | Earlier editor kept it despite a detailed answer |
| Pipeline | daily ingestion job, validation, PostgreSQL load, rejected-row replay | State the owned job and flow; retain Python/PostgreSQL | One logged edit succeeded; another awkwardly said “worked on … that built” |

## What the history establishes

1. The system can ask useful ownership/mechanism questions and turn detailed answers into grounded
   edits. PostgreSQL, background work, checkout and pipeline demonstrate this.
2. More generated candidates can expose distinct valuable facts. Reporting migration and
   checkout/payments demonstrate this when the candidate pool is curated.
3. More candidates also produce generic impact, challenges, tool restatements and redundant
   ownership. Real complete runs demonstrate this repeatedly.
4. The coordinator can choose a good set from a good pool, but it has not reliably repaired a
   noisy real pool. Isolated coordinator success therefore does not prove end-to-end quality.
5. Exact keyword assertions have mislabeled acceptable questions as failures. Future evals should
   score the missing fact and expected resume change, with wording checks limited to clear banned
   premises.
6. KEEP/ASK is still unstable for some strong bullets, especially the LLM platform. A hard KEEP
   gate can hide useful questions; an incorrect ASK creates filler. The benchmark must judge the
   value of candidate facts as well as the initial action.
7. Transport/contract failures (missing decisions, extra review entries, malformed JSON) are real
   reliability defects but separate from semantic question quality.

## Proposed acceptance measures

Evaluate each configuration on the same bullets and repeat each stochastic case three times.

- **Useful-fact recall:** proportion of benchmark valuable facts represented by at least one
  generated candidate.
- **Shown precision:** proportion of selected questions labelled useful.
- **Strong-bullet burden:** selected questions on bullets with no required missing fact.
- **Distinctness:** selected questions whose answers would add different facts.
- **Answer utilization:** useful supplied answers that appear faithfully in an edit.
- **Edit gain:** edits judged materially clearer or more relevant than the original.
- **Grounding failures:** unsupported technologies, quantities, ownership or results shown.
- **Reliability:** unavailable reviews, malformed contracts and incomplete runs.

A release should require zero grounding failures and zero incomplete runs. Thresholds for useful
fact recall, shown precision and strong-bullet burden should be set only after the owner reviews
the proposed labels above.

## Evidence included

This draft incorporates all paid outputs pasted in the working session: the early 14-case
tailoring eval; the 27/30-case reviewer work; complete-resume suites for JobMatcha, robotics,
web/backend and fit-edge resumes; isolated V2 reviewer experiments; multi-gap and independent-
workstream pool experiments; ROS overlap, migration-padding and result-validation coordinator
experiments; the deployed JobMatcha question/edit run; and the later complete JobMatcha runs that
exposed missing decisions, noisy pools, triage behavior, extra review entries and the latest
answer-to-edit results.
