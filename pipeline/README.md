# Pipeline

The recurring pipeline is intentionally separated into four logical execution
steps:

```text
source files
    |
    v
1. ingest_raw.py
    |
    v
raw
    |
    v
2. transform_staging.py
    |
    v
staging
    |
    v
3. load_core.py
    |
    v
core
    |
    v
4. analytical mart generation    (later implementation step)
    |
    v
marts / CSV deliverables
```

## Step 1: raw ingestion

`ingest_raw.py` loads the five source CSVs and every `.json` file under
`data/raw/json/`.

It deliberately does **not** read:

- `data/raw/_defect_log.csv`
- `data/raw/_generation_manifest.json`

The evaluation defect log must never be an input to the cleaning pipeline.

### Connection

Set `DATABASE_URL` to a PostgreSQL connection string. Credentials must remain
outside Git.

Example:

```bash
export DATABASE_URL='postgresql://...'
python pipeline/ingest_raw.py --raw-dir data/raw --batch-name initial_synthetic_batch
```

For Supabase, use a direct or session-pooler PostgreSQL connection suitable for
a long-running script and require SSL in the connection configuration.

### Idempotency

The loader computes a SHA-256 batch fingerprint from the relative path and
content hash of the five source CSVs plus all JSON claim-detail files.

If the exact batch already completed successfully, rerunning the loader does not
insert duplicate raw records.

### Transaction behavior

The batch-control row is registered first. All source-file metadata and raw
business rows are then loaded in one transaction.

- success: raw rows commit and the run becomes `succeeded`;
- failure: raw inserts roll back and the run becomes `failed`;
- retry of a failed batch safely reuses the same `load_id`.

This gives the assessment's initial one-time load the same mechanics needed for
future recurring batches.


## Step 2: staging transformation

`transform_staging.py` executes `sql/10_staging_transformations.sql` for one
successful raw `load_id`. It defaults to the latest successful ingestion run.

```bash
python pipeline/transform_staging.py
```

Or run a specific batch:

```bash
python pipeline/transform_staging.py --load-id 3
```

The staging step is atomic and idempotent per load. It rebuilds that load's
staging rows and performs deterministic normalization, duplicate handling,
recoverable JSON schema-drift normalization, JSON flattening, data-quality
logging, and quarantine.

Reusable parsing/normalization functions are defined in
`sql/07_create_staging_helpers.sql`; they are database setup objects and are
not recreated for every batch.

Source-of-truth reconciliation across systems is intentionally deferred to the
core step. For example, staging normalizes `Claim.region`, but the core step
will compare it with the authoritative `Customer.province` and use the
customer value when they conflict.


## Step 3: core reconciliation and load

`load_core.py` executes `sql/20_core_load.sql` for one staged `load_id`.

```bash
python pipeline/load_core.py --load-id 3
```

The core step applies the documented source-of-truth rules and loads the
canonical PK/FK-constrained relational model. In particular:

- duplicate customer aliases are re-keyed to the canonical survivor;
- Policy.csv owns policy/customer relationships;
- Customer.csv owns province/region;
- Claim.csv owns claim existence and valid claim-header amount;
- JSON can repair demonstrably corrupt/missing claim amount or service date but
  otherwise remains reconciliation evidence;
- Claim_Payment.csv owns decision outcome/payment lifecycle;
- premium customer copies are discarded after policy-owner reconciliation;
- age band is re-derived from canonical DOB when possible.

Cross-source conflicts and deterministic repairs are appended to
`staging.data_quality_log` with `CORE_*` rule IDs.


## Step 4: analytics marts and CSV export

Build all five objective-specific marts:

```bash
python pipeline/build_marts.py
```

Then export the five clean CSV deliverables plus the data-quality log:

```bash
python pipeline/export_outputs.py --load-id 3
```

This creates:

- `output/fraud_detection.csv`
- `output/customer_retention.csv`
- `output/operational_efficiency.csv`
- `output/region_wise_insights.csv`
- `output/policy_optimization.csv`
- `output/data_quality_log.csv`

The marts deliberately contain engineered features and summaries rather than
fraud/churn model predictions. The retention mart is the one exception in that
it contains the documented future-window `churned` outcome label, because that
label is explicitly defined by the project design for supervised downstream
analysis.

The policy mart reports an exposure-aligned
`claims_to_premium_performance_ratio`; it is not described as an actuarial
loss ratio.
