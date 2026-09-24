"""Build all five analytics marts from the canonical core schema."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg


ROOT = Path(__file__).resolve().parents[1]
SQL_FILES = [
    ROOT / "sql" / "30_fraud_mart.sql",
    ROOT / "sql" / "31_retention_mart.sql",
    ROOT / "sql" / "32_operations_mart.sql",
    ROOT / "sql" / "33_region_mart.sql",
    ROOT / "sql" / "34_policy_mart.sql",
]

MARTS = [
    "fraud_features",
    "retention_features",
    "operations_features",
    "region_summary",
    "policy_performance",
]


def build_marts(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        core_claims = conn.execute("SELECT count(*) FROM core.claims").fetchone()[0]
        if core_claims == 0:
            raise RuntimeError("core is empty; run pipeline/load_core.py first")

        with conn.transaction():
            for path in SQL_FILES:
                conn.execute(path.read_text(encoding="utf-8"))

        print("Mart build succeeded")
        for mart in MARTS:
            count = conn.execute(f"SELECT count(*) FROM marts.{mart}").fetchone()[0]
            print(f"  {mart}: {count}")


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 2

    try:
        build_marts(database_url)
    except Exception as exc:
        print(f"Mart build failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
