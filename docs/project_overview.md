# GMS Data Engineer Case Study: Project Overview, Decisions & Plan

*Reference document for all chats in this project. Last updated: Wednesday, September 23, 2026.*

---

## 1. Context

| Item | Detail |
|---|---|
| Role | Junior Data Engineer, GMS (Group Medical Services), Regina, SK |
| Contact | Augustine Ayo, Manager, Technical Delivery (aayo@gms.ca) |
| Deadline | **9:00 AM Regina time (CST), Monday, September 28, 2026** (target: finish Sunday, Sept 27) |
| Submission | Reply to the same email thread with the work attached (PDF, Word, or other common formats) |
| Evaluation | Screening step for the technical interview (scheduled about 2–3 working days after review). Assesses analytical thinking, technical approach, problem-solving, and communication. |
| Key clarification | GMS provides **no dataset**. Augustine confirmed (Sept 23) that the candidate must **generate their own dataset**. |

The email also asks for assumptions, methodologies, and supporting explanations.

---

## 2. The Task

**Scenario:** an insurer offering health, dental, and travel products is building a unified claims analytics dashboard. The raw data is messy and spread across sources. The data engineer prepares clean, analysis-ready data, and data scientists perform the analysis.

**Raw sources:**
- Claims Master Data: 3 CSV files (policy details, claim amounts, customer demographics)
- Claims Payment Data: 1 CSV file (payment statuses and dates)
- Policy Premium Data: 1 CSV file (premiums paid)
- Claim Details: 200 JSON files, **one JSON file per claim**

**Five analytics objectives:**
1. **Fraud Detection:** identify claims with a high probability of fraud.
2. **Customer Retention:** identify customers at risk of churning, based on claim and payment history.
3. **Operational Efficiency:** analyze claim processing times to find bottlenecks.
4. **Region-Wise Insights:** show claims trends by region and claim type.
5. **Policy Optimization:** optimize policies based on claims data and demographics.

**Deliverables for each objective:**
1. A clean, prepared dataset (CSV).
2. A summary of the solution and how it supports the business goal.
3. A detailed list of cleaning, transformation, and preparation steps, with the reasoning for each.
4. The SQL queries and Python scripts used.

**Scope note:** the job is to make data *ready* for the data scientists, not to build fraud or churn models. Engineered features and rule-based flags are presented as **inputs** for modelling, not as final predictions.

---

## 3. Synthetic Data Strategy

### 3.1 Approach
- Generate the data with **Python**: Faker (`en_CA` locale), NumPy, pandas, and the `json` module.
- Use a **fixed random seed** so the data is reproducible.
- **Generate in two passes:**
  1. A clean "ground truth" dataset.
  2. A corrupted copy with defects injected deliberately.
- Keep a **defect log** recording what was injected and how many records. Cleaning results can then be measured (e.g., "12 duplicates injected, 12 detected and removed").
- Use **realistic statistical distributions**:
  - lognormal for claim amounts
  - Poisson for claim counts
  - exponential or gamma for processing durations
- Make data volumes a **parameter** of the generator, so the pipeline visibly scales beyond the sample size.

### 3.2 Size (initial assumption)
- About 150 customers, 180 policies, and 200–220 claims.
- 200 JSON files, one per claim.
- A few claims have **no JSON file**, and a few JSON files have **no master record**, to demonstrate orphan handling.

### 3.3 Signals to embed (so the objectives have something to find)
- **Fraud:** about 3–5% of claims have suspicious traits:
  - a claim shortly after the policy start or during a waiting period
  - an amount near the benefit maximum
  - repeated claims, or provider concentration
  - line items that don't add up to the claim total
  - missing documents
  - weekend submission

  A hidden `is_fraud` label is included for supervised modelling.
- **Churn:** late or missed premiums and denied claims raise the probability of a policy lapsing.
- **Operations:** certain regions, claim types, or submission channels are slower.
- **Region and policy:** claim frequency and cost vary by province, age group, and product.

### 3.4 How messy
Aim for about **5–15% of records affected**. Defect types:
- **Duplicates:**
  - exact duplicate rows
  - the same claim in two master files
  - near-duplicates that differ only in casing or whitespace
