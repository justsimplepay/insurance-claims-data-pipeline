"""Minimal Streamlit wrapper for the frozen insurance-claims pipeline."""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tempfile
import threading
import traceback
import uuid
import zipfile
from pathlib import Path, PurePosixPath

import pandas as pd
import psycopg
import streamlit as st


REPO_ROOT = Path(__file__).resolve().parent
PIPELINE_RUNNER = REPO_ROOT / "pipeline" / "run_pipeline.py"

REQUIRED_CSVS = {
    "Customer.csv",
    "Policy.csv",
    "Claim.csv",
    "Claim_Payment.csv",
    "Policy_Premium.csv",
}
EXPECTED_OUTPUTS = (
    "fraud_detection.csv",
    "customer_retention.csv",
    "operational_efficiency.csv",
    "region_wise_insights.csv",
    "policy_optimization.csv",
    "data_quality_log.csv",
)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_FILES = 10_000
PIPELINE_TIMEOUT_SECONDS = 15 * 60
PIPELINE_SCHEMAS = ("marts", "core", "staging", "raw")

# The public demo intentionally serializes reset + pipeline execution.
# For a single Streamlit process this prevents overlapping jobs.
_PIPELINE_LOCK = threading.Lock()


class DemoInputError(ValueError):
    """Raised when the uploaded archive does not satisfy the demo contract."""


def _safe_member_path(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)

    if path.is_absolute() or ".." in path.parts:
        raise DemoInputError(f"Unsafe ZIP path: {name}")

    # Reject Windows drive-letter paths such as C:/file.csv.
    if path.parts and re.match(r"^[A-Za-z]:$", path.parts[0]):
        raise DemoInputError(f"Unsafe ZIP path: {name}")

    return path


