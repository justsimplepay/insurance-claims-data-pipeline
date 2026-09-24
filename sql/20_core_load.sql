-- GMS Data Engineer Case Study
-- Recurring staging -> core integration for one load.
--
-- The Python wrapper replaces __LOAD_ID__ with an integer. The complete script
-- runs inside one transaction. Core is the canonical relational state:
-- source-of-truth rules are applied here and non-authoritative denormalized
-- copies (claim customer/region/status, premium customer) are deliberately not
-- stored in core.

DROP TABLE IF EXISTS pg_temp._gms_core_context;
CREATE TEMP TABLE _gms_core_context (
    load_id bigint PRIMARY KEY
) ON COMMIT DROP;

INSERT INTO _gms_core_context(load_id) VALUES (__LOAD_ID__);

DO $$
DECLARE
    v_load_id bigint := (SELECT load_id FROM _gms_core_context);
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM raw.ingestion_runs
        WHERE load_id = v_load_id
          AND status = 'succeeded'
    ) THEN
        RAISE EXCEPTION 'load_id % is not a successful raw ingestion run', v_load_id;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM staging.customers WHERE load_id = v_load_id
    ) THEN
        RAISE EXCEPTION 'staging has not been built for load_id %', v_load_id;
    END IF;
END $$;

-- Core-specific DQ events are rebuilt on rerun of the same load.
DELETE FROM staging.data_quality_log
WHERE load_id = (SELECT load_id FROM _gms_core_context)
  AND rule_id LIKE 'CORE_%';

-- ---------------------------------------------------------------------------
-- 1. Canonical customers.
-- ---------------------------------------------------------------------------

INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value, details
)
SELECT
    c.load_id,
    'Customer.csv',
    'staging.customers',
    c.raw_row_id,
    c.customer_id,
    'CORE_DOB_NULLIFIED',
    'date_of_birth',
    'error',
    'repaired',
    c.date_of_birth::text,
    NULL,
    jsonb_build_object(
        'reason','DOB is outside the valid extract-end age range (18-89) and no authoritative alternate source exists'
    )
FROM staging.customers c
WHERE c.load_id = (SELECT load_id FROM _gms_core_context)
  AND c.record_status = 'accepted'
  AND c.date_of_birth IS NOT NULL
  AND c.date_of_birth NOT BETWEEN DATE '1936-07-01' AND DATE '2008-06-30';

INSERT INTO core.customers (
    customer_id, first_name, last_name, date_of_birth, gender, address, city,
    province, postal_code, phone, email, customer_since, last_updated,
    source_load_id, source_raw_row_id
)
SELECT
    c.customer_id,
    c.first_name,
    c.last_name,
    CASE
        WHEN c.date_of_birth BETWEEN DATE '1936-07-01' AND DATE '2008-06-30'
            THEN c.date_of_birth
        ELSE NULL
    END,
    CASE WHEN c.gender IN ('F','M','X') THEN c.gender ELSE NULL END,
    c.address,
    c.city,
    c.province,
    c.postal_code,
    CASE WHEN c.phone ~ '^\+1[0-9]{10}$' THEN c.phone ELSE NULL END,
    c.email,
    c.customer_since,
    c.last_updated,
    c.load_id,
    c.raw_row_id
FROM staging.customers c
WHERE c.load_id = (SELECT load_id FROM _gms_core_context)
  AND c.record_status = 'accepted'
ON CONFLICT (customer_id) DO UPDATE
SET first_name = EXCLUDED.first_name,
    last_name = EXCLUDED.last_name,
    date_of_birth = EXCLUDED.date_of_birth,
    gender = EXCLUDED.gender,
    address = EXCLUDED.address,
    city = EXCLUDED.city,
    province = EXCLUDED.province,
    postal_code = EXCLUDED.postal_code,
    phone = EXCLUDED.phone,
    email = EXCLUDED.email,
    customer_since = EXCLUDED.customer_since,
    last_updated = EXCLUDED.last_updated,
    source_load_id = EXCLUDED.source_load_id,
    source_raw_row_id = EXCLUDED.source_raw_row_id,
    updated_at = now();

