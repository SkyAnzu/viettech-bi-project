"""Load raw CSV snapshot files into BigQuery staging tables."""

import os
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv
from google.cloud import bigquery


TABLES = [
    "sales_channels",
    "product_categories",
    "products",
    "customers",
    "orders",
    "order_details",
    "order_returns",
    "product_price_history",
]


def load_config(root_dir: Path) -> Dict[str, str | None]:
    env_path = root_dir / "config" / ".env"
    load_dotenv(dotenv_path=env_path)
    return {
        "project_id": os.getenv("PROJECT_ID"),
        "bq_location": os.getenv("BQ_LOCATION", "asia-southeast1"),
        "dataset_stg": os.getenv("BQ_DATASET_STG", "retailbi_stg"),
        "raw_data_dir": os.getenv("RAW_DATA_DIR", "data/raw"),
        "csv_delimiter": os.getenv("CSV_DELIMITER", ","),
    }


def ensure_dataset(client: bigquery.Client, project_id: str, dataset_id: str, location: str) -> None:
    dataset_ref = f"{project_id}.{dataset_id}"
    dataset = bigquery.Dataset(dataset_ref)
    dataset.location = location
    client.create_dataset(dataset, exists_ok=True)


def load_table(
    client: bigquery.Client,
    project_id: str,
    dataset_id: str,
    table_name: str,
    csv_path: Path,
    delimiter: str,
) -> None:
    table_id = f"{project_id}.{dataset_id}.stg_{table_name}"
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        autodetect=True,
        field_delimiter=delimiter,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    with open(csv_path, "rb") as source_file:
        job = client.load_table_from_file(source_file, table_id, job_config=job_config)
    job.result()
    print(f"Loaded {csv_path.name} -> {table_id}")


def get_missing_files(raw_dir: Path, tables: List[str]) -> List[Path]:
    return [raw_dir / f"{table}.csv" for table in tables if not (raw_dir / f"{table}.csv").exists()]


def main() -> None:
    root_dir = Path(__file__).resolve().parents[1]
    config = load_config(root_dir)
    project_id = config["project_id"]
    if not project_id:
        raise ValueError("PROJECT_ID is required in .env")

    dataset_stg = config["dataset_stg"] or "retailbi_stg"
    bq_location = config["bq_location"] or "asia-southeast1"
    csv_delimiter = config["csv_delimiter"] or ","

    # Support relative RAW_DATA_DIR.
    raw_dir = Path(config["raw_data_dir"] or "data/raw")
    if not raw_dir.is_absolute():
        raw_dir = root_dir / raw_dir
    if not raw_dir.exists():
        raise FileNotFoundError(f"Raw data directory not found: {raw_dir}")

    missing_files = get_missing_files(raw_dir, TABLES)
    if missing_files:
        missing_text = "\n".join(str(path) for path in missing_files)
        raise FileNotFoundError(f"Missing required CSV files:\n{missing_text}")

    client = bigquery.Client(project=project_id)
    ensure_dataset(
        client,
        project_id,
        dataset_stg,
        bq_location,
    )

    for table_name in TABLES:
        csv_path = raw_dir / f"{table_name}.csv"
        load_table(
            client,
            project_id,
            dataset_stg,
            table_name,
            csv_path,
            csv_delimiter,
        )

    print("ETL load completed successfully.")


if __name__ == "__main__":
    main()