- **Format inconsistencies:**
  - mixed date formats
  - province spellings (`SK`, `Sask.`, `saskatchewan`)
  - gender codes
  - different column names across the three master files (e.g., `DOB` vs `date_of_birth`)
- **Missing values,** where some are legitimate (e.g., an unpaid claim has no payment date) and some are errors.
- **Invalid values:**
  - negative amounts, impossible ages
  - a payment date before the submission date
  - a claim before the policy start date
- **Type problems:** amounts stored as text (`$1,250.00`).
- **Outliers:** some are genuine and some are errors. Distinguishing them is part of the work, and unusual records are kept where they matter for fraud detection.
- **JSON issues:** missing keys, different structures by product type, nested objects, and one or two malformed files.

**Justification for three master files:** they represent three source systems or product lines (health, dental, travel), each with its own conventions.

---

## 4. Data Model Basis: Industry Practice Plus Public GMS Information

**Framing for the report:** GMS's internal schema is unknown. The model follows standard industry practice and is informed by GMS's publicly available product information.

### 4.1 GMS-specific details (from public sources)
- **Personal health plans:** BasicPlan, ExtendaPlan, OmniPlan, and Replacement Health (guaranteed acceptance).
- **Travel products:** TravelStar emergency medical, trip cancellation and interruption, baggage, Visitors to Canada, and StudentPlan.
- **Group plans:** Silver, Gold, and Platinum tiers.
- **Premiums:**
  - monthly
  - **age-banded** (Under 35, 35–44, 45–54, …)
  - coverage type single, couple, or family, with the oldest applicant setting the rate
  - add-ons: prescription drugs, dental, hospital cash
- **Benefit categories:**
  - prescription drugs, dental, vision
  - health practitioners (chiropractor, physio, massage, and others)
  - ambulance, hearing aids, medical equipment, hospital cash
- **Claim channels:** online submission, and direct billing by participating providers.

### 4.2 Validation rules derived from GMS products
- GMS products are **not offered in Quebec, New Brunswick, or Nunavut**, so customers in QC, NB, or NU are invalid.
- Travellers **aged 80 and over** are covered for emergency medical only **within Canada**.
- **Dental waiting period** (up to 3 months): a claim inside the period is an eligibility or fraud flag.

### 4.3 Fields by source

**Claims Master (3 files: health, dental, travel)**
- **Customer:**
  - `customer_id`, `first_name`, `last_name`, `date_of_birth`, `gender`
  - `province`, `city`, `postal_code`, `email`, `phone`
  - `coverage_type`
- **Policy:**
  - `policy_id`, `product_line`, `plan_name`
  - `policy_start_date`, `policy_end_date`, `policy_status`, `sales_channel`
- **Claim:**
  - `claim_id`, `claim_type`, `service_date`, `submission_date`
  - `claim_amount`, `approved_amount`, `claim_status`

**Claims Payment**
- `payment_id`, `claim_id`
- `approval_date`, `payment_date`, `payment_amount`
- `payment_method`, `payment_status`, `denial_reason`

**Policy Premium**
- `premium_id`, `policy_id`, `billing_month`
- `premium_due`, `premium_paid`, `payment_date`
- `payment_status` (paid, late, missed), `age_band`

**Claim Details (JSON, one per claim)**
- **Common fields:**
  - `claim_id`, `product_line`
  - `provider` (id, name, type)
  - `line_items[]`, `adjuster_id`, `adjuster_notes`, `documents_submitted[]`
  - `submission_channel`
- **Health:** practitioner type, number of visits, prescription information.
- **Dental:** procedure code, category (preventive, basic, major), tooth number.
- **Travel:**
  - `trip_start`, `trip_end`, `destination_country`
  - incident type (medical, cancellation, baggage)
  - currency and exchange rate

### 4.4 Field-to-objective mapping
- **Fraud:**
  - timing relative to policy start and waiting periods
  - amount versus benefit maximum
  - frequency, provider patterns, line-item consistency, documents
