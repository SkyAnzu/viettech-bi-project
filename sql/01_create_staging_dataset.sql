-- 01_create_staging_dataset.sql
-- Provision staging and mart datasets plus ETL operational metadata tables.
-- Rendered by scripts/run_sql_pipeline.py using explicit template placeholders.

CREATE SCHEMA IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_STG}}`;

CREATE SCHEMA IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_MART}}`;

CREATE TABLE IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.etl_watermarks` (
  pipeline_name STRING NOT NULL,
  table_name STRING NOT NULL,
  last_watermark_ts TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.etl_run_audit` (
  run_id STRING NOT NULL,
  mode STRING NOT NULL,
  started_at TIMESTAMP NOT NULL,
  finished_at TIMESTAMP,
  status STRING NOT NULL,
  rows_processed INT64,
  notes STRING
);
