"""Apache Airflow 2.9+ TaskFlow API DAG for VietTech Retail BI & Analytics ELT.

Deploy directly into Airflow (`dags/`) or Google Cloud Composer while keeping
100% zero-dependency importability in local Python 3.12 CI.

Environment Variables:
    VIETTECH_ENGINE: ``local_sqlite`` (default) or ``bigquery``
    VIETTECH_PROJECT_ROOT: Optional path override to the repository root
    VIETTECH_DB_PATH: Optional SQLite warehouse DB path (for ``local_sqlite``)
    VIETTECH_RAW_DIR: Optional raw CSV directory path
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

try:
    from airflow.decorators import dag, task

    AIRFLOW_AVAILABLE = True
except ImportError:
    dag = None
    task = None
    AIRFLOW_AVAILABLE = False

LOGGER = logging.getLogger("viettech.airflow.alerts")

DAG_ID = "viettech_retail_elt_daily"
SCHEDULE = "0 2 * * *"
START_DATE = datetime(2024, 1, 1, tzinfo=timezone.utc)
DAG_TAGS = ["viettech", "retail-bi", "kimball-scd2", "pdpl"]

AIRFLOW_TASK_TOPOLOGY: dict[str, tuple[str, ...]] = {
    "validate_data_contracts": (),
    "load_staging_layer": ("validate_data_contracts",),
    "build_dimensions_scd2": ("load_staging_layer",),
    "load_facts_incremental": ("build_dimensions_scd2",),
    "build_rfm_snapshot": ("load_facts_incremental",),
    "run_dq_reconciliation": ("build_rfm_snapshot",),
    "run_prescriptive_mining": ("run_dq_reconciliation",),
}


def resolve_project_root() -> Path:
    """Resolve VietTech repository root from env override or relative DAG path."""
    env_root = os.environ.get("VIETTECH_PROJECT_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parents[2]


def get_execution_engine() -> str:
    """Return active warehouse engine ('local_sqlite' or 'bigquery')."""
    engine = os.environ.get("VIETTECH_ENGINE", "local_sqlite").strip().lower()
    if engine not in ("local_sqlite", "bigquery"):
        raise ValueError(
            f"Unsupported VIETTECH_ENGINE={engine!r}; expected 'local_sqlite' or 'bigquery'."
        )
    return engine


def on_task_failure_callback(context: dict[str, Any]) -> None:
    """Emit a structured JSON alert payload for Slack/PagerDuty/Cloud Logging ingestion."""
    ti = context.get("task_instance")
    dag_run = context.get("dag_run")
    exc = context.get("exception")
    payload = {
        "event": "airflow_task_failure",
        "dag_id": getattr(ti, "dag_id", context.get("dag_id", DAG_ID)),
        "task_id": getattr(ti, "task_id", context.get("task_id", "unknown")),
        "run_id": getattr(dag_run, "run_id", context.get("run_id", "unknown")),
        "logical_date": str(context.get("ds") or context.get("logical_date") or ""),
        "try_number": getattr(ti, "try_number", context.get("try_number", 0)),
        "engine": os.environ.get("VIETTECH_ENGINE", "local_sqlite"),
        "error": repr(exc) if exc is not None else "unknown",
    }
    LOGGER.error(json.dumps(payload, ensure_ascii=False))


DEFAULT_ARGS: dict[str, Any] = {
    "owner": "viettech-data-platform",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
    "execution_timeout": timedelta(minutes=30),
    "sla": timedelta(minutes=30),
    "on_failure_callback": on_task_failure_callback,
}


def _ensure_src_on_path(project_root: Path) -> None:
    src_dir = str(project_root / "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


def _run_subprocess(cmd: list[str], cwd: Path) -> dict[str, Any]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit={proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    return {"command": cmd, "returncode": proc.returncode}


def _execute_local_task(
    task_id: str,
    logical_date: str,
    run_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one canonical VietTech task against the local SQLite warehouse."""
    project_root = resolve_project_root()
    _ensure_src_on_path(project_root)

    from viettech.orchestration.models import TaskContext
    from viettech.orchestration.retail_dag import (
        DEFAULT_DB_PATH,
        DEFAULT_STATE_DB_PATH,
        build_retail_tasks,
    )

    raw_dir = Path(os.environ.get("VIETTECH_RAW_DIR", str(project_root / "data" / "raw")))
    db_path = Path(os.environ.get("VIETTECH_DB_PATH", str(DEFAULT_DB_PATH)))
    state_db_path = Path(
        os.environ.get("VIETTECH_STATE_DB_PATH", str(DEFAULT_STATE_DB_PATH))
    )
    tasks = {
        spec.task_id: spec
        for spec in build_retail_tasks(
            raw_dir=raw_dir,
            db_path=db_path,
            state_db_path=state_db_path,
            quiet=False,
        )
    }
    ctx = TaskContext(
        dag_id=DAG_ID,
        run_id=run_id,
        logical_date=logical_date,
        attempt=1,
        params=params or {},
        upstream_outputs={},
    )
    result = tasks[task_id].fn(ctx)
    return dict(result) if result is not None else {}


