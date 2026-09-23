# GMS Data Engineer Case Study: Project Overview, Decisions & Plan

*Reference document for all chats in this project. Version 1.2, last updated Wednesday, September 23, 2026.*

---

## 0. Change Log

| Version | Date | Changes |
|---|---|---|
| 1.0 | Sept 23, 2026 | Initial planning and architecture decisions. |
| 1.1 | Sept 23, 2026 | Incorporated the final requirements review. Added: snapshot date and time windows (§3.5), `decision_date` / `decision_outcome` replacing `approval_date` (§4.3), source-of-truth rules for claim status (§4.5), probabilistic fraud label (§3.3), exposure-matched loss ratio (§4.4), small-sample handling for the region mart (§3.2, §7), data minimization in marts (Decision 5), thin-slice build order (Decision 6), exact 200 JSON files, deadline in UTC, and submission logistics (§9.3). |
| 1.2 | Sept 23, 2026 | Split the data model work into two chats: a Source Data Dictionary (raw files, feeds the generator) and a Database Model (staging, core, marts; designed against generated data). Added the completed Git setup. Updated §9.1 chat structure, §9.2 timeline, §9.3 repo docs, and §9.4 next step. |

---

## 1. Context

| Item | Detail |
|---|---|
| Role | Junior Data Engineer, GMS (Group Medical Services), Regina, SK |
| Contact | Augustine Ayo, Manager, Technical Delivery (aayo@gms.ca) |
| Deadline | **9:00 AM Regina time (CST), Monday, September 28, 2026** = **15:00 UTC** (Saskatchewan does not observe daylight saving time, so CST is exact). Target: finish Sunday, Sept 27. |
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
- Claim Details: **exactly 200 JSON files**, one JSON file per claim

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

**Interpretations of ambiguous points in the brief** (stated as assumptions in §8):
- **Three master files:** the brief does not say why there are three. They are interpreted as three product-line source systems (health, dental, travel), each with its own conventions.
- **JSON-to-claim ratio:** "each JSON file represents details of individual claims" is interpreted as one claim per file. The file count is held at exactly 200 to match the brief literally.

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

### 3.2 Size and distribution
- About 150 customers, 180 policies, and 200–220 claims.
- **Exactly 200 JSON files**, counting the malformed files and the orphan files within the 200.
- A few claims have **no JSON file**, and a few JSON files have **no master record**, to demonstrate orphan handling.
- **History window:** 36 months of activity, from 2023-07-01 to the extract end date of 2026-06-30 (see §3.5). This gives Objective 4 a real time axis.
- **Provincial weighting** reflects a Saskatchewan-based insurer, so regional cells are not uniformly thin:

| Province | Share of customers |
|---|---|
| SK | ~35% |
| AB | ~20% |
| MB | ~15% |
| ON | ~15% |
| BC | ~8% |
| NS, PE, NL, YT, NT combined | ~7% |

  QC, NB, and NU receive only deliberately injected invalid records (see §4.2).

### 3.3 Signals to embed (so the objectives have something to find)
- **Fraud:** about 3–5% of claims have suspicious traits:
  - a claim shortly after the policy start or during a waiting period
  - an amount near the benefit maximum
  - repeated claims, or provider concentration
  - line items that don't add up to the claim total
  - missing documents
  - weekend submission

  **Labelling approach:** the traits *raise the probability* of fraud rather than determine it. Some fraud claims show few traits, and some legitimate claims show several. This prevents the rule-based flags from matching the label perfectly, which would make the exercise look circular. The label column is named **`is_fraud_synthetic_label`**. The report notes that in production, labels would come from special investigation outcomes.
- **Churn:** late or missed premiums and denied claims raise the probability of a policy lapsing. Lapses are timed so that some occur inside the outcome window defined in §3.5.
- **Operations:** certain regions, claim types, submission channels, and incomplete document sets are slower. Denied claims also carry decision times, so their bottlenecks are measurable.
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
  - customers in QC, NB, or NU
