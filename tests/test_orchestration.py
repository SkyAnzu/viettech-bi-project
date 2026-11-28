"""White-Box & Gray-Box test suite for VietTech Enterprise Data Orchestration Layer.

Covers:
- White-Box (<50ms, zero warehouse DB, injectable sleep/clock):
  * Cycle detection (CycleDetectedError) & unknown upstream (UnknownUpstreamError)
  * Deterministic Kahn topological ordering on diamond DAG + XCom output passing
  * Transient error + exponential backoff retries + task_attempts audit log
  * Exhausted retries -> on_failure_callback + UPSTREAM_FAILED short-circuit + independent branch isolation
  * Checkpoint resume skipping succeeded tasks (call_count == 0) while rehydrating XCom outputs
  * SLA breach detection in both DagRunResult and SQLite metastore
  * 1-to-1 parity between Apache Airflow 2.9+ DAG topology and RETAIL_DAG_TOPOLOGY
- Gray-Box End-to-End Integration (isolated TemporaryDirectory):
  * Full 7-task VietTech Retail ELT DAG (0.0000 VND reconciliation + all 3 mining marts)
  * Pre-ingestion contract failure short-circuit (zero staging/warehouse DB mutation)
  * Multi-date idempotent historical backfill across 3 logical dates
  * Thin CLI runner (`scripts/run_orchestrator.py`) execution & `--status` inspection
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
RAW_DIR = PROJECT_ROOT / "data" / "raw"
AIRFLOW_DAG_PATH = PROJECT_ROOT / "orchestration" / "dags" / "viettech_retail_elt_dag.py"

for path_item in (SRC_DIR, SCRIPTS_DIR):
    if str(path_item) not in sys.path:
        sys.path.insert(0, str(path_item))

import run_orchestrator as orchestrator_cli  # noqa: E402
from viettech.orchestration import (  # noqa: E402
    CycleDetectedError,
    DagOrchestrator,
    DagRunStatus,
    OrchestratorStateStore,
    RETAIL_DAG_ID,
    RETAIL_DAG_TOPOLOGY,
    TaskContext,
    TaskSpec,
    TaskStatus,
    UnknownUpstreamError,
    build_retail_orchestrator,
    run_retail_dag,
    validate_dag,
)


class TestOrchestratorWhiteBox(unittest.TestCase):
    """Fast deterministic White-Box unit tests for DAG engine, state store, and Airflow parity."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="viettech_orch_wb_")
        self.tmp_dir = Path(self._tmp.name)
        self.store = OrchestratorStateStore(self.tmp_dir / "state.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_cycle_and_unknown_upstream_validation(self) -> None:
        def noop(_ctx: TaskContext) -> None:
            return None

        # 3-node cycle: A -> B -> C -> A
        cyclic_tasks = [
            TaskSpec(task_id="a", fn=noop, upstream_ids=("c",)),
            TaskSpec(task_id="b", fn=noop, upstream_ids=("a",)),
            TaskSpec(task_id="c", fn=noop, upstream_ids=("b",)),
        ]
        with self.assertRaises(CycleDetectedError):
            validate_dag(cyclic_tasks)

        # Self-cycle: X -> X
        with self.assertRaises(CycleDetectedError):
            validate_dag([TaskSpec(task_id="x", fn=noop, upstream_ids=("x",))])

        # Unknown upstream dependency
        with self.assertRaises(UnknownUpstreamError):
            validate_dag([TaskSpec(task_id="child", fn=noop, upstream_ids=("ghost_task",))])

        # Duplicate task_id
        with self.assertRaises(ValueError):
            validate_dag([TaskSpec(task_id="dup", fn=noop), TaskSpec(task_id="dup", fn=noop)])

    def test_deterministic_topological_order_diamond_dag(self) -> None:
        execution_order: list[str] = []
        captured_d_inputs: dict[str, dict[str, Any]] = {}

        def make_fn(name: str) -> Any:
            def _fn(ctx: TaskContext) -> dict[str, Any]:
                execution_order.append(name)
                if name == "D":
                    captured_d_inputs.update(ctx.upstream_outputs)
                return {"produced_by": name, "logical_date": ctx.logical_date}

            return _fn

        # Intentionally scrambled input order to prove deterministic tie-breaking (B before C)
        tasks = [
            TaskSpec(task_id="D", fn=make_fn("D"), upstream_ids=("C", "B")),
            TaskSpec(task_id="C", fn=make_fn("C"), upstream_ids=("A",)),
            TaskSpec(task_id="B", fn=make_fn("B"), upstream_ids=("A",)),
            TaskSpec(task_id="A", fn=make_fn("A"), upstream_ids=()),
        ]
        orch = DagOrchestrator("diamond_dag", tasks, self.store, sleep_fn=lambda _s: None)
        self.assertEqual(orch.ordered_task_ids, ("A", "B", "C", "D"))

        result = orch.run("2025-01-01")
        self.assertEqual(result.status, DagRunStatus.SUCCESS)
        self.assertEqual(execution_order, ["A", "B", "C", "D"])
        self.assertEqual(captured_d_inputs["B"]["produced_by"], "B")
        self.assertEqual(captured_d_inputs["C"]["produced_by"], "C")

    def test_transient_error_exponential_backoff_retry_succeeds(self) -> None:
        delays: list[float] = []
        attempts_seen: list[int] = []

        def flaky_task(ctx: TaskContext) -> dict[str, Any]:
            attempts_seen.append(ctx.attempt)
            if ctx.attempt < 3:
                raise ConnectionError(f"transient failure on attempt {ctx.attempt}")
            return {"recovered_on_attempt": ctx.attempt}

        tasks = [
            TaskSpec(
                task_id="flaky_extract",
                fn=flaky_task,
                retries=2,
                retry_delay_sec=0.5,
                retry_backoff_factor=2.0,
            )
        ]
        orch = DagOrchestrator(
            "retry_dag",
            tasks,
            self.store,
            sleep_fn=delays.append,
        )
        result = orch.run("2025-01-01")

        self.assertEqual(result.status, DagRunStatus.SUCCESS)
        self.assertEqual(attempts_seen, [1, 2, 3])
        self.assertEqual(delays, [0.5, 1.0])

        tr = result.task_results["flaky_extract"]
        self.assertEqual(tr.status, TaskStatus.SUCCESS)
        self.assertEqual(tr.attempts, 3)
        self.assertEqual(tr.output["recovered_on_attempt"], 3)

        attempts = self.store.list_task_attempts(result.run_id, "flaky_extract")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(
            [a["status"] for a in attempts],
            ["FAILED", "FAILED", "SUCCESS"],
        )
        self.assertEqual([a["attempt_number"] for a in attempts], [1, 2, 3])

    def test_exhausted_retries_callback_short_circuit_and_independent_branch(self) -> None:
        callback_events: list[tuple[str, int, str]] = []
        called_tasks: list[str] = []

        def on_fail(ctx: TaskContext, exc: Exception) -> None:
            callback_events.append((ctx.run_id, ctx.attempt, f"{type(exc).__name__}: {exc}"))

        def task_a(_ctx: TaskContext) -> dict[str, Any]:
            called_tasks.append("A")
            return {"ok": True}

        def task_b_fail(_ctx: TaskContext) -> dict[str, Any]:
            called_tasks.append("B")
            raise RuntimeError("hard database crash")

        def task_c_downstream(_ctx: TaskContext) -> dict[str, Any]:
            called_tasks.append("C")
            return {"should_never_run": True}

        def task_independent(_ctx: TaskContext) -> dict[str, Any]:
            called_tasks.append("E_independent")
            return {"independent": "completed"}

        tasks = [
            TaskSpec(task_id="A", fn=task_a),
            TaskSpec(task_id="B", fn=task_b_fail, upstream_ids=("A",), retries=1, retry_delay_sec=0.1),
            TaskSpec(task_id="C", fn=task_c_downstream, upstream_ids=("B",)),
            TaskSpec(task_id="E_independent", fn=task_independent),
        ]
        orch = DagOrchestrator(
            "failure_isolation_dag",
            tasks,
            self.store,
            sleep_fn=lambda _s: None,
            on_failure_callback=on_fail,
        )
        result = orch.run("2025-01-01")

        self.assertEqual(result.status, DagRunStatus.FAILED)
        self.assertEqual(result.task_results["A"].status, TaskStatus.SUCCESS)
        self.assertEqual(result.task_results["B"].status, TaskStatus.FAILED)
        self.assertEqual(result.task_results["B"].attempts, 2)
        self.assertEqual(result.task_results["C"].status, TaskStatus.UPSTREAM_FAILED)
        self.assertEqual(result.task_results["C"].attempts, 0)
        self.assertEqual(result.task_results["E_independent"].status, TaskStatus.SUCCESS)

        self.assertNotIn("C", called_tasks)
        self.assertIn("E_independent", called_tasks)
        self.assertEqual(len(callback_events), 1)
        self.assertEqual(callback_events[0][0], result.run_id)
        self.assertEqual(callback_events[0][1], 2)
        self.assertIn("hard database crash", callback_events[0][2])

    def test_checkpoint_resume_skips_succeeded_tasks_and_rehydrates_xcom(self) -> None:
        call_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
        fail_c = True
        c_received_upstreams: dict[str, dict[str, Any]] = {}

        def fn_a(_ctx: TaskContext) -> dict[str, Any]:
            call_counts["A"] += 1
            return {"token_a": "alpha_42"}

        def fn_b(ctx: TaskContext) -> dict[str, Any]:
            call_counts["B"] += 1
            return {"token_b": f"beta_from_{ctx.upstream_outputs['A']['token_a']}"}

        def fn_c(ctx: TaskContext) -> dict[str, Any]:
            call_counts["C"] += 1
            c_received_upstreams.clear()
            c_received_upstreams.update(ctx.upstream_outputs)
            if fail_c:
                raise RuntimeError("transient warehouse outage at C")
            return {
                "combined": f"{ctx.upstream_outputs['A']['token_a']}+{ctx.upstream_outputs['B']['token_b']}"
            }

        def fn_d(ctx: TaskContext) -> dict[str, Any]:
            call_counts["D"] += 1
            return {"final": ctx.upstream_outputs["C"]["combined"]}

        tasks = [
            TaskSpec(task_id="A", fn=fn_a),
            TaskSpec(task_id="B", fn=fn_b, upstream_ids=("A",)),
            TaskSpec(task_id="C", fn=fn_c, upstream_ids=("A", "B")),
            TaskSpec(task_id="D", fn=fn_d, upstream_ids=("C",)),
        ]
        orch = DagOrchestrator("resume_dag", tasks, self.store, sleep_fn=lambda _s: None)

        # Run 1: A & B succeed, C fails, D is UPSTREAM_FAILED
        run1 = orch.run("2025-01-01")
        self.assertEqual(run1.status, DagRunStatus.FAILED)
        self.assertEqual(run1.task_results["A"].status, TaskStatus.SUCCESS)
        self.assertEqual(run1.task_results["B"].status, TaskStatus.SUCCESS)
        self.assertEqual(run1.task_results["C"].status, TaskStatus.FAILED)
        self.assertEqual(run1.task_results["D"].status, TaskStatus.UPSTREAM_FAILED)
        self.assertEqual(call_counts, {"A": 1, "B": 1, "C": 1, "D": 0})

        # Reset counters & resolve C's failure before resuming
        call_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
        fail_c = False

        run2 = orch.run(
            "2025-01-01",
            resume_from_run_id=run1.run_id,
            trigger_mode="resume",
        )
        self.assertEqual(run2.status, DagRunStatus.SUCCESS)
        self.assertEqual(run2.resumed_from_run_id, run1.run_id)

        # A & B MUST NOT re-execute on Run 2
        self.assertEqual(call_counts["A"], 0)
        self.assertEqual(call_counts["B"], 0)
        self.assertEqual(call_counts["C"], 1)
        self.assertEqual(call_counts["D"], 1)

        self.assertEqual(run2.task_results["A"].status, TaskStatus.SKIPPED)
        self.assertEqual(run2.task_results["B"].status, TaskStatus.SKIPPED)
        self.assertEqual(run2.task_results["A"].output["resumed_from"], run1.run_id)
        self.assertEqual(run2.task_results["B"].output["resumed_from"], run1.run_id)
        self.assertEqual(c_received_upstreams["A"]["token_a"], "alpha_42")
        self.assertEqual(c_received_upstreams["B"]["token_b"], "beta_from_alpha_42")
        self.assertEqual(
            run2.task_results["D"].output["final"],
            "alpha_42+beta_from_alpha_42",
        )

    def test_sla_breach_detection_in_result_and_metastore(self) -> None:
        # Deterministic virtual clock: advances by 0.25s on each call so task exceeds 0.1s SLA instantaneously
        ticks = iter([10.0, 10.0, 10.0, 10.25, 10.25, 10.25])

        def virtual_clock() -> float:
            return next(ticks)

        tasks = [
            TaskSpec(
                task_id="slow_task",
                fn=lambda _ctx: {"done": True},
                sla_seconds=0.1,
            )
        ]
        orch = DagOrchestrator(
            "sla_dag",
            tasks,
            self.store,
            sleep_fn=lambda _s: None,
            clock_fn=virtual_clock,
        )
        result = orch.run("2025-01-01")

        self.assertEqual(result.status, DagRunStatus.SUCCESS)
        self.assertEqual(result.sla_breaches, 1)
        self.assertTrue(result.task_results["slow_task"].sla_breached)

        stored_run = self.store.get_dag_run(result.run_id)
        self.assertIsNotNone(stored_run)
        assert stored_run is not None
        self.assertEqual(stored_run["sla_breaches"], 1)

        stored_tasks = self.store.list_task_instances(result.run_id)
        self.assertEqual(len(stored_tasks), 1)
        self.assertTrue(stored_tasks[0]["sla_breached"])

    def test_airflow_dag_topology_parity_with_retail_dag(self) -> None:
        self.assertTrue(AIRFLOW_DAG_PATH.exists(), f"Missing Airflow DAG file: {AIRFLOW_DAG_PATH}")
        spec = importlib.util.spec_from_file_location(
            "viettech_retail_elt_dag", AIRFLOW_DAG_PATH
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(module.DAG_ID, RETAIL_DAG_ID)
        self.assertEqual(module.AIRFLOW_TASK_TOPOLOGY, RETAIL_DAG_TOPOLOGY)
        self.assertEqual(module.SCHEDULE, "0 2 * * *")
        self.assertEqual(module.DEFAULT_ARGS["retries"], 2)
        self.assertTrue(module.DEFAULT_ARGS["retry_exponential_backoff"])

        # Verify structured JSON on_task_failure_callback logs cleanly
        with self.assertLogs("viettech.airflow.alerts", level="ERROR") as captured:
            module.on_task_failure_callback(
                {
                    "dag_id": module.DAG_ID,
                    "task_id": "run_dq_reconciliation",
                    "run_id": "manual__2025-01-01",
                    "ds": "2025-01-01",
                    "exception": RuntimeError("3-way reconciliation drift"),
                }
            )
        payload = json.loads(captured.output[0].split(":", 2)[-1])
        self.assertEqual(payload["dag_id"], RETAIL_DAG_ID)
        self.assertEqual(payload["task_id"], "run_dq_reconciliation")
        self.assertIn("3-way reconciliation drift", payload["error"])


class TestOrchestratorGrayBoxEndToEnd(unittest.TestCase):
    """Gray-Box end-to-end integration tests running the real 7-task VietTech Retail ELT DAG."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="viettech_orch_gb_")
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_full_seven_task_retail_elt_dag_end_to_end(self) -> None:
        db_path = self.tmp_dir / "warehouse.db"
        state_db_path = self.tmp_dir / "orchestrator_state.db"
        lineage_path = self.tmp_dir / "lineage_manifest.json"

        result = run_retail_dag(
            logical_date="2025-01-01",
            raw_dir=RAW_DIR,
            db_path=db_path,
            state_db_path=state_db_path,
            lineage_path=lineage_path,
            mode="full",
            skip_mining=False,
            retry_delay_sec=0.0,
            quiet=True,
        )

        self.assertEqual(result.status, DagRunStatus.SUCCESS)
        self.assertEqual(len(result.task_results), 7)
        for task_id in RETAIL_DAG_TOPOLOGY:
            self.assertEqual(
                result.task_results[task_id].status,
                TaskStatus.SUCCESS,
                f"Expected task {task_id} to succeed: {result.task_results[task_id]}",
            )

        # Verify metastore recorded all 7 SUCCESS task_instances
        store = OrchestratorStateStore(state_db_path)
        instances = store.list_task_instances(result.run_id)
        self.assertEqual(len(instances), 7)
        self.assertTrue(all(ti["status"] == "SUCCESS" for ti in instances))

        # Verify 0.0000 VND reconciliation output & lineage manifest
        dq_out = result.task_results["run_dq_reconciliation"].output
        self.assertEqual(dq_out["critical_failed"], 0)
        self.assertEqual(dq_out["critical_passed"], 11)
        self.assertIn("max_diff=0.0000 VND", dq_out["reconciliation_observed"])
        self.assertTrue(lineage_path.exists())

        # Verify all 3 prescriptive data mining marts are populated in warehouse DB
        conn = sqlite3.connect(str(db_path))
        try:
            mba_cnt = int(conn.execute("SELECT COUNT(*) FROM mart_market_basket_rules").fetchone()[0])
            km_cnt = int(conn.execute("SELECT COUNT(*) FROM mart_rfm_kmeans_clusters").fetchone()[0])
            rr_cnt = int(conn.execute("SELECT COUNT(*) FROM mart_return_risk_scores").fetchone()[0])
        finally:
            conn.close()

        self.assertGreater(mba_cnt, 0)
        self.assertGreater(km_cnt, 0)
        self.assertGreater(rr_cnt, 0)

    def test_contract_failure_short_circuits_all_downstream_tasks(self) -> None:
        corrupted_raw = self.tmp_dir / "corrupted_raw"
        corrupted_raw.mkdir(parents=True, exist_ok=True)
        for csv_file in RAW_DIR.glob("*.csv"):
            shutil.copy2(csv_file, corrupted_raw / csv_file.name)

        # Corrupt orders.csv header by dropping required columns
        (corrupted_raw / "orders.csv").write_text(
            "id,order_code,corrupted_col\n1,ORD-BAD,oops\n",
            encoding="utf-8",
        )

        db_path = self.tmp_dir / "untouched_warehouse.db"
        state_db_path = self.tmp_dir / "state.db"

        result = run_retail_dag(
            logical_date="2025-01-01",
            raw_dir=corrupted_raw,
            db_path=db_path,
            state_db_path=state_db_path,
            mode="full",
            retry_delay_sec=0.0,
            sleep_fn=lambda _s: None,
            quiet=True,
        )

        self.assertEqual(result.status, DagRunStatus.FAILED)
        self.assertEqual(
            result.task_results["validate_data_contracts"].status,
            TaskStatus.FAILED,
        )
        downstream_tasks = [
            tid for tid in RETAIL_DAG_TOPOLOGY if tid != "validate_data_contracts"
        ]
        self.assertEqual(len(downstream_tasks), 6)
        for tid in downstream_tasks:
            self.assertEqual(
                result.task_results[tid].status,
                TaskStatus.UPSTREAM_FAILED,
                f"Expected {tid} to be UPSTREAM_FAILED",
            )
            self.assertEqual(result.task_results[tid].attempts, 0)

        # Warehouse DB must NEVER be created or touched when contract gate fails
        self.assertFalse(db_path.exists())

    def test_multi_date_idempotent_backfill_populates_three_rfm_snapshots(self) -> None:
        db_path = self.tmp_dir / "backfill_warehouse.db"
        state_db_path = self.tmp_dir / "backfill_state.db"
        dates = ["2024-11-01", "2024-12-01", "2025-01-01"]

        orch = build_retail_orchestrator(
            raw_dir=RAW_DIR,
            db_path=db_path,
            state_db_path=state_db_path,
            retry_delay_sec=0.0,
            sleep_fn=lambda _s: None,
            quiet=True,
        )
        results = orch.backfill(
            dates,
            params={"mode": "full", "watermark": None, "skip_mining": True},
        )

        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.status == DagRunStatus.SUCCESS for r in results))
        self.assertTrue(all(r.trigger_mode == "backfill" for r in results))

        store = OrchestratorStateStore(state_db_path)
        stored_runs = store.list_dag_runs(dag_id=RETAIL_DAG_ID, limit=10)
        self.assertEqual(len(stored_runs), 3)
        self.assertTrue(all(r["trigger_mode"] == "backfill" for r in stored_runs))

        conn = sqlite3.connect(str(db_path))
        try:
            snapshot_rows = conn.execute(
                """
                SELECT snapshot_date, COUNT(*) AS cnt
                FROM mart_rfm_snapshot
                GROUP BY snapshot_date
                ORDER BY snapshot_date ASC
                """
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual([str(row[0]) for row in snapshot_rows], dates)
        self.assertTrue(all(int(row[1]) > 0 for row in snapshot_rows))

    def test_cli_runner_full_skip_mining_and_status(self) -> None:
        db_path = self.tmp_dir / "cli_wh.db"
        state_db_path = self.tmp_dir / "cli_state.db"

        stdout_run = io.StringIO()
        with contextlib.redirect_stdout(stdout_run):
            rc_run = orchestrator_cli.main(
                [
                    "--mode",
                    "full",
                    "--logical-date",
                    "2025-01-01",
                    "--db-path",
                    str(db_path),
                    "--state-db-path",
                    str(state_db_path),
                    "--skip-mining",
                    "--json",
                ]
            )
        self.assertEqual(rc_run, 0)
        parsed = json.loads(stdout_run.getvalue())
        self.assertEqual(parsed["status"], "SUCCESS")
        self.assertEqual(len(parsed["task_results"]), 7)

        stdout_status = io.StringIO()
        with contextlib.redirect_stdout(stdout_status):
            rc_status = orchestrator_cli.main(
                [
                    "--status",
                    "--state-db-path",
                    str(state_db_path),
                ]
            )
        self.assertEqual(rc_status, 0)
        self.assertIn(parsed["run_id"], stdout_status.getvalue())


if __name__ == "__main__":
    unittest.main()
