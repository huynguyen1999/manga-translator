CREATE TABLE IF NOT EXISTS translation_presets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS translation_presets_name_ci_uq
    ON translation_presets (lower(btrim(name)));

CREATE INDEX IF NOT EXISTS translation_presets_is_default_idx
    ON translation_presets (is_default)
    WHERE is_default;
