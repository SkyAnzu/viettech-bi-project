"""Load raw CSV snapshot files into BigQuery staging tables.

Supports offline ``--dry-run`` validation with Python 3.10+ stdlib only
(``google-cloud-bigquery`` is imported lazily only when executing live loads)
and safely strips UTF-8 BOM headers before uploading to BigQuery.
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Sequence

LOGGER = logging.getLogger("viettech.etl_csv_to_bq")

TABLES: List[str] = [
    "sales_channels",
    "product_categories",
    "products",
    "customers",
    "orders",
    "order_details",
    "order_returns",
    "product_price_history",
]

UTF8_BOM = b"\xef\xbb\xbf"


def _load_dotenv_safe(env_path: Path) -> None:
    """Load key=value pairs from .env using python-dotenv if available, else stdlib."""
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore import-not-found

        load_dotenv(dotenv_path=env_path)
        return
    except ImportError:
        pass

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_config(root_dir: Path) -> Dict[str, str | None]:
    env_path = root_dir / "config" / ".env"
    _load_dotenv_safe(env_path)
    return {
        "project_id": os.getenv("PROJECT_ID"),
        "bq_location": os.getenv("BQ_LOCATION", "asia-southeast1"),
        "dataset_stg": os.getenv("BQ_DATASET_STG", "retailbi_stg"),
        "raw_data_dir": os.getenv("RAW_DATA_DIR", "data/raw"),
        "csv_delimiter": os.getenv("CSV_DELIMITER", ","),
    }


def strip_utf8_bom_bytes(raw_bytes: bytes) -> bytes:
    """Remove leading UTF-8 BOM bytes so BigQuery never sees '\\ufeffid'."""
    if raw_bytes.startswith(UTF8_BOM):
        return raw_bytes[len(UTF8_BOM) :]
    return raw_bytes


def inspect_csv(csv_path: Path, delimiter: str) -> tuple[List[str], int, bool]:
    """Validate CSV readability, BOM presence, header columns, and row count."""
    raw_bytes = csv_path.read_bytes()
    had_bom = raw_bytes.startswith(UTF8_BOM)
    text_stream = io.StringIO(strip_utf8_bom_bytes(raw_bytes).decode("utf-8"))
    reader = csv.reader(text_stream, delimiter=delimiter)
    header = next(reader, [])
    if not header:
        raise ValueError(f"CSV file is empty or missing header: {csv_path}")
    row_count = sum(1 for _ in reader)
    return header, row_count, had_bom


def get_missing_files(raw_dir: Path, tables: Sequence[str]) -> List[Path]:
    return [raw_dir / f"{table}.csv" for table in tables if not (raw_dir / f"{table}.csv").exists()]


def ensure_dataset(client: object, project_id: str, dataset_id: str, location: str) -> None:
    from google.cloud import bigquery  # type: ignore import-not-found

    dataset_ref = f"{project_id}.{dataset_id}"
    dataset = bigquery.Dataset(dataset_ref)
    dataset.location = location
    client.create_dataset(dataset, exists_ok=True)  # type: ignore[attr-defined]


def load_table(
    client: object,
    project_id: str,
    dataset_id: str,
    table_name: str,
    csv_path: Path,
    delimiter: str,
) -> int:
    from google.cloud import bigquery  # type: ignore import-not-found

    table_id = f"{project_id}.{dataset_id}.stg_{table_name}"
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        autodetect=True,
        field_delimiter=delimiter,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    clean_bytes = strip_utf8_bom_bytes(csv_path.read_bytes())
    buffer = io.BytesIO(clean_bytes)
    job = client.load_table_from_file(buffer, table_id, job_config=job_config)  # type: ignore[attr-defined]
    job.result()
    output_rows = int(getattr(job, "output_rows", 0) or 0)
    LOGGER.info("Loaded %s -> %s (%d rows)", csv_path.name, table_id, output_rows)
    return output_rows


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load raw VietTech CSV snapshots into BigQuery staging tables.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate CSV files, strip BOM check, and inspect row counts offline without BigQuery.",
    )
    parser.add_argument("--project-id", help="Override PROJECT_ID from environment/.env.")
    parser.add_argument("--dataset-stg", help="Override BQ_DATASET_STG from environment/.env.")
    parser.add_argument("--location", help="Override BQ_LOCATION from environment/.env.")
    parser.add_argument("--raw-dir", help="Override RAW_DATA_DIR from environment/.env.")
    parser.add_argument("--delimiter", help="Override CSV_DELIMITER (default: ',').")
    parser.add_argument(
        "--tables",
        nargs="+",
        choices=TABLES,
        default=TABLES,
        help="Optional subset of tables to process (default: all 8 tables).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )

    root_dir = Path(__file__).resolve().parents[1]
    config = load_config(root_dir)

    project_id = args.project_id or config["project_id"]
    if not project_id:
        if args.dry_run:
            project_id = "dry-run-project"
            LOGGER.info("PROJECT_ID not set; using placeholder '%s' for --dry-run.", project_id)
        else:
            raise ValueError("PROJECT_ID is required in config/.env or via --project-id")

    dataset_stg = args.dataset_stg or config["dataset_stg"] or "retailbi_stg"
    bq_location = args.location or config["bq_location"] or "asia-southeast1"
    csv_delimiter = args.delimiter or config["csv_delimiter"] or ","

    raw_dir = Path(args.raw_dir or config["raw_data_dir"] or "data/raw")
    if not raw_dir.is_absolute():
        raw_dir = root_dir / raw_dir
    if not raw_dir.exists():
        raise FileNotFoundError(f"Raw data directory not found: {raw_dir}")

    missing_files = get_missing_files(raw_dir, args.tables)
    if missing_files:
        missing_text = "\n".join(str(path) for path in missing_files)
        raise FileNotFoundError(f"Missing required CSV files:\n{missing_text}")

    client = None
    if not args.dry_run:
        from google.cloud import bigquery  # type: ignore import-not-found

        client = bigquery.Client(project=project_id, location=bq_location)
        ensure_dataset(client, project_id, dataset_stg, bq_location)

    timings: List[tuple[str, int, float]] = []
    pipeline_start = time.perf_counter()

    for table_name in args.tables:
        csv_path = raw_dir / f"{table_name}.csv"
        t0 = time.perf_counter()
        if args.dry_run:
            header, row_count, had_bom = inspect_csv(csv_path, csv_delimiter)
            elapsed = time.perf_counter() - t0
            LOGGER.info(
                "[DRY-RUN] %s -> %s.%s.stg_%s | cols=%d | rows=%d | bom_stripped=%s | %.3fs",
                csv_path.name,
                project_id,
                dataset_stg,
                table_name,
                len(header),
                row_count,
                had_bom,
                elapsed,
            )
            timings.append((table_name, row_count, elapsed))
        else:
            row_count = load_table(
                client,
                project_id,
                dataset_stg,
                table_name,
                csv_path,
                csv_delimiter,
            )
            elapsed = time.perf_counter() - t0
            timings.append((table_name, row_count, elapsed))

    total_elapsed = time.perf_counter() - pipeline_start
    total_rows = sum(rows for _, rows, _ in timings)
    LOGGER.info(
        "ETL CSV load %s complete: %d tables, %d total rows in %.3fs.",
        "dry-run" if args.dry_run else "execution",
        len(timings),
        total_rows,
        total_elapsed,
    )


if __name__ == "__main__":
    main()
