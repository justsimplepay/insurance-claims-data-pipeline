# Source Data Dictionary: Raw Claims Sources

*GMS Data Engineer Case Study. Version 1.2, Wednesday, September 23, 2026. Based on the project overview v1.5.*
*Machine-readable companion: `docs/source_data_dictionary.yaml` (the YAML is authoritative; the tables below are rendered from it).*

---

## 1. Purpose and Scope

This document defines what the **raw, messy source files** look like before any processing: five CSV extracts and 200 JSON claim-detail files.

**Source-model assumption:** the three Claims Master Data files are represented as separate entity extracts—`Customer.csv`, `Policy.csv`, and `Claim.csv`—corresponding to customer demographics, policy details, and claim information in the case study. This is a modelling assumption, not a claim that the brief permits only this interpretation.

**Design discipline:** source complexity is included only where it serves the assessment. Every field or rule must support at least one analytics objective, establish an important relationship/grain, or create/resolve a meaningful data-quality or reconciliation case. Complexity for its own sake is avoided. The raw layer is intentionally not perfectly normalized: selected redundant attributes and heterogeneous JSON shapes are retained where they create realistic reconciliation, schema-normalization, or data-quality work; staging/core establish the canonical values.

It serves two readers:

- the **generator** (chat 4), which builds a clean ground truth to this specification and then injects the listed defects;
- the **pipeline** (chats 5 to 7), which must parse, validate and reconcile these files.

Out of scope: the staging, core and mart schemas, which belong to the Database Model (`docs/database_model.md`). No data is generated here.

Every field is described by its **ground-truth** definition. The *Defects injected* table under each file lists how the raw file departs from it.

---

## 2. Conventions

| Item | Convention |
|---|---|
| Encoding / CSV | UTF-8, comma-delimited, `"` quoting, header row |
| Nulls | CSV: empty field. JSON: `null`. In JSON an **absent key** is not the same as `null`; absence is a defect (`JSON_MISSING_KEY`). |
| Dates | ISO 8601 `YYYY-MM-DD` in the ground truth |
| Timestamps | ISO 8601 with offset `-06:00` (America/Regina, no daylight saving time) |
| Money | CAD, 2 decimals. Travel line items may be in a foreign currency and carry an exchange rate. |
| Identifiers | Upper-case prefix plus zero-padded digits, stored as **text** (leading zeros matter) |
| Enum values | `lower_snake_case`, except proper names (plan names, province codes, `F`/`M`/`X`, age bands) |

**Columns in the field tables**

- **Type** is the logical type in the ground truth. In the raw CSV every value is text; the type is what the value must parse to.
- **Nullable**: `No`, or `Yes` followed by *when* a null is legitimate. A null outside that condition is an error (`MISS_ERROR`).
- **Key**: `PK` primary key, `FK →` foreign key (the parent is authoritative), `Copy of` denormalized copy (**not** authoritative; see overview §4.5), `NK` natural key from an external system.
- **Obj.**: objectives that use the field: **1** Fraud, **2** Retention, **3** Operations, **4** Region, **5** Policy. `–` means the field is used only for integrity or entity resolution.
- **PII** (Decision 5): raw preserves source values; downstream layers retain only what is necessary. Names/contact/address fields are scrubbed after staging identity resolution, exact DOB is retained only where needed for age-band derivation, and quasi-identifiers reach marts only in generalized form (age band, province/FSA).

### Generation parameters

| Parameter | Value |
|---|---|
| Random seed | 20260923 |
| History window | 2023-07-01 to 2026-06-30 (extract end) |
| Retention observation | 2025-04-01 to 2026-03-31; snapshot 2026-03-31; outcome window 2026-04-01 to 2026-06-30 |
| Base volumes | 150 customers, 180 policies, 210 claims, exactly 200 JSON files |

---

## 3. File Inventory

| File | Brief group | Grain | Primary key | Expected raw rows | Non-ISO date format |
|---|---|---|---|---|---|
| `Customer.csv` | Claims Master Data (1 of 3) | one row per customer | `customer_id` | ~157 (150 base + 4 duplicate-person rows + 3 unserved-province customers) | `DD/MM/YYYY`, `Mon DD, YYYY` |
| `Policy.csv` | Claims Master Data (2 of 3) | one row per policy | `policy_id` | ~183 (180 base + 3 policies of unserved-province customers) | `MM/DD/YYYY`, `Mon DD, YYYY` |
| `Claim.csv` | Claims Master Data (3 of 3) | one row per claim | `claim_id` | ~220 (210 base + 6 exact duplicates + 4 near-duplicates) | `DD/MM/YYYY`, `Mon DD, YYYY` |
| `Claim_Payment.csv` | Claims Payment Data | one row per adjudication decision (at most one per claim in the ground truth) | `payment_id` | ~203 (~195 decided claims + 5 exact duplicates + 3 orphans) | `YYYYMMDD`, `Mon DD, YYYY` |
| `Policy_Premium.csv` | Policy Premium Data | one row per premium instalment | `premium_id` | ~3,500 (instalments due 2023-07-01 to 2026-06-30, + 15 duplicates + 5 orphans) | `MM/DD/YYYY`, `Mon DD, YYYY` |
| `json/<claim_id>.json` | Claim Details | one file per claim | `claim_id` | exactly 200 files | ISO only |

**Relationships between sources** (keys only; the parent is authoritative):

```
Customer.csv ──< Policy.csv ──< Claim.csv ──< Claim_Payment.csv   (0..1 per claim)
                     │              └──< json/<claim_id>.json    (0..1 per claim)
                     └──< Policy_Premium.csv
Denormalized copies:  Claim.customer_id, Policy_Premium.customer_id  → Policy.customer_id
                      Claim.region                                   → Customer.province
                      Claim.claim_status                             → Claim_Payment.decision_outcome
```

**Secondary date conventions.** Each source system writes non-ISO dates in *one* documented slash or compact convention (column "Non-ISO date format" above). `DD/MM` and `MM/DD` are ambiguous whenever the day is 12 or less, so the pipeline must parse by the documented convention of the file and never guess row by row. In a real engagement this convention would be confirmed with the source-system owner; recording it here is part of the dictionary's job.

---

## 4. `Customer.csv`: Customer Demographics

Master file 1 of 3. Grain: one row per customer.

| Field | Type | Description | Allowed values / format | Nullable | Key | Obj. | PII |
|---|---|---|---|---|---|---|---|
| `customer_id` | string | Customer identifier assigned by the policy administration system. | `C` + 5 digits, e.g. `C00017` | No | PK | 1,2,3,4,5 | none |
| `first_name` | string | Given name. Used transiently for duplicate-person resolution in staging, then scrubbed. | Title case, trimmed (Faker en_CA) | No | – | – | direct |
| `last_name` | string | Family name. Used transiently for duplicate-person resolution in staging, then scrubbed. | Title case, trimmed (Faker en_CA) | No | – | – | direct |
| `date_of_birth` | date | Policyholder date of birth. Marts receive age / age band only. | ISO date; age 18-89 on the extract end date | No | – | 1,2,4,5 | direct |
| `gender` | enum | Self-reported gender. | `F`, `M`, `X` | Yes — legit: not disclosed (~3%). No error nulls are injected in this field. | – | 2,5 | none |
| `address` | string | Street address (civic number, street, optional unit). | e.g. `2410 Albert St Unit 5` | No | – | – | direct |
| `city` | string | City of residence. Retained in raw for source fidelity and staging validation, then scrubbed before core. | Title case; from reference_data.provinces_served[].cities | No | – | – | quasi |
| `province` | enum | Home province/territory code. Authoritative source for a claim's region (overview 4.5). | `SK`, `AB`, `MB`, `ON`, `BC`, `NS`, `PE`, `NL`, `YT`, `NT` | No | – | 1,2,3,4,5 | quasi |
| `postal_code` | string | Canadian postal code. First letter must agree with province; marts receive the FSA (first 3 characters) only. | `A1A 1A1`, upper case, one space | No | – | 4 | direct |
| `phone` | string | Primary phone number; area code consistent with province. | E.164, e.g. `+13065550142` | Yes — legit: not provided (~5%). | – | – | direct |
| `email` | string | Contact e-mail address. | lower case, `local@domain.tld` | Yes — legit: not provided (~8%). | – | – | direct |
| `customer_since` | date | Start of the customer relationship (first-ever policy, including policies older than the extract). True tenure for retention. | ISO date; 2005-01-01 <= value <= earliest Policy.start_date of the customer | No | – | 2,5 | none |
| `last_updated` | datetime | Last modification timestamp in the source system. Tie-breaker ('most recent') when merging duplicate persons. | ISO datetime with -06:00 offset; customer_since <= value <= extract end | No | – | – | none |

