"""Thin CLI facade for the VietTech Enterprise DAG Orchestrator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from viettech.contracts import DEFAULT_CONTRACT_PATH, DEFAULT_RAW_DIR  # noqa: E402
from viettech.orchestration import (  # noqa: E402
    DEFAULT_DB_PATH,
    DEFAULT_STATE_DB_PATH,
    RETAIL_DAG_ID,
    DagRunResult,
    DagRunStatus,
    OrchestratorStateStore,
    build_retail_orchestrator,
)
from viettech.warehouse import DEFAULT_SNAPSHOT_DATE  # noqa: E402


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VietTech Pure-Python DAG Orchestrator (Retries, Checkpoint Resume, Backfill & SLA)."
    )
    parser.add_argument("--mode", choices=["full", "incremental"], default="full")
    parser.add_argument("--logical-date", type=str, default=DEFAULT_SNAPSHOT_DATE)
    parser.add_argument("--watermark", type=str, default=None)
    parser.add_argument("--resume-run-id", type=str, default=None)
    parser.add_argument("--backfill-dates", nargs="+", default=None)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--skip-mining", action="store_true")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--state-db-path", type=Path, default=DEFAULT_STATE_DB_PATH)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--contract-path", type=Path, default=DEFAULT_CONTRACT_PATH)
    parser.add_argument("--status", action="store_true", help="Display recent DAG runs and task states.")
    parser.add_argument("--limit", type=int, default=10, help="Max runs to show with --status.")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit JSON output.")
    return parser.parse_args(argv)


def _print_run_table(result: DagRunResult) -> None:
    print(
        f"\n=== DAG Run: {result.run_id} | dag={result.dag_id} | "
        f"logical_date={result.logical_date} | status={result.status.value} | "
        f"mode={result.trigger_mode} | duration={result.duration_ms:.1f}ms | "
        f"sla_breaches={result.sla_breaches} ==="
    )
    print(f"{'TASK_ID':<28} {'STATUS':<16} {'ATTEMPTS':>8} {'DURATION_MS':>12} {'SLA_BREACH':>10}")
    print("-" * 78)
    for task_id, tr in result.task_results.items():
        print(
            f"{task_id:<28} {tr.status.value:<16} {tr.attempts:>8} "
            f"{tr.duration_ms:>12.2f} {str(tr.sla_breached):>10}"
        )
        if tr.error_message:
            print(f"  -> error: {tr.error_message}")


def _print_status(state_db_path: Path, limit: int, as_json: bool) -> int:
    store = OrchestratorStateStore(state_db_path)
    runs = store.list_dag_runs(dag_id=RETAIL_DAG_ID, limit=limit)
    if as_json:
        payload = [
            {**run, "tasks": store.list_task_instances(run["run_id"])}
            for run in runs
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not runs:
        print(f"No DAG runs recorded in {state_db_path}.")
        return 0
    print(f"\nRecent DAG runs in {state_db_path}:")
    print(f"{'RUN_ID':<46} {'DATE':<12} {'MODE':<10} {'STATUS':<8} {'DURATION_MS':>12}")
    print("-" * 92)
    for run in runs:
        print(f"{run['run_id']:<46} {run['logical_date']:<12} {run['trigger_mode']:<10} {run['status']:<8} {run['duration_ms']:>12.2f}")
    print(f"\nLatest run tasks ({runs[0]['run_id']}):")
    for ti in store.list_task_instances(runs[0]["run_id"]):
        print(f"  - {ti['task_id']:<26} {ti['status']:<16} attempts={ti['attempts']} duration_ms={ti['duration_ms']:.2f}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.status:
        return _print_status(args.state_db_path, args.limit, args.as_json)

    orchestrator = build_retail_orchestrator(
        raw_dir=args.raw_dir,
        db_path=args.db_path,
        state_db_path=args.state_db_path,
        contract_path=args.contract_path,
        quiet=args.as_json,
    )
    params = {"mode": args.mode, "watermark": args.watermark, "skip_mining": args.skip_mining}

    if args.backfill_dates:
        results = orchestrator.backfill(args.backfill_dates, params=params, continue_on_error=args.continue_on_error)
        if args.as_json:
            print(json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False))
        else:
            for res in results:
                _print_run_table(res)
        return 0 if all(r.status == DagRunStatus.SUCCESS for r in results) else 1

    result = orchestrator.run(
        logical_date=args.logical_date,
        resume_from_run_id=args.resume_run_id,
        params=params,
        trigger_mode="resume" if args.resume_run_id else "manual",
    )
    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_run_table(result)
    return 0 if result.status == DagRunStatus.SUCCESS else 1


if __name__ == "__main__":
    raise SystemExit(main())
