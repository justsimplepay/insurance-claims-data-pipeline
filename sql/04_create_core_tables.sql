-- GMS Data Engineer Case Study
-- Canonical relational model.
-- Cross-source source-of-truth rules are applied before rows enter these tables.

CREATE TABLE IF NOT EXISTS core.customers (
    customer_id        text PRIMARY KEY,
    date_of_birth      date,
    gender             text,
    province           text NOT NULL,
    fsa                text,
    customer_since     date NOT NULL,
    last_updated       timestamptz NOT NULL,

    source_load_id     bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_row_id  bigint NOT NULL REFERENCES raw.customer_csv(raw_row_id),
    updated_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_customer_id
        CHECK (customer_id ~ '^C[0-9]{5}
CREATE TABLE IF NOT EXISTS core.policies (
    policy_id           text PRIMARY KEY,
    customer_id         text NOT NULL REFERENCES core.customers(customer_id),

    policy_type         text NOT NULL,
    plan_name           text,
    coverage_type       text NOT NULL,
    start_date          date NOT NULL,
    end_date            date,
    status              text NOT NULL,
    sales_channel       text,
    coverage_amount     numeric(12,2) NOT NULL,
    deductible_amount   numeric(12,2) NOT NULL,

    source_load_id      bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_row_id   bigint NOT NULL REFERENCES raw.policy_csv(raw_row_id),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_policy_id
        CHECK (policy_id ~ '^P[0-9]{5}$'),
    CONSTRAINT ck_core_policy_type
        CHECK (policy_type IN ('health', 'dental', 'travel')),
    CONSTRAINT ck_core_policy_plan
        CHECK (
            plan_name IS NULL
            OR plan_name IN (
                'BasicPlan',
                'ExtendaPlan',
                'OmniPlan',
                'Replacement Health',
                'Dental Basic',
                'Dental Plus',
                'TravelStar',
                'StudentPlan'
            )
        ),
    CONSTRAINT ck_core_policy_coverage_type
        CHECK (coverage_type IN ('single', 'couple', 'family')),
    CONSTRAINT ck_core_policy_status
        CHECK (status IN ('active', 'lapsed', 'cancelled', 'expired')),
    CONSTRAINT ck_core_policy_sales_channel
        CHECK (sales_channel IS NULL OR sales_channel IN ('online', 'broker', 'call_centre')),
    CONSTRAINT ck_core_policy_date_order
        CHECK (end_date IS NULL OR end_date >= start_date),
    CONSTRAINT ck_core_policy_coverage_amount
        CHECK (coverage_amount > 0),
    CONSTRAINT ck_core_policy_deductible_amount
        CHECK (deductible_amount >= 0)
);

CREATE TABLE IF NOT EXISTS core.claims (
    claim_id            text PRIMARY KEY,
    policy_id           text NOT NULL REFERENCES core.policies(policy_id),

    claim_type          text NOT NULL,
    service_date        date,
    claim_date          date NOT NULL,
    claim_amount        numeric(12,2),
    approved_amount     numeric(12,2),

    source_load_id      bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_row_id   bigint NOT NULL REFERENCES raw.claim_csv(raw_row_id),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_claim_id
        CHECK (claim_id ~ '^[HDT][0-9]{5}$'),
    CONSTRAINT ck_core_claim_type
        CHECK (
            claim_type IN (
                'prescription_drugs',
                'health_practitioner',
                'vision',
                'hearing_aids',
                'medical_equipment',
                'ambulance',
                'hospital_cash',
                'preventive',
                'basic',
                'major',
                'emergency_medical',
                'trip_cancellation',
                'trip_interruption',
                'baggage'
            )
        ),
    CONSTRAINT ck_core_claim_date_order
        CHECK (service_date IS NULL OR service_date <= claim_date),
    CONSTRAINT ck_core_claim_amount
        CHECK (claim_amount IS NULL OR claim_amount > 0),
    CONSTRAINT ck_core_claim_approved_amount
        CHECK (
            approved_amount IS NULL
            OR (
                approved_amount >= 0
                AND (claim_amount IS NULL OR approved_amount <= claim_amount)
            )
        )
);

CREATE TABLE IF NOT EXISTS core.claim_payments (
    payment_id              text PRIMARY KEY,
    claim_id                text NOT NULL UNIQUE REFERENCES core.claims(claim_id),

    processing_start_date   date,
    decision_date           date,
    decision_outcome        text NOT NULL,
    payment_date            date,
    payment_amount          numeric(12,2) NOT NULL,
    payment_method          text,
    payment_status          text,
    transaction_reference   text UNIQUE,
    denial_reason           text,

    source_load_id          bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_row_id       bigint NOT NULL REFERENCES raw.claim_payment_csv(raw_row_id),
    updated_at              timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_payment_id
        CHECK (payment_id ~ '^PAY[0-9]{5}$'),
    CONSTRAINT ck_core_payment_decision_outcome
        CHECK (decision_outcome IN ('approved', 'partially_approved', 'denied')),
    CONSTRAINT ck_core_payment_method
        CHECK (payment_method IS NULL OR payment_method IN ('direct_deposit', 'cheque', 'provider_direct')),
    CONSTRAINT ck_core_payment_status
        CHECK (payment_status IS NULL OR payment_status IN ('paid', 'scheduled')),
    CONSTRAINT ck_core_payment_denial_reason
        CHECK (
            denial_reason IS NULL
            OR denial_reason IN (
                'not_covered',
                'waiting_period',
                'benefit_maximum_reached',
                'missing_documentation',
                'policy_inactive',
                'late_submission',
                'duplicate_claim'
            )
        ),
    CONSTRAINT ck_core_payment_amount
        CHECK (payment_amount >= 0),
    CONSTRAINT ck_core_payment_processing_decision_order
        CHECK (
            processing_start_date IS NULL
            OR decision_date IS NULL
            OR decision_date >= processing_start_date
        ),
    CONSTRAINT ck_core_payment_decision_payment_order
        CHECK (
            decision_date IS NULL
            OR payment_date IS NULL
            OR payment_date >= decision_date
        ),
    CONSTRAINT ck_core_payment_denied_semantics
        CHECK (
            decision_outcome <> 'denied'
            OR (
                payment_amount = 0
                AND payment_date IS NULL
                AND payment_method IS NULL
                AND payment_status IS NULL
                AND transaction_reference IS NULL
                AND denial_reason IS NOT NULL
            )
        ),
    CONSTRAINT ck_core_payment_nondenied_semantics
        CHECK (
            decision_outcome = 'denied'
            OR payment_method IS NOT NULL
        ),
    CONSTRAINT ck_core_payment_paid_semantics
        CHECK (
            payment_status IS DISTINCT FROM 'paid'
            OR (
                payment_date IS NOT NULL
                AND transaction_reference IS NOT NULL
            )
        ),
    CONSTRAINT ck_core_payment_scheduled_semantics
        CHECK (
            payment_status IS DISTINCT FROM 'scheduled'
            OR (
                payment_date IS NULL
                AND transaction_reference IS NULL
            )
        )
);

CREATE TABLE IF NOT EXISTS core.policy_premiums (
    premium_id          text PRIMARY KEY,
    policy_id           text NOT NULL REFERENCES core.policies(policy_id),

    premium_amount      numeric(12,2) NOT NULL,
    premium_frequency   text NOT NULL,
    age_band            text NOT NULL,
    due_date            date NOT NULL,
    paid_date           date,
    payment_status      text NOT NULL,
    payment_method      text,

    source_load_id      bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_row_id   bigint NOT NULL REFERENCES raw.policy_premium_csv(raw_row_id),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_premium_id
        CHECK (premium_id ~ '^PRM[0-9]{6}$'),
    CONSTRAINT ck_core_premium_amount
        CHECK (premium_amount > 0),
    CONSTRAINT ck_core_premium_frequency
        CHECK (premium_frequency IN ('monthly', 'quarterly', 'yearly', 'single')),
    CONSTRAINT ck_core_premium_age_band
        CHECK (age_band IN ('Under 35', '35-44', '45-54', '55-64', '65-74', '75+')),
    CONSTRAINT ck_core_premium_status
        CHECK (payment_status IN ('paid', 'late', 'missed')),
    CONSTRAINT ck_core_premium_method
        CHECK (payment_method IS NULL OR payment_method IN ('pad', 'credit_card', 'cheque')),
    CONSTRAINT ck_core_premium_status_dates
        CHECK (
            (payment_status = 'paid' AND paid_date IS NOT NULL AND paid_date <= due_date)
            OR
            (payment_status = 'late' AND paid_date IS NOT NULL AND paid_date > due_date)
            OR
            (payment_status = 'missed' AND paid_date IS NULL AND payment_method IS NULL)
        )
);

CREATE TABLE IF NOT EXISTS core.claim_details (
    claim_id                    text PRIMARY KEY REFERENCES core.claims(claim_id),

    schema_version              text NOT NULL,
    product_line                text NOT NULL,
    submission_channel          text,
    submitted_at                timestamptz NOT NULL,

    provider_id                 text NOT NULL,
    provider_name               text NOT NULL,
    provider_type               text,

    adjuster_id                 text,
    adjuster_notes              text,
    documents_submitted         text[],

    practitioner_type           text,
    number_of_visits            integer,
    prescription_din            text,
    prescription_drug_name      text,
    prescription_days_supply    integer,
    prescription_is_generic     boolean,

    dental_claim_category       text,

    trip_start                  date,
    trip_end                    date,
    destination_country         text,
    incident_type               text,
    incident_date               date,
    currency                    text,
    exchange_rate_to_cad        numeric(10,4),
    incident_sub_limit          numeric(12,2),

    source_load_id              bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_file_id          bigint NOT NULL REFERENCES raw.claim_detail_files(source_file_id),
    updated_at                  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_detail_product_line
        CHECK (product_line IN ('health', 'dental', 'travel')),
    CONSTRAINT ck_core_detail_submission_channel
        CHECK (
            submission_channel IS NULL
            OR submission_channel IN ('online_portal', 'mobile_app', 'provider_direct_billing', 'mail')
        ),
    CONSTRAINT ck_core_detail_adjuster_id
        CHECK (adjuster_id IS NULL OR adjuster_id ~ '^ADJ[0-9]{3}$'),
    CONSTRAINT ck_core_detail_provider_id
        CHECK (provider_id ~ '^PRV[0-9]{4}$'),
    CONSTRAINT ck_core_detail_visits
        CHECK (number_of_visits IS NULL OR number_of_visits >= 1),
    CONSTRAINT ck_core_detail_prescription_days
        CHECK (prescription_days_supply IS NULL OR prescription_days_supply BETWEEN 1 AND 90),
    CONSTRAINT ck_core_detail_trip_dates
        CHECK (trip_start IS NULL OR trip_end IS NULL OR trip_end >= trip_start),
    CONSTRAINT ck_core_detail_fx_rate
        CHECK (exchange_rate_to_cad IS NULL OR exchange_rate_to_cad > 0),
    CONSTRAINT ck_core_detail_incident_sub_limit
        CHECK (incident_sub_limit IS NULL OR incident_sub_limit > 0)
);

CREATE TABLE IF NOT EXISTS core.claim_line_items (
    claim_id            text NOT NULL REFERENCES core.claims(claim_id),
    line_no             integer NOT NULL,

    service_date        date NOT NULL,
    description         text NOT NULL,
    quantity            integer NOT NULL,
    unit_amount         numeric(12,2) NOT NULL,
    amount              numeric(12,2) NOT NULL,

    procedure_code      text,
    procedure_category  text,
    tooth_number        integer,

    source_load_id      bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_file_id  bigint NOT NULL REFERENCES raw.claim_detail_files(source_file_id),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (claim_id, line_no),

    CONSTRAINT ck_core_line_no
        CHECK (line_no > 0),
    CONSTRAINT ck_core_line_quantity
        CHECK (quantity > 0),
    CONSTRAINT ck_core_line_unit_amount
        CHECK (unit_amount > 0),
    CONSTRAINT ck_core_line_amount
        CHECK (amount > 0),
    CONSTRAINT ck_core_line_arithmetic
        CHECK (abs(amount - (quantity * unit_amount)) <= 0.01),
    CONSTRAINT ck_core_line_procedure_code
        CHECK (procedure_code IS NULL OR procedure_code ~ '^[0-9]{5}$'),
    CONSTRAINT ck_core_line_procedure_category
        CHECK (
            procedure_category IS NULL
            OR procedure_category IN ('preventive', 'basic', 'major')
        ),
    CONSTRAINT ck_core_line_tooth_number
        CHECK (
            tooth_number IS NULL
            OR tooth_number BETWEEN 11 AND 18
            OR tooth_number BETWEEN 21 AND 28
            OR tooth_number BETWEEN 31 AND 38
            OR tooth_number BETWEEN 41 AND 48
        )
);
),
    CONSTRAINT ck_core_customer_gender
        CHECK (gender IS NULL OR gender IN ('F', 'M', 'X')),
    CONSTRAINT ck_core_customer_province
        CHECK (province IN ('SK', 'AB', 'MB', 'ON', 'BC', 'NS', 'PE', 'NL', 'YT', 'NT')),
    CONSTRAINT ck_core_customer_fsa
        CHECK (fsa IS NULL OR fsa ~ '^[ABCEGHJ-NPRSTVXY][0-9][ABCEGHJ-NPRSTV-Z]
CREATE TABLE IF NOT EXISTS core.policies (
    policy_id           text PRIMARY KEY,
    customer_id         text NOT NULL REFERENCES core.customers(customer_id),

    policy_type         text NOT NULL,
    plan_name           text,
    coverage_type       text NOT NULL,
    start_date          date NOT NULL,
    end_date            date,
    status              text NOT NULL,
    sales_channel       text,
    coverage_amount     numeric(12,2) NOT NULL,
    deductible_amount   numeric(12,2) NOT NULL,

    source_load_id      bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_row_id   bigint NOT NULL REFERENCES raw.policy_csv(raw_row_id),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_policy_id
        CHECK (policy_id ~ '^P[0-9]{5}$'),
    CONSTRAINT ck_core_policy_type
        CHECK (policy_type IN ('health', 'dental', 'travel')),
    CONSTRAINT ck_core_policy_plan
        CHECK (
            plan_name IS NULL
            OR plan_name IN (
                'BasicPlan',
                'ExtendaPlan',
                'OmniPlan',
                'Replacement Health',
                'Dental Basic',
                'Dental Plus',
                'TravelStar',
                'StudentPlan'
            )
        ),
    CONSTRAINT ck_core_policy_coverage_type
        CHECK (coverage_type IN ('single', 'couple', 'family')),
    CONSTRAINT ck_core_policy_status
        CHECK (status IN ('active', 'lapsed', 'cancelled', 'expired')),
    CONSTRAINT ck_core_policy_sales_channel
        CHECK (sales_channel IS NULL OR sales_channel IN ('online', 'broker', 'call_centre')),
    CONSTRAINT ck_core_policy_date_order
        CHECK (end_date IS NULL OR end_date >= start_date),
    CONSTRAINT ck_core_policy_coverage_amount
        CHECK (coverage_amount > 0),
    CONSTRAINT ck_core_policy_deductible_amount
        CHECK (deductible_amount >= 0)
);

CREATE TABLE IF NOT EXISTS core.claims (
    claim_id            text PRIMARY KEY,
    policy_id           text NOT NULL REFERENCES core.policies(policy_id),

    claim_type          text NOT NULL,
    service_date        date,
    claim_date          date NOT NULL,
    claim_amount        numeric(12,2),
    approved_amount     numeric(12,2),

    source_load_id      bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_row_id   bigint NOT NULL REFERENCES raw.claim_csv(raw_row_id),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_claim_id
        CHECK (claim_id ~ '^[HDT][0-9]{5}$'),
    CONSTRAINT ck_core_claim_type
        CHECK (
            claim_type IN (
                'prescription_drugs',
                'health_practitioner',
                'vision',
                'hearing_aids',
                'medical_equipment',
                'ambulance',
                'hospital_cash',
                'preventive',
                'basic',
                'major',
                'emergency_medical',
                'trip_cancellation',
                'trip_interruption',
                'baggage'
            )
        ),
    CONSTRAINT ck_core_claim_date_order
        CHECK (service_date IS NULL OR service_date <= claim_date),
    CONSTRAINT ck_core_claim_amount
        CHECK (claim_amount IS NULL OR claim_amount > 0),
    CONSTRAINT ck_core_claim_approved_amount
        CHECK (
            approved_amount IS NULL
            OR (
                approved_amount >= 0
                AND (claim_amount IS NULL OR approved_amount <= claim_amount)
            )
        )
);

CREATE TABLE IF NOT EXISTS core.claim_payments (
    payment_id              text PRIMARY KEY,
    claim_id                text NOT NULL UNIQUE REFERENCES core.claims(claim_id),

    processing_start_date   date,
    decision_date           date,
    decision_outcome        text NOT NULL,
    payment_date            date,
    payment_amount          numeric(12,2) NOT NULL,
    payment_method          text,
    payment_status          text,
    transaction_reference   text UNIQUE,
    denial_reason           text,

    source_load_id          bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_row_id       bigint NOT NULL REFERENCES raw.claim_payment_csv(raw_row_id),
    updated_at              timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_payment_id
        CHECK (payment_id ~ '^PAY[0-9]{5}$'),
    CONSTRAINT ck_core_payment_decision_outcome
        CHECK (decision_outcome IN ('approved', 'partially_approved', 'denied')),
    CONSTRAINT ck_core_payment_method
        CHECK (payment_method IS NULL OR payment_method IN ('direct_deposit', 'cheque', 'provider_direct')),
    CONSTRAINT ck_core_payment_status
        CHECK (payment_status IS NULL OR payment_status IN ('paid', 'scheduled')),
    CONSTRAINT ck_core_payment_denial_reason
        CHECK (
            denial_reason IS NULL
            OR denial_reason IN (
                'not_covered',
                'waiting_period',
                'benefit_maximum_reached',
                'missing_documentation',
                'policy_inactive',
                'late_submission',
                'duplicate_claim'
            )
        ),
    CONSTRAINT ck_core_payment_amount
        CHECK (payment_amount >= 0),
    CONSTRAINT ck_core_payment_processing_decision_order
        CHECK (
            processing_start_date IS NULL
            OR decision_date IS NULL
            OR decision_date >= processing_start_date
        ),
    CONSTRAINT ck_core_payment_decision_payment_order
        CHECK (
            decision_date IS NULL
            OR payment_date IS NULL
            OR payment_date >= decision_date
        ),
    CONSTRAINT ck_core_payment_denied_semantics
        CHECK (
            decision_outcome <> 'denied'
            OR (
                payment_amount = 0
                AND payment_date IS NULL
                AND payment_method IS NULL
                AND payment_status IS NULL
                AND transaction_reference IS NULL
                AND denial_reason IS NOT NULL
            )
        ),
    CONSTRAINT ck_core_payment_nondenied_semantics
        CHECK (
            decision_outcome = 'denied'
            OR payment_method IS NOT NULL
        ),
    CONSTRAINT ck_core_payment_paid_semantics
        CHECK (
            payment_status IS DISTINCT FROM 'paid'
            OR (
                payment_date IS NOT NULL
                AND transaction_reference IS NOT NULL
            )
        ),
    CONSTRAINT ck_core_payment_scheduled_semantics
        CHECK (
            payment_status IS DISTINCT FROM 'scheduled'
            OR (
                payment_date IS NULL
                AND transaction_reference IS NULL
            )
        )
);

CREATE TABLE IF NOT EXISTS core.policy_premiums (
    premium_id          text PRIMARY KEY,
    policy_id           text NOT NULL REFERENCES core.policies(policy_id),

    premium_amount      numeric(12,2) NOT NULL,
    premium_frequency   text NOT NULL,
    age_band            text NOT NULL,
    due_date            date NOT NULL,
    paid_date           date,
    payment_status      text NOT NULL,
    payment_method      text,

    source_load_id      bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_row_id   bigint NOT NULL REFERENCES raw.policy_premium_csv(raw_row_id),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_premium_id
        CHECK (premium_id ~ '^PRM[0-9]{6}$'),
    CONSTRAINT ck_core_premium_amount
        CHECK (premium_amount > 0),
    CONSTRAINT ck_core_premium_frequency
        CHECK (premium_frequency IN ('monthly', 'quarterly', 'yearly', 'single')),
    CONSTRAINT ck_core_premium_age_band
        CHECK (age_band IN ('Under 35', '35-44', '45-54', '55-64', '65-74', '75+')),
    CONSTRAINT ck_core_premium_status
        CHECK (payment_status IN ('paid', 'late', 'missed')),
    CONSTRAINT ck_core_premium_method
        CHECK (payment_method IS NULL OR payment_method IN ('pad', 'credit_card', 'cheque')),
    CONSTRAINT ck_core_premium_status_dates
        CHECK (
            (payment_status = 'paid' AND paid_date IS NOT NULL AND paid_date <= due_date)
            OR
            (payment_status = 'late' AND paid_date IS NOT NULL AND paid_date > due_date)
            OR
            (payment_status = 'missed' AND paid_date IS NULL AND payment_method IS NULL)
        )
);

CREATE TABLE IF NOT EXISTS core.claim_details (
    claim_id                    text PRIMARY KEY REFERENCES core.claims(claim_id),

    schema_version              text NOT NULL,
    product_line                text NOT NULL,
    submission_channel          text,
    submitted_at                timestamptz NOT NULL,

    provider_id                 text NOT NULL,
    provider_name               text NOT NULL,
    provider_type               text,

    adjuster_id                 text,
    adjuster_notes              text,
    documents_submitted         text[],

    practitioner_type           text,
    number_of_visits            integer,
    prescription_din            text,
    prescription_drug_name      text,
    prescription_days_supply    integer,
    prescription_is_generic     boolean,

    dental_claim_category       text,

    trip_start                  date,
    trip_end                    date,
    destination_country         text,
    incident_type               text,
    incident_date               date,
    currency                    text,
    exchange_rate_to_cad        numeric(10,4),
    incident_sub_limit          numeric(12,2),

    source_load_id              bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_file_id          bigint NOT NULL REFERENCES raw.claim_detail_files(source_file_id),
    updated_at                  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_detail_product_line
        CHECK (product_line IN ('health', 'dental', 'travel')),
    CONSTRAINT ck_core_detail_submission_channel
        CHECK (
            submission_channel IS NULL
            OR submission_channel IN ('online_portal', 'mobile_app', 'provider_direct_billing', 'mail')
        ),
    CONSTRAINT ck_core_detail_adjuster_id
        CHECK (adjuster_id IS NULL OR adjuster_id ~ '^ADJ[0-9]{3}$'),
    CONSTRAINT ck_core_detail_provider_id
        CHECK (provider_id ~ '^PRV[0-9]{4}$'),
    CONSTRAINT ck_core_detail_visits
        CHECK (number_of_visits IS NULL OR number_of_visits >= 1),
    CONSTRAINT ck_core_detail_prescription_days
        CHECK (prescription_days_supply IS NULL OR prescription_days_supply BETWEEN 1 AND 90),
    CONSTRAINT ck_core_detail_trip_dates
        CHECK (trip_start IS NULL OR trip_end IS NULL OR trip_end >= trip_start),
    CONSTRAINT ck_core_detail_fx_rate
        CHECK (exchange_rate_to_cad IS NULL OR exchange_rate_to_cad > 0),
    CONSTRAINT ck_core_detail_incident_sub_limit
        CHECK (incident_sub_limit IS NULL OR incident_sub_limit > 0)
);

CREATE TABLE IF NOT EXISTS core.claim_line_items (
    claim_id            text NOT NULL REFERENCES core.claims(claim_id),
    line_no             integer NOT NULL,

    service_date        date NOT NULL,
    description         text NOT NULL,
    quantity            integer NOT NULL,
    unit_amount         numeric(12,2) NOT NULL,
    amount              numeric(12,2) NOT NULL,

    procedure_code      text,
    procedure_category  text,
    tooth_number        integer,

    source_load_id      bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_file_id  bigint NOT NULL REFERENCES raw.claim_detail_files(source_file_id),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (claim_id, line_no),

    CONSTRAINT ck_core_line_no
        CHECK (line_no > 0),
    CONSTRAINT ck_core_line_quantity
        CHECK (quantity > 0),
    CONSTRAINT ck_core_line_unit_amount
        CHECK (unit_amount > 0),
    CONSTRAINT ck_core_line_amount
        CHECK (amount > 0),
    CONSTRAINT ck_core_line_arithmetic
        CHECK (abs(amount - (quantity * unit_amount)) <= 0.01),
    CONSTRAINT ck_core_line_procedure_code
        CHECK (procedure_code IS NULL OR procedure_code ~ '^[0-9]{5}$'),
    CONSTRAINT ck_core_line_procedure_category
        CHECK (
            procedure_category IS NULL
            OR procedure_category IN ('preventive', 'basic', 'major')
        ),
    CONSTRAINT ck_core_line_tooth_number
        CHECK (
            tooth_number IS NULL
            OR tooth_number BETWEEN 11 AND 18
            OR tooth_number BETWEEN 21 AND 28
            OR tooth_number BETWEEN 31 AND 38
            OR tooth_number BETWEEN 41 AND 48
        )
);
)
);

