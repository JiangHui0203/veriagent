"""Offline scorer for completed DA01/DA02/DA10 real-model smoke traces."""

from __future__ import annotations

import argparse
import json
import math
import numbers
from pathlib import Path
from typing import Any, Optional

from veriagent.data_agent.trace import load_trace


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RESULTS_ROOT = PROJECT_ROOT / "results" / "data_agent" / "qwen3_8b_smoke_v0"
DEFAULT_GOLD_FILE = (
    PROJECT_ROOT / "veriagent" / "datasets" / "olistbr" / "tasks" / "v0" / "gold_v0.jsonl"
)
SCORED_TASK_IDS = ("DA01", "DA02", "DA10")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _load_gold(path: Path) -> dict[str, dict[str, Any]]:
    records = {}
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict) or not isinstance(record.get("task_id"), str):
                raise ValueError(f"invalid Gold record on line {line_number}")
            records[record["task_id"]] = record
    missing = [task_id for task_id in SCORED_TASK_IDS if task_id not in records]
    if missing:
        raise ValueError("missing Gold records: " + ", ".join(missing))
    return records


def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    return None


def _single_numeric(values: Any, preferred_key: str) -> Optional[float]:
    if isinstance(values, dict):
        preferred = _numeric(values.get(preferred_key))
        if preferred is not None:
            return preferred
        candidates = [_numeric(value) for value in values.values()]
    elif isinstance(values, (list, tuple)):
        candidates = [_numeric(value) for value in values]
    else:
        candidates = [_numeric(values)]
    candidates = [value for value in candidates if value is not None]
    return candidates[0] if len(candidates) == 1 else None


def _sql_numeric(events: list[Any], preferred_key: str) -> Optional[float]:
    completed = [
        event
        for event in events
        if event.event_type == "sql_execution_completed" and event.status == "success"
    ]
    if not completed:
        return None
    result = completed[-1].data.get("tool_result", {}).get("result", {})
    columns = result.get("columns", [])
    rows = result.get("rows", [])
    if not isinstance(columns, list) or not isinstance(rows, list) or len(rows) != 1:
        return None
    row = rows[0]
    if not isinstance(row, (list, tuple)) or len(row) != len(columns):
        return None
    if preferred_key in columns:
        return _numeric(row[columns.index(preferred_key)])
    return _single_numeric(row, preferred_key)


def _close(actual: Optional[float], expected: float, tolerance: float) -> bool:
    return actual is not None and abs(actual - expected) <= tolerance


def _selected_metrics(events: list[Any]) -> list[str]:
    resolved = []
    for event in events:
        if event.event_type != "metric_search_completed" or event.status != "success":
            continue
        metric_id = event.data.get("tool_result", {}).get("result", {}).get("metric_id")
        if isinstance(metric_id, str) and metric_id not in resolved:
            resolved.append(metric_id)
    if resolved:
        return resolved
    analyses = [event for event in events if event.event_type == "analysis_generated"]
    if not analyses:
        return []
    metrics = analyses[-1].data.get("metrics", [])
    return metrics if isinstance(metrics, list) else []


def _runtime_failure_map(smoke_summary: dict[str, Any]) -> dict[str, Optional[str]]:
    mapping = {}
    for task in smoke_summary.get("tasks", []):
        if isinstance(task, dict) and isinstance(task.get("task_id"), str):
            mapping[task["task_id"]] = task.get("failure_category")
    return mapping


def _score_numeric_task(
    task_id: str,
    run_summary: dict[str, Any],
    events: list[Any],
    gold: dict[str, Any],
    runtime_failure: Optional[str],
) -> dict[str, Any]:
    metric_key, expected = next(iter(gold["gold_values"].items()))
    expected = float(expected)
    tolerance = 0.0 if task_id == "DA01" else float(
        gold.get("numeric_tolerance", {}).get("money_absolute", 0.01)
    )
    sql_value = _sql_numeric(events, metric_key)
    final_values = run_summary.get("final_answer", {}).get("key_values", {})
    final_value = _single_numeric(final_values, metric_key)
    selected = _selected_metrics(events)
    required_metrics = gold.get("required_metrics", [])
    cross_matched = None
    if task_id == "DA02":
        cross_events = [event for event in events if event.event_type == "cross_check_completed"]
        if cross_events:
            cross_matched = bool(
                cross_events[-1].data.get("tool_result", {}).get("result", {}).get("matched")
            )

    checks = {
        "status": run_summary.get("status") == "SUCCESS",
        "metric_selection": all(metric in selected for metric in required_metrics),
        "sql_numeric": _close(sql_value, expected, tolerance),
        "final_numeric": _close(final_value, expected, tolerance),
    }
    if task_id == "DA02":
        checks["cross_check"] = cross_matched is True

    failure_category = runtime_failure
    if failure_category is None and not checks["metric_selection"]:
        failure_category = "METRIC_CONTRACT_VIOLATION"
    if failure_category is None and not checks["sql_numeric"]:
        failure_category = "METRIC_CONTRACT_VIOLATION"
    if failure_category is None and task_id == "DA02" and not checks["cross_check"]:
        failure_category = "CROSS_CHECK_FAILURE"
    if failure_category is None and not checks["final_numeric"]:
        failure_category = "FINAL_ANSWER_FAILURE"

    passed = all(checks.values())
    return {
        "task_id": task_id,
        "passed": passed,
        "failure_category": None if passed else failure_category,
        "checks": checks,
        "observed": {
            "selected_metrics": selected,
            "sql_numeric": sql_value,
            "final_numeric": final_value,
            "cross_check_matched": cross_matched,
        },
        "expected": {metric_key: expected},
        "tolerance": tolerance,
    }