- **Retention:** premium payment history, policy status, denied claims, tenure.
- **Operations:** submission, approval, and payment dates; channel; adjuster; claim type; document completeness.
- **Region:** province, city, product line, claim type, amounts.
- **Policy optimization:** the **loss ratio** (claims paid ÷ premiums) by plan, age band, coverage type, and province.

---

## 5. Role of the JSON Files

- Each JSON file holds the **detailed record of one claim**. It simulates semi-structured data from a claims portal, an app, provider direct billing, or an API.
- The JSON files **enrich** claims that already exist in the master CSVs. They do not create claims. The master CSVs are the system of record.
- The CSVs and the JSON files are **independent sources**, linked only by `claim_id`.
- In the pipeline, the JSON files are:
  1. ingested as a batch, with malformed files logged rather than crashing the run
  2. stored raw
  3. flattened into **two tables**:
     - `claim_details`: one row per claim
     - `claim_line_items`: one row per line item
  4. validated
  5. joined to the claims master
  6. checked for orphans in both directions
- **Reconciliation check:** the sum of line items in the JSON should equal `claim_amount` in the master CSV. Mismatches become data-quality and fraud flags.

---

## 6. Workflow (confirmed understanding)

```
SOURCES (raw, messy)
├── 3 Claims Master CSVs ─┐
├── 1 Payments CSV ───────┤
├── 1 Premiums CSV ───────┤
└── 200 JSON claim files ─┤  (parsed and flattened)
                          ▼
STAGING / CLEANING  (each source cleaned independently)
                          ▼
CORE DATABASE  (integrated relational model linked by keys)
                          ▼
SQL QUERIES  (one or more per objective)
                          ▼
5 OUTPUT CSVs → Fraud | Retention | Operations | Region | Policy
                          ▼
Data scientists
```

- There are CSVs at **both ends**: the **input** CSVs are messy raw sources, and the **output** CSVs are the clean deliverables produced by querying the database.
- Data generation (creating the CSVs and JSON together from a single ground truth, then corrupting each source) **simulates the sources**. It is not part of the pipeline itself.

---

## 7. Architecture Decisions

### Decision 1: Build a database-style model, not just cleaned files
**Reasons:**
- The master files mix customer, policy, and claim entities, and customers appear across files. Deduplication requires separating out a single customers table.
- Every objective needs joins across several sources.
- One integrated model is a single source of truth, which avoids repeating cleaning logic five times.

### Decision 2: A layered pipeline (raw → staging → core → marts)

| Layer | Purpose |
|---|---|
| `raw` | Original files loaded as-is, never modified. Allows reprocessing and before/after comparison. |
| `staging` | Per-source cleaning: types, formats, nulls, duplicates within a file, JSON flattening, validation flags. |
| `core` | Integrated entity tables with keys. |
| `marts` | Five flat, objective-specific datasets exported as CSV. |

**Core tables:**

| Table | Grain (one row per…) | Built from |
|---|---|---|
| `customers` | customer | 3 master files, deduplicated |
| `policies` | policy | master files |
| `claims` | claim | 3 master files unioned (central fact table) |
| `claim_details` | claim | JSON files |
| `claim_line_items` | line item | JSON files |
| `claim_payments` | payment | payments CSV |
| `premiums` | policy × billing month | premiums CSV |

**Keys:**
- `customers` → `policies` (`customer_id`)
- `policies` → `claims` (`policy_id`)
- `policies` → `premiums` (`policy_id`)
- `claims` → `claim_details`, `claim_line_items`, and `claim_payments` (`claim_id`)

**Grain of the marts:**
- Fraud: one row per claim.
- Operations: one row per claim.
- Retention: one row per customer.
- Region and Policy: aggregated rows.

### Decision 3: Database platform is Supabase (managed PostgreSQL)
**Reasons:**
- Production-grade PostgreSQL, the industry standard.
- A web SQL editor and no server setup.
- One schema per layer (`raw`, `staging`, `core`, `marts`) makes the architecture visible.
- **JSONB** lets the raw JSON be stored untouched and flattened in SQL (e.g., `jsonb_array_elements`).
- A **Canada Central (`ca-central-1`)** region is available. Choose it at project creation and mention data residency awareness for health data.