-- Compatibility migration for databases created before PII minimization.
ALTER TABLE core.customers DROP CONSTRAINT IF EXISTS ck_core_customer_postal;
ALTER TABLE core.customers DROP CONSTRAINT IF EXISTS ck_core_customer_phone;
ALTER TABLE core.customers ADD COLUMN IF NOT EXISTS fsa text;
ALTER TABLE core.customers DROP COLUMN IF EXISTS first_name;
ALTER TABLE core.customers DROP COLUMN IF EXISTS last_name;
ALTER TABLE core.customers DROP COLUMN IF EXISTS address;
ALTER TABLE core.customers DROP COLUMN IF EXISTS city;
ALTER TABLE core.customers DROP COLUMN IF EXISTS postal_code;
ALTER TABLE core.customers DROP COLUMN IF EXISTS phone;
ALTER TABLE core.customers DROP COLUMN IF EXISTS email;

CREATE TABLE IF NOT EXISTS core.policies (
    policy_id           text PRIMARY KEY,
    customer_id         text NOT NULL REFERENCES core.customers(customer_id),

    policy_type         text NOT NULL,
    plan_name           text,
    coverage_type       text NOT NULL,
    start_date          date NOT NULL,
    end_date            date,
    status              text NOT NULL,
    sales_channel       text,
    coverage_amount     numeric(12,2) NOT NULL,
    deductible_amount   numeric(12,2) NOT NULL,

    source_load_id      bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_row_id   bigint NOT NULL REFERENCES raw.policy_csv(raw_row_id),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_policy_id
        CHECK (policy_id ~ '^P[0-9]{5}$'),
    CONSTRAINT ck_core_policy_type
        CHECK (policy_type IN ('health', 'dental', 'travel')),
    CONSTRAINT ck_core_policy_plan
        CHECK (
            plan_name IS NULL
            OR plan_name IN (
                'BasicPlan',
                'ExtendaPlan',
                'OmniPlan',
                'Replacement Health',
                'Dental Basic',
                'Dental Plus',
                'TravelStar',
                'StudentPlan'
            )
        ),
    CONSTRAINT ck_core_policy_coverage_type
        CHECK (coverage_type IN ('single', 'couple', 'family')),
    CONSTRAINT ck_core_policy_status
        CHECK (status IN ('active', 'lapsed', 'cancelled', 'expired')),
    CONSTRAINT ck_core_policy_sales_channel
        CHECK (sales_channel IS NULL OR sales_channel IN ('online', 'broker', 'call_centre')),
    CONSTRAINT ck_core_policy_date_order
        CHECK (end_date IS NULL OR end_date >= start_date),
    CONSTRAINT ck_core_policy_coverage_amount
        CHECK (coverage_amount > 0),
    CONSTRAINT ck_core_policy_deductible_amount
        CHECK (deductible_amount >= 0)
);

