BEGIN;

ALTER TABLE jobs DROP COLUMN IF EXISTS applied_at;

CREATE INDEX IF NOT EXISTS idempotency_requests_user_created_idx
  ON idempotency_requests (user_id, created_at);

COMMIT;