-- ---------------------------------------------------------------------------
-- 2. Policies. Policy.csv owns policy -> customer.
-- Duplicate-customer aliases are re-keyed through customer_identity_map.
-- ---------------------------------------------------------------------------

INSERT INTO core.policies (
    policy_id, customer_id, policy_type, plan_name, coverage_type,
    start_date, end_date, status, sales_channel, coverage_amount,
    deductible_amount, source_load_id, source_raw_row_id
)
SELECT
    p.policy_id,
    m.canonical_customer_id,
    p.policy_type,
    p.plan_name,
    p.coverage_type,
    p.start_date,
    p.end_date,
    p.status,
    p.sales_channel,
    p.coverage_amount,
    p.deductible_amount,
    p.load_id,
    p.raw_row_id
FROM staging.policies p
JOIN staging.customer_identity_map m
  ON m.load_id = p.load_id
 AND m.source_customer_id = p.customer_id
JOIN core.customers c
  ON c.customer_id = m.canonical_customer_id
WHERE p.load_id = (SELECT load_id FROM _gms_core_context)
  AND p.record_status = 'accepted'
ON CONFLICT (policy_id) DO UPDATE
SET customer_id = EXCLUDED.customer_id,
    policy_type = EXCLUDED.policy_type,
    plan_name = EXCLUDED.plan_name,
    coverage_type = EXCLUDED.coverage_type,
    start_date = EXCLUDED.start_date,
    end_date = EXCLUDED.end_date,
    status = EXCLUDED.status,
    sales_channel = EXCLUDED.sales_channel,
    coverage_amount = EXCLUDED.coverage_amount,
    deductible_amount = EXCLUDED.deductible_amount,
    source_load_id = EXCLUDED.source_load_id,
    source_raw_row_id = EXCLUDED.source_raw_row_id,
    updated_at = now();

-- ---------------------------------------------------------------------------
-- 3. Build claim reconciliation evidence.
-- ---------------------------------------------------------------------------

DROP TABLE IF EXISTS pg_temp._core_claim_recon;
CREATE TEMP TABLE _core_claim_recon ON COMMIT DROP AS
WITH json_evidence AS (
    SELECT
        d.load_id,
        d.claim_id,
        d.product_line,
        d.exchange_rate_to_cad,
        min(li.service_date) AS json_service_date,
        CASE
            WHEN d.product_line IN ('health','dental')
                THEN round(sum(li.amount), 2)
            WHEN d.product_line = 'travel' AND d.exchange_rate_to_cad IS NOT NULL
                THEN round(sum(li.amount) * d.exchange_rate_to_cad, 2)
            ELSE NULL
        END AS json_claim_amount
    FROM staging.claim_details d
    JOIN staging.claim_line_items li
      ON li.load_id = d.load_id
     AND li.raw_file_id = d.raw_file_id
    WHERE d.load_id = (SELECT load_id FROM _gms_core_context)
      AND d.record_status = 'accepted'
    GROUP BY d.load_id, d.claim_id, d.product_line, d.exchange_rate_to_cad
),
base AS (
    SELECT
        c.*,
        p.start_date AS policy_start_date,
        p.end_date AS policy_end_date,
        j.json_service_date,
        j.json_claim_amount
    FROM staging.claims c
    JOIN core.policies p ON p.policy_id = c.policy_id
    LEFT JOIN json_evidence j
      ON j.load_id = c.load_id
     AND j.claim_id = c.claim_id
    WHERE c.load_id = (SELECT load_id FROM _gms_core_context)
      AND c.record_status = 'accepted'
)
SELECT
    b.*,
    CASE
        WHEN b.service_date IS NOT NULL
         AND b.service_date >= b.policy_start_date
         AND (b.policy_end_date IS NULL OR b.service_date <= b.policy_end_date)
         AND b.service_date <= b.claim_date
            THEN b.service_date
        WHEN b.json_service_date IS NOT NULL
         AND b.json_service_date >= b.policy_start_date
         AND (b.policy_end_date IS NULL OR b.json_service_date <= b.policy_end_date)
         AND b.json_service_date <= b.claim_date
            THEN b.json_service_date
        ELSE NULL
    END AS core_service_date,
    CASE
        WHEN b.claim_amount IS NULL OR b.claim_amount <= 0
            THEN CASE WHEN b.json_claim_amount > 0 THEN b.json_claim_amount ELSE NULL END
        WHEN b.json_claim_amount > 0
         AND (
              b.claim_amount / b.json_claim_amount >= 10
              OR b.json_claim_amount / b.claim_amount >= 10
         )
            THEN b.json_claim_amount
        ELSE b.claim_amount
    END AS core_claim_amount
