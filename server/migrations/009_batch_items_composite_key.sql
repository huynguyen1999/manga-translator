-- Change batch_items primary key to (batch_id, id) so item IDs are scoped to each batch.
ALTER TABLE batch_items DROP CONSTRAINT IF EXISTS batch_items_pkey;
DROP INDEX IF EXISTS batch_items_batch_id_id_uq;
ALTER TABLE batch_items ADD CONSTRAINT batch_items_pkey PRIMARY KEY (batch_id, id);
