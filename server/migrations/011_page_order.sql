ALTER TABLE pages ADD COLUMN IF NOT EXISTS page_order INTEGER;
ALTER TABLE batch_items ADD COLUMN IF NOT EXISTS page_order INTEGER;

WITH ranked AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY manga_group_id
            ORDER BY active DESC, original_sort_key, folder
        )::INTEGER AS page_order
    FROM pages
)
UPDATE pages AS p
SET page_order = ranked.page_order
FROM ranked
WHERE p.id = ranked.id
  AND p.page_order IS NULL;

ALTER TABLE pages ALTER COLUMN page_order SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS pages_group_page_order_uq
    ON pages (manga_group_id, page_order)
    WHERE active;

CREATE INDEX IF NOT EXISTS pages_group_page_order_idx
    ON pages (manga_group_id, page_order)
    WHERE active;