FROM base b;

-- Deterministic claim repairs.
INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value, details
)
SELECT
    r.load_id,
    'Claim.csv + JSON',
    'staging.claims',
    r.raw_row_id,
    r.claim_id,
    'CORE_SERVICE_DATE_REPAIRED',
    'service_date',
    'error',
    'repaired',
    r.service_date::text,
    r.core_service_date::text,
    jsonb_build_object('json_service_date', r.json_service_date)
FROM _core_claim_recon r
WHERE r.service_date IS DISTINCT FROM r.core_service_date
  AND r.core_service_date IS NOT NULL

UNION ALL

SELECT
    r.load_id,
    'Claim.csv + JSON',
    'staging.claims',
    r.raw_row_id,
    r.claim_id,
    'CORE_SERVICE_DATE_UNRECOVERABLE',
    'service_date',
    'error',
    'flagged',
    r.service_date::text,
    NULL,
    jsonb_build_object('json_service_date', r.json_service_date)
FROM _core_claim_recon r
WHERE r.core_service_date IS NULL

UNION ALL

SELECT
    r.load_id,
    'Claim.csv + JSON',
    'staging.claims',
    r.raw_row_id,
    r.claim_id,
    'CORE_CLAIM_AMOUNT_REPAIRED',
    'claim_amount',
    'error',
    'repaired',
    r.claim_amount::text,
    r.core_claim_amount::text,
    jsonb_build_object('json_claim_amount', r.json_claim_amount)
FROM _core_claim_recon r
WHERE r.claim_amount IS DISTINCT FROM r.core_claim_amount
  AND r.core_claim_amount IS NOT NULL

UNION ALL

SELECT
    r.load_id,
    'Claim.csv + JSON',
    'staging.claims',
    r.raw_row_id,
    r.claim_id,
    'CORE_CLAIM_AMOUNT_UNRECOVERABLE',
    'claim_amount',
    'error',
    'flagged',
    r.claim_amount::text,
    NULL,
    jsonb_build_object('json_claim_amount', r.json_claim_amount)
FROM _core_claim_recon r
WHERE r.core_claim_amount IS NULL

UNION ALL

SELECT
    r.load_id,
    'Claim.csv + JSON',
    'staging.claims',
    r.raw_row_id,
    r.claim_id,
    'CORE_AMOUNT_RECON_MISMATCH',
    'claim_amount',
    'warning',
    'flagged',
    r.claim_amount::text,
    r.json_claim_amount::text,
    jsonb_build_object(
        'difference_cad', round(r.claim_amount - r.json_claim_amount, 2),
        'treatment','retain valid Claim.csv header amount; mismatch remains an analytical/fraud signal'
    )
FROM _core_claim_recon r
WHERE r.claim_amount > 0
  AND r.json_claim_amount > 0
  AND r.core_claim_amount = r.claim_amount
  AND abs(r.claim_amount - r.json_claim_amount) > 0.01;

