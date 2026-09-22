ALTER TABLE pages
    ADD COLUMN IF NOT EXISTS asset_version BIGINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS pages_active_manga_sort_idx
    ON pages (manga_title, original_sort_key, folder)
    WHERE active;