def _contains_numeric(value: Any) -> bool:
    if _numeric(value) is not None:
        return True
    if isinstance(value, dict):
        return any(_contains_numeric(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_numeric(item) for item in value)
    return False


def _score_data_gap_task(
    run_summary: dict[str, Any],
    events: list[Any],
    runtime_failure: Optional[str],
) -> dict[str, Any]:
    final_values = run_summary.get("final_answer", {}).get("key_values", {})
    sql_event_types = {"sql_generated", "sql_execution_completed"}
    execute_calls = [
        event
        for event in events
        if event.event_type == "tool_call_started" and event.tool == "execute_sql"
    ]
    checks = {
        "status": run_summary.get("status") == "STOP_WITH_DATA_GAP",
        "no_numeric_answer": not _contains_numeric(final_values),
        "no_sql": not any(event.event_type in sql_event_types for event in events)
        and not execute_calls,
    }
    passed = all(checks.values())
    failure_category = runtime_failure
    if failure_category is None and not passed:
        failure_category = "METRIC_CONTRACT_VIOLATION"
    return {
        "task_id": "DA10",
        "passed": passed,
        "failure_category": None if passed else failure_category,
        "checks": checks,
        "observed": {"final_key_values": final_values, "sql_event_count": len(execute_calls)},
        "expected": {"status": "STOP_WITH_DATA_GAP", "numeric_answer": None},
    }


def score_smoke(*, results_root: Path, gold_file: Path) -> dict[str, Any]:
    results_root = Path(results_root).resolve()
    smoke_summary_path = results_root / "smoke_summary.json"
    if not smoke_summary_path.is_file():
        raise FileNotFoundError(
            "runtime smoke_summary.json is required before offline scoring: "
            f"{smoke_summary_path}"
        )

    # The runtime completion artifact is deliberately checked before Gold is opened.
    smoke_summary = _read_json(smoke_summary_path)
    if smoke_summary.get("status") not in {"COMPLETED", "COMPLETED_WITH_FAILURES"}:
        raise ValueError("runtime smoke is not complete")
    gold = _load_gold(Path(gold_file).resolve())
    runtime_failures = _runtime_failure_map(smoke_summary)

    task_scores = []
    for task_id in SCORED_TASK_IDS:
        run_dir = results_root / task_id
        try:
            run_summary = _read_json(run_dir / "run_summary.json")
            events = load_trace(run_dir / "trace.jsonl")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            score = {
                "task_id": task_id,
                "passed": False,
                "failure_category": runtime_failures.get(task_id) or "TRACE_WRITE_FAILURE",
                "checks": {"trace_load": False},
                "observed": {"error": f"{type(error).__name__}: {error}"},
                "expected": {},
            }
            task_scores.append(score)
            print(f"{task_id} FAIL")
            continue
        if task_id == "DA10":
            score = _score_data_gap_task(run_summary, events, runtime_failures.get(task_id))
        else:
            score = _score_numeric_task(
                task_id,
                run_summary,
                events,
                gold[task_id],
                runtime_failures.get(task_id),
            )
        task_scores.append(score)
        print(f"{task_id} {'PASS' if score['passed'] else 'FAIL'}")

    passed = sum(score["passed"] for score in task_scores)
    payload = {
        "score_id": "qwen3_8b_smoke_v0_offline",
        "model": smoke_summary.get("model"),
        "execution_backend": smoke_summary.get("execution_backend"),
        "passed": passed,
        "total": len(task_scores),
        "all_passed": passed == len(task_scores),
        "tasks": task_scores,
    }
    (results_root / "offline_score.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"offline score: {passed}/{len(task_scores)}")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--gold-file", type=Path, default=DEFAULT_GOLD_FILE)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = score_smoke(results_root=args.results_root, gold_file=args.gold_file)
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