-- Cross-source owner and region copies are checked, then discarded from core.
INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value, details
)
SELECT
    c.load_id,
    'Claim.csv vs Policy.csv',
    'staging.claims',
    c.raw_row_id,
    c.claim_id,
    'CORE_OWNER_CONFLICT',
    'customer_id',
    'warning',
    'repaired',
    c.customer_id,
    p.customer_id,
    jsonb_build_object('authoritative_source','Policy.csv.customer_id')
FROM staging.claims c
JOIN staging.policies sp
  ON sp.load_id = c.load_id
 AND sp.policy_id = c.policy_id
 AND sp.record_status = 'accepted'
JOIN staging.customer_identity_map pm
  ON pm.load_id = sp.load_id
 AND pm.source_customer_id = sp.customer_id
LEFT JOIN staging.customer_identity_map cm
  ON cm.load_id = c.load_id
 AND cm.source_customer_id = c.customer_id
JOIN core.policies p ON p.policy_id = c.policy_id
WHERE c.load_id = (SELECT load_id FROM _gms_core_context)
  AND c.record_status = 'accepted'
  AND COALESCE(cm.canonical_customer_id, c.customer_id)
      IS DISTINCT FROM pm.canonical_customer_id

UNION ALL

SELECT
    c.load_id,
    'Claim.csv vs Customer.csv',
    'staging.claims',
    c.raw_row_id,
    c.claim_id,
    'CORE_REGION_CONFLICT',
    'region',
    'warning',
    'repaired',
    c.region,
    cu.province,
    jsonb_build_object('authoritative_source','Customer.csv.province')
FROM staging.claims c
JOIN core.policies p ON p.policy_id = c.policy_id
JOIN core.customers cu ON cu.customer_id = p.customer_id
WHERE c.load_id = (SELECT load_id FROM _gms_core_context)
  AND c.record_status = 'accepted'
  AND c.region IS DISTINCT FROM cu.province;

-- Load the canonical claim header. approved_amount is retained when it is
-- internally usable; otherwise it becomes unknown rather than breaking core.
INSERT INTO core.claims (
    claim_id, policy_id, claim_type, service_date, claim_date,
    claim_amount, approved_amount, source_load_id, source_raw_row_id
)
SELECT
    r.claim_id,
    r.policy_id,
    r.claim_type,
    r.core_service_date,
    r.claim_date,
    r.core_claim_amount,
    CASE
        WHEN r.approved_amount IS NULL THEN NULL
        WHEN r.approved_amount < 0 THEN NULL
        WHEN r.core_claim_amount IS NOT NULL AND r.approved_amount > r.core_claim_amount THEN NULL
        ELSE r.approved_amount
    END,
    r.load_id,
    r.raw_row_id
FROM _core_claim_recon r
ON CONFLICT (claim_id) DO UPDATE
SET policy_id = EXCLUDED.policy_id,
    claim_type = EXCLUDED.claim_type,
    service_date = EXCLUDED.service_date,
    claim_date = EXCLUDED.claim_date,
    claim_amount = EXCLUDED.claim_amount,
    approved_amount = EXCLUDED.approved_amount,
    source_load_id = EXCLUDED.source_load_id,
    source_raw_row_id = EXCLUDED.source_raw_row_id,
    updated_at = now();

-- ---------------------------------------------------------------------------
-- 4. Authoritative adjudication/payment records.
-- ---------------------------------------------------------------------------

DROP TABLE IF EXISTS pg_temp._core_payment_recon;
CREATE TEMP TABLE _core_payment_recon ON COMMIT DROP AS
SELECT
    p.*,
    c.claim_date,
    CASE
        WHEN p.decision_date IS NOT NULL
         AND p.processing_start_date IS NOT NULL
         AND p.decision_date < p.processing_start_date
            THEN NULL
        ELSE p.decision_date
    END AS core_decision_date
