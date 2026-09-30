-- Covering and partial indexes for gallery manga group listing performance.
CREATE INDEX IF NOT EXISTS pages_active_review_group_idx
    ON pages (manga_group_id, id, finished_at DESC)
    WHERE active AND (
        metadata->>'reviewStatus' = 'pending'
        OR text_regions @> '[{"review_required": true}]'::jsonb
    );

CREATE INDEX IF NOT EXISTS manga_summaries_active_group_idx
    ON manga_summaries (group_id)
    WHERE NULLIF(payload->>'summary', '') IS NOT NULL;

CREATE INDEX IF NOT EXISTS pages_active_translated_group_idx
    ON pages (manga_group_id)
    WHERE active AND source_type = 'translated';

CREATE INDEX IF NOT EXISTS pages_active_group_order_folder_idx
    ON pages (manga_group_id, page_order, folder)
    WHERE active;

CREATE INDEX IF NOT EXISTS pages_active_group_finished_only_idx
    ON pages (manga_group_id, finished_at DESC)
    WHERE active;
