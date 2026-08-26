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
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
-- one resume per user — this is what `ON CONFLICT (user_id)` in upsert_resume needs
CREATE UNIQUE INDEX resumes_user_id_key ON resumes (user_id);

CREATE TABLE jobs (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  raw_description   text NOT NULL,
  title             text,
  summary           text,
  no_bs_translation text,
  skills            jsonb NOT NULL DEFAULT '[]'::jsonb,
  company_name      text,
  location          text,
  work_type         work_type,
  match_score       numeric,
  match_detail      jsonb,
  status            status_type NOT NULL DEFAULT 'saved',
  notes             text,
  deadline          date,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
-- matches the list query: WHERE user_id = %s ORDER BY created_at DESC
CREATE INDEX jobs_user_created_idx ON jobs (user_id, created_at DESC);

CREATE TABLE idempotency_requests (
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  idempotency_key text NOT NULL,
  request_hash    text NOT NULL,
  job_id          uuid REFERENCES jobs(id) ON DELETE CASCADE,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, idempotency_key)
);
CREATE INDEX idempotency_requests_user_created_idx
  ON idempotency_requests (user_id, created_at);
