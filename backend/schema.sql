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
                CHECK (status IN ('running', 'completed', 'failed', 'limit_reached')),
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
  completed_at  timestamptz
);
CREATE INDEX tailoring_runs_user_created_idx ON tailoring_runs (user_id, started_at DESC);
CREATE INDEX tailoring_runs_job_idx ON tailoring_runs (job_id, started_at DESC);

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
  status        text NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed', 'accepted', 'rejected')),
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX proposed_edits_run_idx ON proposed_edits (run_id, created_at);
-- one accepted rewrite per bullet per run: two would leave the renderer picking silently
CREATE UNIQUE INDEX proposed_edits_one_accepted_per_bullet
  ON proposed_edits (run_id, bullet_id)
  WHERE status = 'accepted' AND bullet_id IS NOT NULL;
CREATE INDEX proposed_edits_user_status_idx ON proposed_edits (user_id, status);

-- the citations. Each row names the tool call that surfaced the bullet, which is what makes
-- "found during this run" checkable.
CREATE TABLE evidence_links (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  edit_id      uuid NOT NULL REFERENCES proposed_edits(id) ON DELETE CASCADE,
  bullet_id    uuid NOT NULL REFERENCES resume_bullets(id) ON DELETE CASCADE,
  tool_call_id uuid NOT NULL REFERENCES tool_calls(id) ON DELETE CASCADE,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (edit_id, bullet_id)
);
CREATE INDEX evidence_links_bullet_idx ON evidence_links (bullet_id);

CREATE TABLE gaps (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id       uuid NOT NULL REFERENCES tailoring_runs(id) ON DELETE CASCADE,
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  requirement  text NOT NULL,
  note         text,
  searched     jsonb NOT NULL DEFAULT '[]'::jsonb,   -- what was tried before giving up
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX gaps_run_idx ON gaps (run_id, created_at);

-- every paid model call: what it cost, how long it took, who it was for
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
  outcome           text NOT NULL DEFAULT 'ok' CHECK (outcome IN ('ok', 'timeout', 'error')),
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llm_calls_user_created_idx ON llm_calls (user_id, created_at DESC);
CREATE INDEX llm_calls_kind_created_idx ON llm_calls (kind, created_at DESC);