- **Type problems:** amounts stored as text (`$1,250.00`).
- **Outliers:** some are genuine and some are errors. Distinguishing them is part of the work, and unusual records are kept where they matter for fraud detection.
- **Cross-source conflicts:** a small number of claims whose `claim_status` in the master file disagrees with the `decision_outcome` in the payments file. These are resolved by the rules in §4.5.
- **JSON issues:** missing keys, different structures by product type, nested objects, and one or two malformed files.

**Justification for three master files:** they represent three source systems or product lines (health, dental, travel), each with its own conventions.

### 3.5 Snapshot date and time windows

| Parameter | Value | Purpose |
|---|---|---|
| History start | 2023-07-01 | Earliest generated activity |
| `snapshot_date` | 2026-03-31 | Retention features use only events on or before this date |
| Outcome window | 2026-04-01 to 2026-06-30 (90 days) | Churn label = policy lapsed or cancelled inside this window |
| Extract end date | 2026-06-30 | Last date in the data; Fraud, Operations, Region, and Policy marts use the full history to this date |

**Reason:** tenure, days since last claim, premium arrears, and the churn label all depend on an as-of date. Computing features only from events before the snapshot, and the label only from events after it, prevents label leakage in the retention mart. All dates are parameters of the generator and the pipeline.

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

`customer_id` is consistent across the three files. Cross-file customer duplicates are near-duplicates in formatting only, which keeps entity resolution in scope without requiring fuzzy matching.

**Claims Payment (one row per adjudication decision)**
- `payment_id`, `claim_id`
- `decision_date`, `decision_outcome` (approved, partially approved, denied)
- `payment_date`, `payment_amount`
- `payment_method`, `payment_status`, `denial_reason`

Row rules:
- **Denied claims have a row:** `decision_date` is populated, `payment_amount` = 0, and the payment fields are null. Nulls here are legitimate.
- **Pending claims have no row.** Their absence is legitimate and is flagged as `is_pending` in core.

*Changed from v1.0:* `approval_date` is replaced by `decision_date` plus `decision_outcome`, so processing time is measurable for denied claims as well as approved ones.

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
  - `is_fraud_synthetic_label` (for supervised modelling)
- **Retention:**
  - premium payment history, policy status, denied claims, tenure
  - all computed as of `snapshot_date`
  - churn label from the outcome window
- **Operations:**
  - submission, decision, and payment dates
  - derived durations: submission → decision, decision → payment, submission → payment
  - channel, adjuster, claim type, document completeness, decision outcome
- **Region:**
  - province, city, product line, claim type, amounts
  - aggregated by quarter
- **Policy optimization:**
  - the **loss ratio** (claims paid ÷ premiums earned) by plan, age band, coverage type, and province
  - claims and premiums are matched to the **same exposure period** at each grain
  - `policy_months` shows exposure, and `low_exposure_flag` marks cells below a minimum threshold, so newly written policies do not show inflated ratios

### 4.5 Source-of-truth and reconciliation rules

| Attribute | Authoritative source | Reconciliation |
|---|---|---|
| Claim existence | Claims master | JSON files without a master record are logged as orphans and excluded from core |
| Claim amount | Claims master | Compared with the sum of JSON line items; mismatches become data-quality and fraud flags |
| Decision outcome and dates | Payments file | Master `claim_status` is compared against it; conflicts are logged, and the payments value wins |
| Payment amount | Payments file | Compared with `approved_amount`; mismatches are flagged |
| Customer attributes | Most recent record across the three master files | Differences are logged in the deduplication step |

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
CORE DATABASE  (integrated relational model linked by keys,
                source-of-truth rules applied)
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
| `core` | Integrated entity tables with keys and source-of-truth rules applied. |
| `marts` | Five flat, objective-specific datasets exported as CSV. |

**Core tables:**

| Table | Grain (one row per…) | Built from |
|---|---|---|
| `customers` | customer | 3 master files, deduplicated |
| `policies` | policy | master files |
| `claims` | claim | 3 master files unioned (central fact table) |
| `claim_details` | claim | JSON files |
| `claim_line_items` | line item | JSON files |
| `claim_payments` | adjudication decision | payments CSV |
| `premiums` | policy × billing month | premiums CSV |

