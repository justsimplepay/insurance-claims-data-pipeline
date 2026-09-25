-- GMS Data Engineer Case Study
-- Recurring raw -> staging transformation for one successful raw load.
--
-- The Python wrapper replaces __LOAD_ID__ with an integer and executes this
-- file inside a single transaction. Staging is idempotent per load: rerunning
-- the same load removes that load's prior staging/DQ rows and rebuilds them.
--
-- Scope of this layer:
--   * deterministic format/type normalization
--   * per-source duplicate handling
--   * recoverable JSON key/type/shape normalization
--   * JSON flattening
--   * data-quality logging and quarantine
--   * basic referential/orphan checks
--
-- Cross-source source-of-truth reconciliation (for example Claim.region vs
-- Customer.province, or Claim.claim_status vs payment decision) is deferred to
-- the core-load step.

DROP TABLE IF EXISTS pg_temp._gms_load_context;
CREATE TEMP TABLE _gms_load_context (
    load_id bigint PRIMARY KEY
) ON COMMIT DROP;

INSERT INTO _gms_load_context(load_id) VALUES (__LOAD_ID__);

DO $$
DECLARE
    v_load_id bigint := (SELECT load_id FROM _gms_load_context);
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM raw.ingestion_runs
        WHERE load_id = v_load_id
          AND status = 'succeeded'
    ) THEN
        RAISE EXCEPTION 'load_id % is not a successful raw ingestion run', v_load_id;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 0. Make the transformation idempotent for this load.
-- ---------------------------------------------------------------------------

DELETE FROM staging.customer_identity_map
WHERE load_id = (SELECT load_id FROM _gms_load_context);

DELETE FROM staging.data_quality_log
WHERE load_id = (SELECT load_id FROM _gms_load_context);

DELETE FROM staging.quarantine
WHERE load_id = (SELECT load_id FROM _gms_load_context);

DELETE FROM staging.claim_line_items
WHERE load_id = (SELECT load_id FROM _gms_load_context);

DELETE FROM staging.claim_details
WHERE load_id = (SELECT load_id FROM _gms_load_context);

DELETE FROM staging.claim_payments
WHERE load_id = (SELECT load_id FROM _gms_load_context);

DELETE FROM staging.claims
WHERE load_id = (SELECT load_id FROM _gms_load_context);

DELETE FROM staging.policy_premiums
WHERE load_id = (SELECT load_id FROM _gms_load_context);

DELETE FROM staging.policies
WHERE load_id = (SELECT load_id FROM _gms_load_context);

DELETE FROM staging.customers
WHERE load_id = (SELECT load_id FROM _gms_load_context);

-- ---------------------------------------------------------------------------
-- 1. Customer.csv
-- ---------------------------------------------------------------------------

INSERT INTO staging.customers (
    load_id,
    raw_row_id,
    customer_id,
    first_name,
    last_name,
    date_of_birth,
    gender,
    address,
    city,
    province,
    postal_code,
    phone,
    email,
    customer_since,
    last_updated
)
SELECT
    sf.load_id,
    r.raw_row_id,
    staging.normalize_id(r.customer_id),
    CASE WHEN staging.clean_text(r.first_name) IS NULL
         THEN NULL ELSE initcap(lower(staging.clean_text(r.first_name))) END,
    CASE WHEN staging.clean_text(r.last_name) IS NULL
         THEN NULL ELSE initcap(lower(staging.clean_text(r.last_name))) END,
    staging.parse_date(r.date_of_birth, 'DMY'),
    staging.normalize_gender(r.gender),
    staging.clean_text(r.address),
    CASE WHEN staging.clean_text(r.city) IS NULL
         THEN NULL ELSE initcap(lower(staging.clean_text(r.city))) END,
    COALESCE(
        staging.normalize_province(r.province),
        staging.postal_province(r.postal_code, r.city)
    ),
    staging.normalize_postal(r.postal_code),
    staging.normalize_phone(r.phone),
    lower(staging.clean_text(r.email)),
    staging.parse_date(r.customer_since, 'DMY'),
    staging.parse_timestamptz(r.last_updated)
FROM raw.customer_csv r
JOIN raw.source_files sf USING (source_file_id)
WHERE sf.load_id = (SELECT load_id FROM _gms_load_context);

-- Customer normalization log.
INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value
)
SELECT
    c.load_id, sf.source_name, 'raw.customer_csv', r.raw_row_id, c.customer_id,
    v.rule_id, v.field_name, 'info', v.action, v.original_value, v.clean_value
FROM staging.customers c
JOIN raw.customer_csv r ON r.raw_row_id = c.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id = r.source_file_id
CROSS JOIN LATERAL (
    VALUES
      ('FMT_DATE', 'date_of_birth', 'normalized', r.date_of_birth,
       c.date_of_birth::text),
      ('FMT_DATE', 'customer_since', 'normalized', r.customer_since,
       c.customer_since::text),
      ('FMT_GENDER', 'gender', 'normalized', r.gender, c.gender),
      ('FMT_PROVINCE', 'province',
       CASE WHEN r.province IS NULL AND c.province IS NOT NULL THEN 'repaired' ELSE 'normalized' END,
       r.province, c.province),
      ('FMT_POSTAL', 'postal_code', 'normalized', r.postal_code, c.postal_code),
      ('FMT_PHONE', 'phone', 'normalized', r.phone, c.phone),
      ('FMT_TEXT_WHITESPACE', 'first_name', 'normalized', r.first_name, c.first_name),
      ('FMT_TEXT_WHITESPACE', 'last_name', 'normalized', r.last_name, c.last_name),
      ('FMT_TEXT_WHITESPACE', 'city', 'normalized', r.city, c.city),
      ('FMT_TEXT_WHITESPACE', 'email', 'normalized', r.email, c.email)
) AS v(rule_id, field_name, action, original_value, clean_value)
WHERE c.load_id = (SELECT load_id FROM _gms_load_context)
  AND v.original_value IS DISTINCT FROM v.clean_value
  AND NOT (v.original_value IS NULL AND v.clean_value IS NULL);

-- Customer source-quality issues. Missing DOB is retained as NULL because the
-- core design deliberately preserves the entity and avoids cascading data loss.
INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value, details
)
SELECT
    c.load_id, sf.source_name, 'raw.customer_csv', r.raw_row_id, c.customer_id,
    x.rule_id, x.field_name, x.severity, x.action, x.original_value,
    x.clean_value, x.details
FROM staging.customers c
JOIN raw.customer_csv r ON r.raw_row_id = c.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id = r.source_file_id
CROSS JOIN LATERAL (
    VALUES
      (
        'MISS_ERROR', 'date_of_birth', 'error', 'flagged',
        r.date_of_birth, c.date_of_birth::text,
        CASE WHEN r.date_of_birth IS NULL
             THEN jsonb_build_object('reason','required source DOB is missing')
             ELSE NULL END
      ),
      (
        'PARSE_DATE', 'date_of_birth', 'error', 'flagged',
        r.date_of_birth, c.date_of_birth::text,
        CASE WHEN r.date_of_birth IS NOT NULL AND c.date_of_birth IS NULL
             THEN jsonb_build_object('reason','DOB could not be parsed using the declared source conventions')
             ELSE NULL END
      ),
      (
        'INV_AGE', 'date_of_birth', 'error', 'flagged',
        r.date_of_birth, c.date_of_birth::text,
        CASE WHEN c.date_of_birth = DATE '1900-01-01'
                   OR c.date_of_birth > DATE '2026-06-30'
             THEN jsonb_build_object('reason','sentinel or future DOB')
             ELSE NULL END
      ),
      (
        'INV_UNSERVED_PROVINCE', 'province', 'error', 'quarantined',
        r.province, c.province,
        CASE WHEN c.province IN ('QC','NB','NU')
             THEN jsonb_build_object('reason','province is outside the served-province domain')
             ELSE NULL END
      ),
      (
        'MISS_ERROR', 'province', 'error',
        CASE WHEN r.province IS NULL AND c.province IS NOT NULL THEN 'repaired' ELSE 'flagged' END,
        r.province, c.province,
        CASE WHEN r.province IS NULL
             THEN jsonb_build_object('recovered_from_postal_code', c.province IS NOT NULL)
             ELSE NULL END
      )
) AS x(rule_id, field_name, severity, action, original_value, clean_value, details)
WHERE c.load_id = (SELECT load_id FROM _gms_load_context)
  AND x.details IS NOT NULL;

