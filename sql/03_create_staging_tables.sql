-- GMS Data Engineer Case Study
-- Typed, per-source staging tables.
-- Staging remains permissive: cross-source business FKs are validated by the
-- transformation pipeline rather than enforced here.

CREATE TABLE IF NOT EXISTS staging.customers (
    stg_customer_row_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    load_id              bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    raw_row_id           bigint NOT NULL UNIQUE REFERENCES raw.customer_csv(raw_row_id),
    record_status        text NOT NULL DEFAULT 'candidate',
    dq_issue_count       integer NOT NULL DEFAULT 0,

    customer_id          text,
    first_name           text,
    last_name            text,
    date_of_birth        date,
    gender               text,
    address              text,
    city                 text,
    province             text,
    postal_code          text,
    phone                text,
    email                text,
    customer_since       date,
    last_updated         timestamptz,

    CONSTRAINT ck_stg_customers_status
        CHECK (record_status IN ('candidate', 'accepted', 'duplicate', 'rejected')),
    CONSTRAINT ck_stg_customers_dq_count
        CHECK (dq_issue_count >= 0)
);

CREATE TABLE IF NOT EXISTS staging.policies (
    stg_policy_row_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    load_id              bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    raw_row_id           bigint NOT NULL UNIQUE REFERENCES raw.policy_csv(raw_row_id),
    record_status        text NOT NULL DEFAULT 'candidate',
    dq_issue_count       integer NOT NULL DEFAULT 0,

    policy_id            text,
    customer_id          text,
    policy_type          text,
    plan_name            text,
    coverage_type        text,
    start_date           date,
    end_date             date,
    status               text,
    sales_channel        text,
    coverage_amount      numeric(12,2),
    deductible_amount    numeric(12,2),

    CONSTRAINT ck_stg_policies_status
        CHECK (record_status IN ('candidate', 'accepted', 'duplicate', 'rejected')),
    CONSTRAINT ck_stg_policies_dq_count
        CHECK (dq_issue_count >= 0)
);

CREATE TABLE IF NOT EXISTS staging.claims (
    stg_claim_row_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    load_id              bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    raw_row_id           bigint NOT NULL UNIQUE REFERENCES raw.claim_csv(raw_row_id),
    record_status        text NOT NULL DEFAULT 'candidate',
    dq_issue_count       integer NOT NULL DEFAULT 0,

    claim_id             text,
    policy_id            text,
    customer_id          text,
    claim_type           text,
    service_date         date,
    claim_date           date,
    claim_amount         numeric(12,2),
    approved_amount      numeric(12,2),
    claim_status         text,
    region               text,

    CONSTRAINT ck_stg_claims_status
        CHECK (record_status IN ('candidate', 'accepted', 'duplicate', 'rejected')),
    CONSTRAINT ck_stg_claims_dq_count
        CHECK (dq_issue_count >= 0)
);

CREATE TABLE IF NOT EXISTS staging.claim_payments (
    stg_payment_row_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    load_id                  bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    raw_row_id               bigint NOT NULL UNIQUE REFERENCES raw.claim_payment_csv(raw_row_id),
    record_status            text NOT NULL DEFAULT 'candidate',
    dq_issue_count           integer NOT NULL DEFAULT 0,

    payment_id               text,
    claim_id                 text,
    processing_start_date    date,
    decision_date            date,
    decision_outcome         text,
    payment_date             date,
    payment_amount           numeric(12,2),
    payment_method           text,
    payment_status           text,
    transaction_reference    text,
    denial_reason            text,

    CONSTRAINT ck_stg_claim_payments_status
        CHECK (record_status IN ('candidate', 'accepted', 'duplicate', 'rejected')),
    CONSTRAINT ck_stg_claim_payments_dq_count
        CHECK (dq_issue_count >= 0)
);