**Keys:**
- `customers` → `policies` (`customer_id`)
- `policies` → `claims` (`policy_id`)
- `policies` → `premiums` (`policy_id`)
- `claims` → `claim_details`, `claim_line_items`, and `claim_payments` (`claim_id`)

**Grain of the marts:**

| Mart | Grain | Time scope |
|---|---|---|
| Fraud | one row per claim | full history to extract end |
| Operations | one row per claim | full history to extract end |
| Retention | one row per customer | features ≤ `snapshot_date`; label from outcome window |
| Region | province × product line × claim type × quarter, with `claim_count` | full history, quarterly |
| Policy | plan × age band × coverage type × province, with `policy_months` and `low_exposure_flag` | exposure-matched |

A supporting `data_quality_log` table (and CSV) records every rule applied, the rows affected, and the action taken. It feeds the "defects injected vs. detected" section of the report.

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
- **Portability:** only `DATABASE_URL` needs to change to run on any PostgreSQL instance, including a local one.

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
- **Orchestration:** a single entry point, `run_pipeline.py`, runs every step in order (raw load → staging → core → marts → CSV export).

This covers the "SQL queries or Python scripts" deliverable with both.

**Report justification sentence:** "PostgreSQL (via Supabase) was chosen for its relational integrity, native JSONB support for semi-structured claim details, schema-based layer separation, and Canadian region hosting; all transformations are scripted for full reproducibility on any PostgreSQL instance."

### Decision 5: Data minimization in the marts
- Direct identifiers (names, email, phone, full postal code, exact date of birth) stay in `core.customers` and **never appear in the five output CSVs**.
- The marts use `customer_id`, age band, province, city, and the **forward sortation area** (first three characters of the postal code).
- **Reason:** the data scientists do not need direct identifiers, and minimizing them is standard practice for health data. Together with `ca-central-1` hosting, this is mentioned in the report as privacy awareness for a Saskatchewan health insurer.

### Decision 6: Build order is a thin slice first
- First, a small generated dataset runs end to end: raw → staging → core → one mart.
- Then each layer is widened (all defect types, all sources, all five marts).
- **Reason:** integration problems surface early rather than on Sunday.
- **If time runs short, cut in this order:**
  1. the "possible extensions" section
  2. defect variety
  3. polish in the report

  The five marts and the cleaning log are never cut, because they are what the brief grades.

---

## 8. Assumptions (to state in the report)
1. The data is synthetic, modelled on industry practice and public GMS product information, not on GMS's internal schema.
2. Each JSON file represents exactly one claim, and exactly 200 JSON files are produced, as in the brief.
3. The three master files represent three product-line source systems (health, dental, travel) with differing conventions.
4. The sample size is small by design (the brief fixes 200 JSON files), and the generator is parameterized to scale. Regional and policy aggregates carry counts and exposure so that small cells are visible.
5. Processing is a batch run. Event-driven or streaming ingestion is noted as a possible extension.
6. Model-ready features and flags are provided; model building is out of scope.
7. Currency is CAD. Travel claims in foreign currency are converted using a recorded exchange rate.
8. The data covers 2023-07-01 to 2026-06-30. Retention uses a snapshot date of 2026-03-31 and a 90-day outcome window.
9. The fraud label is synthetic and probabilistic. In production, it would come from investigation outcomes.
10. The customer base is weighted toward Saskatchewan and western Canada, reflecting a Regina-based insurer.

---

## 9. Plan

### 9.1 Chat structure in this project

