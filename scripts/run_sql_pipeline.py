"""Run the BigQuery SQL warehouse pipeline (01..06) in a deterministic order.

Features:
- Explicit ``{{TOKEN}}`` template rendering with zero-unrendered-token validation.
- Supports ``--dry-run`` completely offline with Python 3.10+ stdlib only
  (``google-cloud-bigquery`` is imported lazily only for live execution).
- Supports ``--mode full|incremental``, ``--watermark``, ``--snapshot-date``, and ``--skip-dq``.
- Structured logging and per-file timing summary.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
import logging
import os
from pathlib import Path
import re
import time
from typing import Dict, List, Sequence

LOGGER = logging.getLogger("viettech.run_sql_pipeline")

SQL_FILES: List[str] = [
    "01_create_staging_dataset.sql",
    "02_create_star_schema.sql",
    "03_load_star_schema.sql",
    "04_rfm_snapshot.sql",
    "05_bi_views.sql",
    "06_data_quality_checks.sql",
]

FULL_WATERMARK_DEFAULT = "1970-01-01 00:00:00"
INCREMENTAL_WATERMARK_DRY_RUN_DEFAULT = "2024-12-02 00:00:00"
DEFAULT_SNAPSHOT_DATE = "2025-01-01"
UNRENDERED_TOKEN_PATTERN = re.compile(r"\{\{[^}]+\}\}")


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
        "dataset_mart": os.getenv("BQ_DATASET_MART", "retailbi_mart"),
    }


def validate_timestamp_str(value: str) -> str:
    """Validate YYYY-MM-DD HH:MM:SS timestamp format."""
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise ValueError(
            f"Invalid --watermark '{value}'. Expected 'YYYY-MM-DD HH:MM:SS'."
        ) from exc
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def validate_date_str(value: str) -> str:
    """Validate YYYY-MM-DD date format."""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid --snapshot-date '{value}'. Expected 'YYYY-MM-DD'."
        ) from exc
    return parsed.isoformat()


def render_sql(sql_text: str, tokens: Dict[str, str]) -> str:
    """Replace all {{TOKEN}} placeholders and verify none remain unrendered."""
    rendered = sql_text
    for key, value in tokens.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)

    remaining = sorted(set(UNRENDERED_TOKEN_PATTERN.findall(rendered)))
    if remaining:
        raise ValueError(f"Unrendered SQL template token(s) detected: {', '.join(remaining)}")
    return rendered


def validate_sql_files(sql_dir: Path, sql_files: Sequence[str]) -> None:
    missing_files = [name for name in sql_files if not (sql_dir / name).exists()]
    if missing_files:
        missing_text = "\n".join(missing_files)
        raise FileNotFoundError(f"Missing SQL files:\n{missing_text}")


def resolve_watermark(
    mode: str,
    explicit_watermark: str | None,
    dry_run: bool,
    client: object | None,
    project_id: str,
    dataset_mart: str,
) -> str:
    """Determine the watermark timestamp for full or incremental runs."""
    if explicit_watermark:
        return validate_timestamp_str(explicit_watermark)
    if mode == "full":
        return FULL_WATERMARK_DEFAULT
    if dry_run or client is None:
        LOGGER.info(
            "Incremental --dry-run without --watermark; defaulting to %s.",
            INCREMENTAL_WATERMARK_DRY_RUN_DEFAULT,
        )
        return INCREMENTAL_WATERMARK_DRY_RUN_DEFAULT

    query = (
        f"SELECT FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S', MIN(last_watermark_ts)) AS wm "
        f"FROM `{project_id}.{dataset_mart}.etl_watermarks` "
        f"WHERE pipeline_name = 'viettech_star_schema'"
    )
    try:
        rows = list(client.query(query).result())  # type: ignore[attr-defined]
        if rows and rows[0]["wm"]:
            return validate_timestamp_str(str(rows[0]["wm"]))
    except Exception as exc:  # pragma: no cover - live BigQuery fallback
        LOGGER.warning("Could not query etl_watermarks (%s); falling back to full watermark.", exc)
    return FULL_WATERMARK_DEFAULT


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render and execute VietTech BigQuery SQL warehouse scripts (01..06).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render and validate all SQL templates offline without connecting to BigQuery.",
    )
    parser.add_argument(
        "--mode",
        choices=["full", "incremental"],
        default="full",
        help="Load mode: 'full' (watermark 1970-01-01) or 'incremental' (default: full).",
    )
    parser.add_argument(
        "--watermark",
        help="Explicit watermark timestamp 'YYYY-MM-DD HH:MM:SS' for incremental runs.",
    )
    parser.add_argument(
        "--snapshot-date",
        default=DEFAULT_SNAPSHOT_DATE,
        help=f"RFM snapshot date 'YYYY-MM-DD' (default: {DEFAULT_SNAPSHOT_DATE}).",
    )
    parser.add_argument(
        "--skip-dq",
        action="store_true",
        help="Skip executing 06_data_quality_checks.sql.",
    )
    parser.add_argument("--project-id", help="Override PROJECT_ID from environment/.env.")
    parser.add_argument("--dataset-stg", help="Override BQ_DATASET_STG from environment/.env.")
    parser.add_argument("--dataset-mart", help="Override BQ_DATASET_MART from environment/.env.")
    parser.add_argument("--location", help="Override BQ_LOCATION from environment/.env.")
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
    dataset_mart = args.dataset_mart or config["dataset_mart"] or "retailbi_mart"
    bq_location = args.location or config["bq_location"] or "asia-southeast1"
    snapshot_date = validate_date_str(args.snapshot_date)

    selected_files = [
        name for name in SQL_FILES if not (args.skip_dq and name == "06_data_quality_checks.sql")
    ]
    sql_dir = root_dir / "sql"
    validate_sql_files(sql_dir, selected_files)

    client = None
    if not args.dry_run:
        from google.cloud import bigquery  # type: ignore import-not-found

        client = bigquery.Client(project=project_id, location=bq_location)

    watermark_ts = resolve_watermark(
        mode=args.mode,
        explicit_watermark=args.watermark,
        dry_run=args.dry_run,
        client=client,
        project_id=project_id,
        dataset_mart=dataset_mart,
    )

    tokens = {
        "PROJECT_ID": project_id,
        "BQ_DATASET_STG": dataset_stg,
        "BQ_DATASET_MART": dataset_mart,
        "SNAPSHOT_DATE": snapshot_date,
        "WATERMARK_TS": watermark_ts,
    }

    LOGGER.info(
        "Starting SQL pipeline | mode=%s | dry_run=%s | project=%s | stg=%s | mart=%s | snapshot=%s | watermark=%s",
        args.mode,
        args.dry_run,
        project_id,
        dataset_stg,
        dataset_mart,
        snapshot_date,
        watermark_ts,
    )

    timings: List[tuple[str, int, float]] = []
    pipeline_start = time.perf_counter()

    for sql_file in selected_files:
        sql_path = sql_dir / sql_file
        t0 = time.perf_counter()
        raw_sql = sql_path.read_text(encoding="utf-8-sig")
        rendered_sql = render_sql(raw_sql, tokens)

        if args.dry_run:
            elapsed = time.perf_counter() - t0
            LOGGER.info(
                "[DRY-RUN] Validated %s (%d chars, 0 unrendered tokens) in %.3fs",
                sql_file,
                len(rendered_sql),
                elapsed,
            )
        else:
            client.query(rendered_sql).result()  # type: ignore[attr-defined]
            elapsed = time.perf_counter() - t0
            LOGGER.info("Executed %s in %.3fs", sql_file, elapsed)

        timings.append((sql_file, len(rendered_sql), elapsed))

    total_elapsed = time.perf_counter() - pipeline_start
    LOGGER.info("Timing summary:")
    for sql_file, char_count, elapsed in timings:
        LOGGER.info("  %-34s %7d chars  %7.3fs", sql_file, char_count, elapsed)
    LOGGER.info(
        "SQL pipeline %s completed successfully (%d scripts in %.3fs).",
        "dry-run" if args.dry_run else "execution",
        len(timings),
        total_elapsed,
    )


if __name__ == "__main__":
    main()