FROM staging.claim_payments p
JOIN core.claims c ON c.claim_id = p.claim_id
WHERE p.load_id = (SELECT load_id FROM _gms_core_context)
  AND p.record_status = 'accepted';

ALTER TABLE _core_payment_recon
ADD COLUMN core_payment_date date,
ADD COLUMN core_payment_status text;

UPDATE _core_payment_recon
SET core_payment_date = CASE
        WHEN payment_date IS NULL THEN NULL
        WHEN payment_date < claim_date THEN NULL
        WHEN core_decision_date IS NOT NULL AND payment_date < core_decision_date THEN NULL
        ELSE payment_date
    END;

UPDATE _core_payment_recon
SET core_payment_status = CASE
        WHEN payment_status = 'paid' AND core_payment_date IS NULL THEN NULL
        ELSE payment_status
    END;

-- Claim status is a non-authoritative copy. A missing payment row means pending.
INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value, details
)
SELECT
    c.load_id,
    'Claim.csv vs Claim_Payment.csv',
    'staging.claims',
    c.raw_row_id,
    c.claim_id,
    'CORE_STATUS_CONFLICT',
    'claim_status',
    'warning',
    'repaired',
    c.claim_status,
    COALESCE(p.decision_outcome, 'pending'),
    jsonb_build_object('authoritative_source','Claim_Payment.csv decision presence/outcome')
FROM staging.claims c
LEFT JOIN _core_payment_recon p
  ON p.load_id = c.load_id
 AND p.claim_id = c.claim_id
WHERE c.load_id = (SELECT load_id FROM _gms_core_context)
  AND c.record_status = 'accepted'
  AND c.claim_status IS DISTINCT FROM COALESCE(p.decision_outcome, 'pending');

-- Payment amount remains authoritative for payment; mismatch with the claim
-- approved copy is logged rather than overwritten.
INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value, details
)
SELECT
    p.load_id,
    'Claim.csv vs Claim_Payment.csv',
    'staging.claim_payments',
    p.raw_row_id,
    p.payment_id,
    'CORE_PAYMENT_AMOUNT_CONFLICT',
    'payment_amount',
    'warning',
    'flagged',
    c.approved_amount::text,
    p.payment_amount::text,
    jsonb_build_object('authoritative_source','Claim_Payment.csv.payment_amount')
FROM _core_payment_recon p
JOIN staging.claims c
  ON c.load_id = p.load_id
 AND c.claim_id = p.claim_id
 AND c.record_status = 'accepted'
WHERE c.approved_amount IS DISTINCT FROM p.payment_amount;

-- Operational dates can be unknown without erasing a valid decision row.
INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value, details
)
SELECT
    p.load_id,
    'Claim_Payment.csv',
    'staging.claim_payments',
    p.raw_row_id,
    p.payment_id,
    'CORE_DECISION_DATE_NULLIFIED',
    'decision_date',
    'error',
    'repaired',
    p.decision_date::text,
    NULL,
    jsonb_build_object('processing_start_date', p.processing_start_date)
FROM _core_payment_recon p
WHERE p.decision_date IS NOT NULL
  AND p.core_decision_date IS NULL

UNION ALL

SELECT
    p.load_id,
    'Claim_Payment.csv + Claim.csv',
    'staging.claim_payments',
    p.raw_row_id,
    p.payment_id,
    'CORE_PAYMENT_DATE_NULLIFIED',
    'payment_date',
    'error',
    'repaired',
    p.payment_date::text,
    NULL,
    jsonb_build_object(
        'claim_date', p.claim_date,
        'decision_date', p.core_decision_date,
        'payment_status_treatment',
        CASE WHEN p.payment_status='paid' THEN 'set core payment_status to null because paid date is unusable'
             ELSE 'unchanged' END
    )
FROM _core_payment_recon p
WHERE p.payment_date IS NOT NULL
  AND p.core_payment_date IS NULL;

