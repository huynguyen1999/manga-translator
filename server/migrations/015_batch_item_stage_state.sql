ALTER TABLE batch_items
    ADD COLUMN IF NOT EXISTS stage_started_at BIGINT;

CREATE INDEX IF NOT EXISTS batch_items_batch_status_stage_idx
    ON batch_items (batch_id, status, stage);
