"""Airflow-style SQLite metastore for DAG runs, task instances, and retry attempts."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import sqlite3
from typing import Any

from viettech.common.utils import utc_now_iso_z
from viettech.orchestration.models import DagRunStatus, TaskStatus

METASTORE_DDL = """
CREATE TABLE IF NOT EXISTS dag_runs (
    run_id TEXT PRIMARY KEY,
    dag_id TEXT NOT NULL,
    logical_date TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger_mode TEXT NOT NULL,
    resumed_from_run_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms REAL NOT NULL DEFAULT 0.0,
    sla_breaches INTEGER NOT NULL DEFAULT 0,
    params_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS task_instances (
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL NOT NULL DEFAULT 0.0,
    sla_breached INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    output_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    PRIMARY KEY (run_id, task_id),
    FOREIGN KEY (run_id) REFERENCES dag_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    duration_ms REAL NOT NULL DEFAULT 0.0,
    error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES dag_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_dag_runs_dag_started
    ON dag_runs(dag_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_task_instances_run_status
    ON task_instances(run_id, status);

CREATE INDEX IF NOT EXISTS idx_task_attempts_run_task
    ON task_attempts(run_id, task_id, attempt_number);
"""


def _parse_json_dict(raw: str | None) -> dict[str, Any]:
    """Decode a JSON object string into a dictionary safely."""
    if not raw:
        return {}
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


def _row_to_dag_run_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a dag_runs SQLite row into a normalized dictionary."""
    return {
        "run_id": str(row["run_id"]),
        "dag_id": str(row["dag_id"]),
        "logical_date": str(row["logical_date"]),
        "status": str(row["status"]),
        "trigger_mode": str(row["trigger_mode"]),
        "resumed_from_run_id": (
            str(row["resumed_from_run_id"])
            if row["resumed_from_run_id"] is not None
            else None
        ),
        "started_at": str(row["started_at"]),
        "finished_at": (
            str(row["finished_at"]) if row["finished_at"] is not None else None
        ),
        "duration_ms": float(row["duration_ms"]),
        "sla_breaches": int(row["sla_breaches"]),
        "params": _parse_json_dict(row["params_json"]),
    }


def _row_to_task_instance_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a task_instances SQLite row into a normalized dictionary."""
    return {
        "run_id": str(row["run_id"]),
        "task_id": str(row["task_id"]),
        "status": str(row["status"]),
        "attempts": int(row["attempts"]),
        "duration_ms": float(row["duration_ms"]),
        "sla_breached": bool(row["sla_breached"]),
        "error_message": (
            str(row["error_message"]) if row["error_message"] is not None else None
        ),
        "output": _parse_json_dict(row["output_json"]),
        "started_at": str(row["started_at"]),
        "finished_at": (
            str(row["finished_at"]) if row["finished_at"] is not None else None
        ),
    }


class OrchestratorStateStore:
    """SQLite-backed state store persisting DAG runs, task states, XCom outputs, and attempts."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.init_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def init_schema(self) -> None:
        """Create metastore tables and indexes idempotently."""
        conn = self._connect()
        try:
            conn.executescript(METASTORE_DDL)
        finally:
            conn.close()

    def create_dag_run(
        self,
        *,
        run_id: str,
        dag_id: str,
        logical_date: str,
        trigger_mode: str = "manual",
        resumed_from_run_id: str | None = None,
        params: Mapping[str, Any] | None = None,
        started_at: str | None = None,
    ) -> None:
        """Insert a new RUNNING DAG run record."""
        params_json = json.dumps(dict(params) if params is not None else {}, ensure_ascii=False)
        effective_started = started_at or utc_now_iso_z()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO dag_runs (
                        run_id, dag_id, logical_date, status, trigger_mode,
                        resumed_from_run_id, started_at, params_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        dag_id,
                        logical_date,
                        DagRunStatus.RUNNING.value,
                        trigger_mode,
                        resumed_from_run_id,
                        effective_started,
                        params_json,
                    ),
                )
        finally:
            conn.close()

    def finish_dag_run(
        self,
        *,
        run_id: str,
        status: DagRunStatus,
        duration_ms: float,
        sla_breaches: int,
        finished_at: str | None = None,
    ) -> None:
        """Mark a DAG run as completed (SUCCESS or FAILED) with timing and SLA metrics."""
        effective_finished = finished_at or utc_now_iso_z()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE dag_runs
                    SET status = ?, finished_at = ?, duration_ms = ?, sla_breaches = ?
                    WHERE run_id = ?
                    """,
                    (
                        status.value,
                        effective_finished,
                        round(duration_ms, 3),
                        int(sla_breaches),
                        run_id,
                    ),
                )
        finally:
            conn.close()

    def record_task_attempt(
        self,
        *,
        run_id: str,
        task_id: str,
        attempt_number: int,
        status: TaskStatus,
        duration_ms: float,
        started_at: str,
        finished_at: str,
        error_message: str | None = None,
    ) -> int:
        """Append a single task execution attempt audit row and return attempt_id."""
        conn = self._connect()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO task_attempts (
                        run_id, task_id, attempt_number, status, duration_ms,
                        error_message, started_at, finished_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        task_id,
                        int(attempt_number),
                        status.value,
                        round(duration_ms, 3),
                        error_message,
                        started_at,
                        finished_at,
                    ),
                )
                return int(cursor.lastrowid or 0)
        finally:
            conn.close()

    def upsert_task_instance(
        self,
        *,
        run_id: str,
        task_id: str,
        status: TaskStatus,
        attempts: int,
        duration_ms: float,
        sla_breached: bool,
        started_at: str,
        finished_at: str | None = None,
        error_message: str | None = None,
        output: Mapping[str, Any] | None = None,
    ) -> None:
        """Insert or update the current state and XCom output of a task instance."""
        output_json = json.dumps(dict(output) if output is not None else {}, ensure_ascii=False)
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO task_instances (
                        run_id, task_id, status, attempts, duration_ms, sla_breached,
                        error_message, output_json, started_at, finished_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, task_id) DO UPDATE SET
                        status = excluded.status,
                        attempts = excluded.attempts,
                        duration_ms = excluded.duration_ms,
                        sla_breached = excluded.sla_breached,
                        error_message = excluded.error_message,
                        output_json = excluded.output_json,
                        started_at = excluded.started_at,
                        finished_at = excluded.finished_at
                    """,
                    (
                        run_id,
                        task_id,
                        status.value,
                        int(attempts),
                        round(duration_ms, 3),
                        1 if sla_breached else 0,
                        error_message,
                        output_json,
                        started_at,
                        finished_at,
                    ),
                )
        finally:
            conn.close()

    def get_succeeded_tasks(self, run_id: str) -> dict[str, dict[str, Any]]:
        """Return `{task_id: parsed_output}` for tasks that succeeded (or resumed-skipped) in `run_id`."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT task_id, output_json
                FROM task_instances
                WHERE run_id = ? AND status IN (?, ?)
                ORDER BY task_id ASC
                """,
                (run_id, TaskStatus.SUCCESS.value, TaskStatus.SKIPPED.value),
            ).fetchall()
            return {
                str(row["task_id"]): _parse_json_dict(row["output_json"])
                for row in rows
            }
        finally:
            conn.close()

    def get_dag_run(self, run_id: str) -> dict[str, Any] | None:
        """Fetch a single DAG run metadata dictionary by `run_id`."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM dag_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return _row_to_dag_run_dict(row) if row is not None else None
        finally:
            conn.close()

    def list_dag_runs(
        self,
        dag_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List recent DAG runs in reverse chronological order."""
        conn = self._connect()
        try:
            if dag_id is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM dag_runs
                    WHERE dag_id = ?
                    ORDER BY started_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (dag_id, int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM dag_runs
                    ORDER BY started_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
            return [_row_to_dag_run_dict(row) for row in rows]
        finally:
            conn.close()

    def list_task_instances(self, run_id: str) -> list[dict[str, Any]]:
        """List all task instances recorded for a given DAG `run_id`."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM task_instances
                WHERE run_id = ?
                ORDER BY started_at ASC, rowid ASC
                """,
                (run_id,),
            ).fetchall()
            return [_row_to_task_instance_dict(row) for row in rows]
        finally:
            conn.close()

    def list_task_attempts(
        self,
        run_id: str,
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List recorded task retry attempts for a DAG run (optionally filtered by task_id)."""
        conn = self._connect()
        try:
            if task_id is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM task_attempts
                    WHERE run_id = ? AND task_id = ?
                    ORDER BY attempt_number ASC, attempt_id ASC
                    """,
                    (run_id, task_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM task_attempts
                    WHERE run_id = ?
                    ORDER BY attempt_id ASC
                    """,
                    (run_id,),
                ).fetchall()
            return [
                {
                    "attempt_id": int(row["attempt_id"]),
                    "run_id": str(row["run_id"]),
                    "task_id": str(row["task_id"]),
                    "attempt_number": int(row["attempt_number"]),
                    "status": str(row["status"]),
                    "duration_ms": float(row["duration_ms"]),
                    "error_message": (
                        str(row["error_message"])
                        if row["error_message"] is not None
                        else None
                    ),
                    "started_at": str(row["started_at"]),
                    "finished_at": str(row["finished_at"]),
                }
                for row in rows
            ]
        finally:
            conn.close()