INSERT INTO core.claim_payments (
    payment_id, claim_id, processing_start_date, decision_date,
    decision_outcome, payment_date, payment_amount, payment_method,
    payment_status, transaction_reference, denial_reason,
    source_load_id, source_raw_row_id
)
SELECT
    p.payment_id,
    p.claim_id,
    p.processing_start_date,
    p.core_decision_date,
    p.decision_outcome,
    CASE WHEN p.decision_outcome='denied' THEN NULL ELSE p.core_payment_date END,
    CASE WHEN p.decision_outcome='denied' THEN 0 ELSE p.payment_amount END,
    CASE WHEN p.decision_outcome='denied' THEN NULL ELSE p.payment_method END,
    CASE WHEN p.decision_outcome='denied' THEN NULL ELSE p.core_payment_status END,
    CASE WHEN p.decision_outcome='denied' THEN NULL ELSE p.transaction_reference END,
    p.denial_reason,
    p.load_id,
    p.raw_row_id
FROM _core_payment_recon p
WHERE
    (p.decision_outcome = 'denied' AND p.denial_reason IS NOT NULL)
 OR (p.decision_outcome <> 'denied' AND p.payment_method IS NOT NULL)
ON CONFLICT (payment_id) DO UPDATE
SET claim_id = EXCLUDED.claim_id,
    processing_start_date = EXCLUDED.processing_start_date,
    decision_date = EXCLUDED.decision_date,
    decision_outcome = EXCLUDED.decision_outcome,
    payment_date = EXCLUDED.payment_date,
    payment_amount = EXCLUDED.payment_amount,
    payment_method = EXCLUDED.payment_method,
    payment_status = EXCLUDED.payment_status,
    transaction_reference = EXCLUDED.transaction_reference,
    denial_reason = EXCLUDED.denial_reason,
    source_load_id = EXCLUDED.source_load_id,
    source_raw_row_id = EXCLUDED.source_raw_row_id,
    updated_at = now();

-- ---------------------------------------------------------------------------
-- 5. Premiums. Policy ownership wins over the premium customer copy.
-- Age band is deterministically re-derived when canonical DOB is available.
-- ---------------------------------------------------------------------------

DROP TABLE IF EXISTS pg_temp._core_premium_recon;
CREATE TEMP TABLE _core_premium_recon ON COMMIT DROP AS
SELECT
    pr.*,
    p.customer_id AS canonical_customer_id,
    cu.date_of_birth,
    CASE
        WHEN cu.date_of_birth IS NULL THEN pr.age_band
        WHEN extract(year from age(pr.due_date, cu.date_of_birth)) < 35 THEN 'Under 35'
        WHEN extract(year from age(pr.due_date, cu.date_of_birth)) < 45 THEN '35-44'
        WHEN extract(year from age(pr.due_date, cu.date_of_birth)) < 55 THEN '45-54'
        WHEN extract(year from age(pr.due_date, cu.date_of_birth)) < 65 THEN '55-64'
        WHEN extract(year from age(pr.due_date, cu.date_of_birth)) < 75 THEN '65-74'
        ELSE '75+'
    END AS core_age_band
FROM staging.policy_premiums pr
JOIN core.policies p ON p.policy_id = pr.policy_id
JOIN core.customers cu ON cu.customer_id = p.customer_id
WHERE pr.load_id = (SELECT load_id FROM _gms_core_context)
  AND pr.record_status = 'accepted';

INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value, details
)
SELECT
    pr.load_id,
    'Policy_Premium.csv vs Policy.csv',
    'staging.policy_premiums',
    pr.raw_row_id,
    pr.premium_id,
    'CORE_PREMIUM_OWNER_CONFLICT',
    'customer_id',
    'warning',
    'repaired',
    pr.customer_id,
    p.customer_id,
    jsonb_build_object('authoritative_source','Policy.csv.customer_id')
