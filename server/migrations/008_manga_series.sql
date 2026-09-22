CREATE TABLE IF NOT EXISTS manga_series (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS manga_series_title_ci_uq
    ON manga_series (lower(btrim(title)));

ALTER TABLE manga_groups
    ADD COLUMN IF NOT EXISTS series_id TEXT REFERENCES manga_series(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS series_position INTEGER;

ALTER TABLE manga_groups
    DROP CONSTRAINT IF EXISTS manga_groups_series_position_check;

ALTER TABLE manga_groups
    ADD CONSTRAINT manga_groups_series_position_check
    CHECK (
        (series_id IS NULL AND series_position IS NULL)
        OR (series_id IS NOT NULL AND series_position IS NOT NULL AND series_position > 0)
    );

CREATE UNIQUE INDEX IF NOT EXISTS manga_groups_series_position_uq
    ON manga_groups (series_id, series_position)
    WHERE series_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS manga_groups_series_idx
    ON manga_groups (series_id, series_position)
    WHERE series_id IS NOT NULL;
