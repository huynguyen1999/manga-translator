CREATE TABLE IF NOT EXISTS result_documents (
    folder TEXT NOT NULL,
    name TEXT NOT NULL,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (folder, name)
);

CREATE INDEX IF NOT EXISTS result_documents_name_idx
    ON result_documents (name, folder);
