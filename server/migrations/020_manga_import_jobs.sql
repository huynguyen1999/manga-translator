CREATE TABLE IF NOT EXISTS manga_import_jobs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
    client_upload_id TEXT,
    accepted_order BIGSERIAL NOT NULL UNIQUE,
    accepted_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS manga_import_jobs_fifo_idx
    ON manga_import_jobs (accepted_at, id)
    WHERE status = 'queued';

CREATE UNIQUE INDEX IF NOT EXISTS manga_import_jobs_single_active_idx
    ON manga_import_jobs ((status))
    WHERE status = 'processing';

CREATE UNIQUE INDEX IF NOT EXISTS manga_import_jobs_client_upload_idx
    ON manga_import_jobs (client_upload_id)
    WHERE client_upload_id IS NOT NULL;
