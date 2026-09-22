CREATE INDEX IF NOT EXISTS pages_active_original_sort_idx
    ON pages (original_sort_key, folder)
    WHERE active;

CREATE INDEX IF NOT EXISTS pages_active_finished_idx
    ON pages (finished_at DESC, folder DESC)
    WHERE active;

CREATE INDEX IF NOT EXISTS batches_active_added_idx
    ON batches (added_at, id)
    WHERE active;
