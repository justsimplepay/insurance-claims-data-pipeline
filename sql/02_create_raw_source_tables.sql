-- GMS Data Engineer Case Study
-- Raw source tables.
-- Business fields intentionally remain TEXT so malformed source values,
-- duplicates and invalid relationships can be loaded without rejection.

CREATE TABLE IF NOT EXISTS raw.customer_csv (
    raw_row_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_file_id     bigint NOT NULL REFERENCES raw.source_files(source_file_id),
    source_row_number  integer NOT NULL,

    customer_id        text,
    first_name         text,
    last_name          text,
    date_of_birth      text,
    gender             text,
    address            text,
    city               text,
    province           text,
    postal_code        text,
    phone              text,
    email              text,
    customer_since     text,
    last_updated       text,

    CONSTRAINT uq_raw_customer_source_row
        UNIQUE (source_file_id, source_row_number),
    CONSTRAINT ck_raw_customer_source_row_number
        CHECK (source_row_number > 0)
);

CREATE TABLE IF NOT EXISTS raw.policy_csv (
    raw_row_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_file_id     bigint NOT NULL REFERENCES raw.source_files(source_file_id),
    source_row_number  integer NOT NULL,

    policy_id          text,
    customer_id        text,
    policy_type        text,
    plan_name          text,
    coverage_type      text,
    start_date         text,
    end_date           text,
    status             text,
    sales_channel      text,
    coverage_amount    text,
    deductible_amount  text,

    CONSTRAINT uq_raw_policy_source_row
        UNIQUE (source_file_id, source_row_number),
    CONSTRAINT ck_raw_policy_source_row_number
        CHECK (source_row_number > 0)
);

CREATE TABLE IF NOT EXISTS raw.claim_csv (
    raw_row_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_file_id     bigint NOT NULL REFERENCES raw.source_files(source_file_id),
    source_row_number  integer NOT NULL,

    claim_id           text,
    policy_id          text,
    customer_id        text,
    claim_type         text,
    service_date       text,
    claim_date         text,
    claim_amount       text,
    approved_amount    text,
    claim_status       text,
    region             text,

    CONSTRAINT uq_raw_claim_source_row
        UNIQUE (source_file_id, source_row_number),
    CONSTRAINT ck_raw_claim_source_row_number
        CHECK (source_row_number > 0)
);

CREATE TABLE IF NOT EXISTS raw.claim_payment_csv (
    raw_row_id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_file_id          bigint NOT NULL REFERENCES raw.source_files(source_file_id),
    source_row_number       integer NOT NULL,

    payment_id              text,
    claim_id                text,
    processing_start_date   text,
    decision_date           text,
    decision_outcome        text,
    payment_date            text,
    payment_amount          text,
    payment_method          text,
    payment_status          text,
    transaction_reference   text,
    denial_reason           text,

    CONSTRAINT uq_raw_claim_payment_source_row
        UNIQUE (source_file_id, source_row_number),
    CONSTRAINT ck_raw_claim_payment_source_row_number
        CHECK (source_row_number > 0)
);

CREATE TABLE IF NOT EXISTS raw.policy_premium_csv (
    raw_row_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_file_id     bigint NOT NULL REFERENCES raw.source_files(source_file_id),
    source_row_number  integer NOT NULL,

    premium_id         text,
    policy_id          text,
    customer_id        text,
    premium_amount     text,
    premium_frequency  text,
    age_band           text,
    due_date           text,
    paid_date          text,
    payment_status     text,
    payment_method     text,

    CONSTRAINT uq_raw_policy_premium_source_row
        UNIQUE (source_file_id, source_row_number),
    CONSTRAINT ck_raw_policy_premium_source_row_number
        CHECK (source_row_number > 0)
);

CREATE TABLE IF NOT EXISTS raw.claim_detail_files (
    source_file_id  bigint PRIMARY KEY REFERENCES raw.source_files(source_file_id),
    raw_text        text NOT NULL,
    payload_jsonb   jsonb,
    parse_status    text NOT NULL,
    parse_error     text,
    CONSTRAINT ck_claim_detail_parse_status
        CHECK (parse_status IN ('parsed', 'malformed')),
    CONSTRAINT ck_claim_detail_parse_result
        CHECK (
            (parse_status = 'parsed' AND payload_jsonb IS NOT NULL AND parse_error IS NULL)
            OR
            (parse_status = 'malformed' AND payload_jsonb IS NULL)
        )
);