CREATE TABLE IF NOT EXISTS staging.policy_premiums (
    stg_premium_row_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    load_id              bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    raw_row_id           bigint NOT NULL UNIQUE REFERENCES raw.policy_premium_csv(raw_row_id),
    record_status        text NOT NULL DEFAULT 'candidate',
    dq_issue_count       integer NOT NULL DEFAULT 0,

    premium_id           text,
    policy_id            text,
    customer_id          text,
    premium_amount       numeric(12,2),
    premium_frequency    text,
    age_band             text,
    due_date             date,
    paid_date            date,
    payment_status       text,
    payment_method       text,

    CONSTRAINT ck_stg_policy_premiums_status
        CHECK (record_status IN ('candidate', 'accepted', 'duplicate', 'rejected')),
    CONSTRAINT ck_stg_policy_premiums_dq_count
        CHECK (dq_issue_count >= 0)
);

CREATE TABLE IF NOT EXISTS staging.claim_details (
    stg_claim_detail_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    load_id                    bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    raw_file_id                bigint NOT NULL UNIQUE REFERENCES raw.claim_detail_files(source_file_id),
    record_status              text NOT NULL DEFAULT 'candidate',
    dq_issue_count             integer NOT NULL DEFAULT 0,

    schema_version             text,
    claim_id                   text,
    product_line               text,
    submission_channel         text,
    submitted_at               timestamptz,

    provider_id                text,
    provider_name              text,
    provider_type              text,

    adjuster_id                text,
    adjuster_notes             text,
    documents_submitted        text[],

    practitioner_type          text,
    number_of_visits           integer,
    prescription_din           text,
    prescription_drug_name     text,
    prescription_days_supply   integer,
    prescription_is_generic    boolean,

    dental_claim_category      text,

    trip_start                 date,
    trip_end                   date,
    destination_country        text,
    incident_type              text,
    incident_date              date,
    currency                   text,
    exchange_rate_to_cad       numeric(10,4),
    incident_sub_limit         numeric(12,2),

    CONSTRAINT ck_stg_claim_details_status
        CHECK (record_status IN ('candidate', 'accepted', 'duplicate', 'rejected')),
    CONSTRAINT ck_stg_claim_details_dq_count
        CHECK (dq_issue_count >= 0)
);

CREATE TABLE IF NOT EXISTS staging.claim_line_items (
    stg_line_item_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    load_id               bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    raw_file_id           bigint NOT NULL REFERENCES raw.claim_detail_files(source_file_id),

    source_array_index    integer NOT NULL,
    claim_id              text,
    line_no               integer,
    service_date          date,
    description           text,
    quantity              integer,
    unit_amount           numeric(12,2),
    amount                numeric(12,2),

    procedure_code        text,
    procedure_category    text,
    tooth_number          integer,

    CONSTRAINT uq_stg_claim_line_item_source
        UNIQUE (raw_file_id, source_array_index),
    CONSTRAINT ck_stg_claim_line_item_source_index
        CHECK (source_array_index >= 0)
);

CREATE TABLE IF NOT EXISTS staging.customer_identity_map (
    load_id                       bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_customer_id            text NOT NULL,
    canonical_customer_id         text NOT NULL,
    survivor_stg_customer_row_id  bigint NOT NULL REFERENCES staging.customers(stg_customer_row_id),
    resolution_type               text NOT NULL,

    PRIMARY KEY (load_id, source_customer_id),
    CONSTRAINT ck_customer_identity_resolution
        CHECK (resolution_type IN ('survivor', 'duplicate_alias'))
);

CREATE TABLE IF NOT EXISTS staging.data_quality_log (
    dq_id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    load_id           bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),

    source_name       text NOT NULL,
    source_table      text NOT NULL,
    source_record_id  bigint,
    business_key      text,

    rule_id           text NOT NULL,
    field_name        text,
    severity          text NOT NULL,
    action            text NOT NULL,

    original_value    text,
    clean_value       text,
    details           jsonb,

    detected_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_dq_severity
        CHECK (severity IN ('info', 'warning', 'error')),
    CONSTRAINT ck_dq_action
        CHECK (action IN ('normalized', 'repaired', 'deduplicated', 'flagged', 'quarantined', 'excluded'))
);

CREATE TABLE IF NOT EXISTS staging.quarantine (
    quarantine_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    load_id           bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),

    source_name       text NOT NULL,
    source_table      text NOT NULL,
    source_record_id  bigint,
    business_key      text,

    rule_id           text NOT NULL,
    reason            text NOT NULL,
    details           jsonb,

    quarantined_at    timestamptz NOT NULL DEFAULT now()
);
