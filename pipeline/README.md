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
2. staging transformations       (next implementation step)
    |
    v
staging
    |
    v
3. core load/reconciliation      (next implementation step)
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
