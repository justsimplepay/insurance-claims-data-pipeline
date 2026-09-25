"""Export the five marts and the data-quality log to CSV files."""

from __future__ import annotations

import argparse
import csv
import json
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


def _normalized_dq_rows(conn: psycopg.Connection, load_id: int):
    columns = [
        "dq_id", "load_id", "source_name", "source_table", "source_record_id",
        "business_key", "rule_id", "field_name", "severity", "action",
        "original_value", "clean_value", "details", "detected_at",
    ]
    rows = conn.execute(
        """
        SELECT dq_id, load_id, source_name, source_table, source_record_id,
               business_key, rule_id, field_name, severity, action,
               original_value, clean_value, details, detected_at
        FROM staging.data_quality_log
        WHERE load_id=%s
        ORDER BY dq_id
        """,
        (load_id,),
    ).fetchall()

    customer_ids = {
        r[0] for r in conn.execute(
            "SELECT customer_id FROM staging.customers WHERE load_id=%s", (load_id,)
        )
    }
    policy_ids = {
        r[0] for r in conn.execute(
            "SELECT policy_id FROM staging.policies WHERE load_id=%s", (load_id,)
        )
    }
    claim_ids = {
        r[0] for r in conn.execute(
            "SELECT claim_id FROM staging.claims WHERE load_id=%s", (load_id,)
        )
    }
    policy_parent = dict(conn.execute(
        "SELECT raw_row_id, customer_id FROM staging.policies WHERE load_id=%s",
        (load_id,),
    ).fetchall())
    claim_parent = dict(conn.execute(
        "SELECT raw_row_id, policy_id FROM staging.claims WHERE load_id=%s",
        (load_id,),
    ).fetchall())
    payment_parent = dict(conn.execute(
        "SELECT raw_row_id, claim_id FROM staging.claim_payments WHERE load_id=%s",
        (load_id,),
    ).fetchall())
    premium_parent = dict(conn.execute(
        "SELECT raw_row_id, policy_id FROM staging.policy_premiums WHERE load_id=%s",
        (load_id,),
    ).fetchall())

    out = []
    for row in rows:
        item = dict(zip(columns, row))
        if item["rule_id"] == "JSON_MISSING_FILE":
            continue
        if item["rule_id"] == "JSON_ORPHAN" and item["business_key"] in claim_ids:
            item["rule_id"] = "UPSTREAM_PARENT_REJECTED"
        elif item["rule_id"] == "ORPHAN_FK":
            rid = item["source_record_id"]
            table = item["source_table"]
            cascade = (
                (table == "raw.policy_csv" and policy_parent.get(rid) in customer_ids)
                or (table == "raw.claim_csv" and claim_parent.get(rid) in policy_ids)
                or (table == "raw.claim_payment_csv" and payment_parent.get(rid) in claim_ids)
                or (table == "raw.policy_premium_csv" and premium_parent.get(rid) in policy_ids)
            )
            if cascade:
                item["rule_id"] = "UPSTREAM_PARENT_REJECTED"
        out.append(item)

    physical_json_ids = {
        name[:-5].strip().upper()
        for (name,) in conn.execute(
            """
            SELECT source_name
            FROM raw.source_files
            WHERE load_id=%s AND source_format='json'
            """,
            (load_id,),
        )
        if name.lower().endswith(".json")
    }
    source_claims = {}
    for raw_row_id, claim_id in conn.execute(
        """
        SELECT raw_row_id, claim_id
        FROM staging.claims
        WHERE load_id=%s AND claim_id ~ '^[HDT][0-9]{5}    row = conn.execute(
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
        header, dq_rows = _normalized_dq_rows(conn, selected_load)
        dq_path = output_dir / "data_quality_log.csv"
        with dq_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            writer.writerows(dq_rows)
        print(f"  data_quality_log.csv: {len(dq_rows)} rows (load_id={selected_load})")


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

        ORDER BY raw_row_id
        """,
        (load_id,),
    ):
        source_claims.setdefault(claim_id, raw_row_id)

    for claim_id, raw_row_id in source_claims.items():
        if claim_id not in physical_json_ids:
            out.append({
                "dq_id": None, "load_id": load_id,
                "source_name": "Claim.csv / JSON directory",
                "source_table": "staging.claims",
                "source_record_id": raw_row_id,
                "business_key": claim_id,
                "rule_id": "JSON_MISSING_FILE",
                "field_name": None, "severity": "warning", "action": "flagged",
                "original_value": None, "clean_value": None,
                "details": json.dumps({"reason": "no physical JSON file exists"}),
                "detected_at": None,
            })

    return columns, out


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
                original_value, clean_value, details, detected_at
            FROM staging.data_quality_log
            WHERE load_id=%s
            ORDER BY dq_id
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
