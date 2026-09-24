-- GMS Data Engineer Case Study
-- Raw ingestion lineage and batch-control tables.

CREATE TABLE IF NOT EXISTS raw.ingestion_runs (
    load_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_name         text NOT NULL,
    batch_fingerprint  text NOT NULL UNIQUE,
    started_at         timestamptz NOT NULL DEFAULT now(),
    completed_at       timestamptz,
    status             text NOT NULL DEFAULT 'running',
    notes              text,
    CONSTRAINT ck_ingestion_runs_status
        CHECK (status IN ('running', 'succeeded', 'failed')),
    CONSTRAINT ck_ingestion_runs_completion
        CHECK (completed_at IS NULL OR completed_at >= started_at)
);

CREATE TABLE IF NOT EXISTS raw.source_files (
    source_file_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    load_id         bigint NOT NULL
                    REFERENCES raw.ingestion_runs(load_id),
    source_path     text NOT NULL,
    source_name     text NOT NULL,
    source_format   text NOT NULL,
    sha256          text NOT NULL,
    byte_size       bigint NOT NULL,
    record_count    integer,
    ingested_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_source_files_load_path
        UNIQUE (load_id, source_path),
    CONSTRAINT ck_source_files_format
        CHECK (source_format IN ('csv', 'json')),
    CONSTRAINT ck_source_files_byte_size
        CHECK (byte_size >= 0),
    CONSTRAINT ck_source_files_record_count
        CHECK (record_count IS NULL OR record_count >= 0),
    CONSTRAINT ck_source_files_sha256
        CHECK (sha256 ~ '^[0-9a-fA-F]{64}$')
);