def _execute_bigquery_step(
    task_id: str,
    logical_date: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dispatch BigQuery cloud SQL/ETL runners for the matching DAG stage."""
    project_root = resolve_project_root()
    cfg = params or {}
    mode = str(cfg.get("mode", "incremental"))
    watermark = cfg.get("watermark")

    if task_id == "validate_data_contracts":
        return _run_subprocess(
            [
                sys.executable,
                "scripts/contract_validator.py",
                "--emit-lineage",
                "benchmarks/lineage_manifest.json",
            ],
            cwd=project_root,
        )
    if task_id == "load_staging_layer":
        return _run_subprocess(
            [sys.executable, "scripts/etl_csv_to_bq.py"],
            cwd=project_root,
        )
    if task_id in ("build_dimensions_scd2", "load_facts_incremental"):
        cmd = [
            sys.executable,
            "scripts/run_sql_pipeline.py",
            "--mode",
            mode,
            "--snapshot-date",
            logical_date,
            "--stage",
            task_id,
        ]
        if watermark:
            cmd.extend(["--watermark", str(watermark)])
        return _run_subprocess(cmd, cwd=project_root)
    if task_id in ("build_rfm_snapshot", "run_dq_reconciliation"):
        return _run_subprocess(
            [
                sys.executable,
                "scripts/run_sql_pipeline.py",
                "--mode",
                mode,
                "--snapshot-date",
                logical_date,
                "--stage",
                task_id,
            ],
            cwd=project_root,
        )
    if task_id == "run_prescriptive_mining":
        if bool(cfg.get("skip_mining", False)):
            return {"skipped_by_param": True, "engine": "bigquery"}
        return _run_subprocess(
            [
                sys.executable,
                "scripts/analytics_mining.py",
                "--snapshot-date",
                logical_date,
            ],
            cwd=project_root,
        )
    raise KeyError(f"Unknown VietTech DAG task_id: {task_id!r}")


def dispatch_task(
    task_id: str,
    *,
    logical_date: str,
    run_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dispatch a VietTech DAG task to the configured VIETTECH_ENGINE."""
    engine = get_execution_engine()
    if engine == "bigquery":
        return _execute_bigquery_step(task_id, logical_date, params=params)
    return _execute_local_task(task_id, logical_date, run_id=run_id, params=params)


if AIRFLOW_AVAILABLE and dag is not None and task is not None:

    @dag(
        dag_id=DAG_ID,
        description="VietTech Retail BI & Analytics ELT (Contracts -> Kimball SCD2 -> RFM -> DQ -> Mining)",
        schedule=SCHEDULE,
        start_date=START_DATE,
        catchup=False,
        max_active_runs=1,
        default_args=DEFAULT_ARGS,
        tags=DAG_TAGS,
        params={"mode": "full", "watermark": None, "skip_mining": False},
    )
    def viettech_retail_elt_daily() -> None:
        @task(task_id="validate_data_contracts", retries=1)
        def validate_data_contracts(**context: Any) -> dict[str, Any]:
            return dispatch_task(
                "validate_data_contracts",
                logical_date=str(context["ds"]),
                run_id=str(context["run_id"]),
                params=dict(context.get("params", {})),
            )

        @task(task_id="load_staging_layer", retries=2)
        def load_staging_layer(
            contract_result: dict[str, Any], **context: Any
        ) -> dict[str, Any]:
            _ = contract_result
            return dispatch_task(
                "load_staging_layer",
                logical_date=str(context["ds"]),
                run_id=str(context["run_id"]),
                params=dict(context.get("params", {})),
            )

        @task(task_id="build_dimensions_scd2", retries=1)
        def build_dimensions_scd2(
            staging_result: dict[str, Any], **context: Any
        ) -> dict[str, Any]:
            _ = staging_result
            return dispatch_task(
                "build_dimensions_scd2",
                logical_date=str(context["ds"]),
                run_id=str(context["run_id"]),
                params=dict(context.get("params", {})),
            )

        @task(task_id="load_facts_incremental", retries=2)
        def load_facts_incremental(
            dim_result: dict[str, Any], **context: Any
        ) -> dict[str, Any]:
            _ = dim_result
            return dispatch_task(
                "load_facts_incremental",
                logical_date=str(context["ds"]),
                run_id=str(context["run_id"]),
                params=dict(context.get("params", {})),
            )

        @task(task_id="build_rfm_snapshot", retries=1)
        def build_rfm_snapshot(
            fact_result: dict[str, Any], **context: Any
        ) -> dict[str, Any]:
            _ = fact_result
            return dispatch_task(
                "build_rfm_snapshot",
                logical_date=str(context["ds"]),
                run_id=str(context["run_id"]),
                params=dict(context.get("params", {})),
            )

        @task(task_id="run_dq_reconciliation", retries=0)
        def run_dq_reconciliation(
            rfm_result: dict[str, Any], **context: Any
        ) -> dict[str, Any]:
            _ = rfm_result
            return dispatch_task(
                "run_dq_reconciliation",
                logical_date=str(context["ds"]),
                run_id=str(context["run_id"]),
                params=dict(context.get("params", {})),
            )

        @task(task_id="run_prescriptive_mining", retries=1)
        def run_prescriptive_mining(
            dq_result: dict[str, Any], **context: Any
        ) -> dict[str, Any]:
            _ = dq_result
            return dispatch_task(
                "run_prescriptive_mining",
                logical_date=str(context["ds"]),
                run_id=str(context["run_id"]),
                params=dict(context.get("params", {})),
            )

        contracts = validate_data_contracts()
        staging = load_staging_layer(contracts)
        dims = build_dimensions_scd2(staging)
        facts = load_facts_incremental(dims)
        rfm = build_rfm_snapshot(facts)
        dq = run_dq_reconciliation(rfm)
        run_prescriptive_mining(dq)

    retail_elt_dag = viettech_retail_elt_daily()
