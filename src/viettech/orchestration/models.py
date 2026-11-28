"""Core domain models, statuses, and exceptions for the VietTech DAG orchestrator."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    """Lifecycle status of an individual task instance within a DAG run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UPSTREAM_FAILED = "UPSTREAM_FAILED"
    SKIPPED = "SKIPPED"


class DagRunStatus(str, Enum):
    """Lifecycle status of an overall DAG run."""

    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class CycleDetectedError(ValueError):
    """Raised when a DAG definition contains one or more dependency cycles."""


class UnknownUpstreamError(ValueError):
    """Raised when a task declares an upstream dependency not present in the DAG."""


@dataclass(frozen=True)
class TaskContext:
    """Immutable execution context injected into each task callable."""

    dag_id: str
    run_id: str
    logical_date: str
    attempt: int
    params: Mapping[str, Any]
    upstream_outputs: Mapping[str, dict[str, Any]]


@dataclass(frozen=True)
class TaskSpec:
    """Declarative specification for a single DAG task node."""

    task_id: str
    fn: Callable[[TaskContext], dict[str, Any] | None]
    upstream_ids: tuple[str, ...] = ()
    retries: int = 0
    retry_delay_sec: float = 0.0
    retry_backoff_factor: float = 2.0
    sla_seconds: float | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not self.task_id or not self.task_id.strip():
            raise ValueError("TaskSpec.task_id must be a non-empty string.")
        if self.retries < 0:
            raise ValueError(f"TaskSpec.retries must be >= 0 (got {self.retries}).")
        if self.retry_delay_sec < 0.0:
            raise ValueError(
                f"TaskSpec.retry_delay_sec must be >= 0.0 (got {self.retry_delay_sec})."
            )
        if self.retry_backoff_factor < 1.0:
            raise ValueError(
                f"TaskSpec.retry_backoff_factor must be >= 1.0 (got {self.retry_backoff_factor})."
            )
        if self.sla_seconds is not None and self.sla_seconds < 0.0:
            raise ValueError(
                f"TaskSpec.sla_seconds must be >= 0.0 when set (got {self.sla_seconds})."
            )


@dataclass(frozen=True)
class TaskRunResult:
    """Final recorded outcome of a single task within a DAG run."""

    task_id: str
    status: TaskStatus
    attempts: int
    duration_ms: float
    sla_breached: bool
    started_at: str
    finished_at: str
    error_message: str | None = None
    output: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize task execution outcome into a JSON-compatible dictionary."""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "attempts": self.attempts,
            "duration_ms": self.duration_ms,
            "sla_breached": self.sla_breached,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_message": self.error_message,
            "output": dict(self.output),
        }


@dataclass(frozen=True)
class DagRunResult:
    """Final recorded outcome of an entire DAG run across all tasks."""

    run_id: str
    dag_id: str
    logical_date: str
    status: DagRunStatus
    trigger_mode: str
    resumed_from_run_id: str | None
    started_at: str
    finished_at: str
    duration_ms: float
    sla_breaches: int
    task_results: dict[str, TaskRunResult]

    def to_dict(self) -> dict[str, Any]:
        """Serialize full DAG run outcome into a JSON-compatible dictionary."""
        return {
            "run_id": self.run_id,
            "dag_id": self.dag_id,
            "logical_date": self.logical_date,
            "status": self.status.value,
            "trigger_mode": self.trigger_mode,
            "resumed_from_run_id": self.resumed_from_run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "sla_breaches": self.sla_breaches,
            "task_results": {
                task_id: res.to_dict() for task_id, res in self.task_results.items()
            },
        }
