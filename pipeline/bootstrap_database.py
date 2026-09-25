"""Create or upgrade the database schemas required by the pipeline."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg


ROOT = Path(__file__).resolve().parents[1]
SQL_FILES = [
    ROOT / "sql" / "00_create_schemas.sql",
    ROOT / "sql" / "01_create_raw_control_tables.sql",
    ROOT / "sql" / "02_create_raw_source_tables.sql",
    ROOT / "sql" / "03_create_staging_tables.sql",
    ROOT / "sql" / "04_create_core_tables.sql",
    ROOT / "sql" / "05_create_indexes.sql",
    ROOT / "sql" / "06_enable_rls.sql",
    ROOT / "sql" / "07_create_staging_helpers.sql",
]


def bootstrap_database(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            for path in SQL_FILES:
                conn.execute(path.read_text(encoding="utf-8"))
                print(f"Applied {path.name}")


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 2
    try:
        bootstrap_database(database_url)
    except Exception as exc:
        print(f"Database bootstrap failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("Database bootstrap succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