FROM staging.policy_premiums pr
JOIN core.policies p ON p.policy_id = pr.policy_id
LEFT JOIN staging.customer_identity_map cm
  ON cm.load_id = pr.load_id
 AND cm.source_customer_id = pr.customer_id
WHERE pr.load_id = (SELECT load_id FROM _gms_core_context)
  AND pr.record_status = 'accepted'
  AND COALESCE(cm.canonical_customer_id, pr.customer_id)
      IS DISTINCT FROM p.customer_id

UNION ALL

SELECT
    r.load_id,
    'Policy_Premium.csv + Customer.csv',
    'staging.policy_premiums',
    r.raw_row_id,
    r.premium_id,
    'CORE_AGE_BAND_REPAIRED',
    'age_band',
    'warning',
    'repaired',
    r.age_band,
    r.core_age_band,
    jsonb_build_object('authoritative_basis','canonical DOB at premium due_date')
FROM _core_premium_recon r
WHERE r.age_band IS DISTINCT FROM r.core_age_band;

INSERT INTO core.policy_premiums (
    premium_id, policy_id, premium_amount, premium_frequency, age_band,
    due_date, paid_date, payment_status, payment_method,
    source_load_id, source_raw_row_id
)
SELECT
    r.premium_id,
    r.policy_id,
    r.premium_amount,
    r.premium_frequency,
    r.core_age_band,
    r.due_date,
    r.paid_date,
    r.payment_status,
    r.payment_method,
    r.load_id,
    r.raw_row_id
FROM _core_premium_recon r
ON CONFLICT (premium_id) DO UPDATE
SET policy_id = EXCLUDED.policy_id,
    premium_amount = EXCLUDED.premium_amount,
    premium_frequency = EXCLUDED.premium_frequency,
    age_band = EXCLUDED.age_band,
    due_date = EXCLUDED.due_date,
    paid_date = EXCLUDED.paid_date,
    payment_status = EXCLUDED.payment_status,
    payment_method = EXCLUDED.payment_method,
    source_load_id = EXCLUDED.source_load_id,
    source_raw_row_id = EXCLUDED.source_raw_row_id,
    updated_at = now();

-- ---------------------------------------------------------------------------
-- 6. JSON enrichment. Claim.csv controls claim existence.
-- ---------------------------------------------------------------------------

INSERT INTO core.claim_details (
    claim_id, schema_version, product_line, submission_channel, submitted_at,
    provider_id, provider_name, provider_type, adjuster_id, adjuster_notes,
    documents_submitted, practitioner_type, number_of_visits,
    prescription_din, prescription_drug_name, prescription_days_supply,
    prescription_is_generic, dental_claim_category, trip_start, trip_end,
    destination_country, incident_type, incident_date, currency,
    exchange_rate_to_cad, incident_sub_limit,
    source_load_id, source_raw_file_id
)
SELECT
    d.claim_id, d.schema_version, d.product_line, d.submission_channel, d.submitted_at,
    d.provider_id, d.provider_name, d.provider_type, d.adjuster_id, d.adjuster_notes,
    d.documents_submitted, d.practitioner_type, d.number_of_visits,
    d.prescription_din, d.prescription_drug_name, d.prescription_days_supply,
    d.prescription_is_generic, d.dental_claim_category, d.trip_start, d.trip_end,
    d.destination_country, d.incident_type, d.incident_date, d.currency,
    d.exchange_rate_to_cad, d.incident_sub_limit,
    d.load_id, d.raw_file_id
FROM staging.claim_details d
JOIN core.claims c ON c.claim_id = d.claim_id
WHERE d.load_id = (SELECT load_id FROM _gms_core_context)
  AND d.record_status = 'accepted'
