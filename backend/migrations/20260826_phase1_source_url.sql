ALTER TABLE public.jobs
ADD COLUMN IF NOT EXISTS source_url text;
