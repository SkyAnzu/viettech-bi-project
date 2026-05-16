"""Run the SQL pipeline in a fixed order against BigQuery."""

import os
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv
from google.cloud import bigquery


SQL_FILES = [
    "01_create_staging_dataset.sql",
    "02_create_star_schema.sql",
    "03_load_star_schema.sql",
    "04_rfm_snapshot.sql",
    "05_bi_views.sql",
]


def load_config(root_dir: Path) -> Dict[str, str | None]:
    env_path = root_dir / "config" / ".env"
    load_dotenv(dotenv_path=env_path)
    return {
        "project_id": os.getenv("PROJECT_ID"),
        "bq_location": os.getenv("BQ_LOCATION", "asia-southeast1"),
        "dataset_stg": os.getenv("BQ_DATASET_STG", "retailbi_stg"),
        "dataset_mart": os.getenv("BQ_DATASET_MART", "retailbi_mart"),
    }


def render_sql(sql_text: str, project_id: str, dataset_stg: str, dataset_mart: str) -> str:
    """Replace project and dataset placeholders before sending the query."""
    return (
        sql_text.replace("your-project-id", project_id)
        .replace("retailbi_stg", dataset_stg)
        .replace("retailbi_mart", dataset_mart)
    )


def run_sql_file(
    client: bigquery.Client,
    sql_file_path: Path,
    project_id: str,
    dataset_stg: str,
    dataset_mart: str,
) -> None:
    sql_text = sql_file_path.read_text(encoding="utf-8")
    query = render_sql(sql_text, project_id, dataset_stg, dataset_mart)
    client.query(query).result()
    print(f"Executed: {sql_file_path.name}")


def validate_sql_files(sql_dir: Path, sql_files: List[str]) -> None:
    missing_files = [name for name in sql_files if not (sql_dir / name).exists()]
    if missing_files:
        missing_text = "\n".join(missing_files)
        raise FileNotFoundError(f"Missing SQL files:\n{missing_text}")


def main() -> None:
    root_dir = Path(__file__).resolve().parents[1]
    config = load_config(root_dir)
    project_id = config["project_id"]
    if not project_id:
        raise ValueError("PROJECT_ID is required in .env")

    dataset_stg = config["dataset_stg"] or "retailbi_stg"
    dataset_mart = config["dataset_mart"] or "retailbi_mart"
    bq_location = config["bq_location"] or "asia-southeast1"

    sql_dir = root_dir / "sql"
    validate_sql_files(sql_dir, SQL_FILES)

    client = bigquery.Client(project=project_id, location=bq_location)
    for sql_file in SQL_FILES:
        run_sql_file(
            client,
            sql_dir / sql_file,
            project_id,
            dataset_stg,
            dataset_mart,
        )

    print("SQL pipeline completed successfully.")


if __name__ == "__main__":
    main()
