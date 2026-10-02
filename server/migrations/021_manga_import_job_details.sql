ALTER TABLE manga_import_jobs
    ADD COLUMN IF NOT EXISTS result_items JSONB NOT NULL DEFAULT '[]'::jsonb;

UPDATE manga_import_jobs
SET result_items = payload->'items', payload = payload - 'items'
WHERE payload ? 'items';

DROP INDEX IF EXISTS manga_import_jobs_fifo_idx;
CREATE INDEX manga_import_jobs_fifo_idx
    ON manga_import_jobs (accepted_order)
    WHERE status = 'queued';
