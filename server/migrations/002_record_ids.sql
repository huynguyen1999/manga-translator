CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE manga_summaries DROP CONSTRAINT IF EXISTS manga_summaries_title_fkey;
ALTER TABLE page_artifacts DROP CONSTRAINT IF EXISTS page_artifacts_folder_fkey;

ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS id TEXT;
UPDATE schema_migrations SET id = gen_random_uuid()::text WHERE id IS NULL;
ALTER TABLE schema_migrations ALTER COLUMN id SET NOT NULL;
ALTER TABLE schema_migrations DROP CONSTRAINT IF EXISTS schema_migrations_pkey;
ALTER TABLE schema_migrations ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (id);
CREATE UNIQUE INDEX IF NOT EXISTS schema_migrations_version_uq ON schema_migrations(version);

ALTER TABLE manga_groups ADD COLUMN IF NOT EXISTS id TEXT;
UPDATE manga_groups SET id = gen_random_uuid()::text WHERE id IS NULL;
ALTER TABLE manga_groups ALTER COLUMN id SET NOT NULL;
ALTER TABLE manga_groups DROP CONSTRAINT IF EXISTS manga_groups_pkey;
ALTER TABLE manga_groups ADD CONSTRAINT manga_groups_pkey PRIMARY KEY (id);
CREATE UNIQUE INDEX IF NOT EXISTS manga_groups_title_uq ON manga_groups(title);

ALTER TABLE pages ADD COLUMN IF NOT EXISTS id TEXT;
UPDATE pages SET id = gen_random_uuid()::text WHERE id IS NULL;
ALTER TABLE pages ALTER COLUMN id SET NOT NULL;
ALTER TABLE pages DROP CONSTRAINT IF EXISTS pages_pkey;
ALTER TABLE pages ADD CONSTRAINT pages_pkey PRIMARY KEY (id);
CREATE UNIQUE INDEX IF NOT EXISTS pages_folder_uq ON pages(folder);

ALTER TABLE page_artifacts ADD COLUMN IF NOT EXISTS id TEXT;
UPDATE page_artifacts SET id = gen_random_uuid()::text WHERE id IS NULL;
ALTER TABLE page_artifacts ALTER COLUMN id SET NOT NULL;
ALTER TABLE page_artifacts DROP CONSTRAINT IF EXISTS page_artifacts_pkey;
ALTER TABLE page_artifacts ADD CONSTRAINT page_artifacts_pkey PRIMARY KEY (id);
CREATE UNIQUE INDEX IF NOT EXISTS page_artifacts_folder_name_uq ON page_artifacts(folder, name);

ALTER TABLE manga_summaries ADD COLUMN IF NOT EXISTS id TEXT;
UPDATE manga_summaries SET id = gen_random_uuid()::text WHERE id IS NULL;
ALTER TABLE manga_summaries ALTER COLUMN id SET NOT NULL;
ALTER TABLE manga_summaries DROP CONSTRAINT IF EXISTS manga_summaries_pkey;
ALTER TABLE manga_summaries ADD CONSTRAINT manga_summaries_pkey PRIMARY KEY (id);
CREATE UNIQUE INDEX IF NOT EXISTS manga_summaries_title_uq ON manga_summaries(title);

ALTER TABLE batch_items DROP CONSTRAINT IF EXISTS batch_items_pkey;
ALTER TABLE batch_items ADD CONSTRAINT batch_items_pkey PRIMARY KEY (id);
CREATE UNIQUE INDEX IF NOT EXISTS batch_items_batch_id_id_uq ON batch_items(batch_id, id);

ALTER TABLE reading_progress ADD COLUMN IF NOT EXISTS id TEXT;
UPDATE reading_progress SET id = gen_random_uuid()::text WHERE id IS NULL;
ALTER TABLE reading_progress ALTER COLUMN id SET NOT NULL;
ALTER TABLE reading_progress DROP CONSTRAINT IF EXISTS reading_progress_pkey;
ALTER TABLE reading_progress ADD CONSTRAINT reading_progress_pkey PRIMARY KEY (id);
CREATE UNIQUE INDEX IF NOT EXISTS reading_progress_installation_title_uq
    ON reading_progress(installation_id, manga_title);

ALTER TABLE migration_records ADD COLUMN IF NOT EXISTS id TEXT;
UPDATE migration_records SET id = gen_random_uuid()::text WHERE id IS NULL;
ALTER TABLE migration_records ALTER COLUMN id SET NOT NULL;
ALTER TABLE migration_records DROP CONSTRAINT IF EXISTS migration_records_pkey;
ALTER TABLE migration_records ADD CONSTRAINT migration_records_pkey PRIMARY KEY (id);
CREATE UNIQUE INDEX IF NOT EXISTS migration_records_source_uq
    ON migration_records(source_type, source_key);

ALTER TABLE page_artifacts
    ADD CONSTRAINT page_artifacts_folder_fkey
    FOREIGN KEY (folder) REFERENCES pages(folder) ON DELETE CASCADE;
ALTER TABLE manga_summaries
    ADD CONSTRAINT manga_summaries_title_fkey
    FOREIGN KEY (title) REFERENCES manga_groups(title) ON DELETE CASCADE;
