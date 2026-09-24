-- Objective 5: Policy Optimization
-- Grain: plan x age band x coverage type x province.
--
-- The performance metric is approved claim amount / premium amount due over
-- the same 2023-07-01..2026-06-30 analysis window. It is intentionally NOT
-- labelled an actuarial loss ratio.
--
-- low_exposure_flag uses < 12 policy-months as a transparent small-cell rule:
-- fewer than one aggregate policy-year of exposure should be interpreted
-- cautiously.

DROP TABLE IF EXISTS marts.policy_performance;

CREATE TABLE marts.policy_performance AS
WITH params AS (
    SELECT DATE '2023-07-01' AS history_start,
           DATE '2026-06-30' AS extract_end
),
policy_months AS (
    SELECT
        p.policy_id,
        p.policy_type,
        COALESCE(p.plan_name,'Unknown') AS plan_name,
        p.coverage_type,
        cu.province,
        m.month_start::date AS exposure_month,
        COALESCE(
            CASE
                WHEN cu.date_of_birth IS NULL THEN NULL
                WHEN extract(year from age(m.month_start::date, cu.date_of_birth)) < 35 THEN 'Under 35'
                WHEN extract(year from age(m.month_start::date, cu.date_of_birth)) < 45 THEN '35-44'
                WHEN extract(year from age(m.month_start::date, cu.date_of_birth)) < 55 THEN '45-54'
                WHEN extract(year from age(m.month_start::date, cu.date_of_birth)) < 65 THEN '55-64'
                WHEN extract(year from age(m.month_start::date, cu.date_of_birth)) < 75 THEN '65-74'
                ELSE '75+'
            END,
            (
                SELECT pr.age_band
                FROM core.policy_premiums pr
                WHERE pr.policy_id = p.policy_id
                ORDER BY abs(pr.due_date - m.month_start::date), pr.due_date
                LIMIT 1
            ),
            'Unknown'
        ) AS age_band
    FROM core.policies p
    JOIN core.customers cu ON cu.customer_id = p.customer_id
    CROSS JOIN params x
    CROSS JOIN LATERAL generate_series(
        date_trunc('month', greatest(p.start_date, x.history_start))::timestamp,
        date_trunc('month', least(COALESCE(p.end_date, x.extract_end), x.extract_end))::timestamp,
        interval '1 month'
    ) AS m(month_start)
    WHERE p.start_date <= x.extract_end
      AND COALESCE(p.end_date, x.extract_end) >= x.history_start
),
exposure AS (
    SELECT
        policy_type,
        plan_name,
        coverage_type,
        province,
        age_band,
        count(*)::integer AS policy_months,
        count(DISTINCT policy_id)::integer AS policy_count
    FROM policy_months
    GROUP BY policy_type, plan_name, coverage_type, province, age_band
),
premium AS (
    SELECT
        p.policy_type,
        COALESCE(p.plan_name,'Unknown') AS plan_name,
        p.coverage_type,
        cu.province,
        pr.age_band,
        count(*)::integer AS premium_installment_count,
        round(sum(pr.premium_amount),2) AS premium_due_total
    FROM core.policy_premiums pr
    JOIN core.policies p ON p.policy_id = pr.policy_id
    JOIN core.customers cu ON cu.customer_id = p.customer_id
    CROSS JOIN params x
    WHERE pr.due_date BETWEEN x.history_start AND x.extract_end
    GROUP BY p.policy_type, COALESCE(p.plan_name,'Unknown'),
             p.coverage_type, cu.province, pr.age_band
),
claim_rows AS (
    SELECT
        p.policy_type,
        COALESCE(p.plan_name,'Unknown') AS plan_name,
        p.coverage_type,
        cu.province,
        COALESCE(
            CASE
                WHEN cu.date_of_birth IS NULL THEN NULL
                WHEN extract(year from age(c.claim_date, cu.date_of_birth)) < 35 THEN 'Under 35'
                WHEN extract(year from age(c.claim_date, cu.date_of_birth)) < 45 THEN '35-44'
                WHEN extract(year from age(c.claim_date, cu.date_of_birth)) < 55 THEN '45-54'
                WHEN extract(year from age(c.claim_date, cu.date_of_birth)) < 65 THEN '55-64'
                WHEN extract(year from age(c.claim_date, cu.date_of_birth)) < 75 THEN '65-74'
                ELSE '75+'
            END,
            (
                SELECT pr.age_band
                FROM core.policy_premiums pr
                WHERE pr.policy_id = p.policy_id
                ORDER BY abs(pr.due_date - c.claim_date), pr.due_date
                LIMIT 1
            ),
            'Unknown'
        ) AS age_band,
        c.claim_id,
        cp.claim_id AS decided_claim_id,
        cp.decision_outcome,
        cp.payment_amount
    FROM core.claims c
    JOIN core.policies p ON p.policy_id = c.policy_id
    JOIN core.customers cu ON cu.customer_id = p.customer_id
    LEFT JOIN core.claim_payments cp ON cp.claim_id = c.claim_id
    CROSS JOIN params x
    WHERE c.claim_date BETWEEN x.history_start AND x.extract_end
),
claims AS (
    SELECT
        policy_type,
        plan_name,
        coverage_type,
        province,
        age_band,
        count(*)::integer AS claim_count,
        count(decided_claim_id)::integer AS decided_claim_count,
        count(*) FILTER (
            WHERE decision_outcome IN ('approved','partially_approved')
        )::integer AS approved_claim_count,
        round(sum(payment_amount),2) AS approved_claim_amount
    FROM claim_rows
    GROUP BY policy_type, plan_name, coverage_type, province, age_band
),
keys AS (
    SELECT policy_type, plan_name, coverage_type, province, age_band FROM exposure
    UNION
    SELECT policy_type, plan_name, coverage_type, province, age_band FROM premium
    UNION
    SELECT policy_type, plan_name, coverage_type, province, age_band FROM claims
)
SELECT
    k.policy_type,
    k.plan_name,
    k.age_band,
    k.coverage_type,
    k.province,
    coalesce(e.policy_count,0) AS policy_count,
    coalesce(e.policy_months,0) AS policy_months,
    coalesce(p.premium_installment_count,0) AS premium_installment_count,
    coalesce(p.premium_due_total,0) AS premium_due_total,
    coalesce(c.claim_count,0) AS claim_count,
    coalesce(c.decided_claim_count,0) AS decided_claim_count,
    coalesce(c.approved_claim_count,0) AS approved_claim_count,
    coalesce(c.approved_claim_amount,0) AS approved_claim_amount,
    CASE
        WHEN coalesce(p.premium_due_total,0) = 0 THEN NULL
        ELSE round(coalesce(c.approved_claim_amount,0) / p.premium_due_total, 4)
    END AS claims_to_premium_performance_ratio,
    (coalesce(e.policy_months,0) < 12) AS low_exposure_flag
FROM keys k
LEFT JOIN exposure e USING (policy_type, plan_name, coverage_type, province, age_band)
LEFT JOIN premium p USING (policy_type, plan_name, coverage_type, province, age_band)
LEFT JOIN claims c USING (policy_type, plan_name, coverage_type, province, age_band);

ALTER TABLE marts.policy_performance
ADD PRIMARY KEY (policy_type, plan_name, age_band, coverage_type, province);
ALTER TABLE marts.policy_performance ENABLE ROW LEVEL SECURITY;
