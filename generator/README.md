# Synthetic data generator

This package implements `docs/generator_specification.md` and produces the five raw CSV extracts plus exactly 200 claim-detail JSON files.

## Run

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python generate_data.py
```

The default reference run uses seed `20260923` and writes to `data/raw/`.

Generated QA-only artifacts:

- `data/raw/_defect_log.csv` — injected defects; **must never be used by the pipeline**.
- `data/raw/_clean_qa.json` — clean invariant/signal gate results.
- `data/raw/_generation_manifest.json` — seed, versions, counts, QA summary, and SHA-256 hashes.

## Reproducibility

The generator uses stable stage-specific NumPy random streams, deterministic ordering, fixed decimal rounding, and deterministic serialization. With the same code, configuration, seed, and dependency environment, a rerun should produce the same hashes in `_generation_manifest.json`.

To use the reference seed explicitly:

```bash
python generate_data.py --seed 20260923
```

The submitted reference dataset is `scale=1`; larger-scale generation is intentionally not enabled yet.
