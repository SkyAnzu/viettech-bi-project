"""Pure-Python deterministic state-machine DAG orchestrator with retries, SLA, resume & backfill."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import heapq
import time
from types import MappingProxyType
from typing import Any
import uuid

from viettech.common.utils import utc_now_iso_z
from viettech.orchestration.models import (
    CycleDetectedError,
    DagRunResult,
    DagRunStatus,
    TaskContext,
    TaskRunResult,
    TaskSpec,
    TaskStatus,
    UnknownUpstreamError,
)
from viettech.orchestration.state_store import OrchestratorStateStore


def validate_dag(tasks: Sequence[TaskSpec]) -> list[TaskSpec]:
    """Validate task IDs/dependencies and return tasks in deterministic topological order."""
    if not tasks:
        raise ValueError("DAG must contain at least one TaskSpec.")

    task_map: dict[str, TaskSpec] = {}
    for spec in tasks:
        if spec.task_id in task_map:
            raise ValueError(f"Duplicate task_id detected in DAG: {spec.task_id!r}")
        task_map[spec.task_id] = spec

    in_degree: dict[str, int] = {tid: 0 for tid in task_map}
    dependents: dict[str, list[str]] = {tid: [] for tid in task_map}

    for spec in tasks:
        seen_upstreams: set[str] = set()
        for up_id in spec.upstream_ids:
            if up_id not in task_map:
                raise UnknownUpstreamError(
                    f"Task {spec.task_id!r} references unknown upstream task {up_id!r}."
                )
            if up_id in seen_upstreams:
                continue
            seen_upstreams.add(up_id)
            dependents[up_id].append(spec.task_id)
            in_degree[spec.task_id] += 1

    ready_heap: list[str] = [tid for tid, deg in in_degree.items() if deg == 0]
    heapq.heapify(ready_heap)

    ordered: list[TaskSpec] = []
    while ready_heap:
        current_id = heapq.heappop(ready_heap)
        ordered.append(task_map[current_id])
        for child_id in dependents[current_id]:
            in_degree[child_id] -= 1
            if in_degree[child_id] == 0:
                heapq.heappush(ready_heap, child_id)

    if len(ordered) != len(tasks):
        cyclic_nodes = sorted(tid for tid, deg in in_degree.items() if deg > 0)
        raise CycleDetectedError(
            f"Cycle detected in DAG involving task(s): {', '.join(cyclic_nodes)}"
        )

    return ordered


class DagOrchestrator:
    """Deterministic state-machine DAG runner backed by OrchestratorStateStore."""

    def __init__(
        self,
        dag_id: str,
        tasks: Sequence[TaskSpec],
        state_store: OrchestratorStateStore,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        on_failure_callback: Callable[[TaskContext, Exception], None] | None = None,
        clock_fn: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not dag_id or not dag_id.strip():
            raise ValueError("dag_id must be a non-empty string.")
        self.dag_id = dag_id
        self._ordered_tasks = validate_dag(tasks)
        self.state_store = state_store
        self._sleep_fn = sleep_fn
        self._on_failure_callback = on_failure_callback
        self._clock_fn = clock_fn

    @property
    def ordered_task_ids(self) -> tuple[str, ...]:
        """Return validated task IDs in deterministic topological execution order."""
        return tuple(task.task_id for task in self._ordered_tasks)

    def _record_short_circuit(
        self,
        *,
        run_id: str,
        task_id: str,
        failed_upstreams: list[str],
    ) -> TaskRunResult:
        now_iso = utc_now_iso_z()
        err_msg = f"Upstream task(s) failed: {', '.join(failed_upstreams)}"
        self.state_store.upsert_task_instance(
            run_id=run_id,
            task_id=task_id,
            status=TaskStatus.UPSTREAM_FAILED,
            attempts=0,
            duration_ms=0.0,
            sla_breached=False,
            started_at=now_iso,
            finished_at=now_iso,
            error_message=err_msg,
            output={},
        )
        return TaskRunResult(
            task_id=task_id,
            status=TaskStatus.UPSTREAM_FAILED,
            attempts=0,
            duration_ms=0.0,
            sla_breached=False,
            started_at=now_iso,
            finished_at=now_iso,
            error_message=err_msg,
            output={},
        )

    def _record_resumed_skip(
        self,
        *,
        run_id: str,
        task_id: str,
        resume_from_run_id: str,
        prior_output: dict[str, Any],
    ) -> TaskRunResult:
        now_iso = utc_now_iso_z()
        rehydrated = dict(prior_output)
        rehydrated["resumed_from"] = resume_from_run_id
        self.state_store.upsert_task_instance(
            run_id=run_id,
            task_id=task_id,
            status=TaskStatus.SKIPPED,
            attempts=0,
            duration_ms=0.0,
            sla_breached=False,
            started_at=now_iso,
            finished_at=now_iso,
            error_message=None,
            output=rehydrated,
        )
        return TaskRunResult(
            task_id=task_id,
            status=TaskStatus.SKIPPED,
            attempts=0,
            duration_ms=0.0,
            sla_breached=False,
            started_at=now_iso,
            finished_at=now_iso,
            error_message=None,
            output=rehydrated,
        )

    def _execute_task_with_retries(
        self,
        *,
        task: TaskSpec,
        run_id: str,
        logical_date: str,
        params: dict[str, Any],
        xcom_outputs: dict[str, dict[str, Any]],
    ) -> TaskRunResult:
        task_started_at = utc_now_iso_z()
        task_t0 = self._clock_fn()
        self.state_store.upsert_task_instance(
            run_id=run_id,
            task_id=task.task_id,
            status=TaskStatus.RUNNING,
            attempts=0,
            duration_ms=0.0,
            sla_breached=False,
            started_at=task_started_at,
            finished_at=None,
            error_message=None,
            output={},
        )

        max_attempts = 1 + task.retries
        upstream_snapshot = {
            uid: dict(xcom_outputs.get(uid, {})) for uid in task.upstream_ids
        }
        readonly_params = MappingProxyType(dict(params))
        readonly_upstreams = MappingProxyType(upstream_snapshot)

        last_error: str | None = None
        for attempt in range(1, max_attempts + 1):
            ctx = TaskContext(
                dag_id=self.dag_id,
                run_id=run_id,
                logical_date=logical_date,
                attempt=attempt,
                params=readonly_params,
                upstream_outputs=readonly_upstreams,
            )
            att_started_at = utc_now_iso_z()
            att_t0 = self._clock_fn()
            try:
                raw_output = task.fn(ctx)
            except Exception as exc:
                att_duration_ms = round((self._clock_fn() - att_t0) * 1000.0, 3)
                att_finished_at = utc_now_iso_z()
                last_error = f"{type(exc).__name__}: {exc}"
                self.state_store.record_task_attempt(
                    run_id=run_id,
                    task_id=task.task_id,
                    attempt_number=attempt,
                    status=TaskStatus.FAILED,
                    duration_ms=att_duration_ms,
                    started_at=att_started_at,
                    finished_at=att_finished_at,
                    error_message=last_error,
                )
                if attempt < max_attempts:
                    delay_sec = task.retry_delay_sec * (
                        task.retry_backoff_factor ** (attempt - 1)
                    )
                    if delay_sec > 0.0:
                        self._sleep_fn(delay_sec)
                    continue

                total_duration_ms = round((self._clock_fn() - task_t0) * 1000.0, 3)
                sla_breached = (
                    task.sla_seconds is not None
                    and (total_duration_ms / 1000.0) > task.sla_seconds
                )
                if self._on_failure_callback is not None:
                    self._on_failure_callback(ctx, exc)
                self.state_store.upsert_task_instance(
                    run_id=run_id,
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    attempts=attempt,
                    duration_ms=total_duration_ms,
                    sla_breached=sla_breached,
                    started_at=task_started_at,
                    finished_at=att_finished_at,
                    error_message=last_error,
                    output={},
                )
                return TaskRunResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    attempts=attempt,
                    duration_ms=total_duration_ms,
                    sla_breached=sla_breached,
                    started_at=task_started_at,
                    finished_at=att_finished_at,
                    error_message=last_error,
                    output={},
                )

            att_duration_ms = round((self._clock_fn() - att_t0) * 1000.0, 3)
            att_finished_at = utc_now_iso_z()
            out_dict = dict(raw_output) if raw_output is not None else {}
            self.state_store.record_task_attempt(
                run_id=run_id,
                task_id=task.task_id,
                attempt_number=attempt,
                status=TaskStatus.SUCCESS,
                duration_ms=att_duration_ms,
                started_at=att_started_at,
                finished_at=att_finished_at,
                error_message=None,
            )
            total_duration_ms = round((self._clock_fn() - task_t0) * 1000.0, 3)
            sla_breached = (
                task.sla_seconds is not None
                and (total_duration_ms / 1000.0) > task.sla_seconds
            )
            self.state_store.upsert_task_instance(
                run_id=run_id,
                task_id=task.task_id,
                status=TaskStatus.SUCCESS,
                attempts=attempt,
                duration_ms=total_duration_ms,
                sla_breached=sla_breached,
                started_at=task_started_at,
                finished_at=att_finished_at,
                error_message=None,
                output=out_dict,
            )
            return TaskRunResult(
                task_id=task.task_id,
                status=TaskStatus.SUCCESS,
                attempts=attempt,
                duration_ms=total_duration_ms,
                sla_breached=sla_breached,
                started_at=task_started_at,
                finished_at=att_finished_at,
                error_message=None,
                output=out_dict,
            )

        raise RuntimeError(f"Unreachable retry state for task {task.task_id!r}: {last_error}")

    def run(
        self,
        logical_date: str,
        *,
        run_id: str | None = None,
        resume_from_run_id: str | None = None,
        params: dict[str, Any] | None = None,
        trigger_mode: str = "manual",
    ) -> DagRunResult:
        """Execute a full or checkpoint-resumed DAG run for `logical_date`."""
        effective_run_id = (
            run_id or f"{self.dag_id}__{logical_date}__{uuid.uuid4().hex[:8]}"
        )
        effective_params: dict[str, Any] = dict(params) if params is not None else {}

        resumed_outputs: dict[str, dict[str, Any]] = {}
        if resume_from_run_id is not None:
            prior_run = self.state_store.get_dag_run(resume_from_run_id)
            if prior_run is None:
                raise ValueError(
                    f"Cannot resume: unknown prior run_id {resume_from_run_id!r}."
                )
            resumed_outputs = self.state_store.get_succeeded_tasks(resume_from_run_id)

        dag_started_at = utc_now_iso_z()
        dag_t0 = self._clock_fn()
        self.state_store.create_dag_run(
            run_id=effective_run_id,
            dag_id=self.dag_id,
            logical_date=logical_date,
            trigger_mode=trigger_mode,
            resumed_from_run_id=resume_from_run_id,
            params=effective_params,
            started_at=dag_started_at,
        )

        task_results: dict[str, TaskRunResult] = {}
        xcom_outputs: dict[str, dict[str, Any]] = {}

        for task in self._ordered_tasks:
            failed_upstreams = [
                uid
                for uid in task.upstream_ids
                if task_results[uid].status
                in (TaskStatus.FAILED, TaskStatus.UPSTREAM_FAILED)
            ]
            if failed_upstreams:
                task_results[task.task_id] = self._record_short_circuit(
                    run_id=effective_run_id,
                    task_id=task.task_id,
                    failed_upstreams=failed_upstreams,
                )
                continue

            if resume_from_run_id is not None and task.task_id in resumed_outputs:
                resumed_res = self._record_resumed_skip(
                    run_id=effective_run_id,
                    task_id=task.task_id,
                    resume_from_run_id=resume_from_run_id,
                    prior_output=resumed_outputs[task.task_id],
                )
                task_results[task.task_id] = resumed_res
                xcom_outputs[task.task_id] = dict(resumed_res.output)
                continue

            res = self._execute_task_with_retries(
                task=task,
                run_id=effective_run_id,
                logical_date=logical_date,
                params=effective_params,
                xcom_outputs=xcom_outputs,
            )
            task_results[task.task_id] = res
            if res.status == TaskStatus.SUCCESS:
                xcom_outputs[task.task_id] = dict(res.output)

        dag_duration_ms = round((self._clock_fn() - dag_t0) * 1000.0, 3)
        dag_finished_at = utc_now_iso_z()
        sla_breaches = sum(1 for res in task_results.values() if res.sla_breached)
        has_failures = any(
            res.status in (TaskStatus.FAILED, TaskStatus.UPSTREAM_FAILED)
            for res in task_results.values()
        )
        dag_status = DagRunStatus.FAILED if has_failures else DagRunStatus.SUCCESS

        self.state_store.finish_dag_run(
            run_id=effective_run_id,
            status=dag_status,
            duration_ms=dag_duration_ms,
            sla_breaches=sla_breaches,
            finished_at=dag_finished_at,
        )

        return DagRunResult(
            run_id=effective_run_id,
            dag_id=self.dag_id,
            logical_date=logical_date,
            status=dag_status,
            trigger_mode=trigger_mode,
            resumed_from_run_id=resume_from_run_id,
            started_at=dag_started_at,
            finished_at=dag_finished_at,
            duration_ms=dag_duration_ms,
            sla_breaches=sla_breaches,
            task_results=task_results,
        )

    def backfill(
        self,
        logical_dates: Sequence[str],
        *,
        params: dict[str, Any] | None = None,
        continue_on_error: bool = False,
    ) -> list[DagRunResult]:
        """Run chronological idempotent DAG executions across a sequence of logical dates."""
        results: list[DagRunResult] = []
        for logical_date in logical_dates:
            result = self.run(
                logical_date,
                params=params,
                trigger_mode="backfill",
            )
            results.append(result)
            if result.status == DagRunStatus.FAILED and not continue_on_error:
                break
        return results
