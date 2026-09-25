"""Execute the recurring raw -> staging transformation for one load.\n\nIncludes post-transform DQ semantic validation.\n"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg


SQL_PATH = Path(__file__).resolve().parents[1] / "sql" / "10_staging_transformations.sql"


def latest_successful_load(conn: psycopg.Connection) -> int:
    row = conn.execute(
        """
        SELECT load_id
        FROM raw.ingestion_runs
        WHERE status = 'succeeded'
        ORDER BY load_id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("No successful raw ingestion run exists.")
    return int(row[0])


def transform_staging(database_url: str, load_id: int | None) -> int:
    sql_template = SQL_PATH.read_text(encoding="utf-8")

    with psycopg.connect(database_url) as conn:
        selected_load_id = load_id or latest_successful_load(conn)

        # The replacement value is constrained to Python int, so it cannot
        # inject arbitrary SQL. All transformations run atomically.
        sql_text = sql_template.replace("__LOAD_ID__", str(int(selected_load_id)))

        with conn.transaction():
            conn.execute(sql_text)

        summary = conn.execute(
            """
            SELECT 'customers' AS table_name, record_status, count(*)
            FROM staging.customers WHERE load_id=%s GROUP BY record_status
            UNION ALL
            SELECT 'policies', record_status, count(*)
            FROM staging.policies WHERE load_id=%s GROUP BY record_status
            UNION ALL
            SELECT 'claims', record_status, count(*)
            FROM staging.claims WHERE load_id=%s GROUP BY record_status
            UNION ALL
            SELECT 'claim_payments', record_status, count(*)
            FROM staging.claim_payments WHERE load_id=%s GROUP BY record_status
            UNION ALL
            SELECT 'policy_premiums', record_status, count(*)
            FROM staging.policy_premiums WHERE load_id=%s GROUP BY record_status
            UNION ALL
            SELECT 'claim_details', record_status, count(*)
            FROM staging.claim_details WHERE load_id=%s GROUP BY record_status
            ORDER BY table_name, record_status
            """,
            (selected_load_id,) * 6,
        ).fetchall()

        line_count = conn.execute(
            "SELECT count(*) FROM staging.claim_line_items WHERE load_id=%s",
            (selected_load_id,),
        ).fetchone()[0]
        dq_count = conn.execute(
            "SELECT count(*) FROM staging.data_quality_log WHERE load_id=%s",
            (selected_load_id,),
        ).fetchone()[0]
        quarantine_count = conn.execute(
            "SELECT count(*) FROM staging.quarantine WHERE load_id=%s",
            (selected_load_id,),
        ).fetchone()[0]

    print(f"Staging transformation succeeded: load_id={selected_load_id}")
    for table_name, status, count in summary:
        print(f"  {table_name}: {status}={count}")
    print(f"  claim_line_items: {line_count}")
    print(f"  data_quality_log: {dq_count}")
    print(f"  quarantine: {quarantine_count}")
    return selected_load_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transform one successful raw load into typed staging tables."
    )
    parser.add_argument(
        "--load-id",
        type=int,
        default=None,
        help="Raw ingestion load_id. Defaults to the latest successful load.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 2

    try:
        transform_staging(database_url, args.load_id)
    except Exception as exc:
        print(f"Staging transformation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
