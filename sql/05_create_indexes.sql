-- GMS Data Engineer Case Study
-- Workload-justified indexes beyond primary/unique constraints.

CREATE INDEX IF NOT EXISTS idx_core_policies_customer_id
    ON core.policies (customer_id);

CREATE INDEX IF NOT EXISTS idx_core_claims_policy_claim_date
    ON core.claims (policy_id, claim_date);

CREATE INDEX IF NOT EXISTS idx_core_policy_premiums_policy_due_date
    ON core.policy_premiums (policy_id, due_date);

CREATE INDEX IF NOT EXISTS idx_core_claim_details_provider_id
    ON core.claim_details (provider_id);

CREATE INDEX IF NOT EXISTS idx_core_claim_details_adjuster_id
    ON core.claim_details (adjuster_id);
