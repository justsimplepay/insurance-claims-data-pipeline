-- Objective 2: Customer Retention
-- Grain: one row per customer eligible at the 2026-03-31 snapshot.
-- Predictive features use only the 2025-04-01..2026-03-31 observation window.
-- The churn label is evaluated only in the future 2026-04-01..2026-06-30
-- outcome window.

DROP TABLE IF EXISTS marts.retention_features;

CREATE TABLE marts.retention_features AS
WITH params AS (
    SELECT
        DATE '2025-04-01' AS observation_start,
        DATE '2026-03-31' AS snapshot_date,
        DATE '2026-04-01' AS outcome_start,
        DATE '2026-06-30' AS extract_end
),
snapshot_policies AS (
    SELECT p.*
    FROM core.policies p, params x
    WHERE p.policy_type IN ('health','dental')
      AND p.start_date <= x.snapshot_date
      AND (p.end_date IS NULL OR p.end_date >= x.snapshot_date)
),
eligible AS (
    SELECT DISTINCT customer_id
    FROM snapshot_policies
),
premium_rows AS (
    SELECT
        p.customer_id,
        pr.policy_id,
        pr.due_date,
        pr.premium_amount,
        pr.age_band,
        pr.payment_status,
        lag(pr.age_band) OVER (
            PARTITION BY pr.policy_id ORDER BY pr.due_date, pr.premium_id
        ) AS previous_age_band,
        lag(pr.premium_amount) OVER (
            PARTITION BY pr.policy_id ORDER BY pr.due_date, pr.premium_id
        ) AS previous_premium_amount
    FROM core.policy_premiums pr
    JOIN core.policies p ON p.policy_id = pr.policy_id
    CROSS JOIN params x
    WHERE p.policy_type IN ('health','dental')
      AND pr.due_date BETWEEN x.observation_start AND x.snapshot_date
),
premium_features AS (
    SELECT
        customer_id,
        count(*)::integer AS premium_installment_count_12m,
        count(*) FILTER (WHERE payment_status='late')::integer AS late_premium_count_12m,
        count(*) FILTER (WHERE payment_status='missed')::integer AS missed_premium_count_12m,
        count(*) FILTER (WHERE payment_status IN ('late','missed'))::integer AS late_or_missed_count_12m,
        round(
            count(*) FILTER (WHERE payment_status IN ('late','missed'))::numeric
            / NULLIF(count(*),0), 4
        ) AS late_or_missed_rate_12m,
        round(sum(premium_amount),2) AS premium_due_total_12m,
        round(sum(premium_amount) FILTER (WHERE payment_status IN ('paid','late')),2) AS premium_paid_total_12m,
        count(*) FILTER (
            WHERE previous_age_band IS NOT NULL AND age_band IS DISTINCT FROM previous_age_band
        )::integer AS age_band_change_count_12m,
        count(*) FILTER (
            WHERE previous_age_band IS NOT NULL
              AND age_band IS DISTINCT FROM previous_age_band
              AND previous_premium_amount IS NOT NULL
              AND premium_amount > previous_premium_amount
        )::integer AS age_band_change_with_increase_count_12m
    FROM premium_rows
    GROUP BY customer_id
),
claim_features AS (
    SELECT
        p.customer_id,
        count(*)::integer AS claim_count_12m,
        count(cp.claim_id)::integer AS decided_claim_count_12m,
        count(*) FILTER (WHERE cp.decision_outcome='denied')::integer AS denied_claim_count_12m,
        round(
            count(*) FILTER (WHERE cp.decision_outcome='denied')::numeric
            / NULLIF(count(cp.claim_id),0), 4
        ) AS denied_claim_rate_12m,
        round(sum(c.claim_amount),2) AS claimed_amount_total_12m,
        round(sum(cp.payment_amount),2) AS approved_amount_total_12m,
        max(c.claim_date) AS last_claim_date_12m
    FROM core.claims c
    JOIN core.policies p ON p.policy_id = c.policy_id
    LEFT JOIN core.claim_payments cp ON cp.claim_id = c.claim_id
    CROSS JOIN params x
    WHERE p.policy_type IN ('health','dental')
      AND c.claim_date BETWEEN x.observation_start AND x.snapshot_date
    GROUP BY p.customer_id
),
snapshot_features AS (
    SELECT
        customer_id,
        count(*)::integer AS active_hd_policy_count,
        count(*) FILTER (WHERE policy_type='health')::integer AS active_health_policy_count,
        count(*) FILTER (WHERE policy_type='dental')::integer AS active_dental_policy_count
    FROM snapshot_policies
    GROUP BY customer_id
),
outcome AS (
    SELECT
        e.customer_id,
        CASE
            WHEN bool_and(
                sp.status IN ('lapsed','cancelled')
                AND sp.end_date BETWEEN x.outcome_start AND x.extract_end
            )
            AND NOT EXISTS (
                SELECT 1
                FROM core.policies replacement
                WHERE replacement.customer_id = e.customer_id
                  AND replacement.policy_type IN ('health','dental')
                  AND replacement.start_date > x.snapshot_date
                  AND replacement.start_date <= x.extract_end
            )
            THEN 1 ELSE 0
        END AS churned
    FROM eligible e
    JOIN snapshot_policies sp ON sp.customer_id = e.customer_id
    CROSS JOIN params x
    GROUP BY e.customer_id, x.outcome_start, x.snapshot_date, x.extract_end
)
SELECT
    cu.customer_id,
    CASE
        WHEN cu.date_of_birth IS NULL THEN 'Unknown'
        WHEN extract(year from age(x.snapshot_date, cu.date_of_birth)) < 35 THEN 'Under 35'
        WHEN extract(year from age(x.snapshot_date, cu.date_of_birth)) < 45 THEN '35-44'
        WHEN extract(year from age(x.snapshot_date, cu.date_of_birth)) < 55 THEN '45-54'
        WHEN extract(year from age(x.snapshot_date, cu.date_of_birth)) < 65 THEN '55-64'
        WHEN extract(year from age(x.snapshot_date, cu.date_of_birth)) < 75 THEN '65-74'
        ELSE '75+'
    END AS age_band_at_snapshot,
    cu.gender,
    cu.province,
    cu.city,
    left(cu.postal_code,3) AS fsa,
    (x.snapshot_date - cu.customer_since) AS tenure_days_at_snapshot,
    round((x.snapshot_date - cu.customer_since) / 30.4375, 1) AS tenure_months_at_snapshot,
    sf.active_hd_policy_count,
    sf.active_health_policy_count,
    sf.active_dental_policy_count,
    coalesce(pf.premium_installment_count_12m,0) AS premium_installment_count_12m,
    coalesce(pf.late_premium_count_12m,0) AS late_premium_count_12m,
    coalesce(pf.missed_premium_count_12m,0) AS missed_premium_count_12m,
    coalesce(pf.late_or_missed_count_12m,0) AS late_or_missed_count_12m,
    coalesce(pf.late_or_missed_rate_12m,0) AS late_or_missed_rate_12m,
    coalesce(pf.premium_due_total_12m,0) AS premium_due_total_12m,
    coalesce(pf.premium_paid_total_12m,0) AS premium_paid_total_12m,
    coalesce(pf.age_band_change_count_12m,0) AS age_band_change_count_12m,
    coalesce(pf.age_band_change_with_increase_count_12m,0) AS age_band_change_with_increase_count_12m,
    coalesce(cf.claim_count_12m,0) AS claim_count_12m,
    coalesce(cf.decided_claim_count_12m,0) AS decided_claim_count_12m,
    coalesce(cf.denied_claim_count_12m,0) AS denied_claim_count_12m,
    coalesce(cf.denied_claim_rate_12m,0) AS denied_claim_rate_12m,
    coalesce(cf.claimed_amount_total_12m,0) AS claimed_amount_total_12m,
    coalesce(cf.approved_amount_total_12m,0) AS approved_amount_total_12m,
    cf.last_claim_date_12m,
    CASE
        WHEN cf.last_claim_date_12m IS NULL THEN NULL
        ELSE x.snapshot_date - cf.last_claim_date_12m
    END AS days_since_last_claim_at_snapshot,
    o.churned,
    x.observation_start,
    x.snapshot_date,
    x.outcome_start,
    x.extract_end AS outcome_end_date
FROM eligible e
JOIN core.customers cu ON cu.customer_id = e.customer_id
JOIN snapshot_features sf ON sf.customer_id = e.customer_id
LEFT JOIN premium_features pf ON pf.customer_id = e.customer_id
LEFT JOIN claim_features cf ON cf.customer_id = e.customer_id
JOIN outcome o ON o.customer_id = e.customer_id
CROSS JOIN params x;

ALTER TABLE marts.retention_features ADD PRIMARY KEY (customer_id);
ALTER TABLE marts.retention_features ENABLE ROW LEVEL SECURITY;