CREATE TABLE IF NOT EXISTS core.claims (
    claim_id            text PRIMARY KEY,
    policy_id           text NOT NULL REFERENCES core.policies(policy_id),

    claim_type          text NOT NULL,
    service_date        date,
    claim_date          date NOT NULL,
    claim_amount        numeric(12,2),
    approved_amount     numeric(12,2),

    source_load_id      bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_row_id   bigint NOT NULL REFERENCES raw.claim_csv(raw_row_id),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_claim_id
        CHECK (claim_id ~ '^[HDT][0-9]{5}$'),
    CONSTRAINT ck_core_claim_type
        CHECK (
            claim_type IN (
                'prescription_drugs',
                'health_practitioner',
                'vision',
                'hearing_aids',
                'medical_equipment',
                'ambulance',
                'hospital_cash',
                'preventive',
                'basic',
                'major',
                'emergency_medical',
                'trip_cancellation',
                'trip_interruption',
                'baggage'
            )
        ),
    CONSTRAINT ck_core_claim_date_order
        CHECK (service_date IS NULL OR service_date <= claim_date),
    CONSTRAINT ck_core_claim_amount
        CHECK (claim_amount IS NULL OR claim_amount > 0),
    CONSTRAINT ck_core_claim_approved_amount
        CHECK (
            approved_amount IS NULL
            OR (
                approved_amount >= 0
                AND (claim_amount IS NULL OR approved_amount <= claim_amount)
            )
        )
);

