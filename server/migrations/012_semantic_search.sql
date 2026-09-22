CREATE TABLE IF NOT EXISTS search_jobs (
    id TEXT PRIMARY KEY,
    group_ids JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS search_one_active_job
    ON search_jobs ((true)) WHERE status IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS search_job_items (
    job_id TEXT NOT NULL REFERENCES search_jobs(id) ON DELETE CASCADE,
    source_key TEXT NOT NULL,
    group_id TEXT NOT NULL,
    page_id TEXT,
    modality TEXT NOT NULL CHECK (modality IN ('summary', 'image')),
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, source_key)
);
CREATE INDEX IF NOT EXISTS search_job_items_pending ON search_job_items(job_id, status);

-- Retain orphan records until Qdrant deletion succeeds; do not cascade these.
CREATE TABLE IF NOT EXISTS search_sources (
    source_key TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    page_id TEXT,
    modality TEXT NOT NULL CHECK (modality IN ('summary', 'image')),
    fingerprint TEXT NOT NULL,
    profile TEXT NOT NULL,
    point_ids JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS search_sources_group ON search_sources(group_id);