**Risks and mitigations:**
- **Free-plan projects pause after about 7 days of low activity, and the free plan keeps no backups.** The submission must therefore be fully self-contained: every script can rebuild the database from scratch, and reviewers are not expected to access the live instance.
- **Credentials** go in a `.env` file loaded with `python-dotenv`, with a `.env.example` provided. The real `.env` is never submitted.
- **Portability:** only the connection string needs to change to run on any PostgreSQL instance, including a local one.

### Decision 4: Tooling
- **Python:**
  - generation: Faker, NumPy
  - ingestion and cleaning: pandas
  - database connection: SQLAlchemy + psycopg, python-dotenv
- **SQL (PostgreSQL):**
  - DDL
  - JSON flattening
  - core integration
  - mart queries

This covers the "SQL queries or Python scripts" deliverable with both.

**Report justification sentence:** "PostgreSQL (via Supabase) was chosen for its relational integrity, native JSONB support for semi-structured claim details, schema-based layer separation, and Canadian region hosting; all transformations are scripted for full reproducibility on any PostgreSQL instance."

---

## 8. Assumptions (to state in the report)
1. The data is synthetic, modelled on industry practice and public GMS product information, not on GMS's internal schema.
2. Each JSON file represents exactly one claim.
3. The three master files represent three product-line or source systems with differing conventions.
4. The sample size is small by design (the brief fixes 200 JSON files), and the generator is parameterized to scale.
5. Processing is a batch run. Event-driven or streaming ingestion is noted as a possible extension.
6. Model-ready features and flags are provided; model building is out of scope.
7. Currency is CAD. Travel claims in foreign currency are converted using a recorded exchange rate.

---

## 9. Plan

### 9.1 Chat structure in this project
1. **General / planning** (this chat): overview, requirements, decisions.
2. **Data model and data dictionary:** tables, fields, types, allowed values, keys, validation rules, and the objective each field serves. *Save the output as a project file.*
3. **Synthetic data generation:** the Python generator, embedded signals, defect injection, and the defect log.
4. **Supabase setup, ingestion and cleaning:** schemas, DDL, loading raw data, staging transformations, JSON flattening.
5. **Core model and objective datasets:** integration, feature engineering, and the five mart queries and CSV exports. This can be split per objective if long.
6. **Report and submission:** written report, cleaning-steps log with reasoning, assumptions, packaging, and the reply email.

**Tip:** start each new chat with one line, for example: *"This is the [X] chat for the GMS case study; use the project overview and data dictionary in the project files."*

### 9.2 Timeline

| Day | Work |
|---|---|
| **Wed, Sept 23** | Planning and architecture decisions (done). Create the Supabase project in `ca-central-1`. |
| **Thu, Sept 24** | Data model and data dictionary finalized and saved to project files. Start the generator. |
| **Fri, Sept 25** | Finish generation (clean ground truth, then defects and defect log). Set up schemas and load raw data. |
| **Sat, Sept 26** | Staging cleaning, JSON flattening, core model, validation and reconciliation checks. |
| **Sun, Sept 27** | Five mart datasets and CSV exports. Write the report. Final review and rerun from scratch to confirm reproducibility. |
| **Mon, Sept 28** | Buffer only. **Submit before 9:00 AM CST**, ideally Sunday evening. |

### 9.3 Submission package
- **Report (PDF or Word):**
  - executive summary
  - architecture diagram (layers and core tables)
  - data model
  - assumptions
  - one section per objective (summary, alignment with the business goal, cleaning and transformation steps with reasoning)
  - data-quality results (defects injected vs. detected)
  - possible extensions
- **Zipped folder:**
  - `generator/`: data generation scripts
  - `data/raw/`: the 5 CSVs and 200 JSON files
  - `sql/`: DDL, staging, core, and mart queries
  - `pipeline/`: Python ingestion and cleaning scripts
  - `output/`: the 5 final CSVs
  - `README.md`: how to run the pipeline
  - `.env.example`
  - `requirements.txt`

### 9.4 Immediate next step
Open the **Data Model and Data Dictionary** chat and produce the formal dictionary: field name, type, description, allowed values or format, key role, and the objectives that use it. Save it to the project files.