UPDATE staging.customers
SET record_status = 'rejected'
WHERE load_id = (SELECT load_id FROM _gms_load_context)
  AND (
      customer_id IS NULL OR customer_id !~ '^C[0-9]{5}$'
      OR first_name IS NULL
      OR last_name IS NULL
      OR address IS NULL
      OR city IS NULL
      OR province IS NULL
      OR province IN ('QC','NB','NU')
      OR postal_code IS NULL
      OR postal_code !~ '^[ABCEGHJ-NPRSTVXY][0-9][ABCEGHJ-NPRSTV-Z] [0-9][ABCEGHJ-NPRSTV-Z][0-9]$'
      OR customer_since IS NULL
      OR last_updated IS NULL
  );

UPDATE staging.customers
SET record_status = 'accepted'
WHERE load_id = (SELECT load_id FROM _gms_load_context)
  AND record_status = 'candidate';

-- Resolve duplicate persons deterministically.
--
-- Primary match: normalized first name + last name + DOB + postal code.
--
-- High-confidence fallback: the generated source contains one duplicate-person
-- row whose DOB was independently corrupted by an age defect. To avoid losing
-- that identity link, a pair may still match when name, postal code, address
-- and city all agree, at least one contact value agrees, and exactly one DOB is
-- outside the source's valid extract-end age range (18-89). This is deliberately
-- conservative: a conflicting pair of otherwise valid DOBs is never merged.
WITH eligible AS (
    SELECT
        c.*,
        (
            c.date_of_birth BETWEEN DATE '1936-07-01' AND DATE '2008-06-30'
        ) AS dob_is_valid,
        (
            (c.date_of_birth IS NOT NULL)::int
          + (c.province IS NOT NULL)::int
          + (c.phone IS NOT NULL)::int
          + (c.email IS NOT NULL)::int
        ) AS completeness_score
    FROM staging.customers c
    WHERE c.load_id = (SELECT load_id FROM _gms_load_context)
      AND c.record_status <> 'rejected'
),
candidate_matches AS (
    SELECT
        r.stg_customer_row_id AS source_row_id,
        r.customer_id AS source_customer_id,
        s.stg_customer_row_id AS candidate_survivor_row_id,
        s.customer_id AS candidate_survivor_customer_id,
        s.dob_is_valid,
        s.completeness_score,
        s.last_updated,
        CASE
            WHEN r.stg_customer_row_id = s.stg_customer_row_id THEN 'self'
            WHEN lower(r.first_name) = lower(s.first_name)
             AND lower(r.last_name) = lower(s.last_name)
             AND r.date_of_birth = s.date_of_birth
             AND replace(r.postal_code, ' ', '') = replace(s.postal_code, ' ', '')
                THEN 'exact_identity_key'
            ELSE 'high_confidence_dob_repair'
        END AS match_type
    FROM eligible r
    JOIN eligible s
      ON r.stg_customer_row_id = s.stg_customer_row_id
      OR (
          lower(r.first_name) = lower(s.first_name)
          AND lower(r.last_name) = lower(s.last_name)
          AND replace(r.postal_code, ' ', '') = replace(s.postal_code, ' ', '')
          AND (
              r.date_of_birth = s.date_of_birth
              OR (
                  lower(r.address) = lower(s.address)
                  AND lower(r.city) = lower(s.city)
                  AND (
                      (
                          r.email IS NOT NULL
                          AND s.email IS NOT NULL
                          AND lower(r.email) = lower(s.email)
                      )
                      OR (
                          r.phone IS NOT NULL
                          AND s.phone IS NOT NULL
                          AND r.phone = s.phone
                      )
                  )
                  AND r.date_of_birth IS DISTINCT FROM s.date_of_birth
                  AND r.dob_is_valid IS DISTINCT FROM s.dob_is_valid
              )
          )
      )
),
resolved AS (
    SELECT *
    FROM (
        SELECT
            m.*,
            row_number() OVER (
                PARTITION BY m.source_row_id
                ORDER BY
                    m.dob_is_valid DESC,
                    m.completeness_score DESC,
                    m.last_updated DESC NULLS LAST,
                    m.candidate_survivor_row_id
            ) AS survivor_rank
        FROM candidate_matches m
    ) ranked
    WHERE survivor_rank = 1
)
INSERT INTO staging.customer_identity_map (
    load_id, source_customer_id, canonical_customer_id,
    survivor_stg_customer_row_id, resolution_type
)
SELECT
    (SELECT load_id FROM _gms_load_context),
    source_customer_id,
    candidate_survivor_customer_id,
    candidate_survivor_row_id,
    CASE
        WHEN source_row_id = candidate_survivor_row_id THEN 'survivor'
        ELSE 'duplicate_alias'
    END
FROM resolved
ON CONFLICT (load_id, source_customer_id) DO UPDATE
SET canonical_customer_id = EXCLUDED.canonical_customer_id,
    survivor_stg_customer_row_id = EXCLUDED.survivor_stg_customer_row_id,
    resolution_type = EXCLUDED.resolution_type;

UPDATE staging.customers c
SET record_status = 'duplicate'
FROM staging.customer_identity_map m
WHERE c.load_id = m.load_id
  AND c.customer_id = m.source_customer_id
  AND m.resolution_type = 'duplicate_alias'
  AND c.load_id = (SELECT load_id FROM _gms_load_context);

INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value, details
)
SELECT
    c.load_id, sf.source_name, 'raw.customer_csv', c.raw_row_id, c.customer_id,
    'DUP_ENTITY', 'customer_id', 'warning', 'deduplicated',
    c.customer_id, m.canonical_customer_id,
    jsonb_build_object(
        'survivor_customer_id', m.canonical_customer_id,
        'resolution',
        CASE
            WHEN c.date_of_birth IS DISTINCT FROM s.date_of_birth
                THEN 'high_confidence_match_with_invalid_dob'
            ELSE 'normalized_identity_key'
        END
    )
FROM staging.customers c
JOIN staging.customer_identity_map m
  ON m.load_id = c.load_id
 AND m.source_customer_id = c.customer_id
JOIN staging.customers s
  ON s.stg_customer_row_id = m.survivor_stg_customer_row_id
JOIN raw.customer_csv r ON r.raw_row_id = c.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id = r.source_file_id
WHERE c.load_id = (SELECT load_id FROM _gms_load_context)
  AND m.resolution_type = 'duplicate_alias';

-- ---------------------------------------------------------------------------
-- 2. Policy.csv
-- ---------------------------------------------------------------------------

