"""Run the complete reproducible claims data pipeline.

Execution order:

    source files
        -> raw ingestion
        -> staging normalization / DQ
        -> core reconciliation
        -> five analytics marts
        -> CSV deliverables
        -> end-to-end validation

The raw loader is fingerprint-idempotent, and the downstream stages are
rerunnable for the selected load_id.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from ingest_raw import run_ingestion
from transform_staging import transform_staging
from load_core import load_core
from build_marts import build_marts
from export_outputs import EXPORTS, export_outputs


EXPECTED_OUTPUTS = [filename for filename, _ in EXPORTS] + ["data_quality_log.csv"]


def _print_stage(number: int, title: str) -> None:
    print()
    print("=" * 72)
    print(f"Stage {number}: {title}")
    print("=" * 72)


def validate_pipeline(
    database_url: str,
    *,
    load_id: int,
    output_dir: Path,
) -> None:
    """Run lightweight end-to-end invariants after export."""

    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            """
            SELECT
                (SELECT count(*) FROM core.claims) AS core_claims,
                (SELECT count(*) FROM marts.fraud_features) AS fraud_rows,
                (SELECT count(*) FROM marts.operations_features) AS operations_rows,
                (SELECT coalesce(sum(claim_count),0) FROM marts.region_summary) AS regional_claims,
                (SELECT count(*) FROM marts.retention_features) AS retention_rows,
                (SELECT count(*) FROM marts.policy_performance) AS policy_rows,
                (
                    SELECT count(*)
                    FROM staging.data_quality_log
                    WHERE load_id=%s
                ) AS dq_events
            """,
            (load_id,),
        ).fetchone()

        (
            core_claims,
            fraud_rows,
            operations_rows,
            regional_claims,
            retention_rows,
            policy_rows,
            dq_events,
        ) = row

        duplicate_retention_customers = conn.execute(
            """
            SELECT count(*)
            FROM (
                SELECT customer_id
                FROM marts.retention_features
                GROUP BY customer_id
                HAVING count(*) > 1
            ) x
            """
        ).fetchone()[0]

        sensitive_columns = conn.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema='marts'
              AND column_name IN (
                  'first_name',
                  'last_name',
                  'email',
                  'phone',
                  'postal_code',
                  'date_of_birth',
                  'address',
                  'city'
              )
            ORDER BY table_name, column_name
            """
        ).fetchall()

        core_direct_columns = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='core'
              AND table_name='customers'
              AND column_name IN (
                  'first_name','last_name','address','city',
                  'postal_code','phone','email'
              )
            ORDER BY column_name
            """
        ).fetchall()

        staging_direct_values = conn.execute(
            """
            SELECT count(*)
            FROM staging.customers
            WHERE load_id=%s
              AND (
                  first_name IS NOT NULL OR last_name IS NOT NULL
                  OR address IS NOT NULL OR city IS NOT NULL
                  OR postal_code IS NOT NULL OR phone IS NOT NULL
                  OR email IS NOT NULL
              )
            """,
            (load_id,),
        ).fetchone()[0]

        exposed_dq_values = conn.execute(
            """
            SELECT count(*)
            FROM staging.data_quality_log
            WHERE load_id=%s
              AND source_table='raw.customer_csv'
              AND field_name IN (
                  'first_name','last_name','date_of_birth','address',
                  'city','postal_code','phone','email'
              )
              AND (original_value IS NOT NULL OR clean_value IS NOT NULL)
            """,
            (load_id,),
        ).fetchone()[0]

    checks = [
        (
            "fraud mart has one row per canonical claim",
            fraud_rows == core_claims,
            f"{fraud_rows} vs {core_claims}",
        ),
        (
            "operations mart has one row per canonical claim",
            operations_rows == core_claims,
            f"{operations_rows} vs {core_claims}",
        ),
        (
            "regional aggregate preserves all canonical claims",
            regional_claims == core_claims,
            f"{regional_claims} vs {core_claims}",
        ),
        (
            "retention mart customer grain is unique",
            duplicate_retention_customers == 0,
            f"duplicate customer groups={duplicate_retention_customers}",
        ),
        (
            "retention mart is non-empty",
            retention_rows > 0,
            f"rows={retention_rows}",
        ),
        (
            "policy mart is non-empty",
            policy_rows > 0,
            f"rows={policy_rows}",
        ),
        (
            "data-quality log contains detected events",
            dq_events > 0,
            f"events={dq_events}",
        ),
        (
            "marts exclude direct and fine-grained quasi identifiers",
            len(sensitive_columns) == 0,
            (
                "none"
                if not sensitive_columns
                else ", ".join(f"{t}.{c}" for t, c in sensitive_columns)
            ),
        ),
        (
            "core customers exclude unnecessary direct-identifier columns",
            len(core_direct_columns) == 0,
            (
                "none"
                if not core_direct_columns
                else ", ".join(c[0] for c in core_direct_columns)
            ),
        ),
        (
            "staging customer direct identifiers are scrubbed after resolution",
            staging_direct_values == 0,
            f"rows_with_direct_values={staging_direct_values}",
        ),
        (
            "staging DQ log redacts customer PII values",
            exposed_dq_values == 0,
            f"events_with_exposed_values={exposed_dq_values}",
        ),
    ]

    failures = []
    for label, ok, detail in checks:
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {label} ({detail})")
        if not ok:
            failures.append(label)

    for filename in EXPECTED_OUTPUTS:
        path = output_dir / filename
        if not path.is_file():
            failures.append(f"missing export: {filename}")
            print(f"  [FAIL] export exists: {filename}")
            continue

        with path.open("r", encoding="utf-8", newline="") as handle:
            row_count = max(sum(1 for _ in csv.reader(handle)) - 1, 0)
        print(f"  [PASS] export exists: {filename} ({row_count} data row(s))")

    if failures:
        raise RuntimeError(
            "End-to-end validation failed: " + "; ".join(failures)
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run raw -> staging -> core -> marts -> CSV export."
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("data/raw"),
        help="Source directory containing the five CSVs and json/ subdirectory.",
    )
    parser.add_argument(
        "--batch-name",
        default=None,
        help=(
            "Human-readable raw batch name. Defaults to an UTC timestamp. "
            "For an already-ingested fingerprint, the existing successful "
            "load_id is reused."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Destination directory for the five deliverables and DQ log.",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip the final end-to-end smoke checks.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "DATABASE_URL is required. Set it to the PostgreSQL/Supabase "
            "connection string before running the pipeline.",
            file=sys.stderr,
        )
        return 2

    batch_name = args.batch_name or datetime.now(timezone.utc).strftime(
        "pipeline_%Y%m%dT%H%M%SZ"
    )

    try:
        _print_stage(1, "Raw ingestion")
        load_id = run_ingestion(
            database_url=database_url,
            raw_dir=args.raw_dir,
            batch_name=batch_name,
        )

        _print_stage(2, "Staging transformation")
        transform_staging(database_url, load_id)

        _print_stage(3, "Core reconciliation and load")
        load_core(database_url, load_id)

        _print_stage(4, "Analytics marts")
        build_marts(database_url)

        _print_stage(5, "CSV export")
        export_outputs(
            database_url=database_url,
            output_dir=args.output_dir,
            load_id=load_id,
        )

        if not args.skip_validation:
            _print_stage(6, "End-to-end validation")
            validate_pipeline(
                database_url,
                load_id=load_id,
                output_dir=args.output_dir,
            )

    except Exception as exc:
        print(
            f"Pipeline failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    print()
    print("=" * 72)
    print(
        f"Pipeline completed successfully: load_id={load_id}, "
        f"output_dir={args.output_dir}"
    )
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
