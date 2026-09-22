-- Normalize domain relationships around stable record IDs.
-- The migration keeps pages.folder and pipeline_runs.folder as filesystem aliases only.

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id TEXT PRIMARY KEY,
    folder TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE pages ADD COLUMN IF NOT EXISTS manga_group_id TEXT;
ALTER TABLE page_artifacts ADD COLUMN IF NOT EXISTS page_id TEXT;
ALTER TABLE result_documents ADD COLUMN IF NOT EXISTS page_id TEXT;
ALTER TABLE result_documents ADD COLUMN IF NOT EXISTS pipeline_run_id TEXT;
ALTER TABLE manga_summaries ADD COLUMN IF NOT EXISTS group_id TEXT;
ALTER TABLE batch_items ADD COLUMN IF NOT EXISTS manga_group_id TEXT;
ALTER TABLE batch_items ADD COLUMN IF NOT EXISTS page_id TEXT;
ALTER TABLE reading_progress ADD COLUMN IF NOT EXISTS group_id TEXT;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='pages' AND column_name='manga_title'
    ) THEN
        INSERT INTO manga_groups(id, title)
        SELECT gen_random_uuid()::text, COALESCE(NULLIF(btrim(manga_title), ''), 'Ungrouped')
        FROM pages
        GROUP BY COALESCE(NULLIF(btrim(manga_title), ''), 'Ungrouped')
        ON CONFLICT(title) DO NOTHING;

        UPDATE pages p
        SET manga_group_id = g.id
        FROM manga_groups g
        WHERE g.title = COALESCE(NULLIF(btrim(p.manga_title), ''), 'Ungrouped')
          AND p.manga_group_id IS NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='batch_items' AND column_name='manga_title'
    ) THEN
        INSERT INTO manga_groups(id, title)
        SELECT gen_random_uuid()::text, COALESCE(NULLIF(btrim(manga_title), ''), 'Ungrouped')
        FROM batch_items
        GROUP BY COALESCE(NULLIF(btrim(manga_title), ''), 'Ungrouped')
        ON CONFLICT(title) DO NOTHING;

        UPDATE batch_items bi
        SET manga_group_id = g.id
        FROM manga_groups g
        WHERE g.title = COALESCE(NULLIF(btrim(bi.manga_title), ''), 'Ungrouped')
          AND bi.manga_group_id IS NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='manga_summaries' AND column_name='title'
    ) THEN
        INSERT INTO manga_groups(id, title)
        SELECT gen_random_uuid()::text, COALESCE(NULLIF(btrim(title), ''), 'Ungrouped')
        FROM manga_summaries
        GROUP BY COALESCE(NULLIF(btrim(title), ''), 'Ungrouped')
        ON CONFLICT(title) DO NOTHING;

        UPDATE manga_summaries ms
        SET group_id = g.id
        FROM manga_groups g
        WHERE g.title = COALESCE(NULLIF(btrim(ms.title), ''), 'Ungrouped')
          AND ms.group_id IS NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='reading_progress' AND column_name='manga_title'
    ) THEN
        INSERT INTO manga_groups(id, title)
        SELECT gen_random_uuid()::text, COALESCE(NULLIF(btrim(manga_title), ''), 'Ungrouped')
        FROM reading_progress
        GROUP BY COALESCE(NULLIF(btrim(manga_title), ''), 'Ungrouped')
        ON CONFLICT(title) DO NOTHING;

        UPDATE reading_progress rp
        SET group_id = g.id
        FROM manga_groups g
        WHERE g.title = COALESCE(NULLIF(btrim(rp.manga_title), ''), 'Ungrouped')
          AND rp.group_id IS NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='page_artifacts' AND column_name='folder'
    ) THEN
        UPDATE page_artifacts pa
        SET page_id = p.id
        FROM pages p
        WHERE p.folder = pa.folder
          AND pa.page_id IS NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='result_documents' AND column_name='folder'
    ) THEN
        INSERT INTO pipeline_runs(id, folder)
        SELECT gen_random_uuid()::text, rd.folder
        FROM result_documents rd
        LEFT JOIN pages p ON p.folder = rd.folder
        WHERE p.id IS NULL
        GROUP BY rd.folder
        ON CONFLICT(folder) DO NOTHING;

        UPDATE result_documents rd
        SET page_id = p.id
        FROM pages p
        WHERE p.folder = rd.folder
          AND rd.page_id IS NULL;

        UPDATE result_documents rd
        SET pipeline_run_id = pr.id
        FROM pipeline_runs pr
        WHERE pr.folder = rd.folder
          AND rd.page_id IS NULL
          AND rd.pipeline_run_id IS NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='reading_progress' AND column_name='page_id'
    ) THEN
        UPDATE reading_progress rp
        SET page_id = p.id
        FROM pages p
        WHERE rp.page_id IS NOT NULL
          AND (p.id = rp.page_id OR p.folder = rp.page_id);

        UPDATE reading_progress rp
        SET page_id = NULL
        WHERE rp.page_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM pages p WHERE p.id = rp.page_id);
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='batch_items' AND column_name='result_folder'
    ) THEN
        UPDATE batch_items bi
        SET page_id = p.id
        FROM pages p
        WHERE bi.result_folder IS NOT NULL
          AND (p.id = bi.result_folder OR p.folder = bi.result_folder)
          AND bi.page_id IS NULL;
    END IF;
END $$;