INSERT INTO staging.policies (
    load_id, raw_row_id, policy_id, customer_id, policy_type, plan_name,
    coverage_type, start_date, end_date, status, sales_channel,
    coverage_amount, deductible_amount
)
SELECT
    sf.load_id,
    r.raw_row_id,
    staging.normalize_id(r.policy_id),
    staging.normalize_id(r.customer_id),
    staging.normalize_category(r.policy_type),
    staging.clean_text(r.plan_name),
    staging.normalize_category(r.coverage_type),
    staging.parse_date(r.start_date, 'MDY'),
    staging.parse_date(r.end_date, 'MDY'),
    staging.normalize_category(r.status),
    staging.normalize_category(r.sales_channel),
    staging.parse_amount(r.coverage_amount),
    staging.parse_amount(r.deductible_amount)
FROM raw.policy_csv r
JOIN raw.source_files sf USING (source_file_id)
WHERE sf.load_id = (SELECT load_id FROM _gms_load_context);

-- Repair a missing plan only when policy_type + coverage amount identifies one
-- plan deterministically in the frozen source dictionary.
UPDATE staging.policies
SET plan_name = CASE
    WHEN policy_type = 'health' AND coverage_amount = 1500 THEN 'BasicPlan'
    WHEN policy_type = 'health' AND coverage_amount = 3000 THEN 'ExtendaPlan'
    WHEN policy_type = 'health' AND coverage_amount = 5000 THEN 'OmniPlan'
    WHEN policy_type = 'health' AND coverage_amount = 2000 THEN 'Replacement Health'
    WHEN policy_type = 'dental' AND coverage_amount = 1000 THEN 'Dental Basic'
    WHEN policy_type = 'dental' AND coverage_amount = 2000 THEN 'Dental Plus'
    WHEN policy_type = 'travel' AND coverage_amount = 5000000 THEN 'TravelStar'
    WHEN policy_type = 'travel' AND coverage_amount = 2000000 THEN 'StudentPlan'
    ELSE NULL
END
WHERE load_id = (SELECT load_id FROM _gms_load_context)
  AND plan_name IS NULL;

INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value
)
SELECT
    p.load_id, sf.source_name, 'raw.policy_csv', r.raw_row_id, p.policy_id,
    v.rule_id, v.field_name, 'info', v.action, v.original_value, v.clean_value
FROM staging.policies p
JOIN raw.policy_csv r ON r.raw_row_id = p.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id = r.source_file_id
CROSS JOIN LATERAL (
    VALUES
      ('FMT_ID','customer_id','normalized',r.customer_id,p.customer_id),
      ('FMT_CATEGORY_CASE','policy_type','normalized',r.policy_type,p.policy_type),
      ('FMT_CATEGORY_CASE','coverage_type','normalized',r.coverage_type,p.coverage_type),
      ('FMT_CATEGORY_CASE','status','normalized',r.status,p.status),
      ('FMT_CATEGORY_CASE','sales_channel','normalized',r.sales_channel,p.sales_channel),
      ('FMT_DATE','start_date','normalized',r.start_date,p.start_date::text),
      ('FMT_DATE','end_date','normalized',r.end_date,p.end_date::text),
      ('TYPE_AMOUNT_TEXT','coverage_amount','normalized',r.coverage_amount,p.coverage_amount::text),
      ('TYPE_AMOUNT_TEXT','deductible_amount','normalized',r.deductible_amount,p.deductible_amount::text),
      ('MISS_ERROR','plan_name',
       CASE WHEN r.plan_name IS NULL AND p.plan_name IS NOT NULL THEN 'repaired' ELSE 'flagged' END,
       r.plan_name,p.plan_name)
) AS v(rule_id, field_name, action, original_value, clean_value)
WHERE p.load_id = (SELECT load_id FROM _gms_load_context)
  AND v.original_value IS DISTINCT FROM v.clean_value
  AND NOT (v.original_value IS NULL AND v.clean_value IS NULL);

-- ---------------------------------------------------------------------------
-- 3. Claim.csv
-- ---------------------------------------------------------------------------

INSERT INTO staging.claims (
    load_id, raw_row_id, claim_id, policy_id, customer_id, claim_type,
    service_date, claim_date, claim_amount, approved_amount, claim_status, region
)
SELECT
    sf.load_id,
    r.raw_row_id,
    staging.normalize_id(r.claim_id),
    staging.normalize_id(r.policy_id),
    staging.normalize_id(r.customer_id),
    staging.normalize_category(r.claim_type),
    staging.parse_date(r.service_date, 'DMY'),
    staging.parse_date(r.claim_date, 'DMY'),
    staging.parse_amount(r.claim_amount),
    staging.parse_amount(r.approved_amount),
    staging.normalize_category(r.claim_status),
    staging.normalize_province(r.region)
FROM raw.claim_csv r
JOIN raw.source_files sf USING (source_file_id)
WHERE sf.load_id = (SELECT load_id FROM _gms_load_context);

INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value
)
SELECT
    c.load_id, sf.source_name, 'raw.claim_csv', r.raw_row_id, c.claim_id,
    v.rule_id, v.field_name, 'info', 'normalized', v.original_value, v.clean_value
FROM staging.claims c
JOIN raw.claim_csv r ON r.raw_row_id = c.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id = r.source_file_id
CROSS JOIN LATERAL (
    VALUES
      ('FMT_ID','claim_id',r.claim_id,c.claim_id),
      ('FMT_ID','policy_id',r.policy_id,c.policy_id),
      ('FMT_ID','customer_id',r.customer_id,c.customer_id),
      ('FMT_CATEGORY_CASE','claim_type',r.claim_type,c.claim_type),
      ('FMT_CATEGORY_CASE','claim_status',r.claim_status,c.claim_status),
      ('FMT_PROVINCE','region',r.region,c.region),
      ('FMT_DATE','service_date',r.service_date,c.service_date::text),
      ('FMT_DATE','claim_date',r.claim_date,c.claim_date::text),
      ('TYPE_AMOUNT_TEXT','claim_amount',r.claim_amount,c.claim_amount::text),
      ('TYPE_AMOUNT_TEXT','approved_amount',r.approved_amount,c.approved_amount::text)
) AS v(rule_id, field_name, original_value, clean_value)
WHERE c.load_id = (SELECT load_id FROM _gms_load_context)
  AND v.original_value IS DISTINCT FROM v.clean_value
  AND NOT (v.original_value IS NULL AND v.clean_value IS NULL);

-- Detect the 6 exact duplicate rows and the 4 normalized-key near duplicates.
WITH ranked AS (
    SELECT
        c.stg_claim_row_id,
        c.claim_id,
        row_number() OVER (
            PARTITION BY c.claim_id
            ORDER BY c.raw_row_id
        ) AS key_rank,
        row_number() OVER (
            PARTITION BY
                r.claim_id, r.policy_id, r.customer_id, r.claim_type,
                r.service_date, r.claim_date, r.claim_amount, r.approved_amount,
                r.claim_status, r.region
            ORDER BY r.raw_row_id
        ) AS exact_rank
    FROM staging.claims c
    JOIN raw.claim_csv r ON r.raw_row_id = c.raw_row_id
    WHERE c.load_id = (SELECT load_id FROM _gms_load_context)
),
dupes AS (
    SELECT * FROM ranked WHERE key_rank > 1
)
UPDATE staging.claims c
SET record_status = 'duplicate'
FROM dupes d
WHERE c.stg_claim_row_id = d.stg_claim_row_id;