CREATE TABLE IF NOT EXISTS core.claim_payments (
    payment_id              text PRIMARY KEY,
    claim_id                text NOT NULL UNIQUE REFERENCES core.claims(claim_id),

    processing_start_date   date,
    decision_date           date,
    decision_outcome        text NOT NULL,
    payment_date            date,
    payment_amount          numeric(12,2) NOT NULL,
    payment_method          text,
    payment_status          text,
    transaction_reference   text UNIQUE,
    denial_reason           text,

    source_load_id          bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_row_id       bigint NOT NULL REFERENCES raw.claim_payment_csv(raw_row_id),
    updated_at              timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_payment_id
        CHECK (payment_id ~ '^PAY[0-9]{5}$'),
    CONSTRAINT ck_core_payment_decision_outcome
        CHECK (decision_outcome IN ('approved', 'partially_approved', 'denied')),
    CONSTRAINT ck_core_payment_method
        CHECK (payment_method IS NULL OR payment_method IN ('direct_deposit', 'cheque', 'provider_direct')),
    CONSTRAINT ck_core_payment_status
        CHECK (payment_status IS NULL OR payment_status IN ('paid', 'scheduled')),
    CONSTRAINT ck_core_payment_denial_reason
        CHECK (
            denial_reason IS NULL
            OR denial_reason IN (
                'not_covered',
                'waiting_period',
                'benefit_maximum_reached',
                'missing_documentation',
                'policy_inactive',
                'late_submission',
                'duplicate_claim'
            )
        ),
    CONSTRAINT ck_core_payment_amount
        CHECK (payment_amount >= 0),
    CONSTRAINT ck_core_payment_processing_decision_order
        CHECK (
            processing_start_date IS NULL
            OR decision_date IS NULL
            OR decision_date >= processing_start_date
        ),
    CONSTRAINT ck_core_payment_decision_payment_order
        CHECK (
            decision_date IS NULL
            OR payment_date IS NULL
            OR payment_date >= decision_date
        ),
    CONSTRAINT ck_core_payment_denied_semantics
        CHECK (
            decision_outcome <> 'denied'
            OR (
                payment_amount = 0
                AND payment_date IS NULL
                AND payment_method IS NULL
                AND payment_status IS NULL
                AND transaction_reference IS NULL
                AND denial_reason IS NOT NULL
            )
        ),
    CONSTRAINT ck_core_payment_nondenied_semantics
        CHECK (
            decision_outcome = 'denied'
            OR payment_method IS NOT NULL
        ),
    CONSTRAINT ck_core_payment_paid_semantics
        CHECK (
            payment_status IS DISTINCT FROM 'paid'
            OR (
                payment_date IS NOT NULL
                AND transaction_reference IS NOT NULL
            )
        ),
    CONSTRAINT ck_core_payment_scheduled_semantics
        CHECK (
            payment_status IS DISTINCT FROM 'scheduled'
            OR (
                payment_date IS NULL
                AND transaction_reference IS NULL
            )
        )
);

