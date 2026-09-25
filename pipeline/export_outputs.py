"""Export the five marts and the data-quality log to CSV files."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

import psycopg


EXPORTS = [
    (
        "fraud_detection.csv",
        "SELECT * FROM marts.fraud_features ORDER BY claim_date, claim_id",
    ),
    (
        "customer_retention.csv",
        "SELECT * FROM marts.retention_features ORDER BY customer_id",
    ),
    (
        "operational_efficiency.csv",
        "SELECT * FROM marts.operations_features ORDER BY submission_date, claim_id",
    ),
    (
        "region_wise_insights.csv",
        "SELECT * FROM marts.region_summary ORDER BY quarter_start, province, product_line, claim_type",
    ),
    (
        "policy_optimization.csv",
        "SELECT * FROM marts.policy_performance ORDER BY policy_type, plan_name, age_band, coverage_type, province",
    ),
]


def write_query(conn: psycopg.Connection, query: str, path: Path, params=None) -> int:
    with conn.cursor() as cur:
        cur.execute(query, params)
        header = [col.name for col in cur.description]
        rows = cur.fetchall()

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)

    return len(rows)


def latest_load_id(conn: psycopg.Connection) -> int:
    row = conn.execute(
        """
        SELECT load_id
        FROM raw.ingestion_runs
        WHERE status='succeeded'
        ORDER BY load_id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("No successful ingestion run exists")
    return int(row[0])


def export_outputs(database_url: str, output_dir: Path, load_id: int | None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(database_url) as conn:
        for filename, query in EXPORTS:
            count = write_query(conn, query, output_dir / filename)
            print(f"  {filename}: {count} rows")

        selected_load = load_id or latest_load_id(conn)
        dq_query = """
            SELECT
                dq_id, load_id, source_name, source_table, source_record_id,
                business_key, rule_id, field_name, severity, action,
                CASE
                    WHEN source_table='raw.customer_csv'
                     AND field_name IN (
                        'first_name','last_name','date_of_birth','address',
                        'city','postal_code','phone','email'
                     )
                        THEN NULL
                    ELSE original_value
                END AS original_value,
                CASE
                    WHEN source_table='raw.customer_csv'
                     AND field_name IN (
                        'first_name','last_name','date_of_birth','address',
                        'city','postal_code','phone','email'
                     )
                        THEN NULL
                    ELSE clean_value
                END AS clean_value,
                CASE
                    WHEN source_table='raw.customer_csv'
                     AND field_name IN (
                        'first_name','last_name','date_of_birth','address',
                        'city','postal_code','phone','email'
                     )
                        THEN COALESCE(details, '{}'::jsonb)
                             || jsonb_build_object('pii_value_redacted', true)
                    ELSE details
                END AS details,
                NULL::timestamptz AS detected_at
            FROM staging.data_quality_log
            WHERE load_id=%s
            ORDER BY
                source_name,
                source_table,
                source_record_id,
                business_key,
                rule_id,
                field_name,
                severity,
                action,
                COALESCE(original_value, ''),
                COALESCE(clean_value, ''),
                COALESCE(details::text, '')
        """
        count = write_query(
            conn,
            dq_query,
            output_dir / "data_quality_log.csv",
            (selected_load,),
        )
        print(f"  data_quality_log.csv: {count} rows (load_id={selected_load})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export mart deliverables to CSV.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Destination directory; default: output",
    )
    parser.add_argument(
        "--load-id",
        type=int,
        default=None,
        help="Load ID for data_quality_log.csv; defaults to latest successful load.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 2

    try:
        export_outputs(database_url, args.output_dir, args.load_id)
    except Exception as exc:
        print(f"Export failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
