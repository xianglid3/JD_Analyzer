-- schema.sql — source of truth for the JD Analyzer database.
-- Run against a fresh Postgres to recreate the full schema (used to build the test DB).
--   psql "$DATABASE_URL" -f schema.sql

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid() (built-in on PG13+, safe no-op)

-- ── Enum types ───────────────────────────────────────────────────────────────
CREATE TYPE status_type AS ENUM (
  'saved', 'applied', 'ghosted', 'rejected', 'interview', 'offer', 'accepted', 'decline'
);

CREATE TYPE work_type AS ENUM ('remote', 'hybrid', 'in_person');

-- ── Tables (in FK dependency order) ──────────────────────────────────────────
CREATE TABLE users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username      text NOT NULL UNIQUE,
  password_hash text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE refresh_tokens (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash text NOT NULL,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
-- every POST /auth/refresh looks a token up by hash — without this it's a full scan
CREATE INDEX refresh_tokens_token_hash_idx ON refresh_tokens (token_hash);
CREATE INDEX refresh_tokens_expires_at_idx ON refresh_tokens (expires_at);

CREATE TABLE resumes (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  education       jsonb NOT NULL DEFAULT '[]'::jsonb,
  work_experience jsonb NOT NULL DEFAULT '[]'::jsonb,
  projects        jsonb NOT NULL DEFAULT '[]'::jsonb,
  skills          jsonb NOT NULL DEFAULT '[]'::jsonb,
  certificates    jsonb NOT NULL DEFAULT '[]'::jsonb,
  resume_text       text,
  storage_path      text,
  original_filename text,
  file_mime_type    text,
  file_size_bytes   bigint,
  file_sha256       text,
  file_uploaded_at  timestamptz,
  -- hash of the resume text the structured evidence was extracted from. When it stops
  -- matching resume_text, the evidence describes a resume the user no longer has.
  evidence_source_hash text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
-- one resume per user — this is what `ON CONFLICT (user_id)` in upsert_resume needs
CREATE UNIQUE INDEX resumes_user_id_key ON resumes (user_id);
CREATE UNIQUE INDEX resumes_storage_path_key
  ON resumes (storage_path)
  WHERE storage_path IS NOT NULL;

CREATE TABLE jobs (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  raw_description   text NOT NULL,
  source_url        text,
  title             text,
  summary           text,
  no_bs_translation text,
  skills            jsonb NOT NULL DEFAULT '[]'::jsonb,
  -- [{skill, importance}] — same skills, plus how the posting framed them. Kept beside
  -- `skills` so the frozen eval still measures the field it labels.
  requirements      jsonb NOT NULL DEFAULT '[]'::jsonb,
  company_name      text,
  location          text,
  work_type         work_type,
  match_score       numeric CHECK (match_score BETWEEN 0 AND 100),
  match_detail      jsonb,
  status            status_type NOT NULL DEFAULT 'saved',
  notes             text,
  deadline          date,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
-- matches the list query: WHERE user_id = %s ORDER BY created_at DESC
CREATE INDEX jobs_user_created_idx ON jobs (user_id, created_at DESC);

CREATE TABLE job_analysis_drafts (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  raw_description   text NOT NULL,
  source_url        text,
  title             text,
  summary           text,
  no_bs_translation text,
  skills            jsonb NOT NULL DEFAULT '[]'::jsonb,
  requirements      jsonb NOT NULL DEFAULT '[]'::jsonb,
  company_name      text,
  location          text,
  work_type         work_type,
  confirmed_job_id  uuid REFERENCES jobs(id) ON DELETE SET NULL,
  expires_at        timestamptz NOT NULL DEFAULT (now() + interval '30 minutes'),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX job_analysis_drafts_user_idx ON job_analysis_drafts (user_id);
CREATE INDEX job_analysis_drafts_expires_idx ON job_analysis_drafts (expires_at);

CREATE TABLE idempotency_requests (
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  idempotency_key text NOT NULL,
  request_hash    text NOT NULL,
  job_id          uuid REFERENCES jobs(id) ON DELETE CASCADE,
  draft_id        uuid REFERENCES job_analysis_drafts(id) ON DELETE SET NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, idempotency_key)
);
CREATE INDEX idempotency_requests_user_created_idx
  ON idempotency_requests (user_id, created_at);

-- ── Structured resume evidence ───────────────────────────────────────────────
-- Every tailoring claim points at a resume_bullets row, so bullet ids have to survive
-- re-extraction. See services/resume_evidence.py.
-- 'skill' matters: a resume's skills line is evidence a requirement can match and cite
CREATE TYPE resume_entry_kind AS ENUM ('experience', 'project', 'education', 'certificate', 'skill');

-- contact details, one row per user
CREATE TABLE resume_headers (
  user_id    uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  full_name  text,
  email      text,
  phone      text,
  location   text,
  links      jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE resume_entries (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind         resume_entry_kind NOT NULL,
  organization text,                       -- company / school / project owner
  title        text,                       -- role / degree / project name
  location     text,
  start_date   text,                       -- free text: resumes say "Jun 2024", not a date
  end_date     text,
  sort_order   smallint NOT NULL DEFAULT 0,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX resume_entries_user_kind_idx ON resume_entries (user_id, kind, sort_order);

CREATE TABLE resume_bullets (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entry_id     uuid NOT NULL REFERENCES resume_entries(id) ON DELETE CASCADE,
  -- denormalized: every tool filters on it, and a check needing a join is one you forget
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  text         text NOT NULL,
  content_hash text NOT NULL,              -- sha256 of normalized text; the re-extraction match key
  sort_order   smallint NOT NULL DEFAULT 0,
  -- generated, so it can't drift from text
  search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX resume_bullets_search_idx ON resume_bullets USING GIN (search_vector);
-- one row per distinct bullet per user, so re-extraction can reuse the id
CREATE UNIQUE INDEX resume_bullets_user_content_key ON resume_bullets (user_id, content_hash);
CREATE INDEX resume_bullets_entry_idx ON resume_bullets (entry_id, sort_order);

-- ── Tailoring agent ──────────────────────────────────────────────────────────
-- One pass over one job is the unit of work, so everything hangs off the run and a pass
-- can be reconstructed whole.
CREATE TABLE tailoring_runs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  job_id        uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  status        text NOT NULL DEFAULT 'running'
                -- 'incomplete': the model stopped while candidates were still unhandled.
                -- Distinct from 'completed' on purpose — conflating them is what hid a
                -- run that did a quarter of its work (AE-02).
                CHECK (status IN ('running', 'waiting_for_user', 'completed', 'incomplete',
                                  'failed', 'limit_reached')),
  model         text NOT NULL,
  max_steps     smallint NOT NULL CHECK (max_steps BETWEEN 1 AND 20),
  steps_used    smallint NOT NULL DEFAULT 0 CHECK (steps_used BETWEEN 0 AND 20),
  input_tokens  integer NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
  output_tokens integer NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
  error_code    text,
  started_at    timestamptz NOT NULL DEFAULT now(),
  -- touched each step. A worker that dies stops updating this, which is how a stranded run
  -- is told apart from one another worker is still driving.
  heartbeat_at  timestamptz NOT NULL DEFAULT now(),
  -- how many requirements the fit engine found no evidence for, counted once when the
  -- run is created. The gaps themselves are recomputed on read from the job and the
  -- resume, so only the number is worth keeping.
  gap_count     smallint NOT NULL DEFAULT 0 CHECK (gap_count >= 0),
  completed_at  timestamptz,
  -- ── worker ownership ──
  -- A run is work in a queue, not a thread in the web process. A worker claims it, and the
  -- claim is fenced: `claim_token` changes on every claim, and every write the worker makes
  -- carries the token it was given. A worker whose lease expired therefore cannot write over
  -- the one that took the run from it.
  claimed_by       text,
  claim_token      uuid,
  lease_expires_at timestamptz,
  -- claims that produced no durable progress. Reset to 0 whenever a step is checkpointed, so
  -- a run that legitimately pauses for several user questions is never parked for it.
  claim_count      smallint NOT NULL DEFAULT 0 CHECK (claim_count >= 0)
);
CREATE INDEX tailoring_runs_user_created_idx ON tailoring_runs (user_id, started_at DESC);
CREATE INDEX tailoring_runs_job_idx ON tailoring_runs (job_id, started_at DESC);
-- what a worker looks for: claimable work, oldest first
CREATE INDEX tailoring_runs_claimable_idx ON tailoring_runs (status, lease_expires_at);
-- One active run per job. 'incomplete' is deliberately outside the predicate: it is
-- resumable but not running, and including it would stop a user ever starting a fresh run
-- for that job without first resuming an old one.
CREATE UNIQUE INDEX tailoring_runs_one_active_per_job
  ON tailoring_runs (user_id, job_id)
  WHERE status IN ('running', 'waiting_for_user');

CREATE TABLE tool_calls (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id        uuid NOT NULL REFERENCES tailoring_runs(id) ON DELETE CASCADE,
  step_number   smallint NOT NULL CHECK (step_number BETWEEN 1 AND 20),
  call_id       text NOT NULL,               -- the provider's id for this call
  tool_name     text NOT NULL,
  arguments     jsonb NOT NULL DEFAULT '{}'::jsonb,
  result        jsonb,
  status        text NOT NULL CHECK (status IN ('completed', 'failed')),
  error_message text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, call_id)
);
CREATE INDEX tool_calls_run_step_idx ON tool_calls (run_id, step_number);

CREATE TABLE proposed_edits (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id        uuid NOT NULL REFERENCES tailoring_runs(id) ON DELETE CASCADE,
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  -- SET NULL not CASCADE: if the bullet is pruned the proposal becomes history, not a hole
  bullet_id     uuid REFERENCES resume_bullets(id) ON DELETE SET NULL,
  requirement   text NOT NULL,
  proposed_text text NOT NULL,
  edit_type     text NOT NULL DEFAULT 'rewrite'
                CHECK (edit_type IN ('rewrite', 'merge')),
  -- How many bullets this edit cited when it was made. Compared at export against how many
  -- of those still exist AND still say what they said: any deletion or reword drops the
  -- count and the edit is refused. Without it a deleted citation would simply vanish from
  -- the join and the edit would look fully supported.
  cited_count   smallint NOT NULL DEFAULT 0 CHECK (cited_count >= 0),
  status        text NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed', 'accepted', 'rejected')),
  -- why this rewrite is an improvement, in the model's own words. Stored because a proposal
  -- the user cannot interrogate is one they have to take on trust, and trust is the thing this
  -- system is trying not to ask for.
  reason        text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX proposed_edits_run_idx ON proposed_edits (run_id, created_at);
-- one accepted rewrite per bullet per run: two would leave the renderer picking silently
CREATE UNIQUE INDEX proposed_edits_one_accepted_per_bullet
  ON proposed_edits (run_id, bullet_id)
  WHERE status = 'accepted' AND bullet_id IS NOT NULL;
CREATE INDEX proposed_edits_user_status_idx ON proposed_edits (user_id, status);

-- Extra source bullets consumed by a merge. `proposed_edits.bullet_id` is the primary
-- position where the combined bullet renders; these rows are removed only in that export.
CREATE TABLE tailoring_edit_bullets (
  edit_id     uuid NOT NULL REFERENCES proposed_edits(id) ON DELETE CASCADE,
  bullet_id   uuid NOT NULL REFERENCES resume_bullets(id) ON DELETE CASCADE,
  sort_order  smallint NOT NULL CHECK (sort_order >= 0),
  PRIMARY KEY (edit_id, bullet_id),
  UNIQUE (edit_id, sort_order)
);
CREATE INDEX tailoring_edit_bullets_bullet_idx ON tailoring_edit_bullets (bullet_id);

-- the citations. Each row names the tool call that surfaced the bullet, which is what makes
-- "found during this run" checkable.
CREATE TABLE evidence_links (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  edit_id      uuid NOT NULL REFERENCES proposed_edits(id) ON DELETE CASCADE,
  bullet_id    uuid NOT NULL REFERENCES resume_bullets(id) ON DELETE CASCADE,
  tool_call_id uuid NOT NULL REFERENCES tool_calls(id) ON DELETE CASCADE,
  -- What the cited bullet said at the moment it was cited.
  --
  -- Bullet ids deliberately survive a reword, which is what keeps citations stable. It also
  -- meant an accepted rewrite could later be applied to a bullet whose text had since been
  -- replaced, putting an unsupported claim into an export. The snapshot is what makes that
  -- detectable: the export re-checks it and refuses edits whose evidence has moved.
  bullet_text  text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (edit_id, bullet_id)
);
CREATE INDEX evidence_links_bullet_idx ON evidence_links (bullet_id);

-- A missing metric or scope detail is a question, never permission to invent a claim.
-- Answering a question resumes the same run; dismissing it tells the model to move on.
CREATE TABLE tailoring_detail_requests (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id       uuid NOT NULL REFERENCES tailoring_runs(id) ON DELETE CASCADE,
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  bullet_id    uuid REFERENCES resume_bullets(id) ON DELETE SET NULL,
  requirement  text NOT NULL,
  question     text NOT NULL,
  answer       text,
  status       text NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'answered', 'dismissed')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  resolved_at  timestamptz,
  UNIQUE (run_id, bullet_id, question)
);
CREATE INDEX tailoring_detail_requests_run_idx
  ON tailoring_detail_requests (run_id, created_at);
CREATE INDEX tailoring_detail_requests_user_status_idx
  ON tailoring_detail_requests (user_id, status);

-- Keeps the provenance of user-supplied details visible on each proposal that used them.
CREATE TABLE tailoring_edit_details (
  edit_id           uuid NOT NULL REFERENCES proposed_edits(id) ON DELETE CASCADE,
  detail_request_id uuid NOT NULL REFERENCES tailoring_detail_requests(id) ON DELETE CASCADE,
  PRIMARY KEY (edit_id, detail_request_id)
);

-- every paid model call: what it cost, how long it took, who it was for
-- The daily spend ceiling, as a row that can be locked.
--
-- `llm_calls` is the log of what was spent; it cannot enforce a limit, because two requests
-- can read the same SUM() and both decide there is room. This table is the enforcement
-- point: reserving takes a row lock, and the reservation is only granted when
-- spent + reserved + this call's ceiling still fits under the cap.
CREATE TABLE llm_daily_budgets (
  user_id      uuid          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  budget_date  date          NOT NULL,
  -- money promised to calls that are in flight, released when they finish
  reserved_usd numeric(10,6) NOT NULL DEFAULT 0 CHECK (reserved_usd >= 0),
  -- money actually spent, including calls that failed after the tokens were sent
  spent_usd    numeric(10,6) NOT NULL DEFAULT 0 CHECK (spent_usd >= 0),
  updated_at   timestamptz   NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, budget_date)
);

-- The system-wide ceiling. Per-user caps bound one account; accounts are free, so they do
-- not bound the bill. This is the row that does.
CREATE TABLE llm_global_budget (
  budget_date  date          PRIMARY KEY,
  reserved_usd numeric(10,6) NOT NULL DEFAULT 0 CHECK (reserved_usd >= 0),
  spent_usd    numeric(10,6) NOT NULL DEFAULT 0 CHECK (spent_usd >= 0),
  updated_at   timestamptz   NOT NULL DEFAULT now()
);

CREATE TABLE llm_calls (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind              text NOT NULL,
  run_id            uuid REFERENCES tailoring_runs(id) ON DELETE SET NULL,
  model             text NOT NULL,
  prompt_tokens     integer NOT NULL DEFAULT 0 CHECK (prompt_tokens >= 0),
  completion_tokens integer NOT NULL DEFAULT 0 CHECK (completion_tokens >= 0),
  cost_usd          numeric(10,6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
  latency_ms        integer NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
  -- 'reserved' is a call that has been paid for but has not come back yet. 'unknown' is one
  -- whose tokens were sent but whose usage never arrived — charged at its ceiling rather
  -- than at zero, because pretending it was free is how a budget gets overrun.
  outcome           text NOT NULL DEFAULT 'ok'
                    CHECK (outcome IN ('reserved', 'ok', 'timeout', 'error', 'unknown')),
  reserved_usd      numeric(10,6) NOT NULL DEFAULT 0 CHECK (reserved_usd >= 0),
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llm_calls_stale_reservation_idx ON llm_calls (outcome, created_at)
  WHERE outcome = 'reserved';
CREATE INDEX llm_calls_user_created_idx ON llm_calls (user_id, created_at DESC);
CREATE INDEX llm_calls_kind_created_idx ON llm_calls (kind, created_at DESC);

-- Skill implication learned from the model, one row per edge.
--
-- `skill_graph.IMPLIES` is the hand-written seed and still ships in code; this is everything
-- asked about since. The cache IS the graph: a term is resolved once and then costs nothing,
-- which is what keeps scoring reproducible while still growing past what anyone typed by hand.
CREATE TABLE skill_relations (
  specific    text        NOT NULL,
  general     text        NOT NULL,
  -- The model's OPINION that naming `general` in a bullet is fair given `specific`. It is
  -- not authority: a learned edge grants nobody permission to write anything until that
  -- user approves it in `skill_rewrite_approvals`. One wrong model answer used to become
  -- every user's claim permission, silently and globally (AE-03). Seeded rows are the
  -- exception — those are hand-written and reviewed, and live in code.
  rewriteable boolean     NOT NULL DEFAULT false,
  source      text        NOT NULL DEFAULT 'model'
                          CHECK (source IN ('seed', 'model', 'user')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (specific, general),
  CHECK (specific <> general)
);
CREATE INDEX skill_relations_general_idx ON skill_relations (general);

-- Terms already looked up, including ones that turned out to have no relations at all.
-- Without this, an unrelated term would be re-asked on every scoring pass forever.
CREATE TABLE skill_relation_lookups (
  term       text        PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now()
);


-- Who has agreed that a learned relation may be written into THEIR resume.
--
-- Scoring relations stay global and shared: guessing wrong nudges a number. Writing a word
-- into someone's resume is a claim they have to stand behind, so it is theirs to allow. This
-- keeps a bad edge contained to whoever accepted it instead of poisoning everyone.
CREATE TABLE skill_rewrite_approvals (
  user_id    uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  specific   text        NOT NULL,
  general    text        NOT NULL,
  approved   boolean     NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, specific, general)
);

-- One row per piece of writing work the planner assigned to a run.
--
-- The model used to be handed a list of candidates and trusted to work through it. Any
-- plain-text reply ended the run, and the backend recorded "completed" — so a run that
-- handled one of four candidates and stopped was indistinguishable from one that genuinely
-- had nothing to do (AE-02). This makes the assignment explicit state the orchestrator owns:
-- it hands out one candidate at a time and decides when the run is actually finished.
CREATE TABLE tailoring_candidates (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id        uuid NOT NULL REFERENCES tailoring_runs(id) ON DELETE CASCADE,
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  position      smallint NOT NULL,                -- the planner's order
  requirement   text NOT NULL,
  normalized    text NOT NULL,                    -- what the tool boundary matches on
  -- every action `agent_candidates` can hand out; widening the planner without widening
  -- this is a 500 at run creation, which is exactly how it was found
  action        text NOT NULL
                CHECK (action IN ('rewrite', 'strengthen', 'confirm', 'show_in_bullet')),
  status        text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'active', 'handled', 'skipped', 'needs_review')),
  -- why it ended where it did, for the run summary
  outcome       text,
  attempts      smallint NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  created_at    timestamptz NOT NULL DEFAULT now(),
  resolved_at   timestamptz,
  UNIQUE (run_id, position)
);
CREATE INDEX tailoring_candidates_run_status_idx ON tailoring_candidates (run_id, status);

-- "I used this on that project" — the user correcting or completing our reading of them.
--
-- Two problems, one cause. A gap we got wrong had no way to be fixed: the fit engine reads the
-- resume, and if the resume never says "algorithms" there was nothing the user could do but
-- rewrite their PDF. And a skill listed only in a keyword block ("claimed, but not shown")
-- had nowhere to go, because no bullet demonstrated it and nothing could authorise saying so.
--
-- An affirmation here is first-party evidence, exactly like the resume itself — the resume was
-- only ever a proxy for what the user says about their own work. It does two things: it scores,
-- and it authorises a rewrite of a bullet IN THAT ENTRY to name the skill. Not other entries:
-- using Python on one project says nothing about another.
CREATE TABLE resume_entry_skills (
  entry_id   uuid        NOT NULL REFERENCES resume_entries(id) ON DELETE CASCADE,
  user_id    uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  skill      text        NOT NULL,          -- as the user saw it
  normalized text        NOT NULL,          -- what matching and the claim check compare on
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (entry_id, normalized)
);
CREATE INDEX resume_entry_skills_user_idx ON resume_entry_skills (user_id, normalized);