**Record rules:** province must be a served province, and the first letter of `postal_code` must belong to it. The phone area code matches the province. `customer_since` can be earlier than the history window (long-tenured customers).

**Defects injected** ("n rows" = absolute count; "x% of dirty pool" = share of the file's dirty-row pool, see §13)

| Defect | Fields | Target | What is injected |
|---|---|---|---|
| `DUP_ENTITY` | `customer_id` | 4 rows/files | 4 persons re-entered under a new customer_id. Normalized first_name, last_name, date_of_birth, postal_code are identical; raw formatting may differ; one row of each pair lacks phone or email; last_updated differs. At least 2 of the duplicate IDs own a policy. |
| `INV_UNSERVED_PROVINCE` | `province`, `postal_code` | 3 rows/files | 3 additional customers in QC, NB, NU with matching postal codes; each owns one policy and 0-1 claims, so dependants must be handled. |
| `INV_AGE` | `date_of_birth` | 3 rows/files | 1 future date, 1 sentinel `1900-01-01`, 1 age of 15 at policy start. |
| `MISS_ERROR` | `date_of_birth`, `province` | 4 rows/files | 2 missing date_of_birth; 2 missing province (recoverable from the postal-code first letter). |
| `FMT_DATE` | `date_of_birth`, `customer_since` | 40% of dirty pool | Variants: `DD/MM/YYYY`, `Mon DD, YYYY` |
| `FMT_PROVINCE` | `province` | 48% of dirty pool | Variants: `Sask.`, `saskatchewan`, `Saskatchewan`, ` sk `, `Alta.`, `Ontario`, `man.` |
| `FMT_GENDER` | `gender` | 60% of dirty pool | Variants: `Female`, `female`, `f`, `Male`, `MALE`, `m`, `Non-binary` |
| `FMT_PHONE` | `phone` | 60% of dirty pool | Variants: `(306) 555-0142`, `306-555-0142`, `306.555.0142`, `3065550142`, `+1 306 555 0142` |
| `FMT_POSTAL` | `postal_code` | 60% of dirty pool | Variants: `s4p 3y2`, `S4P3Y2`, ` S4P 3Y2 ` |
| `FMT_TEXT_WHITESPACE` | `first_name`, `last_name`, `city`, `email` | 32% of dirty pool | Variants: `trailing space`, `UPPER CASE`, `lower case` |

---

## 5. `Policy.csv`: Policy Details

Master file 2 of 3. Grain: one row per policy.

| Field | Type | Description | Allowed values / format | Nullable | Key | Obj. | PII |
|---|---|---|---|---|---|---|---|
| `policy_id` | string | Policy identifier. | `P` + 5 digits, e.g. `P00104` | No | PK | 1,2,4,5 | none |
| `customer_id` | string | Policy owner. Authoritative owner for claims and premiums (overview 4.5). | `C` + 5 digits | No | FK → `Customer.customer_id` | 1,2,5 | none |
| `policy_type` | enum | Product line. | `health`, `dental`, `travel` | No | – | 1,2,3,4,5 | none |
| `plan_name` | enum | Plan within the product line; must belong to policy_type (reference_data.plans). | `BasicPlan`, `ExtendaPlan`, `OmniPlan`, `Replacement Health`, `Dental Basic`, `Dental Plus`, `TravelStar`, `StudentPlan` | No | – | 1,2,5 | none |
| `coverage_type` | enum | Who is covered. The oldest applicant sets the rate; the policyholder is assumed to be the oldest applicant. | `single`, `couple`, `family` | No | – | 2,5 | none |
| `start_date` | date | Coverage effective date. May precede the history start (policy already in force on 2023-07-01). | ISO date; customer_since <= value <= extract end | No | – | 1,2,5 | none |
| `end_date` | date | Date coverage ended or will end: lapse, cancellation or expiry date; for travel, the end of the trip/term. | ISO date; >= start_date | Yes — legit: health/dental policy with status = active (open-ended, renews each period). | – | 2,5 | none |
| `status` | enum | Status AS OF THE EXTRACT END DATE. Retention must derive status as of snapshot_date from dates, not use this column as a feature (leakage). | `active`, `lapsed`, `cancelled`, `expired` | No | – | 2,5 | none |
| `sales_channel` | enum | Channel through which the policy was sold. | `online`, `broker`, `call_centre` | No | – | 2,5 | none |
| `coverage_amount` | decimal(12,2) | Benefit maximum from the plan: per policy year (health, dental) or per term for emergency medical (travel). | > 0; value from reference_data.plans | No | – | 1,5 | none |
| `deductible_amount` | decimal(12,2) | Deductible applied once per policy year (health, dental) or once per term (travel). | >= 0; one of the plan's deductible_options | No | – | 5 | none |

**Record rules:**
- `plan_name` must belong to `policy_type`; `coverage_amount` and `deductible_amount` come from the plan catalogue (§11).
- `status = expired` is used only for travel. `active` means `end_date` is null (health, dental) or after the extract end (travel).
- `status` reflects the **extract end date**. The retention mart must derive status as of the snapshot from `start_date` / `end_date`; using this column as a feature would leak the churn label.

**Defects injected**

| Defect | Fields | Target | What is injected |
|---|---|---|---|
| `ORPHAN_FK` | `customer_id` | 2 rows/files | customer_id that does not exist in Customer.csv. |
| `INV_DATE_ORDER` | `start_date`, `end_date` | 2 rows/files | end_date earlier than start_date. |
| `MISS_ERROR` | `sales_channel`, `plan_name` | 4 rows/files | 2 missing sales_channel, 2 missing plan_name (plan_name recoverable only when coverage_amount is unique to one plan). |
| `FMT_ID` | `customer_id` | 12% of dirty pool | Variants: `c00017`, `C00017 `, ` C00017` |
| `FMT_DATE` | `start_date`, `end_date` | 40% of dirty pool | Variants: `MM/DD/YYYY`, `Mon DD, YYYY` |
| `FMT_CATEGORY_CASE` | `policy_type`, `status`, `coverage_type`, `sales_channel` | 40% of dirty pool | Variants: `Health`, `DENTAL`, `active `, `Single`, `Call Centre` |
| `TYPE_AMOUNT_TEXT` | `coverage_amount`, `deductible_amount` | 40% of dirty pool | Variants: `$5,000.00`, `5000 CAD`, `1,500` |

---

## 6. `Claim.csv`: Claims and Claim Amounts

Master file 3 of 3; system of record for claim existence. Grain: one row per claim.

| Field | Type | Description | Allowed values / format | Nullable | Key | Obj. | PII |
|---|---|---|---|---|---|---|---|
| `claim_id` | string | Claim identifier; system of record for claim existence. Prefix = product line; the numeric part is unique across all prefixes. | `H`/`D`/`T` + 5 digits, e.g. `H00012` | No | PK | 1,2,3,4,5 | none |
| `policy_id` | string | Policy under which the claim is made; its policy_type must match the claim_id prefix. | `P` + 5 digits | No | FK → `Policy.policy_id` | 1,2,3,4,5 | none |
| `customer_id` | string | Claimant. Copy of the policy owner; Policy.customer_id wins on conflict. | `C` + 5 digits | No | Copy of `Policy.customer_id` | 1,2 | none |
| `claim_type` | enum | Benefit claimed; valid values depend on the product line (reference_data.claim_types). | `prescription_drugs`, `health_practitioner`, `vision`, `hearing_aids`, `medical_equipment`, `ambulance`, `hospital_cash`, `preventive`, `basic`, `major`, `emergency_medical`, `trip_cancellation`, `trip_interruption`, `baggage` | No | – | 1,3,4,5 | none |
| `service_date` | date | Date the service was rendered or the incident occurred (for cancellation: the cancellation date). Equals the earliest JSON line-item service_date. | ISO date; within [Policy.start_date, Policy.end_date]; <= claim_date | No | – | 1,3 | none |
| `claim_date` | date | Submission date. Its weekday drives the weekend-submission trait. | ISO date; service_date <= value <= extract end; value - service_date <= 365 days | No | – | 1,2,3,4,5 | none |
| `claim_amount` | decimal(12,2) | Total amount claimed in CAD. Travel claims converted from the JSON currency at exchange_rate_to_cad. Should equal the sum of JSON line items. | > 0; lognormal per claim_type | No | – | 1,3,4,5 | none |
| `approved_amount` | decimal(12,2) | Amount approved for payment after deductible, co-insurance and plan limits. | 0 <= value <= claim_amount; 0 when denied | Yes — legit: claim_status = pending (not yet decided). | – | 1,2,4,5 | none |
| `claim_status` | enum | Status in the claims system. Copy of Claim_Payment.decision_outcome (pending = no payment row); the payment file wins on conflict. | `pending`, `approved`, `partially_approved`, `denied` | No | Copy of `Claim_Payment.decision_outcome` | 1,2,3 | none |
| `region` | enum | Claimant's HOME province (not the incident location; a travel claim in the US still has a Canadian region). Customer.province wins on conflict. | `SK`, `AB`, `MB`, `ON`, `BC`, `NS`, `PE`, `NL`, `YT`, `NT` | No | Copy of `Customer.province` | 4 | none |

**Record rules:**
- The `claim_id` prefix, `Policy.policy_type` and `claim_type` agree (for example `H` + `health` + `vision`).
- `approved_amount` is null only for pending claims and is `0.00` for denied claims.

**Defects injected**

| Defect | Fields | Target | What is injected |
|---|---|---|---|
| `DUP_EXACT` | `*` | 6 rows/files | Whole rows repeated (repeated load). |
| `DUP_NEAR_KEY` | `claim_id` | 4 rows/files | Duplicate rows whose claim_id differs only in case/whitespace, e.g. `h00012` vs `H00012 `. |
| `ORPHAN_FK` | `policy_id` | 3 rows/files | policy_id that does not exist in Policy.csv (the claims keep their JSON files). |
| `XF_STATUS_CONFLICT` | `claim_status` | 8 rows/files | claim_status disagrees with Claim_Payment.decision_outcome, incl. 2 rows showing pending although a decision exists. |
| `XF_REGION_CONFLICT` | `region` | 8 rows/files | region differs from the customer's province. |
| `XF_OWNER_CONFLICT` | `customer_id` | 5 rows/files | customer_id differs from the policy owner (valid ID of another customer). |
| `INV_CLAIM_BEFORE_POLICY` | `service_date` | 3 rows/files | service_date earlier than Policy.start_date (distinct from the early-claim fraud trait, which stays inside coverage). |
| `INV_DATE_ORDER` | `service_date`, `claim_date` | 2 rows/files | service_date later than claim_date. |
| `INV_NEGATIVE_AMOUNT` | `claim_amount` | 3 rows/files | Sign flipped; absolute value matches the JSON line items. |
| `OUT_ERROR` | `claim_amount` | 2 rows/files | Decimal shift (x100). JSON line items still sum to the true value, which separates it from genuine outliers. |
| `MISS_ERROR` | `claim_amount`, `service_date` | 4 rows/files | 2 missing claim_amount, 2 missing service_date; both recoverable from JSON. |
| `FMT_ID` | `policy_id`, `customer_id` | 12% of dirty pool | Variants: `p00104`, `P00104 ` |
| `FMT_DATE` | `service_date`, `claim_date` | 40% of dirty pool | Variants: `DD/MM/YYYY`, `Mon DD, YYYY` |
| `FMT_PROVINCE` | `region` | 40% of dirty pool | Variants: `Sask.`, `saskatchewan`, `Alberta`, ` mb` |
| `FMT_CATEGORY_CASE` | `claim_status`, `claim_type` | 40% of dirty pool | Variants: `Approved`, `APPROVED`, `approved `, `Partially Approved`, `Prescription_Drugs` |
| `TYPE_AMOUNT_TEXT` | `claim_amount`, `approved_amount` | 32% of dirty pool | Variants: `$1,250.00`, `1,250.00`, `1250.00 CAD` |

---

## 7. `Claim_Payment.csv`: Adjudication and Payment

Grain: one row per adjudication decision. The ground truth has at most one row per claim; appeals and re-openings are out of scope.

| Field | Type | Description | Allowed values / format | Nullable | Key | Obj. | PII |
|---|---|---|---|---|---|---|---|
| `payment_id` | string | Adjudication / payment record identifier. | `PAY` + 5 digits, e.g. `PAY00087` | No | PK | 3 | none |
| `claim_id` | string | Claim decided by this record. | `H`/`D`/`T` + 5 digits | No | FK → `Claim.claim_id` | 1,2,3,4,5 | none |
| `processing_start_date` | date | Date an adjuster opened the claim. Queue time = processing_start_date - claim_date. | ISO date; >= Claim.claim_date | No | – | 3 | none |
| `decision_date` | date | Date the adjudication decision was made (for every outcome, including denials). Handling time = decision_date - processing_start_date. | ISO date; >= processing_start_date; <= extract end | No | – | 1,2,3 | none |
| `decision_outcome` | enum | Adjudication result. Authoritative for the claim's decision (overview 4.5). | `approved`, `partially_approved`, `denied` | No | – | 1,2,3,4,5 | none |
| `payment_date` | date | Date funds were issued. | ISO date; >= decision_date; >= Claim.claim_date | Yes — legit: decision_outcome = denied, or payment_status = scheduled. | – | 3,5 | none |
| `payment_amount` | decimal(12,2) | Amount paid or scheduled, CAD. Must equal Claim.approved_amount. | >= 0; 0 when denied | No | – | 1,4,5 | none |
| `payment_method` | enum | How the payment is made; provider_direct if and only if the JSON submission_channel is provider_direct_billing. | `direct_deposit`, `cheque`, `provider_direct` | Yes — legit: decision_outcome = denied. | – | 1,3 | none |
| `payment_status` | enum | Payment state on the extract end date. | `paid`, `scheduled` | Yes — legit: decision_outcome = denied. | – | 3 | none |
| `transaction_reference` | string | Banking transaction reference; unique when present. Used for payment reconciliation and duplicate detection. | `TXN` + 10 digits | Yes — legit: denied, or payment_status = scheduled. | NK | – | none |
| `denial_reason` | enum | Reason for denial, or for the declined part of a partial approval. | `not_covered`, `waiting_period`, `benefit_maximum_reached`, `missing_documentation`, `policy_inactive`, `late_submission`, `duplicate_claim` | Yes — legit: approved (always null) or partially_approved (optional). Required when denied. | – | 1,2,3 | none |

**Row rules (legitimate nulls and absences)**

| Situation | Row present? | Null fields (legitimate) |
|---|---|---|
| Pending claim | No | — (whole row absent) |
| Denied | Yes | `payment_date`, `payment_method`, `payment_status`, `transaction_reference`; `payment_amount = 0.00` |
| Approved / partial, `scheduled` | Yes | `payment_date`, `transaction_reference` |
| Approved / partial, `paid` | Yes | `denial_reason` (always null for approved; optional for partial) |

Derived durations for Objective 3: queue time = `processing_start_date − claim_date`, handling time = `decision_date − processing_start_date`, payment lag = `payment_date − decision_date`, end-to-end = `payment_date − claim_date`.

**Defects injected**

| Defect | Fields | Target | What is injected |
|---|---|---|---|
| `DUP_EXACT` | `*` | 5 rows/files | Whole rows repeated (repeated load). |
| `ORPHAN_FK` | `claim_id` | 3 rows/files | claim_id not present in Claim.csv. |
| `INV_PAYMENT_BEFORE_SUBMISSION` | `payment_date` | 3 rows/files | payment_date earlier than Claim.claim_date. |
| `INV_DATE_ORDER` | `processing_start_date`, `decision_date` | 2 rows/files | decision_date earlier than processing_start_date. |
| `XF_PAYMENT_AMOUNT` | `payment_amount` | 3 rows/files | payment_amount differs from Claim.approved_amount. |
| `MISS_ERROR` | `decision_date` | 2 rows/files | decision_date missing on a decided row (never legitimate). |
| `FMT_ID` | `claim_id` | 12% of dirty pool | Variants: `h00012`, `H00012 ` |
| `FMT_DATE` | `processing_start_date`, `decision_date`, `payment_date` | 40% of dirty pool | Variants: `YYYYMMDD`, `Mon DD, YYYY` |
| `FMT_CATEGORY_CASE` | `decision_outcome`, `payment_status`, `payment_method` | 40% of dirty pool | Variants: `Denied`, `PAID`, `Direct Deposit`, `partially approved` |
| `TYPE_AMOUNT_TEXT` | `payment_amount` | 32% of dirty pool | Variants: `$1,250.00`, `1,250.00` |

---

## 8. `Policy_Premium.csv`: Premiums Due and Paid

Grain: one row per premium instalment.

| Field | Type | Description | Allowed values / format | Nullable | Key | Obj. | PII |
|---|---|---|---|---|---|---|---|
| `premium_id` | string | Premium instalment identifier. | `PRM` + 6 digits, e.g. `PRM001234` | No | PK | – | none |
| `policy_id` | string | Policy the instalment belongs to. | `P` + 5 digits | No | FK → `Policy.policy_id` | 2,5 | none |
| `customer_id` | string | Payer. Copy of the policy owner; Policy.customer_id wins on conflict. | `C` + 5 digits | No | Copy of `Policy.customer_id` | 2 | none |
| `premium_amount` | decimal(12,2) | Amount due for the instalment, CAD = base_premium x age-band factor x coverage-type factor x months in the period. | > 0 | No | – | 2,5 | none |
| `premium_frequency` | enum | Billing frequency; constant within a policy (a policy attribute repeated on each instalment). `single` = one-off travel premium. | `monthly`, `quarterly`, `yearly`, `single` | No | – | 2,5 | none |
| `age_band` | enum | Rating band of the oldest applicant (the policyholder) on the due date; can change over a policy's life. | `Under 35`, `35-44`, `45-54`, `55-64`, `65-74`, `75+` | No | – | 2,5 | none |
| `due_date` | date | Instalment due date. | ISO date; max(Policy.start_date, history_start) <= value <= min(end_date, extract end) | No | – | 2,5 | none |
| `paid_date` | date | Date the instalment was received. | ISO date | Yes — legit: payment_status = missed. | – | 2 | none |
| `payment_status` | enum | Derivable from the dates: paid (paid_date <= due_date), late (paid_date > due_date), missed (paid_date null). | `paid`, `late`, `missed` | No | – | 2 | none |
| `payment_method` | enum | How the premium was paid (pad = pre-authorized debit). | `pad`, `credit_card`, `cheque` | Yes — legit: payment_status = missed. | – | 2 | none |

**Record rules:**
- Instalments are generated only while the policy is in force and within the history window, never after `Policy.end_date`.
- `missed` is generated only for instalments due at least 30 days before the extract end, so recent unpaid instalments are not ambiguous.
- A lapsed policy has at least one missed instalment in the 90 days before its `end_date`.
- `payment_status` is derivable from the dates; the stored value is kept as a cross-check (`INV_DERIVED_STATUS`).
- `age_band` is a denormalized rating value and must equal the band derived from `Customer.date_of_birth` as of `due_date`; disagreements are reconciliation failures.
- Premium = plan base premium × age-band factor × coverage-type factor × months in the period (§11).

**Defects injected**

| Defect | Fields | Target | What is injected |
|---|---|---|---|
| `DUP_EXACT` | `*` | 15 rows/files | One daily load of 15 rows repeated. |
| `ORPHAN_FK` | `policy_id` | 5 rows/files | policy_id not present in Policy.csv. |
| `XF_OWNER_CONFLICT` | `customer_id` | 10 rows/files | customer_id differs from the policy owner. |
| `INV_NEGATIVE_AMOUNT` | `premium_amount` | 4 rows/files | Negative amount; refunds are not modelled, so always an error. |
| `INV_DERIVED_STATUS` | `payment_status` | 5 rows/files | payment_status = paid although paid_date > due_date. |
| `MISS_ERROR` | `paid_date` | 4 rows/files | paid_date missing while payment_status is paid or late. |
| `FMT_ID` | `policy_id` | 8% of dirty pool | Variants: `p00104`, ` P00104` |
| `FMT_DATE` | `due_date`, `paid_date` | 20% of dirty pool | Variants: `MM/DD/YYYY`, `Mon DD, YYYY` |
| `FMT_CATEGORY_CASE` | `payment_status`, `premium_frequency`, `payment_method` | 20% of dirty pool | Variants: `Paid`, `LATE`, `Monthly `, `PAD` |
| `TYPE_AMOUNT_TEXT` | `premium_amount` | 20% of dirty pool | Variants: `$85.00`, `85.00 CAD` |

---

## 9. Claim-Detail JSON Files

**Location and naming:** `data/raw/json/<claim_id>.json`, exactly **200** files. 194 parseable files matching a claim + 2 malformed files (real claims) + 4 orphan files = 200.

**Structure:** all 200 files live in the same raw directory. Each file contains common fields, a `line_items` array, and **exactly one** product object (`health`, `dental` or `travel`) matching `product_line`. The differing product objects are legitimate variation by design, not a defect; health/dental/travel specialization therefore lives primarily in the JSON layer rather than in separate master CSVs. Nested paths are written with dots (`provider.type`).

**JSON variation classes:**
- **Legitimate heterogeneity:** product-specific objects and conditional nested fields expected from the business schema.
- **Recoverable schema drift:** alternate key names, type drift, or array/object/string shape drift that deterministic parsing can normalize.
- **Unrecoverable/quarantined input:** malformed JSON, missing identity needed to route the record, or orphan claim files. These are logged and excluded from canonical core records.

The ingestion path is deterministic; an LLM/LangChain component is not required for these structured/semi-structured files.

### 9.1 Common fields

| Field | Type | Description | Allowed values / format | Nullable | Key | Obj. | PII |
|---|---|---|---|---|---|---|---|
| `schema_version` | string | Version of the portal/API payload schema. | `1.0` | No | – | – | none |
| `claim_id` | string | Claim this file details. Claim.csv decides whether the claim exists. | `H`/`D`/`T` + 5 digits; equals the file name | No | FK → `Claim.claim_id` | 1,2,3,4,5 | none |
| `product_line` | enum | Product line; must match the claim_id prefix and the product object present. | `health`, `dental`, `travel` | No | – | 1,3,4,5 | none |
| `submission_channel` | enum | How the claim arrived. | `online_portal`, `mobile_app`, `provider_direct_billing`, `mail` | No | – | 1,3 | none |
| `submitted_at` | datetime | Submission timestamp; its date part equals Claim.claim_date. Hour and weekday feed fraud traits. | ISO datetime, -06:00 | No | – | 1,3 | none |
| `provider.provider_id` | string | Provider identifier (object `provider`). Provider concentration trait. | `PRV` + 4 digits | No | NK | 1 | none |
| `provider.name` | string | Provider business name. | Free text | No | – | – | none |
| `provider.type` | enum | Provider category; must fit the claim_type (reference_data.claim_types). | `pharmacy`, `clinic`, `practitioner_office`, `optical_store`, `hearing_clinic`, `medical_supplier`, `ambulance_service`, `hospital`, `dental_clinic`, `foreign_hospital`, `foreign_clinic`, `travel_supplier`, `airline` | No | – | 1,3,5 | none |
| `line_items` | array | Billed items (at least one). Sum of amount (x exchange_rate_to_cad for travel) = Claim.claim_amount. | array of line-item objects, >= 1 | No | – | 1,5 | none |
| `adjuster_id` | string | Adjuster assigned to the claim. | `ADJ` + 3 digits (ADJ001-ADJ012) | Yes — legit: pending claim not yet assigned. | – | 1,3 | none |
| `adjuster_notes` | string | Free-text adjuster notes. | Free text, <= 500 chars | Yes — legit: no notes written. | – | 1 | none |
| `documents_submitted` | array | Documents received. Completeness is judged against required_documents for the claim_type; an empty array is a legitimate value. | `receipt`, `invoice`, `itemized_invoice`, `prescription`, `referral`, `treatment_plan`, `xray`, `medical_report`, `hospital_discharge_summary`, `proof_of_travel`, `booking_confirmation`, `cancellation_invoice`, `supporting_statement`, `baggage_irregularity_report` | No | – | 1,3 | none |

### 9.2 Line-item fields (`line_items[]`)

| Field | Type | Description | Allowed values / format | Nullable | Key | Obj. |
|---|---|---|---|---|---|---|
| `line_no` | integer | Sequence within the claim. | 1..n, no gaps | No | – | – |
| `service_date` | date | Date of this service; min over items = Claim.service_date. | ISO date | No | – | 1 |
| `description` | string | Item description. | Free text | No | – | – |
| `quantity` | integer | Units billed. | >= 1 | No | – | 1 |
| `unit_amount` | decimal(12,2) | Price per unit, in the claim currency (CAD unless travel.currency says otherwise). | > 0 | No | – | 1 |
| `amount` | decimal(12,2) | quantity x unit_amount, in the claim currency. | > 0 | No | – | 1,5 |

### 9.3 Health (`health` object)

| Field | Type | Description | Allowed values / format | Nullable | Key | Obj. |
|---|---|---|---|---|---|---|
| `health.practitioner_type` | enum | Practitioner who delivered the service. | `physiotherapist`, `chiropractor`, `massage_therapist`, `psychologist`, `naturopath`, `optometrist` | Yes — legit: claim_type not in (health_practitioner, vision). | – | 1,4,5 |
| `health.number_of_visits` | integer | Visits covered by the claim. | >= 1 | Yes — legit: claim_type <> health_practitioner. | – | 1,5 |
| `health.prescription` | object | Prescription details. | {din, drug_name, days_supply, is_generic} | Yes — legit: claim_type <> prescription_drugs. | – | 1,5 |
| `health.prescription.din` | string | Drug Identification Number (synthetic). | 8 digits, keep leading zeros | No | – | 1 |
| `health.prescription.drug_name` | string | Drug name. | Free text | No | – | – |
| `health.prescription.days_supply` | integer | Days of medication dispensed. | 1-90 | No | – | 1 |
| `health.prescription.is_generic` | boolean | Generic drug dispensed. | `true`, `false` | No | – | 5 |

**Example: `H00012.json`** (physiotherapy, two visits; line items sum to `Claim.claim_amount` = 190.00)

```json
{
  "schema_version": "1.0",
  "claim_id": "H00012",
  "product_line": "health",
  "submission_channel": "online_portal",
  "submitted_at": "2025-03-14T19:42:10-06:00",
  "provider": {"provider_id": "PRV0023", "name": "Queen City Physiotherapy & Wellness", "type": "clinic"},
  "line_items": [
    {"line_no": 1, "service_date": "2025-03-03", "description": "Physiotherapy treatment, 45 min", "quantity": 1, "unit_amount": 95.00, "amount": 95.00},
    {"line_no": 2, "service_date": "2025-03-10", "description": "Physiotherapy treatment, 45 min", "quantity": 1, "unit_amount": 95.00, "amount": 95.00}
  ],
  "health": {"practitioner_type": "physiotherapist", "number_of_visits": 2, "prescription": null},
  "adjuster_id": "ADJ004",
  "adjuster_notes": "Receipts match visit dates.",
  "documents_submitted": ["receipt"]
}
```

### 9.4 Dental (`dental` object and dental line-item fields)

| Field | Type | Description | Allowed values / format | Nullable | Key | Obj. |
|---|---|---|---|---|---|---|
| `dental.claim_category` | enum | Highest procedure category on the claim (major > basic > preventive); equals Claim.claim_type. | `preventive`, `basic`, `major` | No | – | 4,5 |

Additional line-item fields for dental:

| Field | Type | Description | Allowed values / format | Nullable | Key | Obj. |
|---|---|---|---|---|---|---|
| `procedure_code` | string | CDA-style procedure code (synthetic, not validated against the CDA list). | 5 digits as text, e.g. `01202` | No | – | 1,5 |
| `procedure_category` | enum | Category of this procedure. | `preventive`, `basic`, `major` | No | – | 5 |
| `tooth_number` | integer | Tooth treated, FDI notation. | 11-18, 21-28, 31-38, 41-48 | Yes — legit: procedure not tooth-specific (exam, cleaning). | – | 1 |

**Example: `D00047.json`** (recall exam plus one filling; category = highest = `basic`; sum 245.00 = `Claim.claim_amount`)

```json
{
  "schema_version": "1.0",
  "claim_id": "D00047",
  "product_line": "dental",
  "submission_channel": "provider_direct_billing",
  "submitted_at": "2025-11-05T10:15:32-06:00",
  "provider": {"provider_id": "PRV0051", "name": "Wascana Family Dental", "type": "dental_clinic"},
  "line_items": [
    {"line_no": 1, "service_date": "2025-11-05", "description": "Recall examination", "procedure_code": "01202", "procedure_category": "preventive", "tooth_number": null, "quantity": 1, "unit_amount": 65.00, "amount": 65.00},
    {"line_no": 2, "service_date": "2025-11-05", "description": "Composite restoration, one surface", "procedure_code": "23311", "procedure_category": "basic", "tooth_number": 36, "quantity": 1, "unit_amount": 180.00, "amount": 180.00}
  ],
  "dental": {"claim_category": "basic"},
  "adjuster_id": "ADJ009",
  "adjuster_notes": null,
  "documents_submitted": ["receipt"]
}
```

### 9.5 Travel (`travel` object)

| Field | Type | Description | Allowed values / format | Nullable | Key | Obj. |
|---|---|---|---|---|---|---|
| `travel.trip_start` | date | Trip departure date; within the policy term. | ISO date | No | – | 1 |
| `travel.trip_end` | date | Trip return date. | ISO date; >= trip_start | No | – | 1 |
| `travel.destination_country` | string | Destination (CA = out-of-province travel). | ISO 3166-1 alpha-2 | No | – | 1,4,5 |
| `travel.incident_type` | enum | Incident; maps 1:1 to Claim.claim_type. | `medical`, `cancellation`, `interruption`, `baggage` | No | – | 1,4 |
| `travel.incident_date` | date | Incident date. Inside [trip_start, trip_end] except cancellation (before trip_start). | ISO date | No | – | 1 |
| `travel.currency` | string | Currency of line-item amounts. | ISO 4217, e.g. `USD` | No | – | – |
| `travel.exchange_rate_to_cad` | decimal(10,4) | CAD per 1 unit of currency; 1.0000 for CAD. claim_amount = round(sum(amount) x rate, 2). | > 0 | No | – | 1 |
| `travel.incident_sub_limit` | decimal(12,2) | Benefit maximum for this incident type, CAD (reference_data.travel_incident_sub_limits). | > 0 | No | – | 1 |

**Example: `T00188.json`** (emergency room visit in the US, billed in USD; 2,270.00 USD × 1.3650 = **3,098.55 CAD** = `Claim.claim_amount`)

```json
{
  "schema_version": "1.0",
  "claim_id": "T00188",
  "product_line": "travel",
  "submission_channel": "online_portal",
  "submitted_at": "2026-02-02T08:05:44-06:00",
  "provider": {"provider_id": "PRV0072", "name": "Desert Palms Medical Center", "type": "foreign_hospital"},
  "line_items": [
    {"line_no": 1, "service_date": "2026-01-15", "description": "Emergency department visit", "quantity": 1, "unit_amount": 1850.00, "amount": 1850.00},
    {"line_no": 2, "service_date": "2026-01-15", "description": "X-ray, left wrist", "quantity": 1, "unit_amount": 420.00, "amount": 420.00}
  ],
  "travel": {
    "trip_start": "2026-01-10",
    "trip_end": "2026-01-24",
    "destination_country": "US",
    "incident_type": "medical",
    "incident_date": "2026-01-15",
    "currency": "USD",
    "exchange_rate_to_cad": 1.3650,
    "incident_sub_limit": 5000000.00
  },
  "adjuster_id": "ADJ011",
  "adjuster_notes": "Fall on hiking trail; wrist sprain, no fracture.",
  "documents_submitted": ["itemized_invoice", "medical_report", "proof_of_travel"]
}
```

### 9.6 Reconciliation with `Claim.csv`

- health/dental: sum(line_items.amount) = Claim.claim_amount
- travel: round(sum(line_items.amount) x exchange_rate_to_cad, 2) = Claim.claim_amount
- min(line_items.service_date) = Claim.service_date
- date(submitted_at) = Claim.claim_date
- Tolerance: 0.01 CAD.

A mismatch is **not** automatically a defect: `F6_LINE_ITEM_MISMATCH` is an embedded fraud signal. The pipeline flags every mismatch; the defect log tells the evaluator which mismatches were injected errors (`OUT_ERROR`, `INV_NEGATIVE_AMOUNT`) and which were signals.

### 9.7 JSON defects injected

| Defect | Fields | Target | What is injected |
|---|---|---|---|
| `JSON_MALFORMED` | (file) | 2 rows/files | 1 truncated file, 1 with a trailing comma; both named after real claim_ids. Ingestion must log and continue. |
| `JSON_ORPHAN` | (file) | 4 rows/files | Well-formed files whose claim_id is not in Claim.csv. |
| `JSON_MISSING_FILE` | (file) | 14 rows/files | Claims in Claim.csv with no JSON file (in addition to the 2 whose file is malformed). |
| `JSON_MISSING_KEY` | `adjuster_id`, `provider.type`, `documents_submitted`, `submission_channel` | 20% of dirty pool | Key ABSENT (not null). |
| `JSON_KEY_DRIFT` | `line_items`, `claim_id` | 12% of dirty pool | Legacy key names: `lineItems`, `ClaimID`. |
| `JSON_TYPE_DRIFT` | `line_items.amount`, `line_items.unit_amount`, `travel.exchange_rate_to_cad` | 20% of dirty pool | Numbers as strings: `"95.00"`, `"$95.00"`. |
| `JSON_SHAPE_DRIFT` | `documents_submitted`, `line_items` | 8% of dirty pool | documents_submitted as comma-separated string; single line item as an object instead of an array. |

---

## 10. Cross-File Invariants of the Ground Truth

These hold in the clean data. The defects in §4 to §9 deliberately break them, and the pipeline's validation checks are these rules turned around.

| ID | Rule |
|---|---|
| INV01 | Every Policy.customer_id exists in Customer.csv. |
| INV02 | Every Claim.policy_id exists in Policy.csv; the policy's policy_type matches the claim_id prefix and claim_type. |
| INV03 | Claim.customer_id = Policy.customer_id; Policy_Premium.customer_id = Policy.customer_id. |
| INV04 | Claim.region = Customer.province of the claimant. |
| INV05 | Policy.start_date <= Claim.service_date <= Claim.claim_date <= extract end; service_date <= Policy.end_date when end_date is set. |
| INV06 | Each non-pending claim has exactly one Claim_Payment row; each pending claim has none. |
| INV07 | Claim.claim_status = Claim_Payment.decision_outcome (pending when there is no row). |
| INV08 | Claim.claim_date <= processing_start_date <= decision_date <= payment_date. |
| INV09 | Claim_Payment.payment_amount = Claim.approved_amount; 0 for denied. |
| INV10 | Customer.customer_since <= min(Policy.start_date) of the customer. |
| INV11 | Policy_Premium rows exist only while the policy is in force and within [history_start, extract end]. |
| INV12 | Policy.status = lapsed implies a missed instalment in the 90 days before end_date. |
| INV13 | Customer.province is served, and postal_code's first letter belongs to it. |
| INV14 | JSON reconciliation rules hold (amounts, service_date, submission date). |
| INV15 | Payment method provider_direct iff JSON submission_channel = provider_direct_billing. |
| INV16 | Policy_Premium.age_band = the age band derived from the policyholder's date_of_birth as of Policy_Premium.due_date. |

---

## 11. Reference Data

All amounts are **synthetic**. Plan names follow GMS's public product names; coverage, deductibles and rates are illustrative, not GMS figures.

**Plan catalogue**

| Plan | Product | Coverage amount (CAD) | Deductible options | Base monthly premium | Frequencies | Note |
|---|---|---|---|---|---|---|
| BasicPlan | health | 1,500.00 | 0 | 45.00 | monthly, quarterly, yearly |  |
| ExtendaPlan | health | 3,000.00 | 0, 100 | 85.00 | monthly, quarterly, yearly |  |
| OmniPlan | health | 5,000.00 | 0, 100, 250 | 140.00 | monthly, quarterly, yearly |  |
| Replacement Health | health | 2,000.00 | 0 | 120.00 | monthly | guaranteed acceptance |
| Dental Basic | dental | 1,000.00 | 0, 50 | 35.00 | monthly, quarterly, yearly | synthetic name modelled on the GMS dental add-on |
| Dental Plus | dental | 2,000.00 | 0, 50 | 60.00 | monthly, quarterly, yearly | synthetic name modelled on the GMS dental add-on |
| TravelStar | travel | 5,000,000.00 | 0, 250, 500 |  | single | single-trip; premium 40-400 by age band and trip length; policy term = trip |
| StudentPlan | travel | 2,000,000.00 | 0 | 70.00 | monthly, yearly | 12-month term |

**Age bands** (the oldest applicant sets the rate): `Under 35` (1.00), `35-44` (1.15), `45-54` (1.40), `55-64` (1.80), `65-74` (2.40), `75+` (3.20). Coverage-type factors: single 1.00, couple 1.90, family 2.60.

**Claim types by product line**

| Product | claim_type | Share | Provider types | Required documents |
|---|---|---|---|---|
| health | `prescription_drugs` | 35% | `pharmacy` | `receipt`, `prescription` |
| health | `health_practitioner` | 35% | `clinic`, `practitioner_office` | `receipt` |
| health | `vision` | 12% | `optical_store` | `receipt`, `prescription` |
| health | `hearing_aids` | 3% | `hearing_clinic` | `receipt`, `referral` |
| health | `medical_equipment` | 7% | `medical_supplier` | `receipt`, `referral` |
| health | `ambulance` | 4% | `ambulance_service` | `invoice` |
| health | `hospital_cash` | 4% | `hospital` | `hospital_discharge_summary` |
| dental | `preventive` | 55% | `dental_clinic` | `receipt` |
| dental | `basic` | 35% | `dental_clinic` | `receipt` |
| dental | `major` | 10% | `dental_clinic` | `receipt`, `treatment_plan`, `xray` |
| travel | `emergency_medical` | 45% | `foreign_hospital`, `foreign_clinic`, `hospital` | `itemized_invoice`, `medical_report`, `proof_of_travel` |
| travel | `trip_cancellation` | 25% | `travel_supplier` | `cancellation_invoice`, `booking_confirmation`, `supporting_statement` |
| travel | `trip_interruption` | 10% | `travel_supplier` | `receipt`, `booking_confirmation`, `supporting_statement` |
| travel | `baggage` | 20% | `airline` | `baggage_irregularity_report`, `receipt`, `proof_of_travel` |

Dental waiting period: 90 days for `basic` and `major`.

**Travel incident sub-limits** (for the "near benefit maximum" trait): medical = policy `coverage_amount`; cancellation = insured trip cost (1,000 to 8,000); interruption 5,000.00; baggage 1,000.00.

**Provinces:** SK (35.0%), AB (20.0%), MB (15.0%), ON (15.0%), BC (8.0%), NS (2.5%), PE (1.5%), NL (1.5%), YT (0.8%), NT (0.8%). **Not served (invalid): QC, NB, NU.**

**Eligibility / validation rules used by the synthetic model:**

- `ELIG_DENTAL_WAIT`: selected synthetic dental plans use a 90-day waiting period; a basic/major claim with `service_date < policy.start_date + 90 days` is inside that synthetic waiting period.
- `ELIG_SERVED_PROVINCE`: Customer province must be in `provinces_served`.

The previously proposed universal age-80 travel rule is not part of the frozen model because it is product-specific and unnecessary for the assessment.

**Other domains:** submission channels `online_portal`, `mobile_app`, `provider_direct_billing` (pharmacies and dental clinics only), `mail`; claim payment methods `direct_deposit`, `cheque`, `provider_direct` (if and only if the claim was direct-billed); premium payment methods `pad` (pre-authorized debit), `credit_card`, `cheque`; 12 adjusters `ADJ001`–`ADJ012`; about 80 providers `PRV0001`–`PRV0080`.

---

## 12. Embedded Signals (Carriers Only)

Signals are **patterns in valid data**, not defects: they are not written to the defect log and must survive cleaning. The generator chat decides the exact probabilities; this table fixes which fields carry each signal.

| Trait | Carrier fields | Rule |
|---|---|---|
| `F1_EARLY_CLAIM` | `Claim.service_date`, `Policy.start_date` | service_date within 30 days of start_date |
| `F2_WAITING_PERIOD` | `Claim.service_date`, `Policy.start_date`, `Claim.claim_type` | dental basic/major inside the 90-day waiting period |
| `F3_NEAR_MAXIMUM` | `Claim.claim_amount`, `Policy.coverage_amount`, `travel.incident_sub_limit` | >= 90% of the applicable maximum |
| `F4_REPEAT_CLAIMS` | `Claim.customer_id`, `Claim.claim_date` | >= 3 claims by one customer within 30 days |
| `F5_PROVIDER_CONCENTRATION` | `provider.provider_id` | 3-4 providers linked to a disproportionate share of claims carrying other suspicious traits |
| `F6_LINE_ITEM_MISMATCH` | `line_items.amount`, `Claim.claim_amount` | line items do not add up to claim_amount (inflated header amount) |
| `F7_MISSING_DOCUMENTS` | `documents_submitted` | required documents missing |
| `F8_WEEKEND_SUBMISSION` | `Claim.claim_date`, `submitted_at` | submitted Saturday/Sunday or 00:00-05:00 |
| `F9_TRAVEL_DATES` | `travel.incident_date`, `travel.trip_start`, `travel.trip_end` | incident outside the valid trip window (with cancellation handled by its documented pre-trip rule) |

- **Fraud preparation:** there is no synthetic fraud target in the raw sources or marts. The generator controls the prevalence and co-occurrence of suspicious patterns so the fraud mart can expose useful downstream features without fabricating an investigation outcome.
- **Genuine outliers:** 3 very large but legitimate claims (for example a US emergency-medical claim of about CAD 85,000). The JSON agrees with `Claim.csv`, which is what separates them from `OUT_ERROR`. They must be kept.
- **Retention:** the mart grain is one row per eligible customer. Eligibility requires at least one active health or dental policy on 2026-03-31. Features use the 2025-04-01 to 2026-03-31 observation window (with earlier `customer_since` retained for tenure). `churned = 1` only when all health/dental policies active at the snapshot lapse/cancel during 2026-04-01 to 2026-06-30 and no active/replacement health or dental policy starts by 2026-06-30. Travel-only customers and normal travel expiry are excluded from churn.
- **Operations:** slower queue and handling times for provinces outside SK, travel claims, mail submissions, incomplete documents and 2–3 specific adjusters.
- **Region and policy:** claim frequency, approved claim amounts, and premium amounts vary by province, age band and plan so claims-to-premium performance ratios differ across cells. The metric is not described as an actuarial loss ratio.

---

## 13. Defect Catalogue

Categories follow overview §3.4. The detection hint is what the pipeline is expected to do; the Database Model chat turns these into formal rules.

| Defect | Category (§3.4) | Description | Detection hint |
|---|---|---|---|
| `DUP_EXACT` | Duplicates | Whole row repeated by a repeated load. | Row equality on raw values. |
| `DUP_NEAR_KEY` | Duplicates | Duplicate whose key differs only in case/whitespace. | Normalize key (trim, upper) then dedupe. |
| `DUP_ENTITY` | Duplicates | Same person under two customer_ids. | Exact match on normalized name, DOB, postal code. |
| `FMT_DATE` | Format inconsistencies | Dates in a non-ISO format. | Parse ISO, then the file's documented secondary convention; never guess per row. |
| `FMT_PROVINCE` | Format inconsistencies | Province names/abbreviations instead of codes. | Mapping table to 2-letter codes. |
| `FMT_GENDER` | Format inconsistencies | Gender spelled out or in other casing. | Mapping table to F/M/X. |
| `FMT_PHONE` | Format inconsistencies | Phone in non-E.164 formats. | Strip non-digits, prefix +1. |
| `FMT_POSTAL` | Format inconsistencies | Postal code casing/spacing. | Upper, remove spaces, reinsert after 3rd char. |
| `FMT_CATEGORY_CASE` | Format inconsistencies | Categorical values with other casing, spaces or trailing blanks. | Trim, lower, spaces to underscores, validate against enum. |
| `FMT_TEXT_WHITESPACE` | Format inconsistencies | Free text with trailing spaces or wrong case. | Trim; title case names/cities; lower e-mail. |
| `FMT_ID` | Format inconsistencies | Key value with wrong case or whitespace (not a duplicate). | Normalize before joining. |
| `MISS_ERROR` | Missing values | Required value missing. | Null where this dictionary says nullable = false or the null condition is not met. |
| `INV_NEGATIVE_AMOUNT` | Invalid values | Negative money amount. | amount < 0. |
| `INV_AGE` | Invalid values | Impossible date of birth. | Future date, sentinel 1900-01-01, age < 18 at policy start. |
| `INV_UNSERVED_PROVINCE` | Invalid values | Customer in QC, NB or NU. | Province not in provinces_served. |
| `INV_DATE_ORDER` | Invalid values | Date sequence broken within a file. | Compare ordered date pairs. |
| `INV_CLAIM_BEFORE_POLICY` | Invalid values | Service before the policy start. | Join Policy; service_date < start_date. |
| `INV_PAYMENT_BEFORE_SUBMISSION` | Invalid values | Payment before submission. | Join Claim; payment_date < claim_date. |
| `INV_DERIVED_STATUS` | Invalid values | Stored status contradicts the dates it derives from. | Recompute status from dates. |
| `TYPE_AMOUNT_TEXT` | Type problems | Amount stored as formatted text. | Strip $, commas, 'CAD'; cast to numeric. |
| `OUT_ERROR` | Outliers | Erroneous outlier (decimal shift). | Extreme value contradicted by JSON line items. |
| `XF_STATUS_CONFLICT` | Cross-file conflicts | claim_status vs decision_outcome. | Payment file wins; log. |
| `XF_REGION_CONFLICT` | Cross-file conflicts | Claim region vs customer province. | Customer wins; log. |
| `XF_OWNER_CONFLICT` | Cross-file conflicts | customer_id vs policy owner. | Policy wins; log. |
| `XF_PAYMENT_AMOUNT` | Cross-file conflicts | payment_amount vs approved_amount. | Flag; do not overwrite. |
| `ORPHAN_FK` | Cross-file conflicts | Foreign key with no parent. | Anti-join to parent; quarantine. |
| `JSON_MALFORMED` | JSON issues | File is not valid JSON. | Parse error; log file, continue. |
| `JSON_ORPHAN` | JSON issues | JSON claim_id not in Claim.csv. | Anti-join to claims. |
| `JSON_MISSING_FILE` | JSON issues | Claim with no JSON file. | Anti-join from claims. |
| `JSON_MISSING_KEY` | JSON issues | Expected key absent. | Key-presence check vs this schema. |
| `JSON_KEY_DRIFT` | JSON issues | Legacy/alternative key name. | Alias map to canonical names. |
| `JSON_TYPE_DRIFT` | JSON issues | Number stored as string. | Type check; clean and cast. |
| `JSON_SHAPE_DRIFT` | JSON issues | Array/object/string shape differs. | Coerce to canonical shape. |

**Injection rules for the generator**

- Generate and save the clean ground truth first; inject defects into copies only.
- At most one value defect per (row, field); key-breaking defects (ORPHAN_FK, DUP_*) are not stacked on the same row.
- Do not inject defects into signal carriers in a way that destroys the intended embedded pattern; signals are patterns to preserve through cleaning, not labels supplied to the pipeline.
- Count-based defects (target.count) are absolute numbers of rows/files and may fall anywhere in the file.
- Rate-based defects (target.pool_share) apply only to the file's dirty-row pool: sample dirty_row_pool x rows once, then give each defect to pool_share of the pool rows (minimum 1). A pool row may carry several format defects, like a badly keyed record.
- Target: about 5-15% of all records affected overall (overview 3.4). Small master files run higher because cross-file defects concentrate on them.
- Defects are drawn with the same random seed so the log is reproducible.
- Every injected defect is written to the defect log; signals are not.

**Defect log contract** (`data/raw/_defect_log.csv`; an evaluation artefact that the pipeline must never read):

| Column | Type | Description |
|---|---|---|
| `defect_id` | string | Sequential log id, e.g. DL00001 |
| `defect_type` | enum | defect_catalogue id |
| `source` | string | File name, e.g. Claim.csv or json/H00012.json |
| `record_key` | string | Primary key of the affected row/file (ground-truth value) |
| `field` | string | Affected field, or * for whole row |
| `original_value` | string | Ground-truth value (empty for inserted rows) |
| `injected_value` | string | Value written to the raw file |

**Volume summary (targets at default parameters)**

| Source | Raw rows/files (approx.) | Count-based defects | Dirty pool (rate-based) | Est. affected | Share |
|---|---|---|---|---|---|
| Customer.csv | 157 | 14 | 16 (10%) | ~30 | ~19% |
| Policy.csv | 183 | 8 | 18 (10%) | ~26 | ~14% |
| Claim.csv | 220 | 48 | 18 (8%) | ~66 | ~30% |
| Claim_Payment.csv | 203 | 18 | 16 (8%) | ~34 | ~17% |
| Policy_Premium.csv | 3,520 | 43 | 176 (5%) | ~219 | ~6% |
| JSON files | 200 | 20 | 19 (10%) | ~39 | ~20% |
| **All sources** | **4,483** | | | **~414** | **~9%** |

Estimated affected is an upper bound (overlaps reduce it). Overall this sits inside the 5–15% target of overview §3.4. The small master files run higher because the cross-file defects concentrate on them, which is also where the reconciliation work is most visible.

---

## 14. Source-Schema Freeze (v1.2 / Overview v1.5)

The source model is frozen for generator design. The following decisions are now resolved:

| Decision | Frozen position |
|---|---|
| Claims Master files | `Customer.csv`, `Policy.csv`, `Claim.csv` as three entity extracts. |
| Product-specific claim detail | Exactly 200 mixed health/dental/travel JSON files in `data/raw/json/`; product-specific objects are legitimate schema heterogeneity. |
| `Claim_Payment.csv` | One row per adjudicated claim, carrying the downstream adjudication/payment lifecycle needed for processing-time analysis. |
| Fraud target | No synthetic investigation/fraud target is emitted to raw sources or marts. |
| Retention | Customer-level cohort; 12-month observation window ending 2026-03-31 and 90-day outcome window ending 2026-06-30; travel-only customers/expiry are not churn. |
| Policy optimization | Use an exposure-aligned approved-claims-to-premium-due performance ratio; do not call it a formal actuarial loss ratio. |
| Raw messiness | Preserve purposeful redundancy, formatting defects, schema drift, malformed/orphan JSON, and cross-source conflicts where they demonstrate cleaning/reconciliation value. |
| GMS-specific assumptions | Public GMS context informs realism; synthetic premiums, limits, deductibles, distributions and detailed eligibility rules are explicitly synthetic. |
| Defect log | `data/raw/_defect_log.csv` is evaluation-only and must never be read by the pipeline. |

Remaining implementation choices such as exact generator probabilities, customer-survivorship details, and quarantine-table mechanics belong to the generator/database-model stages and must not change these source semantics.
