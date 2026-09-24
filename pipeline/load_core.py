"""Execute the recurring staging -> core reconciliation/load for one batch."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg


SQL_PATH = Path(__file__).resolve().parents[1] / "sql" / "20_core_load.sql"


def latest_successful_staged_load(conn: psycopg.Connection) -> int:
    row = conn.execute(
        """
        SELECT r.load_id
        FROM raw.ingestion_runs r
        WHERE r.status = 'succeeded'
          AND EXISTS (
              SELECT 1 FROM staging.customers s WHERE s.load_id = r.load_id
          )
        ORDER BY r.load_id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("No successful staged load exists.")
    return int(row[0])


def load_core(database_url: str, load_id: int | None) -> int:
    sql_template = SQL_PATH.read_text(encoding="utf-8")

    with psycopg.connect(database_url) as conn:
        selected_load_id = load_id or latest_successful_staged_load(conn)
        sql_text = sql_template.replace("__LOAD_ID__", str(int(selected_load_id)))

        with conn.transaction():
            conn.execute(sql_text)

        counts = conn.execute(
            """
            SELECT 'customers', count(*) FROM core.customers
            UNION ALL SELECT 'policies', count(*) FROM core.policies
            UNION ALL SELECT 'claims', count(*) FROM core.claims
            UNION ALL SELECT 'claim_payments', count(*) FROM core.claim_payments
            UNION ALL SELECT 'policy_premiums', count(*) FROM core.policy_premiums
            UNION ALL SELECT 'claim_details', count(*) FROM core.claim_details
            UNION ALL SELECT 'claim_line_items', count(*) FROM core.claim_line_items
            ORDER BY 1
            """
        ).fetchall()

        reconciliation_events = conn.execute(
            """
            SELECT count(*)
            FROM staging.data_quality_log
            WHERE load_id=%s
              AND rule_id LIKE 'CORE_%%'
            """,
            (selected_load_id,),
        ).fetchone()[0]

    print(f"Core load succeeded: load_id={selected_load_id}")
    for table_name, count in counts:
        print(f"  {table_name}: {count}")
    print(f"  core reconciliation DQ events: {reconciliation_events}")
    return selected_load_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply source-of-truth rules and load canonical core tables."
    )
    parser.add_argument(
        "--load-id",
        type=int,
        default=None,
        help="Staged load_id. Defaults to the latest successful staged load.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 2

    try:
        load_core(database_url, args.load_id)
    except Exception as exc:
        print(f"Core load failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
