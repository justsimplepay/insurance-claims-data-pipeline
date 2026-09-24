-- Objective 3: Operational Efficiency
-- Grain: one row per canonical claim.

DROP TABLE IF EXISTS marts.operations_features;

CREATE TABLE marts.operations_features AS
WITH base AS (
    SELECT
        c.claim_id,
        c.policy_id,
        p.customer_id,
        cu.province,
        p.policy_type AS product_line,
        c.claim_type,
        c.claim_amount,
        c.claim_date,
        d.submitted_at,
        d.submission_channel,
        d.adjuster_id,
        d.provider_type,
        d.documents_submitted,
        cp.processing_start_date,
        cp.decision_date,
        cp.decision_outcome,
        cp.payment_date,
        cp.payment_status,
        cp.payment_amount,
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
        END AS required_documents
    FROM core.claims c
    JOIN core.policies p ON p.policy_id = c.policy_id
    JOIN core.customers cu ON cu.customer_id = p.customer_id
    LEFT JOIN core.claim_details d ON d.claim_id = c.claim_id
    LEFT JOIN core.claim_payments cp ON cp.claim_id = c.claim_id
)
SELECT
    claim_id,
    policy_id,
    customer_id,
    province,
    product_line,
    claim_type,
    claim_amount,
    claim_date AS submission_date,
    submitted_at,
    submission_channel,
    adjuster_id,
    provider_type,
    COALESCE(decision_outcome,'pending') AS decision_outcome,
    processing_start_date,
    decision_date,
    payment_date,
    payment_status,
    payment_amount,
    (decision_outcome IS NULL) AS is_pending,
    (decision_outcome IS NOT NULL) AS is_decided,
    (payment_status='paid') AS is_paid,
    CASE
        WHEN processing_start_date IS NULL THEN NULL
        ELSE processing_start_date - claim_date
    END AS queue_days,
    CASE
        WHEN decision_date IS NULL OR processing_start_date IS NULL THEN NULL
        ELSE decision_date - processing_start_date
    END AS handling_days,
    CASE
        WHEN decision_date IS NULL THEN NULL
        ELSE decision_date - claim_date
    END AS submission_to_decision_days,
    CASE
        WHEN payment_date IS NULL OR decision_date IS NULL THEN NULL
        ELSE payment_date - decision_date
    END AS decision_to_payment_days,
    CASE
        WHEN payment_date IS NULL THEN NULL
        ELSE payment_date - claim_date
    END AS submission_to_payment_days,
    (documents_submitted IS NOT NULL) AS document_information_known,
    array_length(required_documents,1) AS required_document_count,
    CASE WHEN documents_submitted IS NULL THEN NULL
         ELSE cardinality(documents_submitted) END AS submitted_document_count,
    CASE
        WHEN documents_submitted IS NULL THEN NULL
        ELSE documents_submitted @> required_documents
    END AS required_documents_complete
FROM base;

ALTER TABLE marts.operations_features ADD PRIMARY KEY (claim_id);
ALTER TABLE marts.operations_features ENABLE ROW LEVEL SECURITY;