CREATE TABLE IF NOT EXISTS core.policy_premiums (
    premium_id          text PRIMARY KEY,
    policy_id           text NOT NULL REFERENCES core.policies(policy_id),

    premium_amount      numeric(12,2) NOT NULL,
    premium_frequency   text NOT NULL,
    age_band            text NOT NULL,
    due_date            date NOT NULL,
    paid_date           date,
    payment_status      text NOT NULL,
    payment_method      text,

    source_load_id      bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_row_id   bigint NOT NULL REFERENCES raw.policy_premium_csv(raw_row_id),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_premium_id
        CHECK (premium_id ~ '^PRM[0-9]{6}$'),
    CONSTRAINT ck_core_premium_amount
        CHECK (premium_amount > 0),
    CONSTRAINT ck_core_premium_frequency
        CHECK (premium_frequency IN ('monthly', 'quarterly', 'yearly', 'single')),
    CONSTRAINT ck_core_premium_age_band
        CHECK (age_band IN ('Under 35', '35-44', '45-54', '55-64', '65-74', '75+')),
    CONSTRAINT ck_core_premium_status
        CHECK (payment_status IN ('paid', 'late', 'missed')),
    CONSTRAINT ck_core_premium_method
        CHECK (payment_method IS NULL OR payment_method IN ('pad', 'credit_card', 'cheque')),
    CONSTRAINT ck_core_premium_status_dates
        CHECK (
            (payment_status = 'paid' AND paid_date IS NOT NULL AND paid_date <= due_date)
            OR
            (payment_status = 'late' AND paid_date IS NOT NULL AND paid_date > due_date)
            OR
            (payment_status = 'missed' AND paid_date IS NULL AND payment_method IS NULL)
        )
);

