ALTER TABLE public.resumes
ADD COLUMN IF NOT EXISTS resume_text text,
ADD COLUMN IF NOT EXISTS storage_path text,
ADD COLUMN IF NOT EXISTS original_filename text,
ADD COLUMN IF NOT EXISTS file_mime_type text,
ADD COLUMN IF NOT EXISTS file_size_bytes bigint,
ADD COLUMN IF NOT EXISTS file_sha256 text,
ADD COLUMN IF NOT EXISTS file_uploaded_at timestamptz;

CREATE UNIQUE INDEX IF NOT EXISTS resumes_storage_path_key
ON public.resumes (storage_path)
WHERE storage_path IS NOT NULL;
