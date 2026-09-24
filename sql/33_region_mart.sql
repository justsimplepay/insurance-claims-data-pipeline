-- Objective 4: Region-Wise Insights
-- Grain: province x product line x claim type x calendar quarter.

DROP TABLE IF EXISTS marts.region_summary;

CREATE TABLE marts.region_summary AS
SELECT
    cu.province,
    p.policy_type AS product_line,
    c.claim_type,
    date_trunc('quarter', c.claim_date)::date AS quarter_start,
    extract(year from c.claim_date)::integer AS calendar_year,
    extract(quarter from c.claim_date)::integer AS calendar_quarter,
    count(*)::integer AS claim_count,
    count(DISTINCT p.customer_id)::integer AS customer_count,
    count(DISTINCT c.policy_id)::integer AS policy_count,
    round(sum(c.claim_amount),2) AS total_claim_amount,
    round(avg(c.claim_amount),2) AS average_claim_amount,
    count(cp.claim_id)::integer AS decided_claim_count,
    count(*) FILTER (WHERE cp.decision_outcome='denied')::integer AS denied_claim_count,
    count(*) FILTER (WHERE cp.claim_id IS NULL)::integer AS pending_claim_count,
    CASE
        WHEN count(cp.claim_id)=0 THEN NULL
        ELSE round(
            count(*) FILTER (WHERE cp.decision_outcome='denied')::numeric
            / count(cp.claim_id), 4
        )
    END AS denial_rate,
    round(sum(cp.payment_amount),2) AS total_approved_amount,
    round(avg(cp.payment_amount) FILTER (
        WHERE cp.decision_outcome IN ('approved','partially_approved')
    ),2) AS average_approved_amount
FROM core.claims c
JOIN core.policies p ON p.policy_id = c.policy_id
JOIN core.customers cu ON cu.customer_id = p.customer_id
LEFT JOIN core.claim_payments cp ON cp.claim_id = c.claim_id
WHERE c.claim_date BETWEEN DATE '2023-07-01' AND DATE '2026-06-30'
GROUP BY
    cu.province,
    p.policy_type,
    c.claim_type,
    date_trunc('quarter', c.claim_date)::date,
    extract(year from c.claim_date)::integer,
    extract(quarter from c.claim_date)::integer;

ALTER TABLE marts.region_summary
ADD PRIMARY KEY (province, product_line, claim_type, quarter_start);
ALTER TABLE marts.region_summary ENABLE ROW LEVEL SECURITY;