CREATE TABLE IF NOT EXISTS core.claim_details (
    claim_id                    text PRIMARY KEY REFERENCES core.claims(claim_id),

    schema_version              text NOT NULL,
    product_line                text NOT NULL,
    submission_channel          text,
    submitted_at                timestamptz NOT NULL,

    provider_id                 text NOT NULL,
    provider_name               text NOT NULL,
    provider_type               text,

    adjuster_id                 text,
    adjuster_notes              text,
    documents_submitted         text[],

    practitioner_type           text,
    number_of_visits            integer,
    prescription_din            text,
    prescription_drug_name      text,
    prescription_days_supply    integer,
    prescription_is_generic     boolean,

    dental_claim_category       text,

    trip_start                  date,
    trip_end                    date,
    destination_country         text,
    incident_type               text,
    incident_date               date,
    currency                    text,
    exchange_rate_to_cad        numeric(10,4),
    incident_sub_limit          numeric(12,2),

    source_load_id              bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_file_id          bigint NOT NULL REFERENCES raw.claim_detail_files(source_file_id),
    updated_at                  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_core_detail_product_line
        CHECK (product_line IN ('health', 'dental', 'travel')),
    CONSTRAINT ck_core_detail_submission_channel
        CHECK (
            submission_channel IS NULL
            OR submission_channel IN ('online_portal', 'mobile_app', 'provider_direct_billing', 'mail')
        ),
    CONSTRAINT ck_core_detail_adjuster_id
        CHECK (adjuster_id IS NULL OR adjuster_id ~ '^ADJ[0-9]{3}$'),
    CONSTRAINT ck_core_detail_provider_id
        CHECK (provider_id ~ '^PRV[0-9]{4}$'),
    CONSTRAINT ck_core_detail_visits
        CHECK (number_of_visits IS NULL OR number_of_visits >= 1),
    CONSTRAINT ck_core_detail_prescription_days
        CHECK (prescription_days_supply IS NULL OR prescription_days_supply BETWEEN 1 AND 90),
    CONSTRAINT ck_core_detail_trip_dates
        CHECK (trip_start IS NULL OR trip_end IS NULL OR trip_end >= trip_start),
    CONSTRAINT ck_core_detail_fx_rate
        CHECK (exchange_rate_to_cad IS NULL OR exchange_rate_to_cad > 0),
    CONSTRAINT ck_core_detail_incident_sub_limit
        CHECK (incident_sub_limit IS NULL OR incident_sub_limit > 0)
);