def extract_and_validate_zip(uploaded_bytes: bytes, destination: Path) -> Path:
    """Safely extract one accepted input layout and return the pipeline raw directory."""
    if len(uploaded_bytes) > MAX_UPLOAD_BYTES:
        raise DemoInputError("ZIP exceeds the 25 MB demo upload limit.")

    try:
        archive = zipfile.ZipFile(io.BytesIO(uploaded_bytes))
    except zipfile.BadZipFile as exc:
        raise DemoInputError("The uploaded file is not a valid ZIP archive.") from exc

    with archive:
        members = archive.infolist()
        files = [m for m in members if not m.is_dir()]

        if not files:
            raise DemoInputError("The ZIP archive is empty.")
        if len(files) > MAX_ARCHIVE_FILES:
            raise DemoInputError("The ZIP contains too many files.")
        if sum(m.file_size for m in files) > MAX_EXTRACTED_BYTES:
            raise DemoInputError("The extracted ZIP would exceed the 100 MB demo limit.")

        safe_paths: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        seen: set[str] = set()

        for member in files:
            path = _safe_member_path(member.filename)

            # Unix symlink bits in ZIP external attributes.
            unix_mode = member.external_attr >> 16
            if (unix_mode & 0o170000) == 0o120000:
                raise DemoInputError("Symbolic links are not accepted in the ZIP.")

            key = path.as_posix()
            if key in seen:
                raise DemoInputError(f"Duplicate ZIP member: {key}")
            seen.add(key)
            safe_paths.append((member, path))

        # Accept either files at ZIP root or exactly one enclosing directory.
        first_parts = {p.parts[0] for _, p in safe_paths if p.parts}
        root_candidate = PurePosixPath()
        if not REQUIRED_CSVS.issubset({p.name for _, p in safe_paths if len(p.parts) == 1}):
            if len(first_parts) != 1:
                raise DemoInputError(
                    "Place the five required CSVs and json/ directory at the ZIP root "
                    "or inside one enclosing directory."
                )
            root_candidate = PurePosixPath(next(iter(first_parts)))

        relative_files: dict[str, tuple[zipfile.ZipInfo, PurePosixPath]] = {}
        for member, path in safe_paths:
            if root_candidate.parts:
                if not path.parts or path.parts[0] != root_candidate.parts[0]:
                    raise DemoInputError("ZIP layout is inconsistent.")
                relative = PurePosixPath(*path.parts[1:])
            else:
                relative = path

            if not relative.parts:
                continue
            relative_files[relative.as_posix()] = (member, relative)

        root_csvs = {p.name for _, p in relative_files.values() if len(p.parts) == 1}
        missing = REQUIRED_CSVS - root_csvs
        if missing:
            raise DemoInputError("Missing required file(s): " + ", ".join(sorted(missing)))

        json_files = [
            p for _, p in relative_files.values()
            if len(p.parts) >= 2 and p.parts[0] == "json" and p.suffix.lower() == ".json"
        ]
        if not json_files:
            raise DemoInputError("The json/ directory must contain at least one .json file.")

        destination.mkdir(parents=True, exist_ok=True)
        resolved_destination = destination.resolve()

        for member, relative in relative_files.values():
            target = destination.joinpath(*relative.parts)
            resolved_target = target.resolve()
            if resolved_destination != resolved_target and resolved_destination not in resolved_target.parents:
                raise DemoInputError(f"Unsafe ZIP path: {member.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as sink:
                while chunk := source.read(1024 * 1024):
                    sink.write(chunk)

    return destination


def reset_demo_database(database_url: str) -> None:
    """Reset only the fixed pipeline schemas in the dedicated demo database."""
    if os.getenv("DEMO_ALLOW_DB_RESET", "").lower() != "true":
        raise RuntimeError(
            "Demo database reset is disabled. Set DEMO_ALLOW_DB_RESET=true only for "
            "a dedicated demo database."
        )

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            for schema in PIPELINE_SCHEMAS:
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.commit()


def sanitize_log(text: str, database_url: str) -> str:
    if database_url:
        text = text.replace(database_url, "[DATABASE_URL REDACTED]")
    return text[-12_000:]


def run_frozen_pipeline(raw_dir: Path, output_dir: Path, database_url: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    job_id = f"demo_{uuid.uuid4().hex}"

    return subprocess.run(
        [
            sys.executable,
            str(PIPELINE_RUNNER),
            "--raw-dir",
            str(raw_dir),
            "--batch-name",
            job_id,
            "--output-dir",
            str(output_dir),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=PIPELINE_TIMEOUT_SECONDS,
        check=False,
    )


def package_results(output_dir: Path) -> tuple[bytes, dict[str, int]]:
    missing = [name for name in EXPECTED_OUTPUTS if not (output_dir / name).is_file()]
    if missing:
        raise RuntimeError("Pipeline completed without expected output(s): " + ", ".join(missing))

    counts: dict[str, int] = {}
    result = io.BytesIO()

    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in EXPECTED_OUTPUTS:
            path = output_dir / name
            counts[name] = len(pd.read_csv(path))
            archive.write(path, arcname=name)

    return result.getvalue(), counts


def main() -> None:
    st.set_page_config(page_title="Insurance Claims Pipeline Demo", page_icon="📊")
    st.title("Insurance Claims Data Pipeline Demo")
    st.write(
        "Upload one ZIP containing the five required CSV files and a json/ directory. "
        "The existing pipeline will run unchanged and return its six assessment outputs."
    )

    with st.expander("Required ZIP structure"):
        st.code(
            "Customer.csv\n"
            "Policy.csv\n"
            "Claim.csv\n"
            "Claim_Payment.csv\n"
            "Policy_Premium.csv\n"
            "json/\n"
            "  <claim files>.json"
        )

    uploaded = st.file_uploader("Input ZIP", type=["zip"])

    if "result_zip" not in st.session_state:
        st.session_state.result_zip = None
        st.session_state.row_counts = None

    if st.button("Run Pipeline", type="primary", disabled=uploaded is None):
        st.session_state.result_zip = None
        st.session_state.row_counts = None

        database_url = os.getenv("DATABASE_URL", "")
        if not database_url:
            st.error("The demo server is not configured with DATABASE_URL.")
            return

        if not _PIPELINE_LOCK.acquire(blocking=False):
            st.warning("Another pipeline job is currently running. Please try again shortly.")
            return

        try:
            with st.status("Running pipeline...", expanded=True) as status:
                with tempfile.TemporaryDirectory(prefix="claims-demo-") as tmp:
                    workspace = Path(tmp)
                    raw_dir = workspace / "raw"
                    output_dir = workspace / "output"

                    st.write("Validating uploaded ZIP...")
                    extract_and_validate_zip(uploaded.getvalue(), raw_dir)

                    st.write("Resetting dedicated demo database...")
                    reset_demo_database(database_url)

                    st.write("Running the existing pipeline...")
                    try:
                        completed = run_frozen_pipeline(raw_dir, output_dir, database_url)
                    except subprocess.TimeoutExpired as exc:
                        raise RuntimeError("Pipeline exceeded the 15-minute demo timeout.") from exc

                    if completed.returncode != 0:
                        details = sanitize_log(
                            (completed.stdout or "") + "\n" + (completed.stderr or ""),
                            database_url,
                        )
                        raise RuntimeError(
                            f"Pipeline failed with exit code {completed.returncode}.\n\n{details}"
                        )

                    st.write("Packaging results...")
                    result_zip, counts = package_results(output_dir)
                    st.session_state.result_zip = result_zip
                    st.session_state.row_counts = counts
                    status.update(label="Pipeline completed successfully.", state="complete")

        except (DemoInputError, RuntimeError) as exc:
            st.error(str(exc))
        except Exception as exc:
            # Keep credentials out of diagnostics while making deployment failures visible.
            details = sanitize_log(traceback.format_exc(), database_url)
            print(details, file=sys.stderr, flush=True)
            st.error(f"The demo job failed unexpectedly: {type(exc).__name__}: {exc}")
        finally:
            _PIPELINE_LOCK.release()

    if st.session_state.row_counts:
        st.subheader("Output row counts")
        st.dataframe(
            pd.DataFrame(
                [
                    {"Output": name, "Rows": count}
                    for name, count in st.session_state.row_counts.items()
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )

    if st.session_state.result_zip:
        st.download_button(
            "Download results.zip",
            data=st.session_state.result_zip,
            file_name="results.zip",
            mime="application/zip",
        )


if __name__ == "__main__":
    main()
