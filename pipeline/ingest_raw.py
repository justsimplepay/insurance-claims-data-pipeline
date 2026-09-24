"""Load source CSV/JSON files into the PostgreSQL raw schema.

This is the first recurring step of the pipeline:

    source files -> raw -> staging -> core -> marts

The loader is deliberately source-preserving:
- CSV business columns are inserted as text.
- Empty CSV cells are stored as NULL, per the source contract.
- JSON source text is always preserved.
- Valid JSON is also stored as JSONB.
- Malformed JSON is retained with parse_status='malformed'.
- The generator evaluation files (_defect_log.csv and
  _generation_manifest.json) are never read by this pipeline.

The load is idempotent at batch level using a deterministic fingerprint of the
five source CSVs plus all JSON files in data/raw/json.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import psycopg
from psycopg import sql


@dataclass(frozen=True)
class CsvSpec:
    filename: str
    table: str
    columns: tuple[str, ...]


CSV_SPECS: tuple[CsvSpec, ...] = (
    CsvSpec(
        "Customer.csv",
        "raw.customer_csv",
        (
            "customer_id",
            "first_name",
            "last_name",
            "date_of_birth",
            "gender",
            "address",
            "city",
            "province",
            "postal_code",
            "phone",
            "email",
            "customer_since",
            "last_updated",
        ),
    ),
    CsvSpec(
        "Policy.csv",
        "raw.policy_csv",
        (
            "policy_id",
            "customer_id",
            "policy_type",
            "plan_name",
            "coverage_type",
            "start_date",
            "end_date",
            "status",
            "sales_channel",
            "coverage_amount",
            "deductible_amount",
        ),
    ),
    CsvSpec(
        "Claim.csv",
        "raw.claim_csv",
        (
            "claim_id",
            "policy_id",
            "customer_id",
            "claim_type",
            "service_date",
            "claim_date",
            "claim_amount",
            "approved_amount",
            "claim_status",
            "region",
        ),
    ),
    CsvSpec(
        "Claim_Payment.csv",
        "raw.claim_payment_csv",
        (
            "payment_id",
            "claim_id",
            "processing_start_date",
            "decision_date",
            "decision_outcome",
            "payment_date",
            "payment_amount",
            "payment_method",
            "payment_status",
            "transaction_reference",
            "denial_reason",
        ),
    ),
    CsvSpec(
        "Policy_Premium.csv",
        "raw.policy_premium_csv",
        (
            "premium_id",
            "policy_id",
            "customer_id",
            "premium_amount",
            "premium_frequency",
            "age_band",
            "due_date",
            "paid_date",
            "payment_status",
            "payment_method",
        ),
    ),
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def discover_input_files(raw_dir: Path) -> tuple[list[Path], list[Path]]:
    missing = [spec.filename for spec in CSV_SPECS if not (raw_dir / spec.filename).is_file()]
    if missing:
        raise FileNotFoundError(
            "Missing required raw CSV file(s): " + ", ".join(sorted(missing))
        )

    json_dir = raw_dir / "json"
    if not json_dir.is_dir():
        raise FileNotFoundError(f"JSON directory does not exist: {json_dir}")

    csv_paths = [raw_dir / spec.filename for spec in CSV_SPECS]
    json_paths = sorted(path for path in json_dir.glob("*.json") if path.is_file())

    if not json_paths:
        raise FileNotFoundError(f"No JSON files found under: {json_dir}")

    return csv_paths, json_paths


def compute_batch_fingerprint(raw_dir: Path, files: Iterable[Path]) -> str:
    """Hash logical path + file hash for every intended pipeline input file."""
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(raw_dir).as_posix()):
        relative = path.relative_to(raw_dir).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def parse_csv(path: Path, expected_columns: Sequence[str]) -> list[tuple[int, list[str | None]]]:
    """Return physical line number plus source values.

    The header occupies physical line 1, so the first data record is line 2.
    Empty cells are NULL by source-contract convention; all other text,
    including surrounding whitespace/case defects, is preserved.
    """
    result: list[tuple[int, list[str | None]]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        actual_columns = tuple(reader.fieldnames or ())
        if actual_columns != tuple(expected_columns):
            raise ValueError(
                f"{path.name}: header mismatch. "
                f"Expected {list(expected_columns)!r}, got {list(actual_columns)!r}"
            )

        for source_row_number, row in enumerate(reader, start=2):
            values = [None if row[column] == "" else row[column] for column in expected_columns]
            result.append((source_row_number, values))

    return result


def register_or_resume_load(
    conn: psycopg.Connection,
    batch_name: str,
    batch_fingerprint: str,
) -> tuple[int, bool]:
    """Return (load_id, already_succeeded).

    The control row is committed before the data transaction. If a data load
    fails, all source-file/raw-row inserts roll back and the control row is
    marked failed. A later run of the same fingerprint can then safely reuse
    the same load_id.
    """
    with conn.transaction():
        existing = conn.execute(
            """
            SELECT load_id, status
            FROM raw.ingestion_runs
            WHERE batch_fingerprint = %s
            FOR UPDATE
            """,
            (batch_fingerprint,),
        ).fetchone()

        if existing is None:
            load_id = conn.execute(
                """
                INSERT INTO raw.ingestion_runs (
                    batch_name, batch_fingerprint, status
                )
                VALUES (%s, %s, 'running')
                RETURNING load_id
                """,
                (batch_name, batch_fingerprint),
            ).fetchone()[0]
            return load_id, False

        load_id, status = existing

        if status == "succeeded":
            return load_id, True

        existing_files = conn.execute(
            "SELECT count(*) FROM raw.source_files WHERE load_id = %s",
            (load_id,),
        ).fetchone()[0]

        if existing_files != 0:
            raise RuntimeError(
                f"Load {load_id} is {status!r} but already has {existing_files} "
                "registered source file(s). Refusing an ambiguous resume."
            )

        conn.execute(
            """
            UPDATE raw.ingestion_runs
            SET batch_name = %s,
                started_at = now(),
                completed_at = NULL,
                status = 'running',
                notes = NULL
            WHERE load_id = %s
            """,
            (batch_name, load_id),
        )
        return load_id, False


def register_source_file(
    conn: psycopg.Connection,
    *,
    load_id: int,
    raw_dir: Path,
    path: Path,
    source_format: str,
    record_count: int | None,
) -> int:
    relative_path = path.relative_to(raw_dir).as_posix()
    return conn.execute(
        """
        INSERT INTO raw.source_files (
            load_id,
            source_path,
            source_name,
            source_format,
            sha256,
            byte_size,
            record_count
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING source_file_id
        """,
        (
            load_id,
            relative_path,
            path.name,
            source_format,
            file_sha256(path),
            path.stat().st_size,
            record_count,
        ),
    ).fetchone()[0]


def insert_csv_rows(
    conn: psycopg.Connection,
    *,
    spec: CsvSpec,
    source_file_id: int,
    rows: list[tuple[int, list[str | None]]],
) -> None:
    schema_name, table_name = spec.table.split(".", maxsplit=1)
    column_identifiers = [
        sql.Identifier("source_file_id"),
        sql.Identifier("source_row_number"),
        *[sql.Identifier(column) for column in spec.columns],
    ]
    placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in column_identifiers)

    query = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
        sql.Identifier(schema_name),
        sql.Identifier(table_name),
        sql.SQL(", ").join(column_identifiers),
        placeholders,
    )

    parameters = [
        (source_file_id, source_row_number, *values)
        for source_row_number, values in rows
    ]

    if parameters:
        with conn.cursor() as cur:
            cur.executemany(query, parameters)


def ingest_csv_files(
    conn: psycopg.Connection,
    *,
    load_id: int,
    raw_dir: Path,
) -> dict[str, int]:
    counts: dict[str, int] = {}

    for spec in CSV_SPECS:
        path = raw_dir / spec.filename
        rows = parse_csv(path, spec.columns)
        source_file_id = register_source_file(
            conn,
            load_id=load_id,
            raw_dir=raw_dir,
            path=path,
            source_format="csv",
            record_count=len(rows),
        )
        insert_csv_rows(
            conn,
            spec=spec,
            source_file_id=source_file_id,
            rows=rows,
        )
        counts[spec.filename] = len(rows)

    return counts


def ingest_json_files(
    conn: psycopg.Connection,
    *,
    load_id: int,
    raw_dir: Path,
    json_paths: Sequence[Path],
) -> tuple[int, int]:
    parsed_count = 0
    malformed_count = 0

    for path in json_paths:
        raw_bytes = path.read_bytes()
        raw_text = raw_bytes.decode("utf-8")

        try:
            payload = json.loads(raw_text)
            payload_json = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            parse_status = "parsed"
            parse_error = None
            parsed_count += 1
        except json.JSONDecodeError as exc:
            payload_json = None
            parse_status = "malformed"
            parse_error = f"{type(exc).__name__}: {exc}"
            malformed_count += 1

        source_file_id = register_source_file(
            conn,
            load_id=load_id,
            raw_dir=raw_dir,
            path=path,
            source_format="json",
            record_count=1,
        )

        conn.execute(
            """
            INSERT INTO raw.claim_detail_files (
                source_file_id,
                raw_text,
                payload_jsonb,
                parse_status,
                parse_error
            )
            VALUES (%s, %s, %s::jsonb, %s, %s)
            """,
            (
                source_file_id,
                raw_text,
                payload_json,
                parse_status,
                parse_error,
            ),
        )

    return parsed_count, malformed_count


def mark_failed(conn: psycopg.Connection, load_id: int, exc: BaseException) -> None:
    message = f"{type(exc).__name__}: {exc}"
    if len(message) > 4000:
        message = message[:4000] + "..."
    with conn.transaction():
        conn.execute(
            """
            UPDATE raw.ingestion_runs
            SET completed_at = now(),
                status = 'failed',
                notes = %s
            WHERE load_id = %s
            """,
            (message, load_id),
        )


def run_ingestion(database_url: str, raw_dir: Path, batch_name: str) -> int:
    raw_dir = raw_dir.resolve()
    csv_paths, json_paths = discover_input_files(raw_dir)
    input_files = [*csv_paths, *json_paths]
    fingerprint = compute_batch_fingerprint(raw_dir, input_files)

    with psycopg.connect(database_url) as conn:
        load_id, already_succeeded = register_or_resume_load(
            conn,
            batch_name=batch_name,
            batch_fingerprint=fingerprint,
        )

        if already_succeeded:
            print(
                f"Batch already ingested successfully: load_id={load_id}, "
                f"fingerprint={fingerprint}"
            )
            return load_id

        try:
            with conn.transaction():
                csv_counts = ingest_csv_files(
                    conn,
                    load_id=load_id,
                    raw_dir=raw_dir,
                )
                parsed_json, malformed_json = ingest_json_files(
                    conn,
                    load_id=load_id,
                    raw_dir=raw_dir,
                    json_paths=json_paths,
                )

                summary = {
                    "csv_rows": csv_counts,
                    "json_files": len(json_paths),
                    "json_parsed": parsed_json,
                    "json_malformed": malformed_json,
                }

                conn.execute(
                    """
                    UPDATE raw.ingestion_runs
                    SET completed_at = now(),
                        status = 'succeeded',
                        notes = %s
                    WHERE load_id = %s
                    """,
                    (json.dumps(summary, sort_keys=True), load_id),
                )
        except BaseException as exc:
            mark_failed(conn, load_id, exc)
            raise

    print(f"Raw ingestion succeeded: load_id={load_id}")
    print(f"Batch fingerprint: {fingerprint}")
    for filename, count in csv_counts.items():
        print(f"  {filename}: {count} row(s)")
    print(
        f"  JSON: {len(json_paths)} file(s), "
        f"{parsed_json} parsed, {malformed_json} malformed"
    )

    return load_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load the frozen source files into the PostgreSQL raw schema."
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("data/raw"),
        help="Directory containing the five CSVs and json/ subdirectory.",
    )
    parser.add_argument(
        "--batch-name",
        default=None,
        help="Human-readable batch name. Defaults to an UTC timestamp.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "DATABASE_URL is required. Set it to the PostgreSQL/Supabase "
            "connection string; do not commit credentials to Git.",
            file=sys.stderr,
        )
        return 2

    batch_name = args.batch_name or datetime.now(timezone.utc).strftime(
        "raw_%Y%m%dT%H%M%SZ"
    )

    try:
        run_ingestion(
            database_url=database_url,
            raw_dir=args.raw_dir,
            batch_name=batch_name,
        )
    except Exception as exc:
        print(f"Raw ingestion failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
