CREATE TABLE IF NOT EXISTS public.job_analysis_drafts (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  raw_description   text NOT NULL,
  source_url        text,
  title             text,
  summary           text,
  no_bs_translation text,
  skills            jsonb NOT NULL DEFAULT '[]'::jsonb,
  company_name      text,
  location          text,
  work_type         work_type,
  confirmed_job_id  uuid REFERENCES public.jobs(id) ON DELETE SET NULL,
  expires_at        timestamptz NOT NULL DEFAULT (now() + interval '30 minutes'),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS job_analysis_drafts_user_idx
ON public.job_analysis_drafts (user_id);

CREATE INDEX IF NOT EXISTS job_analysis_drafts_expires_idx
ON public.job_analysis_drafts (expires_at);

ALTER TABLE public.idempotency_requests
ADD COLUMN IF NOT EXISTS draft_id uuid
REFERENCES public.job_analysis_drafts(id) ON DELETE SET NULL;
