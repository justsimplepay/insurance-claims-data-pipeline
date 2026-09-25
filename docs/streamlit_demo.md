# Bonus Streamlit Demo

This is an additive presentation layer around the existing data-engineering pipeline. The existing generator, SQL, pipeline modules, CLI runner, and output behavior are unchanged.

## Run locally

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure a **dedicated demo database**. Never point the public demo at the development or production database:

```bash
export DATABASE_URL='postgresql://...'
export DEMO_ALLOW_DB_RESET=true
```

Then start the UI:

```bash
streamlit run app.py
```

The application accepts one ZIP containing:

```text
Customer.csv
Policy.csv
Claim.csv
Claim_Payment.csv
Policy_Premium.csv
json/
  *.json
```

The files may be at the ZIP root or inside one enclosing directory.

## Isolation and safety

The demo validates archive paths, rejects symlinks and traversal paths, limits upload/extracted sizes, uses a temporary workspace, serializes jobs in a single Streamlit process, and deletes the temporary workspace automatically.

Before each run it drops only the pipeline schemas `raw`, `staging`, `core`, and `marts` in the configured database. The frozen pipeline then recreates them through its normal bootstrap stage. For that reason, `DEMO_ALLOW_DB_RESET=true` must only be enabled for a dedicated demo database.

For a deployment that can run multiple application processes or replicas, configure the host to run one Streamlit process or add a cross-process database lock before exposing the demo publicly.

## Existing CLI remains unchanged

The original workflow remains independently runnable:

```bash
python pipeline/run_pipeline.py \
  --raw-dir data/raw \
  --batch-name initial_synthetic_batch \
  --output-dir output
```

The Streamlit application invokes that same runner as a subprocess; it does not replace or alter the pipeline.
