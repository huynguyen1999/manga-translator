CREATE TABLE IF NOT EXISTS page_stage_state (
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK (stage IN (
        'input', 'upscale', 'detection', 'ocr', 'bubble_detection',
        'text_grouping', 'translation', 'mask_generation', 'layout',
        'inpainting', 'rendering', 'finalize'
    )),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'queued', 'running', 'completed', 'failed',
        'blocked', 'interrupted', 'invalidated'
    )),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    input_fingerprint TEXT,
    settings_fingerprint TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_ms BIGINT CHECK (duration_ms IS NULL OR duration_ms >= 0),
    error_code TEXT,
    error_message TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (page_id, stage)
);

CREATE INDEX IF NOT EXISTS page_stage_state_ready_idx
    ON page_stage_state (stage, status, updated_at);

CREATE TABLE IF NOT EXISTS page_stage_attempts (
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK (stage IN (
        'input', 'upscale', 'detection', 'ocr', 'bubble_detection',
        'text_grouping', 'translation', 'mask_generation', 'layout',
        'inpainting', 'rendering', 'finalize'
    )),
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    status TEXT NOT NULL CHECK (status IN (
        'running', 'completed', 'failed', 'interrupted'
    )),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    duration_ms BIGINT CHECK (duration_ms IS NULL OR duration_ms >= 0),
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    error_message TEXT,
    PRIMARY KEY (page_id, stage, attempt)
);

CREATE TABLE IF NOT EXISTS pipeline_documents (
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK (stage IN (
        'input', 'upscale', 'detection', 'ocr', 'bubble_detection',
        'text_grouping', 'translation', 'mask_generation', 'layout',
        'inpainting', 'rendering', 'finalize'
    )),
    document_type TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version > 0),
    payload JSONB NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (page_id, stage, document_type, revision)
);

CREATE UNIQUE INDEX IF NOT EXISTS pipeline_documents_one_active_idx
    ON pipeline_documents (page_id, stage, document_type) WHERE active;

CREATE TABLE IF NOT EXISTS pipeline_artifacts (
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK (stage IN (
        'input', 'upscale', 'detection', 'ocr', 'bubble_detection',
        'text_grouping', 'translation', 'mask_generation', 'layout',
        'inpainting', 'rendering', 'finalize'
    )),
    artifact_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    mime_type TEXT,
    width INTEGER CHECK (width IS NULL OR width > 0),
    height INTEGER CHECK (height IS NULL OR height > 0),
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    checksum TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (page_id, stage, artifact_type, revision)
);

CREATE UNIQUE INDEX IF NOT EXISTS pipeline_artifacts_one_active_idx
    ON pipeline_artifacts (page_id, stage, artifact_type) WHERE active;
