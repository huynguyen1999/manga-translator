CREATE TABLE IF NOT EXISTS schema_migrations (
    id TEXT PRIMARY KEY,
    version TEXT NOT NULL UNIQUE,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS manga_groups (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pages (
    id TEXT PRIMARY KEY,
    folder TEXT NOT NULL UNIQUE,
    manga_title TEXT NOT NULL DEFAULT 'Ungrouped',
    original_name TEXT NOT NULL,
    original_sort_key TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'translated',
    finished_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id TEXT,
    input_name TEXT,
    has_inpainted BOOLEAN NOT NULL DEFAULT FALSE,
    has_regions BOOLEAN NOT NULL DEFAULT FALSE,
    has_thumbnail BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    text_regions JSONB NOT NULL DEFAULT '[]'::jsonb,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pages_manga_sort_idx
    ON pages (manga_title, original_sort_key, folder)
    WHERE active;
CREATE INDEX IF NOT EXISTS pages_manga_finished_idx
    ON pages (manga_title, finished_at DESC, folder)
    WHERE active;
CREATE INDEX IF NOT EXISTS pages_request_id_idx
    ON pages (request_id)
    WHERE active AND request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS page_artifacts (
    id TEXT PRIMARY KEY,
    folder TEXT NOT NULL REFERENCES pages(folder) ON DELETE CASCADE,
    name TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    sha256 TEXT,
    present BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (folder, name)
);

CREATE TABLE IF NOT EXISTS manga_summaries (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL UNIQUE REFERENCES manga_groups(title) ON DELETE CASCADE,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    manga_title TEXT NOT NULL,
    status TEXT NOT NULL,
    dismissed BOOLEAN NOT NULL DEFAULT FALSE,
    added_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    total_items INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    manifest JSONB NOT NULL,
    snapshot_path TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS batches_status_idx ON batches (status, added_at, id) WHERE active;

CREATE TABLE IF NOT EXISTS batch_items (
    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    manga_title TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT,
    error TEXT,
    request_id TEXT,
    result_folder TEXT,
    payload JSONB NOT NULL,
    UNIQUE (batch_id, id)
);

CREATE INDEX IF NOT EXISTS batch_items_request_id_idx ON batch_items (request_id);

CREATE TABLE IF NOT EXISTS reading_progress (
    id TEXT PRIMARY KEY,
    installation_id TEXT NOT NULL,
    manga_title TEXT NOT NULL,
    page_id TEXT,
    page_number INTEGER,
    scroll_top INTEGER NOT NULL DEFAULT 0,
    complete BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (installation_id, manga_title)
);

CREATE TABLE IF NOT EXISTS migration_records (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_key TEXT NOT NULL,
    status TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_type, source_key)
);