DROP INDEX IF EXISTS pages_manga_sort_idx;
DROP INDEX IF EXISTS pages_manga_finished_idx;
DROP INDEX IF EXISTS pages_active_manga_sort_idx;
DROP INDEX IF EXISTS page_artifacts_folder_name_uq;
DROP INDEX IF EXISTS manga_summaries_title_uq;
DROP INDEX IF EXISTS reading_progress_installation_title_uq;
DROP INDEX IF EXISTS result_documents_name_idx;

ALTER TABLE page_artifacts DROP CONSTRAINT IF EXISTS page_artifacts_folder_fkey;
ALTER TABLE manga_summaries DROP CONSTRAINT IF EXISTS manga_summaries_title_fkey;
ALTER TABLE pages DROP CONSTRAINT IF EXISTS pages_manga_group_id_fkey;
ALTER TABLE page_artifacts DROP CONSTRAINT IF EXISTS page_artifacts_page_id_fkey;
ALTER TABLE result_documents DROP CONSTRAINT IF EXISTS result_documents_page_id_fkey;
ALTER TABLE result_documents DROP CONSTRAINT IF EXISTS result_documents_pipeline_run_id_fkey;
ALTER TABLE result_documents DROP CONSTRAINT IF EXISTS result_documents_owner_check;
ALTER TABLE manga_summaries DROP CONSTRAINT IF EXISTS manga_summaries_group_id_fkey;
ALTER TABLE batch_items DROP CONSTRAINT IF EXISTS batch_items_manga_group_id_fkey;
ALTER TABLE batch_items DROP CONSTRAINT IF EXISTS batch_items_page_id_fkey;
ALTER TABLE reading_progress DROP CONSTRAINT IF EXISTS reading_progress_group_id_fkey;
ALTER TABLE reading_progress DROP CONSTRAINT IF EXISTS reading_progress_page_id_fkey;

ALTER TABLE result_documents DROP CONSTRAINT IF EXISTS result_documents_pkey;

ALTER TABLE pages
    ALTER COLUMN manga_group_id SET NOT NULL;
ALTER TABLE pages
    ADD CONSTRAINT pages_manga_group_id_fkey
    FOREIGN KEY (manga_group_id) REFERENCES manga_groups(id) ON DELETE CASCADE;
ALTER TABLE page_artifacts
    ALTER COLUMN page_id SET NOT NULL;
ALTER TABLE page_artifacts
    ADD CONSTRAINT page_artifacts_page_id_fkey
    FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE;
ALTER TABLE result_documents
    ADD CONSTRAINT result_documents_page_id_fkey
    FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE;
ALTER TABLE result_documents
    ADD CONSTRAINT result_documents_pipeline_run_id_fkey
    FOREIGN KEY (pipeline_run_id) REFERENCES pipeline_runs(id) ON DELETE CASCADE;
ALTER TABLE result_documents
    ADD CONSTRAINT result_documents_owner_check
    CHECK ((page_id IS NOT NULL) <> (pipeline_run_id IS NOT NULL));
ALTER TABLE manga_summaries
    ALTER COLUMN group_id SET NOT NULL;
ALTER TABLE manga_summaries
    ADD CONSTRAINT manga_summaries_group_id_fkey
    FOREIGN KEY (group_id) REFERENCES manga_groups(id) ON DELETE CASCADE;
ALTER TABLE batch_items
    ADD CONSTRAINT batch_items_manga_group_id_fkey
    FOREIGN KEY (manga_group_id) REFERENCES manga_groups(id) ON DELETE SET NULL;
ALTER TABLE batch_items
    ADD CONSTRAINT batch_items_page_id_fkey
    FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE SET NULL;
ALTER TABLE reading_progress
    ALTER COLUMN group_id SET NOT NULL;
ALTER TABLE reading_progress
    ADD CONSTRAINT reading_progress_group_id_fkey
    FOREIGN KEY (group_id) REFERENCES manga_groups(id) ON DELETE CASCADE;
ALTER TABLE reading_progress
    ADD CONSTRAINT reading_progress_page_id_fkey
    FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE SET NULL;

CREATE UNIQUE INDEX IF NOT EXISTS page_artifacts_page_name_uq
    ON page_artifacts(page_id, name);
CREATE UNIQUE INDEX IF NOT EXISTS manga_summaries_group_uq
    ON manga_summaries(group_id);
CREATE UNIQUE INDEX IF NOT EXISTS reading_progress_installation_group_uq
    ON reading_progress(installation_id, group_id);
CREATE UNIQUE INDEX IF NOT EXISTS result_documents_page_name_uq
    ON result_documents(page_id, name)
    WHERE page_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS result_documents_pipeline_name_uq
    ON result_documents(pipeline_run_id, name)
    WHERE pipeline_run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS result_documents_pipeline_name_idx
    ON result_documents(name, pipeline_run_id)
    WHERE pipeline_run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS pages_group_sort_idx
    ON pages(manga_group_id, original_sort_key, folder)
    WHERE active;
CREATE INDEX IF NOT EXISTS pages_group_finished_idx
    ON pages(manga_group_id, finished_at DESC, folder)
    WHERE active;

ALTER TABLE pages DROP COLUMN IF EXISTS manga_title;
ALTER TABLE page_artifacts DROP COLUMN IF EXISTS folder;
ALTER TABLE result_documents DROP COLUMN IF EXISTS folder;
ALTER TABLE manga_summaries DROP COLUMN IF EXISTS title;
ALTER TABLE batch_items DROP COLUMN IF EXISTS manga_title;
ALTER TABLE batch_items DROP COLUMN IF EXISTS result_folder;
ALTER TABLE reading_progress DROP COLUMN IF EXISTS manga_title;
ALTER TABLE batches DROP COLUMN IF EXISTS manga_title;