| # | Chat | Scope | Output |
|---|---|---|---|
| 1 | **Planning** | Overview, requirements, decisions, final requirements review | This document (done) |
| 2 | **Git and local setup** | Repo, folder structure, `.gitignore`, virtual environment | Initialized repo (done) |
| 3 | **Source Data Dictionary** | The raw files only: 3 master CSVs (with each file's own naming conventions), payments CSV, premiums CSV, JSON schema per product line, and the defect types injected per file | `docs/source_data_dictionary.md` + YAML read by the generator |
| 4 | **Synthetic Data Generation** | Generator, embedded signals, time windows, defect injection, defect log | `generator/`, `data/raw/`, defect log |
| 5 | **Database Model** | Staging, core, and mart schemas; keys and grain; source-to-core mapping; validation, source-of-truth, and reconciliation rules; mart column lists; ERD (Mermaid) | `docs/database_model.md` + YAML read by the pipeline |
| 6 | **Supabase Setup, Ingestion and Cleaning** | Schemas, DDL, raw loading, staging transformations, JSON flattening | `sql/`, `pipeline/` |
| 7 | **Core Model and Objective Datasets** | Integration, reconciliation, feature engineering, five mart queries, CSV exports (can be split per objective if long) | `output/` |
| 8 | **Report and Submission** | Report, cleaning-steps log with reasoning, assumptions, packaging, reply email | Report PDF, zip, email |

**Boundary between chats 3 and 5:** chat 3 describes what the *messy sources* look like; chat 5 describes what the *clean database* looks like and how the sources map into it. Chat 5 runs after generation so the design is checked against real generated data.

**Before each chat:** add the previous chat's documents (and, for chat 5, a sample of the raw files) to the project files.

**Tip:** start each new chat with one line, for example: *"This is the [X] chat for the GMS case study; use the project overview (v1.2) and the documents in the project files. First, confirm the overview version."*

### 9.2 Timeline

| Day | Work |
|---|---|
| **Wed, Sept 23** | Planning, architecture decisions, and final requirements review (done). Git setup (done). Create the Supabase project in `ca-central-1`. |
| **Thu, Sept 24** | Source Data Dictionary (chat 3). Start the generator (chat 4) and run a **thin slice**: a small generated sample through a minimal raw → staging → core → one mart path. |
| **Fri, Sept 25** | Finish generation (clean ground truth, then defects and defect log). Database Model (chat 5). |
| **Sat, Sept 26** | Supabase schemas, raw loading, staging cleaning, JSON flattening (chat 6). Core model, validation and reconciliation checks (chat 7). |
| **Sun, Sept 27** | Five mart datasets and CSV exports (chat 7). Report (chat 8). Final review and rerun from scratch to confirm reproducibility. **Submit Sunday evening.** |
| **Mon, Sept 28** | Buffer only. **Hard deadline: 9:00 AM CST (15:00 UTC).** |

### 9.3 Submission package
- **Report (PDF, attached separately from the zip):**
  - executive summary
  - architecture diagram (layers and core tables)
  - data model
  - assumptions
  - one section per objective (summary, alignment with the business goal, cleaning and transformation steps with reasoning). Shared cleaning steps are described once and cross-referenced, so each objective section is complete on its own.
  - data-quality results (defects injected vs. detected)
  - possible extensions
- **Zipped folder:**
  - `docs/`: project overview, source data dictionary, database model
  - `generator/`: data generation scripts
  - `data/raw/`: the 5 CSVs and 200 JSON files (shipped so reviewers need not run the generator)
  - `sql/`: DDL, staging, core, and mart queries
  - `pipeline/`: Python ingestion and cleaning scripts, including `run_pipeline.py`
  - `output/`: the 5 final CSVs and `data_quality_log.csv`
  - `README.md`: how to run the pipeline (set `DATABASE_URL`, run `python run_pipeline.py`)
  - `.env.example`
  - `requirements.txt`
- **Email logistics:**
  - Keep the total attachment size well under Gmail's 25 MB limit.
  - Include a GitHub or Google Drive link in the email body as a fallback, in case a mail filter strips the zip.
  - Reply on the original thread.

### 9.4 Immediate next step
1. Create the Supabase project in `ca-central-1` and store the database password in a password manager (never in the repo).
2. Open the **Source Data Dictionary** chat (chat 3). Save its Markdown output to the project files and commit both files to `docs/`.