WITH ranked AS (
    SELECT
        c.stg_claim_row_id,
        c.load_id,
        c.raw_row_id,
        c.claim_id,
        row_number() OVER (PARTITION BY c.claim_id ORDER BY c.raw_row_id) AS key_rank,
        row_number() OVER (
            PARTITION BY
                r.claim_id, r.policy_id, r.customer_id, r.claim_type,
                r.service_date, r.claim_date, r.claim_amount, r.approved_amount,
                r.claim_status, r.region
            ORDER BY r.raw_row_id
        ) AS exact_rank
    FROM staging.claims c
    JOIN raw.claim_csv r ON r.raw_row_id = c.raw_row_id
    WHERE c.load_id = (SELECT load_id FROM _gms_load_context)
)
INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, details
)
SELECT
    x.load_id, sf.source_name, 'raw.claim_csv', x.raw_row_id, x.claim_id,
    CASE WHEN x.exact_rank > 1 THEN 'DUP_EXACT' ELSE 'DUP_NEAR_KEY' END,
    'claim_id', 'warning', 'deduplicated',
    jsonb_build_object('normalized_claim_id', x.claim_id)
FROM ranked x
JOIN raw.claim_csv r ON r.raw_row_id = x.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id = r.source_file_id
WHERE x.key_rank > 1;

-- ---------------------------------------------------------------------------
-- 4. Claim_Payment.csv
-- ---------------------------------------------------------------------------

INSERT INTO staging.claim_payments (
    load_id, raw_row_id, payment_id, claim_id, processing_start_date,
    decision_date, decision_outcome, payment_date, payment_amount,
    payment_method, payment_status, transaction_reference, denial_reason
)
SELECT
    sf.load_id,
    r.raw_row_id,
    staging.normalize_id(r.payment_id),
    staging.normalize_id(r.claim_id),
    staging.parse_date(r.processing_start_date, 'YYYYMMDD'),
    staging.parse_date(r.decision_date, 'YYYYMMDD'),
    staging.normalize_category(r.decision_outcome),
    staging.parse_date(r.payment_date, 'YYYYMMDD'),
    staging.parse_amount(r.payment_amount),
    staging.normalize_category(r.payment_method),
    staging.normalize_category(r.payment_status),
    staging.clean_text(r.transaction_reference),
    staging.normalize_category(r.denial_reason)
FROM raw.claim_payment_csv r
JOIN raw.source_files sf USING (source_file_id)
WHERE sf.load_id = (SELECT load_id FROM _gms_load_context);

INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value
)
SELECT
    p.load_id, sf.source_name, 'raw.claim_payment_csv', r.raw_row_id, p.payment_id,
    v.rule_id, v.field_name, 'info', 'normalized', v.original_value, v.clean_value
FROM staging.claim_payments p
JOIN raw.claim_payment_csv r ON r.raw_row_id = p.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id = r.source_file_id
CROSS JOIN LATERAL (
    VALUES
      ('FMT_ID','claim_id',r.claim_id,p.claim_id),
      ('FMT_DATE','processing_start_date',r.processing_start_date,p.processing_start_date::text),
      ('FMT_DATE','decision_date',r.decision_date,p.decision_date::text),
      ('FMT_DATE','payment_date',r.payment_date,p.payment_date::text),
      ('FMT_CATEGORY_CASE','decision_outcome',r.decision_outcome,p.decision_outcome),
      ('FMT_CATEGORY_CASE','payment_method',r.payment_method,p.payment_method),
      ('FMT_CATEGORY_CASE','payment_status',r.payment_status,p.payment_status),
      ('TYPE_AMOUNT_TEXT','payment_amount',r.payment_amount,p.payment_amount::text)
) AS v(rule_id, field_name, original_value, clean_value)
WHERE p.load_id = (SELECT load_id FROM _gms_load_context)
  AND v.original_value IS DISTINCT FROM v.clean_value
  AND NOT (v.original_value IS NULL AND v.clean_value IS NULL);

WITH ranked AS (
    SELECT
        p.stg_payment_row_id,
        row_number() OVER (PARTITION BY p.payment_id ORDER BY p.raw_row_id) AS rn
    FROM staging.claim_payments p
    WHERE p.load_id = (SELECT load_id FROM _gms_load_context)
)
UPDATE staging.claim_payments p
SET record_status = 'duplicate'
FROM ranked x
WHERE p.stg_payment_row_id = x.stg_payment_row_id
  AND x.rn > 1;

INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action
)
SELECT
    p.load_id, sf.source_name, 'raw.claim_payment_csv', p.raw_row_id, p.payment_id,
    'DUP_EXACT', 'payment_id', 'warning', 'deduplicated'
FROM staging.claim_payments p
JOIN raw.claim_payment_csv r ON r.raw_row_id = p.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id = r.source_file_id
WHERE p.load_id = (SELECT load_id FROM _gms_load_context)
  AND p.record_status = 'duplicate';

-- ---------------------------------------------------------------------------
-- 5. Policy_Premium.csv
-- ---------------------------------------------------------------------------

INSERT INTO staging.policy_premiums (
    load_id, raw_row_id, premium_id, policy_id, customer_id, premium_amount,
    premium_frequency, age_band, due_date, paid_date, payment_status, payment_method
)
SELECT
    sf.load_id,
    r.raw_row_id,
    staging.normalize_id(r.premium_id),
    staging.normalize_id(r.policy_id),
    staging.normalize_id(r.customer_id),
    staging.parse_amount(r.premium_amount),
    staging.normalize_category(r.premium_frequency),
    staging.clean_text(r.age_band),
    staging.parse_date(r.due_date, 'MDY'),
    staging.parse_date(r.paid_date, 'MDY'),
    staging.normalize_category(r.payment_status),
    staging.normalize_category(r.payment_method)
FROM raw.policy_premium_csv r
JOIN raw.source_files sf USING (source_file_id)
WHERE sf.load_id = (SELECT load_id FROM _gms_load_context);

-- payment_status is derivable from dates; repair only when both dates support a
-- deterministic answer. Missed remains missed when paid_date is NULL.
UPDATE staging.policy_premiums
SET payment_status = CASE
    WHEN paid_date IS NULL THEN payment_status
    WHEN paid_date <= due_date THEN 'paid'
    ELSE 'late'
END
WHERE load_id = (SELECT load_id FROM _gms_load_context)
  AND due_date IS NOT NULL;

INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value
)
SELECT
    p.load_id, sf.source_name, 'raw.policy_premium_csv', r.raw_row_id, p.premium_id,
    v.rule_id, v.field_name,
    CASE WHEN v.rule_id = 'INV_DERIVED_STATUS' THEN 'warning' ELSE 'info' END,
    CASE WHEN v.rule_id = 'INV_DERIVED_STATUS' THEN 'repaired' ELSE 'normalized' END,
    v.original_value, v.clean_value
FROM staging.policy_premiums p
JOIN raw.policy_premium_csv r ON r.raw_row_id = p.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id = r.source_file_id
CROSS JOIN LATERAL (
    VALUES
      ('FMT_ID','policy_id',r.policy_id,p.policy_id),
      ('FMT_ID','customer_id',r.customer_id,p.customer_id),
      ('TYPE_AMOUNT_TEXT','premium_amount',r.premium_amount,p.premium_amount::text),
      ('FMT_CATEGORY_CASE','premium_frequency',r.premium_frequency,p.premium_frequency),
      ('FMT_DATE','due_date',r.due_date,p.due_date::text),
      ('FMT_DATE','paid_date',r.paid_date,p.paid_date::text),
      (
        CASE
            WHEN staging.normalize_category(r.payment_status) IS DISTINCT FROM p.payment_status
            THEN 'INV_DERIVED_STATUS'
            ELSE 'FMT_CATEGORY_CASE'
        END,
        'payment_status',r.payment_status,p.payment_status
      ),
      ('FMT_CATEGORY_CASE','payment_method',r.payment_method,p.payment_method)
) AS v(rule_id, field_name, original_value, clean_value)
WHERE p.load_id = (SELECT load_id FROM _gms_load_context)
  AND v.original_value IS DISTINCT FROM v.clean_value
  AND NOT (v.original_value IS NULL AND v.clean_value IS NULL);

