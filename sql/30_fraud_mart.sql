-- Objective 1: Fraud Detection
-- Grain: one row per canonical claim.
-- No fraud label or fraud prediction is manufactured. This mart exposes the
-- documented suspicious-pattern features F1-F9 plus supporting continuous
-- features for downstream analysis/modeling.

DROP TABLE IF EXISTS marts.fraud_features;

CREATE TABLE marts.fraud_features AS
WITH line_recon AS (
    SELECT
        d.claim_id,
        count(li.line_no)::integer AS line_item_count,
        round(sum(li.amount), 2) AS line_item_total_source_currency,
        CASE
            WHEN d.product_line = 'travel' AND d.exchange_rate_to_cad IS NOT NULL
                THEN round(sum(li.amount) * d.exchange_rate_to_cad, 2)
            WHEN d.product_line IN ('health','dental')
                THEN round(sum(li.amount), 2)
            ELSE NULL
        END AS reconciled_line_total_cad
    FROM core.claim_details d
    LEFT JOIN core.claim_line_items li ON li.claim_id = d.claim_id
    GROUP BY d.claim_id, d.product_line, d.exchange_rate_to_cad
),
base AS (
    SELECT
        c.claim_id,
        c.policy_id,
        p.customer_id,
        cu.province,
        cu.fsa,
        p.policy_type AS product_line,
        p.plan_name,
        p.coverage_type,
        p.start_date AS policy_start_date,
        p.end_date AS policy_end_date,
        p.coverage_amount,
        p.deductible_amount,
        c.claim_type,
        c.service_date,
        c.claim_date,
        c.claim_amount,
        c.approved_amount AS claim_header_approved_amount,
        pay.decision_outcome,
        pay.payment_amount AS authoritative_approved_amount,
        d.submission_channel,
        d.submitted_at,
        d.provider_id,
        d.provider_type,
        d.adjuster_id,
        d.documents_submitted,
        d.trip_start,
        d.trip_end,
        d.incident_date,
        d.incident_type,
        d.incident_sub_limit,
        lr.line_item_count,
        lr.reconciled_line_total_cad,
        CASE
            WHEN p.policy_type = 'travel'
                THEN COALESCE(d.incident_sub_limit, p.coverage_amount)
            ELSE p.coverage_amount
        END AS applicable_benefit_maximum,
        CASE c.claim_type
            WHEN 'prescription_drugs' THEN ARRAY['receipt','prescription']::text[]
            WHEN 'health_practitioner' THEN ARRAY['receipt']::text[]
            WHEN 'vision' THEN ARRAY['receipt','prescription']::text[]
            WHEN 'hearing_aids' THEN ARRAY['receipt','referral']::text[]
            WHEN 'medical_equipment' THEN ARRAY['receipt','referral']::text[]
            WHEN 'ambulance' THEN ARRAY['invoice']::text[]
            WHEN 'hospital_cash' THEN ARRAY['hospital_discharge_summary']::text[]
            WHEN 'preventive' THEN ARRAY['receipt']::text[]
            WHEN 'basic' THEN ARRAY['receipt']::text[]
            WHEN 'major' THEN ARRAY['receipt','treatment_plan','xray']::text[]
            WHEN 'emergency_medical' THEN ARRAY['itemized_invoice','medical_report','proof_of_travel']::text[]
            WHEN 'trip_cancellation' THEN ARRAY['cancellation_invoice','booking_confirmation','supporting_statement']::text[]
            WHEN 'trip_interruption' THEN ARRAY['receipt','booking_confirmation','supporting_statement']::text[]
            WHEN 'baggage' THEN ARRAY['baggage_irregularity_report','receipt','proof_of_travel']::text[]
            ELSE ARRAY[]::text[]
        END AS required_documents,
        CASE
            WHEN cu.date_of_birth IS NULL THEN 'Unknown'
            WHEN extract(year from age(c.claim_date, cu.date_of_birth)) < 35 THEN 'Under 35'
            WHEN extract(year from age(c.claim_date, cu.date_of_birth)) < 45 THEN '35-44'
            WHEN extract(year from age(c.claim_date, cu.date_of_birth)) < 55 THEN '45-54'
            WHEN extract(year from age(c.claim_date, cu.date_of_birth)) < 65 THEN '55-64'
            WHEN extract(year from age(c.claim_date, cu.date_of_birth)) < 75 THEN '65-74'
            ELSE '75+'
        END AS age_band_at_claim
    FROM core.claims c
    JOIN core.policies p ON p.policy_id = c.policy_id
    JOIN core.customers cu ON cu.customer_id = p.customer_id
    LEFT JOIN core.claim_payments pay ON pay.claim_id = c.claim_id
    LEFT JOIN core.claim_details d ON d.claim_id = c.claim_id
    LEFT JOIN line_recon lr ON lr.claim_id = c.claim_id
),
features AS (
    SELECT
        b.*,
        (b.service_date - b.policy_start_date) AS days_from_policy_start,
        CASE
            WHEN b.applicable_benefit_maximum IS NULL OR b.applicable_benefit_maximum = 0
                THEN NULL
            ELSE round(b.claim_amount / b.applicable_benefit_maximum, 4)
        END AS claim_amount_to_max_ratio,
        CASE
            WHEN b.claim_amount IS NULL OR b.reconciled_line_total_cad IS NULL THEN NULL
            ELSE round(b.claim_amount - b.reconciled_line_total_cad, 2)
        END AS header_line_amount_difference_cad,
        (
            SELECT count(*)::integer
            FROM core.claims c2
            JOIN core.policies p2 ON p2.policy_id = c2.policy_id
            WHERE p2.customer_id = b.customer_id
              AND c2.claim_date BETWEEN (b.claim_date - INTERVAL '29 days')::date AND b.claim_date
        ) AS claims_including_current_prior_30d,
        CASE
            WHEN b.service_date IS NULL THEN NULL
            ELSE (b.service_date BETWEEN b.policy_start_date AND b.policy_start_date + 30)
        END AS f1_early_claim_flag,
        CASE
            WHEN b.product_line <> 'dental' OR b.claim_type NOT IN ('basic','major') THEN false
            WHEN b.service_date IS NULL THEN NULL
            ELSE b.service_date < b.policy_start_date + 90
        END AS f2_waiting_period_flag,
        CASE
            WHEN b.claim_amount IS NULL OR b.applicable_benefit_maximum IS NULL THEN NULL
            ELSE b.claim_amount >= (0.90 * b.applicable_benefit_maximum)
        END AS f3_near_maximum_flag,
        CASE
            WHEN b.claim_amount IS NULL OR b.reconciled_line_total_cad IS NULL THEN NULL
            ELSE abs(b.claim_amount - b.reconciled_line_total_cad) > 0.01
        END AS f6_line_item_mismatch_flag,
        CASE
            WHEN b.documents_submitted IS NULL THEN NULL
            ELSE NOT (b.documents_submitted @> b.required_documents)
        END AS f7_missing_required_documents_flag,
        CASE
            WHEN b.submitted_at IS NULL THEN NULL
            ELSE (
                extract(isodow from (b.submitted_at AT TIME ZONE 'America/Regina')) IN (6,7)
                OR extract(hour from (b.submitted_at AT TIME ZONE 'America/Regina')) BETWEEN 0 AND 5
            )
        END AS f8_unusual_submission_time_flag,
        CASE
            WHEN b.product_line <> 'travel' THEN false
            WHEN b.trip_start IS NULL OR b.trip_end IS NULL OR b.incident_date IS NULL THEN NULL
            WHEN b.claim_type = 'trip_cancellation' THEN b.incident_date >= b.trip_start
            ELSE b.incident_date < b.trip_start OR b.incident_date > b.trip_end
        END AS f9_travel_date_anomaly_flag
    FROM base b
),
flagged AS (
    SELECT
        f.*,
        (f.claims_including_current_prior_30d >= 3) AS f4_repeat_claims_30d_flag,
        (
            coalesce(f.f1_early_claim_flag::int,0)
          + coalesce(f.f2_waiting_period_flag::int,0)
          + coalesce(f.f3_near_maximum_flag::int,0)
          + (f.claims_including_current_prior_30d >= 3)::int
          + coalesce(f.f6_line_item_mismatch_flag::int,0)
          + coalesce(f.f7_missing_required_documents_flag::int,0)
          + coalesce(f.f8_unusual_submission_time_flag::int,0)
          + coalesce(f.f9_travel_date_anomaly_flag::int,0)
        )::integer AS suspicious_pattern_count_excluding_provider
    FROM features f
),
provider_rollup AS (
    SELECT
        provider_id,
        count(*)::integer AS provider_claim_count,
        count(*) FILTER (
            WHERE suspicious_pattern_count_excluding_provider > 0
        )::integer AS provider_suspicious_claim_count
    FROM flagged
    WHERE provider_id IS NOT NULL
    GROUP BY provider_id
),
provider_total AS (
    SELECT count(*)::numeric AS claims_with_provider
    FROM flagged
    WHERE provider_id IS NOT NULL
)
SELECT
    f.claim_id,
    f.policy_id,
    f.customer_id,
    f.age_band_at_claim,
    f.province,
    f.fsa,
    f.product_line,
    f.plan_name,
    f.coverage_type,
    f.claim_type,
    f.policy_start_date,
    f.service_date,
    f.claim_date,
    f.days_from_policy_start,
    f.claim_amount,
    f.authoritative_approved_amount,
    COALESCE(f.decision_outcome, 'pending') AS decision_outcome,
    f.applicable_benefit_maximum,
    f.claim_amount_to_max_ratio,
    f.submission_channel,
    f.provider_id,
    f.provider_type,
    f.adjuster_id,
    f.line_item_count,
    f.reconciled_line_total_cad,
    f.header_line_amount_difference_cad,
    f.claims_including_current_prior_30d,
    array_length(f.required_documents, 1) AS required_document_count,
    CASE WHEN f.documents_submitted IS NULL THEN NULL
         ELSE cardinality(f.documents_submitted) END AS submitted_document_count,
    f.f1_early_claim_flag,
    f.f2_waiting_period_flag,
    f.f3_near_maximum_flag,
    f.f4_repeat_claims_30d_flag,
    pr.provider_claim_count,
    CASE
        WHEN pr.provider_claim_count IS NULL OR pt.claims_with_provider = 0 THEN NULL
        ELSE round(pr.provider_claim_count / pt.claims_with_provider, 4)
    END AS provider_claim_share,
    pr.provider_suspicious_claim_count,
    CASE
        WHEN pr.provider_claim_count IS NULL OR pr.provider_claim_count = 0 THEN NULL
        ELSE round(pr.provider_suspicious_claim_count::numeric / pr.provider_claim_count, 4)
    END AS provider_suspicious_claim_share,
    f.f6_line_item_mismatch_flag,
    f.f7_missing_required_documents_flag,
    f.f8_unusual_submission_time_flag,
    f.f9_travel_date_anomaly_flag,
    f.suspicious_pattern_count_excluding_provider,
    (f.suspicious_pattern_count_excluding_provider >= 2) AS has_multiple_suspicious_patterns
FROM flagged f
LEFT JOIN provider_rollup pr ON pr.provider_id = f.provider_id
CROSS JOIN provider_total pt;

ALTER TABLE marts.fraud_features ADD PRIMARY KEY (claim_id);
ALTER TABLE marts.fraud_features ENABLE ROW LEVEL SECURITY;