ON CONFLICT (claim_id) DO UPDATE
SET schema_version = EXCLUDED.schema_version,
    product_line = EXCLUDED.product_line,
    submission_channel = EXCLUDED.submission_channel,
    submitted_at = EXCLUDED.submitted_at,
    provider_id = EXCLUDED.provider_id,
    provider_name = EXCLUDED.provider_name,
    provider_type = EXCLUDED.provider_type,
    adjuster_id = EXCLUDED.adjuster_id,
    adjuster_notes = EXCLUDED.adjuster_notes,
    documents_submitted = EXCLUDED.documents_submitted,
    practitioner_type = EXCLUDED.practitioner_type,
    number_of_visits = EXCLUDED.number_of_visits,
    prescription_din = EXCLUDED.prescription_din,
    prescription_drug_name = EXCLUDED.prescription_drug_name,
    prescription_days_supply = EXCLUDED.prescription_days_supply,
    prescription_is_generic = EXCLUDED.prescription_is_generic,
    dental_claim_category = EXCLUDED.dental_claim_category,
    trip_start = EXCLUDED.trip_start,
    trip_end = EXCLUDED.trip_end,
    destination_country = EXCLUDED.destination_country,
    incident_type = EXCLUDED.incident_type,
    incident_date = EXCLUDED.incident_date,
    currency = EXCLUDED.currency,
    exchange_rate_to_cad = EXCLUDED.exchange_rate_to_cad,
    incident_sub_limit = EXCLUDED.incident_sub_limit,
    source_load_id = EXCLUDED.source_load_id,
    source_raw_file_id = EXCLUDED.source_raw_file_id,
    updated_at = now();

-- Replace line items for claims touched by this batch so an upsert cannot leave
-- stale line numbers from an older version of the same claim.
DELETE FROM core.claim_line_items li
USING staging.claim_details d
WHERE d.load_id = (SELECT load_id FROM _gms_core_context)
  AND d.record_status = 'accepted'
  AND li.claim_id = d.claim_id;

INSERT INTO core.claim_line_items (
    claim_id, line_no, service_date, description, quantity,
    unit_amount, amount, procedure_code, procedure_category, tooth_number,
    source_load_id, source_raw_file_id
)
SELECT
    li.claim_id,
    li.line_no,
    li.service_date,
    li.description,
    li.quantity,
    li.unit_amount,
    li.amount,
    li.procedure_code,
    li.procedure_category,
    li.tooth_number,
    li.load_id,
    li.raw_file_id
FROM staging.claim_line_items li
JOIN staging.claim_details d
  ON d.load_id = li.load_id
 AND d.raw_file_id = li.raw_file_id
 AND d.record_status = 'accepted'
JOIN core.claims c ON c.claim_id = li.claim_id
WHERE li.load_id = (SELECT load_id FROM _gms_core_context)
  AND li.line_no > 0
  AND li.service_date IS NOT NULL
  AND li.description IS NOT NULL
  AND li.quantity > 0
  AND li.unit_amount > 0
  AND li.amount > 0
  AND abs(li.amount - (li.quantity * li.unit_amount)) <= 0.01;

-- ---------------------------------------------------------------------------
-- 7. Cross-source JSON reconciliation signals kept for analytics/audit.
-- ---------------------------------------------------------------------------

INSERT INTO staging.data_quality_log (
    load_id, source_name, source_table, source_record_id, business_key,
    rule_id, field_name, severity, action, original_value, clean_value, details
)
SELECT
    r.load_id,
    'Claim.csv + JSON',
    'staging.claims',
    r.raw_row_id,
    r.claim_id,
    'CORE_JSON_SERVICE_DATE_MISMATCH',
    'service_date',
    'warning',
    'flagged',
    r.core_service_date::text,
    r.json_service_date::text,
    jsonb_build_object('treatment','claim remains canonical; mismatch retained for audit')
FROM _core_claim_recon r
WHERE r.core_service_date IS NOT NULL
  AND r.json_service_date IS NOT NULL
  AND r.core_service_date IS DISTINCT FROM r.json_service_date
  AND r.service_date = r.core_service_date;

-- End of core load.