WITH ranked AS (
    SELECT
        p.stg_premium_row_id,
        row_number() OVER (PARTITION BY p.premium_id ORDER BY p.raw_row_id) AS rn
    FROM staging.policy_premiums p
    WHERE p.load_id = (SELECT load_id FROM _gms_load_context)
)
UPDATE staging.policy_premiums p
SET record_status = 'duplicate'
FROM ranked x
WHERE p.stg_premium_row_id = x.stg_premium_row_id
  AND x.rn > 1;

INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action
)
SELECT
    p.load_id, sf.source_name, 'raw.policy_premium_csv', p.raw_row_id, p.premium_id,
    'DUP_EXACT', 'premium_id', 'warning', 'deduplicated'
FROM staging.policy_premiums p
JOIN raw.policy_premium_csv r ON r.raw_row_id = p.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id = r.source_file_id
WHERE p.load_id = (SELECT load_id FROM _gms_load_context)
  AND p.record_status = 'duplicate';

-- ---------------------------------------------------------------------------
-- 6. JSON claim details: normalize recoverable schema drift and flatten.
-- ---------------------------------------------------------------------------

INSERT INTO staging.claim_details (
    load_id, raw_file_id, schema_version, claim_id, product_line,
    submission_channel, submitted_at, provider_id, provider_name, provider_type,
    adjuster_id, adjuster_notes, documents_submitted,
    practitioner_type, number_of_visits, prescription_din,
    prescription_drug_name, prescription_days_supply,
    prescription_is_generic, dental_claim_category,
    trip_start, trip_end, destination_country, incident_type, incident_date,
    currency, exchange_rate_to_cad, incident_sub_limit
)
SELECT
    sf.load_id,
    f.source_file_id,
    staging.clean_text(f.payload_jsonb->>'schema_version'),
    staging.normalize_id(COALESCE(f.payload_jsonb->>'claim_id', f.payload_jsonb->>'ClaimID')),
    staging.normalize_category(f.payload_jsonb->>'product_line'),
    staging.normalize_category(f.payload_jsonb->>'submission_channel'),
    staging.parse_timestamptz(f.payload_jsonb->>'submitted_at'),
    staging.normalize_id(f.payload_jsonb #>> '{provider,provider_id}'),
    staging.clean_text(f.payload_jsonb #>> '{provider,name}'),
    staging.normalize_category(f.payload_jsonb #>> '{provider,type}'),
    staging.normalize_id(f.payload_jsonb->>'adjuster_id'),
    staging.clean_text(f.payload_jsonb->>'adjuster_notes'),
    staging.json_text_array(f.payload_jsonb->'documents_submitted'),
    staging.normalize_category(f.payload_jsonb #>> '{health,practitioner_type}'),
    staging.parse_integer(f.payload_jsonb #>> '{health,number_of_visits}'),
    staging.clean_text(f.payload_jsonb #>> '{health,prescription,din}'),
    staging.clean_text(f.payload_jsonb #>> '{health,prescription,drug_name}'),
    staging.parse_integer(f.payload_jsonb #>> '{health,prescription,days_supply}'),
    staging.parse_boolean(f.payload_jsonb #>> '{health,prescription,is_generic}'),
    staging.normalize_category(f.payload_jsonb #>> '{dental,claim_category}'),
    staging.parse_date(f.payload_jsonb #>> '{travel,trip_start}'),
    staging.parse_date(f.payload_jsonb #>> '{travel,trip_end}'),
    upper(staging.clean_text(f.payload_jsonb #>> '{travel,destination_country}')),
    staging.normalize_category(f.payload_jsonb #>> '{travel,incident_type}'),
    staging.parse_date(f.payload_jsonb #>> '{travel,incident_date}'),
    upper(staging.clean_text(f.payload_jsonb #>> '{travel,currency}')),
    staging.parse_amount(f.payload_jsonb #>> '{travel,exchange_rate_to_cad}'),
    staging.parse_amount(f.payload_jsonb #>> '{travel,incident_sub_limit}')
FROM raw.claim_detail_files f
JOIN raw.source_files sf USING (source_file_id)
WHERE sf.load_id = (SELECT load_id FROM _gms_load_context)
  AND f.parse_status = 'parsed';

WITH payloads AS (
    SELECT
        sf.load_id,
        f.source_file_id AS raw_file_id,
        staging.normalize_id(COALESCE(f.payload_jsonb->>'claim_id', f.payload_jsonb->>'ClaimID')) AS claim_id,
        CASE
            WHEN jsonb_typeof(COALESCE(f.payload_jsonb->'line_items', f.payload_jsonb->'lineItems')) = 'array'
                THEN COALESCE(f.payload_jsonb->'line_items', f.payload_jsonb->'lineItems')
            WHEN jsonb_typeof(COALESCE(f.payload_jsonb->'line_items', f.payload_jsonb->'lineItems')) = 'object'
                THEN jsonb_build_array(COALESCE(f.payload_jsonb->'line_items', f.payload_jsonb->'lineItems'))
            ELSE '[]'::jsonb
        END AS items
    FROM raw.claim_detail_files f
    JOIN raw.source_files sf USING (source_file_id)
    WHERE sf.load_id = (SELECT load_id FROM _gms_load_context)
      AND f.parse_status = 'parsed'
)
INSERT INTO staging.claim_line_items (
    load_id, raw_file_id, source_array_index, claim_id, line_no,
    service_date, description, quantity, unit_amount, amount,
    procedure_code, procedure_category, tooth_number
)
SELECT
    p.load_id,
    p.raw_file_id,
    (item.ordinality - 1)::integer,
    p.claim_id,
    staging.parse_integer(item.value->>'line_no'),
    staging.parse_date(item.value->>'service_date'),
    staging.clean_text(item.value->>'description'),
    staging.parse_integer(item.value->>'quantity'),
    staging.parse_amount(item.value->>'unit_amount'),
    staging.parse_amount(item.value->>'amount'),
    staging.clean_text(item.value->>'procedure_code'),
    staging.normalize_category(item.value->>'procedure_category'),
    staging.parse_integer(item.value->>'tooth_number')
FROM payloads p
CROSS JOIN LATERAL jsonb_array_elements(p.items)
    WITH ORDINALITY AS item(value, ordinality);

-- JSON schema drift and missing-key audit.
INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value, details
)
SELECT
    sf.load_id,
    sf.source_name,
    'raw.claim_detail_files',
    f.source_file_id,
    d.claim_id,
    x.rule_id,
    x.field_name,
    x.severity,
    x.action,
    x.original_value,
    x.clean_value,
    x.details
FROM raw.claim_detail_files f
JOIN raw.source_files sf USING (source_file_id)
LEFT JOIN staging.claim_details d
  ON d.raw_file_id = f.source_file_id
 AND d.load_id = sf.load_id
CROSS JOIN LATERAL (
    VALUES
      (
        'JSON_KEY_DRIFT', 'claim_id', 'warning', 'normalized',
        CASE WHEN f.payload_jsonb ? 'ClaimID' THEN 'ClaimID' ELSE NULL END,
        CASE WHEN f.payload_jsonb ? 'ClaimID' THEN 'claim_id' ELSE NULL END,
        CASE WHEN f.payload_jsonb ? 'ClaimID'
             THEN jsonb_build_object('legacy_key','ClaimID') ELSE NULL END
      ),
      (
        'JSON_KEY_DRIFT', 'line_items', 'warning', 'normalized',
        CASE WHEN f.payload_jsonb ? 'lineItems' THEN 'lineItems' ELSE NULL END,
        CASE WHEN f.payload_jsonb ? 'lineItems' THEN 'line_items' ELSE NULL END,
        CASE WHEN f.payload_jsonb ? 'lineItems'
             THEN jsonb_build_object('legacy_key','lineItems') ELSE NULL END
      ),
      (
        'JSON_SHAPE_DRIFT', 'documents_submitted', 'warning', 'normalized',
        jsonb_typeof(f.payload_jsonb->'documents_submitted'),
        CASE WHEN jsonb_typeof(f.payload_jsonb->'documents_submitted') = 'string'
             THEN 'array' ELSE NULL END,
        CASE WHEN jsonb_typeof(f.payload_jsonb->'documents_submitted') = 'string'
             THEN jsonb_build_object('source_shape','string','target_shape','array') ELSE NULL END
      ),
      (
        'JSON_SHAPE_DRIFT', 'line_items', 'warning', 'normalized',
        jsonb_typeof(COALESCE(f.payload_jsonb->'line_items', f.payload_jsonb->'lineItems')),
        CASE WHEN jsonb_typeof(COALESCE(f.payload_jsonb->'line_items', f.payload_jsonb->'lineItems')) = 'object'
             THEN 'array' ELSE NULL END,
        CASE WHEN jsonb_typeof(COALESCE(f.payload_jsonb->'line_items', f.payload_jsonb->'lineItems')) = 'object'
             THEN jsonb_build_object('source_shape','object','target_shape','array') ELSE NULL END
      ),
      (
        'JSON_MISSING_KEY', 'submission_channel', 'error', 'flagged',
        NULL, NULL,
        CASE WHEN NOT (f.payload_jsonb ? 'submission_channel')
             THEN jsonb_build_object('key_absent',true) ELSE NULL END
      ),
      (
        'JSON_MISSING_KEY', 'documents_submitted', 'error', 'flagged',
        NULL, NULL,
        CASE WHEN NOT (f.payload_jsonb ? 'documents_submitted')
             THEN jsonb_build_object('key_absent',true) ELSE NULL END
      ),
      (
        'JSON_MISSING_KEY', 'provider.type', 'error', 'flagged',
        NULL, NULL,
        CASE WHEN NOT ((f.payload_jsonb->'provider') ? 'type')
             THEN jsonb_build_object('key_absent',true) ELSE NULL END
      ),
      (
        'JSON_MISSING_KEY', 'adjuster_id', 'warning', 'flagged',
        NULL, NULL,
        CASE WHEN NOT (f.payload_jsonb ? 'adjuster_id')
             THEN jsonb_build_object('key_absent',true) ELSE NULL END
      )
) AS x(rule_id, field_name, severity, action, original_value, clean_value, details)
WHERE sf.load_id = (SELECT load_id FROM _gms_load_context)
  AND f.parse_status = 'parsed'
  AND x.details IS NOT NULL;

-- Malformed JSON is intentionally retained in raw, logged, and quarantined.
INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, severity, action, original_value, details
)
SELECT
    sf.load_id, sf.source_name, 'raw.claim_detail_files', f.source_file_id,
    replace(sf.source_name, '.json', ''),
    'JSON_MALFORMED', 'error', 'quarantined', left(f.raw_text, 500),
    jsonb_build_object('parse_error', f.parse_error)
FROM raw.claim_detail_files f
JOIN raw.source_files sf USING (source_file_id)
WHERE sf.load_id = (SELECT load_id FROM _gms_load_context)
  AND f.parse_status = 'malformed';

INSERT INTO staging.quarantine (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, reason, details
)
SELECT
    sf.load_id, sf.source_name, 'raw.claim_detail_files', f.source_file_id,
    replace(sf.source_name, '.json', ''),
    'JSON_MALFORMED', 'JSON could not be parsed',
    jsonb_build_object('parse_error', f.parse_error)
FROM raw.claim_detail_files f
JOIN raw.source_files sf USING (source_file_id)
WHERE sf.load_id = (SELECT load_id FROM _gms_load_context)
  AND f.parse_status = 'malformed';

-- ---------------------------------------------------------------------------
-- 7. Basic validation/orphan handling after all staging sources exist.
-- ---------------------------------------------------------------------------

-- Policies: normalized owner must resolve to a non-rejected customer.
UPDATE staging.policies p
SET record_status = 'rejected'
WHERE p.load_id = (SELECT load_id FROM _gms_load_context)
  AND p.record_status <> 'duplicate'
  AND (
      p.policy_id IS NULL OR p.policy_id !~ '^P[0-9]{5}$'
      OR p.customer_id IS NULL
      OR p.policy_type NOT IN ('health','dental','travel')
      OR p.coverage_type NOT IN ('single','couple','family')
      OR p.start_date IS NULL
      OR (p.end_date IS NOT NULL AND p.end_date < p.start_date)
      OR p.status NOT IN ('active','lapsed','cancelled','expired')
      OR p.coverage_amount IS NULL OR p.coverage_amount <= 0
      OR p.deductible_amount IS NULL OR p.deductible_amount < 0
      OR NOT EXISTS (
          SELECT 1
          FROM staging.customer_identity_map m
          WHERE m.load_id = p.load_id
            AND m.source_customer_id = p.customer_id
      )
  );

UPDATE staging.policies
SET record_status = 'accepted'
WHERE load_id = (SELECT load_id FROM _gms_load_context)
  AND record_status = 'candidate';

-- Claims: preserve recoverable missing service_date / amount for core
-- reconciliation against JSON, but reject invalid keys, type/domain, or orphan
-- policy rows. Duplicate rows were already marked above.
UPDATE staging.claims c
SET record_status = 'rejected'
WHERE c.load_id = (SELECT load_id FROM _gms_load_context)
  AND c.record_status <> 'duplicate'
  AND (
      c.claim_id IS NULL OR c.claim_id !~ '^[HDT][0-9]{5}$'
      OR c.policy_id IS NULL
      OR c.claim_type NOT IN (
          'prescription_drugs','health_practitioner','vision','hearing_aids',
          'medical_equipment','ambulance','hospital_cash','preventive','basic',
          'major','emergency_medical','trip_cancellation','trip_interruption','baggage'
      )
      OR c.claim_date IS NULL
      OR NOT EXISTS (
          SELECT 1 FROM staging.policies p
          WHERE p.load_id = c.load_id
            AND p.policy_id = c.policy_id
            AND p.record_status = 'accepted'
      )
  );

UPDATE staging.claims
SET record_status = 'accepted'
WHERE load_id = (SELECT load_id FROM _gms_load_context)
  AND record_status = 'candidate';

-- Payments: orphan references, unrecoverable required fields, and impossible
-- internal date order are rejected here.
UPDATE staging.claim_payments p
SET record_status = 'rejected'
WHERE p.load_id = (SELECT load_id FROM _gms_load_context)
  AND p.record_status <> 'duplicate'
  AND (
      p.payment_id IS NULL OR p.payment_id !~ '^PAY[0-9]{5}$'
      OR p.claim_id IS NULL
      OR p.decision_outcome NOT IN ('approved','partially_approved','denied')
      OR p.payment_amount IS NULL OR p.payment_amount < 0
      OR NOT EXISTS (
          SELECT 1 FROM staging.claims c
          WHERE c.load_id = p.load_id
            AND c.claim_id = p.claim_id
            AND c.record_status = 'accepted'
      )
  );

UPDATE staging.claim_payments
SET record_status = 'accepted'
WHERE load_id = (SELECT load_id FROM _gms_load_context)
  AND record_status = 'candidate';

-- A corrupt operational date must not erase an otherwise valid adjudication
-- decision. Keep the row accepted, log the defect, and let core null the bad
-- decision_date while retaining Claim_Payment.decision_outcome as authoritative.
INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value, details
)
SELECT
    p.load_id, sf.source_name, 'raw.claim_payment_csv', p.raw_row_id, p.payment_id,
    'INV_DATE_ORDER', 'decision_date', 'error', 'flagged',
    r.decision_date, NULL,
    jsonb_build_object(
        'processing_start_date', p.processing_start_date,
        'decision_date', p.decision_date,
        'core_treatment', 'retain decision row and null invalid decision_date'
    )
FROM staging.claim_payments p
JOIN raw.claim_payment_csv r ON r.raw_row_id = p.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id = r.source_file_id
WHERE p.load_id = (SELECT load_id FROM _gms_load_context)
  AND p.record_status = 'accepted'
  AND p.processing_start_date IS NOT NULL
  AND p.decision_date IS NOT NULL
  AND p.decision_date < p.processing_start_date;

-- Premiums: duplicate rows are already excluded. Rows that cannot satisfy the
-- canonical premium semantics are rejected.
UPDATE staging.policy_premiums p
SET record_status = 'rejected'
WHERE p.load_id = (SELECT load_id FROM _gms_load_context)
  AND p.record_status <> 'duplicate'
  AND (
      p.premium_id IS NULL OR p.premium_id !~ '^PRM[0-9]{6}$'
      OR p.policy_id IS NULL
      OR p.premium_amount IS NULL OR p.premium_amount <= 0
      OR p.premium_frequency NOT IN ('monthly','quarterly','yearly','single')
      OR p.age_band NOT IN ('Under 35','35-44','45-54','55-64','65-74','75+')
      OR p.due_date IS NULL
      OR p.payment_status NOT IN ('paid','late','missed')
      OR (p.payment_status IN ('paid','late') AND p.paid_date IS NULL)
      OR (p.payment_status = 'missed' AND p.paid_date IS NOT NULL)
      OR NOT EXISTS (
          SELECT 1 FROM staging.policies pol
          WHERE pol.load_id = p.load_id
            AND pol.policy_id = p.policy_id
            AND pol.record_status = 'accepted'
      )
  );

UPDATE staging.policy_premiums
SET record_status = 'accepted'
WHERE load_id = (SELECT load_id FROM _gms_load_context)
  AND record_status = 'candidate';

-- JSON details: Claim.csv owns claim existence, so orphan JSON is rejected.
UPDATE staging.claim_details d
SET record_status = 'rejected'
WHERE d.load_id = (SELECT load_id FROM _gms_load_context)
  AND (
      d.claim_id IS NULL OR d.claim_id !~ '^[HDT][0-9]{5}$'
      OR d.product_line NOT IN ('health','dental','travel')
      OR d.submitted_at IS NULL
      OR d.provider_id IS NULL
      OR d.provider_name IS NULL
      OR NOT EXISTS (
          SELECT 1 FROM staging.claims c
          WHERE c.load_id = d.load_id
            AND c.claim_id = d.claim_id
            AND c.record_status = 'accepted'
      )
  );

UPDATE staging.claim_details
SET record_status = 'accepted'
WHERE load_id = (SELECT load_id FROM _gms_load_context)
  AND record_status = 'candidate';

-- DQ log + quarantine for every rejected row caused by validation/orphan checks.
INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, severity, action, details
)
SELECT
    p.load_id, sf.source_name, 'raw.policy_csv', p.raw_row_id, p.policy_id,
    CASE
      WHEN NOT EXISTS (
          SELECT 1 FROM staging.customers c
          WHERE c.load_id=p.load_id AND c.customer_id=p.customer_id
      ) THEN 'ORPHAN_FK'
      WHEN NOT EXISTS (
          SELECT 1 FROM staging.customer_identity_map m
          WHERE m.load_id=p.load_id AND m.source_customer_id=p.customer_id
      ) THEN 'UPSTREAM_PARENT_REJECTED'
      WHEN p.end_date IS NOT NULL AND p.start_date IS NOT NULL AND p.end_date < p.start_date
        THEN 'INV_DATE_ORDER'
      ELSE 'INVALID_REQUIRED'
    END,
    'error', 'quarantined',
    jsonb_build_object('customer_id',p.customer_id)
FROM staging.policies p
JOIN raw.policy_csv r ON r.raw_row_id=p.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id=r.source_file_id
WHERE p.load_id=(SELECT load_id FROM _gms_load_context)
  AND p.record_status='rejected'

UNION ALL

SELECT
    c.load_id, sf.source_name, 'raw.claim_csv', c.raw_row_id, c.claim_id,
    CASE
      WHEN NOT EXISTS (
          SELECT 1 FROM staging.policies p
          WHERE p.load_id=c.load_id AND p.policy_id=c.policy_id
      ) THEN 'ORPHAN_FK'
      WHEN NOT EXISTS (
          SELECT 1 FROM staging.policies p
          WHERE p.load_id=c.load_id AND p.policy_id=c.policy_id AND p.record_status='accepted'
      ) THEN 'UPSTREAM_PARENT_REJECTED'
      ELSE 'INVALID_REQUIRED'
    END,
    'error', 'quarantined',
    jsonb_build_object('policy_id',c.policy_id)
FROM staging.claims c
JOIN raw.claim_csv r ON r.raw_row_id=c.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id=r.source_file_id
WHERE c.load_id=(SELECT load_id FROM _gms_load_context)
  AND c.record_status='rejected'

UNION ALL

SELECT
    p.load_id, sf.source_name, 'raw.claim_payment_csv', p.raw_row_id, p.payment_id,
    CASE
      WHEN NOT EXISTS (
          SELECT 1 FROM staging.claims c
          WHERE c.load_id=p.load_id AND c.claim_id=p.claim_id
      ) THEN 'ORPHAN_FK'
      WHEN NOT EXISTS (
          SELECT 1 FROM staging.claims c
          WHERE c.load_id=p.load_id AND c.claim_id=p.claim_id AND c.record_status='accepted'
      ) THEN 'UPSTREAM_PARENT_REJECTED'
      WHEN p.processing_start_date IS NOT NULL AND p.decision_date IS NOT NULL
           AND p.decision_date < p.processing_start_date THEN 'INV_DATE_ORDER'
      ELSE 'INVALID_REQUIRED'
    END,
    'error', 'quarantined',
    jsonb_build_object('claim_id',p.claim_id)
FROM staging.claim_payments p
JOIN raw.claim_payment_csv r ON r.raw_row_id=p.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id=r.source_file_id
WHERE p.load_id=(SELECT load_id FROM _gms_load_context)
  AND p.record_status='rejected'

UNION ALL

SELECT
    p.load_id, sf.source_name, 'raw.policy_premium_csv', p.raw_row_id, p.premium_id,
    CASE
      WHEN NOT EXISTS (
          SELECT 1 FROM staging.policies pol
          WHERE pol.load_id=p.load_id AND pol.policy_id=p.policy_id
      ) THEN 'ORPHAN_FK'
      WHEN NOT EXISTS (
          SELECT 1 FROM staging.policies pol
          WHERE pol.load_id=p.load_id AND pol.policy_id=p.policy_id AND pol.record_status='accepted'
      ) THEN 'UPSTREAM_PARENT_REJECTED'
      ELSE 'INVALID_REQUIRED'
    END,
    'error', 'quarantined',
    jsonb_build_object('policy_id',p.policy_id)
FROM staging.policy_premiums p
JOIN raw.policy_premium_csv r ON r.raw_row_id=p.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id=r.source_file_id
WHERE p.load_id=(SELECT load_id FROM _gms_load_context)
  AND p.record_status='rejected'

UNION ALL

SELECT
    d.load_id, sf.source_name, 'raw.claim_detail_files', d.raw_file_id, d.claim_id,
    CASE
      WHEN d.claim_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM staging.claims c
          WHERE c.load_id=d.load_id AND c.claim_id=d.claim_id
      ) THEN 'JSON_ORPHAN'
      WHEN d.claim_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM staging.claims c
          WHERE c.load_id=d.load_id AND c.claim_id=d.claim_id AND c.record_status='accepted'
      ) THEN 'UPSTREAM_PARENT_REJECTED'
      ELSE 'JSON_INVALID_REQUIRED'
    END,
    'error', 'quarantined',
    jsonb_build_object('claim_id',d.claim_id)
FROM staging.claim_details d
JOIN raw.source_files sf ON sf.source_file_id=d.raw_file_id
WHERE d.load_id=(SELECT load_id FROM _gms_load_context)
  AND d.record_status='rejected';

INSERT INTO staging.quarantine (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, reason, details
)
SELECT
    q.load_id, q.source_name, q.source_table, q.source_record_id, q.business_key,
    q.rule_id, 'Rejected during staging validation', q.details
FROM staging.data_quality_log q
WHERE q.load_id = (SELECT load_id FROM _gms_load_context)
  AND q.action = 'quarantined'
  AND q.rule_id <> 'JSON_MALFORMED'
  AND NOT EXISTS (
      SELECT 1 FROM staging.quarantine x
      WHERE x.load_id=q.load_id
        AND x.source_table=q.source_table
        AND x.source_record_id IS NOT DISTINCT FROM q.source_record_id
        AND x.rule_id=q.rule_id
  );

-- Missing required values retained for later repair/reconciliation.
INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, details
)
SELECT c.load_id, sf.source_name, 'raw.claim_csv', c.raw_row_id, c.claim_id,
       'MISS_ERROR', 'service_date', 'error', 'flagged',
       jsonb_build_object('repair_candidate','JSON min(line_items.service_date)')
FROM staging.claims c
JOIN raw.claim_csv r ON r.raw_row_id=c.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id=r.source_file_id
WHERE c.load_id=(SELECT load_id FROM _gms_load_context)
  AND c.record_status='accepted'
  AND c.service_date IS NULL

UNION ALL

SELECT c.load_id, sf.source_name, 'raw.claim_csv', c.raw_row_id, c.claim_id,
       'MISS_ERROR', 'claim_amount', 'error', 'flagged',
       jsonb_build_object('repair_candidate','JSON reconciled line-item total')
FROM staging.claims c
JOIN raw.claim_csv r ON r.raw_row_id=c.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id=r.source_file_id
WHERE c.load_id=(SELECT load_id FROM _gms_load_context)
  AND c.record_status='accepted'
  AND c.claim_amount IS NULL

UNION ALL

SELECT p.load_id, sf.source_name, 'raw.claim_payment_csv', p.raw_row_id, p.payment_id,
       'MISS_ERROR', 'decision_date', 'error', 'flagged',
       jsonb_build_object('reason','decided payment row is missing decision_date')
FROM staging.claim_payments p
JOIN raw.claim_payment_csv r ON r.raw_row_id=p.raw_row_id
JOIN raw.source_files sf ON sf.source_file_id=r.source_file_id
WHERE p.load_id=(SELECT load_id FROM _gms_load_context)
  AND p.record_status='accepted'
  AND p.decision_date IS NULL;

-- JSON orphan line items remain staged for audit but will never enter core.
-- Missing JSON files are a cross-source completeness issue and are logged here.
INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, severity, action, details
)
SELECT
    c.load_id, 'Claim.csv / JSON directory', 'staging.claims',
    c.raw_row_id, c.claim_id, 'JSON_MISSING_FILE', 'warning', 'flagged',
    jsonb_build_object('reason','no parseable JSON detail exists for the claim')
FROM staging.claims c
WHERE c.load_id=(SELECT load_id FROM _gms_load_context)
  AND c.record_status='accepted'
  AND NOT EXISTS (
      SELECT 1 FROM staging.claim_details d
      WHERE d.load_id=c.load_id
        AND d.claim_id=c.claim_id
  );

-- ---------------------------------------------------------------------------
-- 8. Issue counts on staged source rows.
-- ---------------------------------------------------------------------------

UPDATE staging.customers c
SET dq_issue_count = x.issue_count
FROM (
    SELECT source_record_id, count(*)::integer AS issue_count
    FROM staging.data_quality_log
    WHERE load_id=(SELECT load_id FROM _gms_load_context)
      AND source_table='raw.customer_csv'
    GROUP BY source_record_id
) x
WHERE c.load_id=(SELECT load_id FROM _gms_load_context)
  AND c.raw_row_id=x.source_record_id;

UPDATE staging.policies p
SET dq_issue_count = x.issue_count
FROM (
    SELECT source_record_id, count(*)::integer AS issue_count
    FROM staging.data_quality_log
    WHERE load_id=(SELECT load_id FROM _gms_load_context)
      AND source_table='raw.policy_csv'
    GROUP BY source_record_id
) x
WHERE p.load_id=(SELECT load_id FROM _gms_load_context)
  AND p.raw_row_id=x.source_record_id;

UPDATE staging.claims c
SET dq_issue_count = x.issue_count
FROM (
    SELECT source_record_id, count(*)::integer AS issue_count
    FROM staging.data_quality_log
    WHERE load_id=(SELECT load_id FROM _gms_load_context)
      AND source_table='raw.claim_csv'
    GROUP BY source_record_id
) x
WHERE c.load_id=(SELECT load_id FROM _gms_load_context)
  AND c.raw_row_id=x.source_record_id;

UPDATE staging.claim_payments p
SET dq_issue_count = x.issue_count
FROM (
    SELECT source_record_id, count(*)::integer AS issue_count
    FROM staging.data_quality_log
    WHERE load_id=(SELECT load_id FROM _gms_load_context)
      AND source_table='raw.claim_payment_csv'
    GROUP BY source_record_id
) x
WHERE p.load_id=(SELECT load_id FROM _gms_load_context)
  AND p.raw_row_id=x.source_record_id;

UPDATE staging.policy_premiums p
SET dq_issue_count = x.issue_count
FROM (
    SELECT source_record_id, count(*)::integer AS issue_count
    FROM staging.data_quality_log
    WHERE load_id=(SELECT load_id FROM _gms_load_context)
      AND source_table='raw.policy_premium_csv'
    GROUP BY source_record_id
) x
WHERE p.load_id=(SELECT load_id FROM _gms_load_context)
  AND p.raw_row_id=x.source_record_id;

UPDATE staging.claim_details d
SET dq_issue_count = x.issue_count
FROM (
    SELECT source_record_id, count(*)::integer AS issue_count
    FROM staging.data_quality_log
    WHERE load_id=(SELECT load_id FROM _gms_load_context)
      AND source_table='raw.claim_detail_files'
    GROUP BY source_record_id
) x
WHERE d.load_id=(SELECT load_id FROM _gms_load_context)
  AND d.raw_file_id=x.source_record_id;