CREATE TABLE IF NOT EXISTS core.claim_line_items (
    claim_id            text NOT NULL REFERENCES core.claims(claim_id),
    line_no             integer NOT NULL,

    service_date        date NOT NULL,
    description         text NOT NULL,
    quantity            integer NOT NULL,
    unit_amount         numeric(12,2) NOT NULL,
    amount              numeric(12,2) NOT NULL,

    procedure_code      text,
    procedure_category  text,
    tooth_number        integer,

    source_load_id      bigint NOT NULL REFERENCES raw.ingestion_runs(load_id),
    source_raw_file_id  bigint NOT NULL REFERENCES raw.claim_detail_files(source_file_id),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (claim_id, line_no),

    CONSTRAINT ck_core_line_no
        CHECK (line_no > 0),
    CONSTRAINT ck_core_line_quantity
        CHECK (quantity > 0),
    CONSTRAINT ck_core_line_unit_amount
        CHECK (unit_amount > 0),
    CONSTRAINT ck_core_line_amount
        CHECK (amount > 0),
    CONSTRAINT ck_core_line_arithmetic
        CHECK (abs(amount - (quantity * unit_amount)) <= 0.01),
    CONSTRAINT ck_core_line_procedure_code
        CHECK (procedure_code IS NULL OR procedure_code ~ '^[0-9]{5}$'),
    CONSTRAINT ck_core_line_procedure_category
        CHECK (
            procedure_category IS NULL
            OR procedure_category IN ('preventive', 'basic', 'major')
        ),
    CONSTRAINT ck_core_line_tooth_number
        CHECK (
            tooth_number IS NULL
            OR tooth_number BETWEEN 11 AND 18
            OR tooth_number BETWEEN 21 AND 28
            OR tooth_number BETWEEN 31 AND 38
            OR tooth_number BETWEEN 41 AND 48
        )
);
