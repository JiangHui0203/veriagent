"""Run the fixed DA01/DA02/DA10 smoke through the real LLM client."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Optional

from veriagent.data_agent import DataAgentRunner
from veriagent.data_agent.llm import LLMClient
from veriagent.data_agent.runner.smoke_contract import smoke_cross_check_sql
from veriagent.data_agent.tools.registry import create_registry
from veriagent.data_agent.trace import load_trace


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TASKS_FILE = (
    PROJECT_ROOT / "veriagent" / "datasets" / "olistbr" / "tasks" / "v0" / "tasks_v0.jsonl"
)
DEFAULT_DATABASE = PROJECT_ROOT / "veriagent" / "datasets" / "olistbr" / "ecommerce.duckdb"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "results" / "data_agent" / "qwen3_8b_smoke_v0"
SMOKE_TASK_IDS = ("DA01", "DA02", "DA10")
EXECUTION_BACKEND = "OpenPAI"
TEMPERATURE = 0.0
MAX_TOKENS = 1024
TIMEOUT_SECONDS = 180.0
FAILURE_CATEGORIES = frozenset(
    {
        "SERVER_START_FAILURE",
        "LLM_API_ERROR",
        "JSON_PARSE_FAILURE",
        "METRIC_SELECTION_FAILURE",
        "SQL_GENERATION_FAILURE",
        "SQL_EXECUTION_FAILURE",
        "METRIC_CONTRACT_VIOLATION",
        "CROSS_CHECK_FAILURE",
        "FINAL_ANSWER_FAILURE",
        "TRACE_WRITE_FAILURE",
    }
)


def load_smoke_tasks(path: Path) -> list[dict[str, str]]:
    records: dict[str, dict[str, Any]] = {}
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid task JSON on line {line_number}") from error
            if not isinstance(record, dict):
                raise ValueError(f"task line {line_number} must be an object")
            task_id = record.get("task_id")
            question = record.get("question")
            if not isinstance(task_id, str) or not isinstance(question, str) or not question.strip():
                raise ValueError(f"task line {line_number} has invalid task_id or question")
            if task_id in records:
                raise ValueError(f"duplicate task_id: {task_id}")
            records[task_id] = record

    missing = [task_id for task_id in SMOKE_TASK_IDS if task_id not in records]
    if missing:
        raise ValueError("missing smoke tasks: " + ", ".join(missing))
    return [
        {"task_id": task_id, "question": records[task_id]["question"]}
        for task_id in SMOKE_TASK_IDS
    ]


def validate_output_root(output_root: Path) -> Path:
    output_root = Path(output_root).resolve()
    existing = [output_root / task_id for task_id in SMOKE_TASK_IDS]
    existing.append(output_root / "smoke_summary.json")
    conflicts = [path for path in existing if path.exists()]
    if conflicts:
        raise FileExistsError(
            "refusing to overwrite existing real smoke outputs: "
            + ", ".join(str(path) for path in conflicts)
        )
    output_root.mkdir(parents=True, exist_ok=True)
    return output_root


def _failure_category(events: list[Any], status: str) -> Optional[str]:
    if status in {"SUCCESS", "STOP_WITH_DATA_GAP"}:
        return None

    failed_events = [event for event in events if event.event_type == "run_failed"]
    raw_error = str(failed_events[-1].data.get("error_type", "")) if failed_events else ""
    llm_events = [event for event in events if event.event_type == "llm_request_completed"]
    stage = str(llm_events[-1].data.get("stage", "")) if llm_events else ""

    if raw_error == "LLM_API_ERROR":
        return "LLM_API_ERROR"
    if raw_error in {"JSON_PARSE_ERROR", "JSON_SCHEMA_ERROR", "FAKE_RESPONSE_SCHEMA_ERROR"}:
        return "JSON_PARSE_FAILURE"
    if any(event.event_type == "cross_check_completed" for event in events):
        return "CROSS_CHECK_FAILURE"
    if stage == "final_answer":
        return "FINAL_ANSWER_FAILURE"
    if raw_error == "SQL_NOT_READ_ONLY":
        return "SQL_GENERATION_FAILURE"
    if raw_error in {"SQL_EXECUTION_ERROR", "SQL_ERROR"}:
        return "SQL_EXECUTION_FAILURE"
    if stage == "sql_generation":
        return "SQL_GENERATION_FAILURE"
    if raw_error in {"METRIC_NOT_FOUND", "METRIC_ERROR", "INVALID_ARGUMENT"}:
        return "METRIC_SELECTION_FAILURE"
    return "METRIC_SELECTION_FAILURE"


def _task_statistics(events: list[Any]) -> dict[str, Any]:
    llm_events = [event for event in events if event.event_type == "llm_request_completed"]
    return {
        "llm_calls": len(llm_events),
        "prompt_tokens": sum(int(event.data.get("prompt_tokens", 0) or 0) for event in llm_events),
        "completion_tokens": sum(
            int(event.data.get("completion_tokens", 0) or 0) for event in llm_events
        ),
        "llm_latency_ms": round(
            sum(float(event.data.get("latency_ms", 0.0) or 0.0) for event in llm_events), 3
        ),
        "event_count": len(events),
    }


def _write_unexpected_failure(
    output_root: Path,
    *,
    task_id: str,
    model: str,
    error: Exception,
) -> dict[str, Any]:
    run_dir = output_root / task_id
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "trace.jsonl"
    category = "TRACE_WRITE_FAILURE"
    summary = {
        "run_id": task_id,
        "task_id": task_id,
        "model": model,
        "execution_backend": EXECUTION_BACKEND,
        "status": "FAILED",
        "failure_category": category,
        "error": f"{type(error).__name__}: {error}",
    }
    try:
        (run_dir / "run_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        category = "TRACE_WRITE_FAILURE"
    return {
        "task_id": task_id,
        "status": "FAILED",
        "failure_category": category,
        "trace_path": str(trace_path),
        "summary_path": str(run_dir / "run_summary.json"),
        "error": summary["error"],
    }


def run_smoke(
    *,
    tasks_file: Path,
    database: Path,
    output_root: Path,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    tasks_file = Path(tasks_file).resolve()
    database = Path(database).resolve()
    if not tasks_file.is_file():
        raise FileNotFoundError(f"tasks file not found: {tasks_file}")
    if not database.is_file():
        raise FileNotFoundError(f"DuckDB database not found: {database}")

    tasks = load_smoke_tasks(tasks_file)
    output_root = validate_output_root(output_root)
    client = LLMClient.from_env(
        environ=environ,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        timeout_seconds=TIMEOUT_SECONDS,
        allow_json_repair=True,
    )
    runner = DataAgentRunner(
        llm_client=client,
        tool_registry=create_registry(database),
        trace_root=output_root,
        execution_backend=EXECUTION_BACKEND,
    )

    task_summaries = []
    for task in tasks:
        task_id = task["task_id"]
        try:
            result = runner.run(
                run_id=task_id,
                task_id=task_id,
                question=task["question"],
                cross_check_sql=smoke_cross_check_sql(task_id),
            )
            events = load_trace(result.trace_path)
            task_summary = {
                "task_id": task_id,
                "status": result.status,
                "failure_category": _failure_category(events, result.status),
                "trace_path": str(result.trace_path),
                "summary_path": str(result.summary_path),
                **_task_statistics(events),
            }
        except Exception as error:
            task_summary = _write_unexpected_failure(
                output_root, task_id=task_id, model=client.model, error=error
            )
        task_summaries.append(task_summary)
        suffix = (
            f" ({task_summary['failure_category']})"
            if task_summary.get("failure_category")
            else ""
        )
        print(f"{task_id}: {task_summary['status']}{suffix}")

    failed = sum(task["status"] == "FAILED" for task in task_summaries)
    payload = {
        "smoke_id": "qwen3_8b_smoke_v0",
        "model": client.model,
        "execution_backend": EXECUTION_BACKEND,
        "generation": {
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "timeout_seconds": TIMEOUT_SECONDS,
            "json_repair_attempts": 1,
        },
        "task_ids": list(SMOKE_TASK_IDS),
        "status": "COMPLETED" if failed == 0 else "COMPLETED_WITH_FAILURES",
        "tasks": task_summaries,
    }
    summary_path = output_root / "smoke_summary.json"
    summary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"smoke: {payload['status']} ({len(task_summaries) - failed}/3 non-failed)")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-file", type=Path, default=DEFAULT_TASKS_FILE)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    run_smoke(
        tasks_file=args.tasks_file,
        database=args.database,
        output_root=args.output_root,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
