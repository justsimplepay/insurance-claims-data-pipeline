-- GMS Data Engineer Case Study
-- Security hardening for internal processing schemas.
--
-- raw, staging, and core are internal pipeline layers. They are not intended
-- for direct anonymous/authenticated client access through Supabase APIs.
-- Enabling RLS with no anon/authenticated policies makes access deny-by-default
-- for those client roles while backend/database roles can continue to perform
-- controlled pipeline work.

ALTER TABLE raw.ingestion_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.source_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.customer_csv ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.policy_csv ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.claim_csv ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.claim_payment_csv ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.policy_premium_csv ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.claim_detail_files ENABLE ROW LEVEL SECURITY;

ALTER TABLE staging.customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.claims ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.claim_payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.policy_premiums ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.claim_details ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.claim_line_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.customer_identity_map ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.data_quality_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.quarantine ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.claims ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.claim_payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.policy_premiums ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.claim_details ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.claim_line_items ENABLE ROW LEVEL SECURITY;
