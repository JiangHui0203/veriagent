"""Offline independent-reference scoring for the frozen memory experiment."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..data_agent.db import read_only_connection
from ..data_agent.memory.store import digest
from ..data_agent.trace import load_trace
from .memory_metrics import paired_outcomes, summarize_group
from .memory_tasks import MEMORY_TASK_SUITES


REFERENCE_QUERIES = {
    "EM01": """
        SELECT COUNT(DISTINCT order_id) AS order_count FROM orders
        WHERE order_purchase_timestamp >= TIMESTAMP '2017-12-01'
          AND order_purchase_timestamp < TIMESTAMP '2018-01-01'
    """,
    "EM02": """
        SELECT SUM(payment_value) AS payment_value FROM order_payments
        WHERE order_id IN (
            SELECT order_id FROM orders WHERE order_status = 'delivered'
              AND order_purchase_timestamp >= TIMESTAMP '2017-12-01'
              AND order_purchase_timestamp < TIMESTAMP '2018-01-01'
        )
    """,
    "EM03": """
        WITH eligible AS (
            SELECT order_id FROM orders WHERE order_status = 'delivered'
              AND order_purchase_timestamp >= TIMESTAMP '2017-10-01'
              AND order_purchase_timestamp < TIMESTAMP '2017-11-01'
        )
        SELECT
            (SELECT SUM(payment_value) FROM order_payments WHERE order_id IN (SELECT order_id FROM eligible)) AS payment_value,
            (SELECT SUM(price) FROM order_items WHERE order_id IN (SELECT order_id FROM eligible)) AS item_revenue,
            (SELECT SUM(freight_value) FROM order_items WHERE order_id IN (SELECT order_id FROM eligible)) AS freight_value
    """,
}


def _v3_queries() -> dict[str, str]:
    # Independently authored reference specifications, never passed to the agent.
    queries = {}
    for task_id, month, status in (("EM21", 7, None), ("EM22", 8, "delivered"), ("EM23", 9, "canceled"),
                                   ("EM24", 1, "delivered"), ("EM25", 2, None), ("EM26", 3, "delivered"),
                                   ("EM27", 4, "delivered"), ("EM28", 5, "delivered"), ("EM29", 6, "delivered")):
        scope = f"order_purchase_timestamp >= TIMESTAMP '2017-{month:02d}-01' AND order_purchase_timestamp < TIMESTAMP '2017-{month + 1:02d}-01'"
        if status:
            scope += f" AND order_status = '{status}'"
        eligible = f"SELECT order_id FROM orders WHERE {scope}"
        if task_id in {"EM21", "EM22", "EM23"}:
            queries[task_id] = f"SELECT COUNT(DISTINCT order_id) AS order_count FROM orders WHERE {scope}"
        elif task_id in {"EM24", "EM25", "EM26"}:
            queries[task_id] = f"SELECT SUM(payment_value) AS payment_value FROM order_payments WHERE order_id IN ({eligible})"
        else:
            queries[task_id] = f"""WITH eligible AS ({eligible}) SELECT
                (SELECT SUM(payment_value) FROM order_payments WHERE order_id IN (SELECT order_id FROM eligible)) AS payment_value,
                (SELECT SUM(price) FROM order_items WHERE order_id IN (SELECT order_id FROM eligible)) AS item_revenue,
                (SELECT SUM(freight_value) FROM order_items WHERE order_id IN (SELECT order_id FROM eligible)) AS freight_value"""
    return queries


def reference_values(database: Path, suite: str = "v1") -> dict[str, Any]:
    queries = REFERENCE_QUERIES
    if suite == "v2":
        queries = {
            "EM11": REFERENCE_QUERIES["EM01"].replace("2017-12-01", "2018-06-01").replace("2018-01-01", "2018-07-01"),
            "EM12": REFERENCE_QUERIES["EM02"].replace("2017-12-01", "2018-05-01").replace("2018-01-01", "2018-06-01"),
            "EM13": REFERENCE_QUERIES["EM03"].replace("2017-10-01", "2018-02-01").replace("2017-11-01", "2018-03-01"),
        }
    elif suite == "v3":
        queries = _v3_queries()
    elif suite != "v1":
        raise ValueError("Unknown memory evaluation suite")
    expected = {task["task_id"]: None for task in MEMORY_TASK_SUITES[suite] if task["task_id"] not in queries}
    with read_only_connection(database) as connection:
        for task_id, query in queries.items():
            cursor = connection.execute(query)
            names = [column[0] for column in cursor.description]
            expected[task_id] = {name: str(value) for name, value in zip(names, cursor.fetchone())}
    return expected


def _matches(actual: Any, expected: dict[str, str]) -> bool:
    if not isinstance(actual, dict) or set(actual) != set(expected):
        return False
    for key, value in actual.items():
        if isinstance(value, bool) or not isinstance(value, (float, int, Decimal)):
            return False
        number = Decimal(str(value))
        tolerance = Decimal(0) if key in {"order_count", "unique_customers"} else Decimal("0.01")
        if not number.is_finite() or abs(number - Decimal(expected[key])) > tolerance:
            return False
    return True


def score_memory_evaluation(output_root: Path, database: Path) -> dict[str, Any]:
    output_root = Path(output_root)
    manifest = json.loads((output_root / "evaluation_manifest.json").read_text())
    if manifest.get("status") != "COMPLETED":
        raise ValueError("Complete both runtime groups before reading independent reference results")
    suite = manifest.get("suite", "v1")
    tasks = MEMORY_TASK_SUITES[suite]
    if manifest.get("tasks") != list(tasks):
        raise ValueError("Evaluation questions differ from the frozen suite")
    snapshot = json.loads((output_root / "memory_snapshot.json").read_text())
    if digest(snapshot) != manifest["memory_snapshot_id"]:
        raise ValueError("Saved memory snapshot differs from the evaluated snapshot")
    repeats = manifest.get("repeats", 1)
    runs = manifest.get("runs")
    if runs is None:  # Compatibility with the original single-run artifacts.
        runs = [{"group": group, "task_id": task["task_id"], "run_id": task["task_id"], "repeat": 1}
                for group in ("without_memory", "with_memory") for task in tasks]
    required = {(group, task["task_id"], repeat) for group in ("without_memory", "with_memory")
                for task in tasks for repeat in range(1, repeats + 1)}
    recorded = {(run["group"], run["task_id"], run["repeat"]) for run in runs}
    if recorded != required or len(recorded) != len(runs):
        raise ValueError("Evaluation manifest is missing or duplicates paired runs")
    if len({(run["group"], run["run_id"]) for run in runs}) != len(runs):
        raise ValueError("Repeated evaluation runs must have distinct trace directories")
    expected = reference_values(database, suite)
    (output_root / "reference_results.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n")
    groups = {}
    for group in ("without_memory", "with_memory"):
        cases = []
        for run in (run for run in runs if run["group"] == group):
            task_id = run["task_id"]
            run_dir = output_root / group / run["run_id"]
            summary = json.loads((run_dir / "run_summary.json").read_text())
            events = load_trace(run_dir / "trace.jsonl")
            if summary["task_id"] != task_id or summary["run_id"] != run["run_id"]:
                raise ValueError("Run summary does not match the evaluated task/repeat")
            start = next(e.data for e in events if e.event_type == "run_started")
            if start.get("memory_enabled") != (group == "with_memory") or start.get("memory_snapshot_id") != (
                manifest["memory_snapshot_id"] if group == "with_memory" else None
            ):
                raise ValueError("Trace memory condition differs from the experiment manifest")
            question = next(e.data["question"] for e in events if e.event_type == "question_received")
            if question != next(task["question"] for task in tasks if task["task_id"] == task_id):
                raise ValueError("Trace question differs from the frozen suite")
            answers = summary.get("final_answer", {}).get("key_values", {})
            sql_events = [e for e in events if e.event_type == "sql_execution_completed"]
            if expected[task_id] is None:
                checks = {
                    "safe_stop": summary["status"] == "STOP_WITH_DATA_GAP",
                    "no_numeric_answer": answers == {},
                    "no_sql": not any(e.event_type in {"sql_generated", "sql_execution_completed"} for e in events),
                }
            else:
                sql_values = {}
                if sql_events and sql_events[-1].status == "success":
                    result = sql_events[-1].data["tool_result"]["result"]
                    if len(result["rows"]) == 1 and not result["truncated"]:
                        sql_values = dict(zip(result["columns"], result["rows"][0]))
                checks = {
                    "status": summary["status"] == "SUCCESS",
                    "sql_numeric": _matches(sql_values, expected[task_id]),
                    "final_numeric": _matches(answers, expected[task_id]),
                }
            calls = [e for e in events if e.event_type == "llm_request_completed"]
            memory_events = [e for e in events if e.event_type == "memory_retrieved"]
            passed = all(checks.values())
            failures = [e.data for e in events if e.event_type == "run_failed"]
            failure = failures[-1] if failures else None
            invalid_table = any(e.event_type == "schema_inspection_completed"
                                and e.data.get("tool_result", {}).get("error_type") == "INVALID_TABLE" for e in events)
            cases.append({
                "task_id": task_id, "run_id": run["run_id"], "repeat": run["repeat"],
                "passed": passed, "status": summary["status"], "checks": checks,
                "answerable": expected[task_id] is not None,
                "analysis_succeeded": any(e.event_type == "analysis_generated" and e.status == "success" for e in events),
                "invalid_table": invalid_table,
                "end_to_end_ms": run.get("elapsed_ms"),
                "false_accept": summary["status"] == "SUCCESS" and not passed,
                "llm_requests": sum(e.data.get("attempts", 1) for e in calls),
                "prompt_tokens": sum(e.data.get("prompt_tokens", 0) for e in calls),
                "completion_tokens": sum(e.data.get("completion_tokens", 0) for e in calls),
                "llm_latency_ms": round(sum(e.data.get("latency_ms", 0) for e in calls), 2),
                "memory_hits": sum(len(e.data.get("experiences", [])) for e in memory_events),
                "final_key_values": answers,
                "failure": failure,
                "failure_category": None if passed else failure["error_type"] if failure else "OFFLINE_REFERENCE_MISMATCH",
            })
        groups[group] = summarize_group(cases)
    report = {
        "model": manifest["model"], "suite": suite, "memory_snapshot_id": manifest["memory_snapshot_id"],
        "unique_tasks": len(tasks), "repeats": repeats,
        "learning_during_evaluation": False, "groups": groups,
        "paired_outcomes": paired_outcomes(groups),
        "correctness_delta": groups["with_memory"]["passed"] - groups["without_memory"]["passed"],
        "limitations": [
            f"{len(tasks)} unique question variants with {repeats} repeat(s); repetitions are not independent new tasks or proof of statistical improvement.",
            "The same deterministic guards and prompts are used in both groups; only retrieved experience differs.",
            "Rules are extracted with fixed templates grounded in existing metric contracts, not newly invented policies.",
            "Net benefit describes paired outcomes; it does not prove causality for any individual stochastic generation.",
            "Invalid-table recurrence covers analysis/schema failures only; numeric mismatches are not attributed to a SQL root cause.",
            "Costs include failed runs and successful data-gap stops; zero correct completions have undefined (null) unit cost.",
            "Token usage is provider-reported, may omit unreported failed-request usage, and is not a monetary bill or cache-cost estimate.",
            "End-to-end time includes the runner and tools, excluding offline scoring; legacy artifacts without timers report null.",
            "Data-gap stops use a shared deterministic guard and cannot be attributed to memory alone.",
        ],
    }
    (output_root / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = ["# 经验记忆对比实验", "", f"请求模型：`{manifest['model']}`。{len(tasks)} 道不同问题，每种条件重复 {repeats} 次，记忆保持冻结。", "",
             "| 条件 | 通过 | 错误答案被接受 | 模型请求 | 输入 token | 输出 token | 经验命中 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for group, title in (("without_memory", "关闭记忆"), ("with_memory", "启用记忆")):
        row = groups[group]
        lines.append(f"| {title} | {row['passed']}/{row['total']} | {row['false_accepts']} | {row['llm_requests']} | {row['prompt_tokens']} | {row['completion_tokens']} | {row['memory_hits']} |")
    paired = report["paired_outcomes"]
    lines += ["", f"成对比较：改善 {paired['helped']}，退化 {paired['harmed']}，均通过 {paired['both_passed']}，均失败 {paired['both_failed']}；净收益 {paired['net_helped']}/{paired['pairs']}。", "",
              "| 条件 | 表名错误复发/可观察机会 | 每次正确完成 token | 每次正确完成请求数 | 每次正确完成总耗时 ms | 任务耗时 P50/P95 ms | 重复结果有波动的题数 |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for group, title in (("without_memory", "关闭记忆"), ("with_memory", "启用记忆")):
        row = groups[group]
        recurrence = row["known_error_recurrence"]["invalid_table"]
        cost = row["cost_per_correct_completion"]
        latency = row["end_to_end_latency"]
        lines.append(f"| {title} | {recurrence['count']}/{recurrence['opportunities']} | {cost['total_tokens']} | {cost['llm_requests']} | {cost['end_to_end_ms']} | {latency['p50_ms']}/{latency['p95_ms']} | {row['stability']['mixed_outcomes']} |")
    lines += ["", "单位成本把失败消耗计入分子；正确完成包括正确的数据缺口停止。零次正确完成时单位成本为 null，而不是 0。",
              "表名错误机会仅包括可回答且成功进入指标选择输出的任务；更早失败的排除数保存在 JSON。没有观察到错误不能证明拦截能力。",
              "重复次数不等于不同任务数；P50/P95 使用 nearest-rank，仅描述本次样本，不能证明稳定的延迟收益。",
              "Token 为服务商报告的用量，未报告的失败请求用量可能缺失，也不代表含缓存计费的账单。",
              "配对的改善/退化是观察结果，不能单独证明某次随机生成变化由记忆造成。",
              "所有参考结果都在两组运行结束后计算，未用于模型提示或本轮记忆更新。", ""]
    for name, group in groups.items():
        for case in group["tasks"]:
            if case["failure"]:
                lines.append(f"- {name}/{case['run_id']}: {case['failure']['error_type']} — {case['failure']['error']}")
    (output_root / "comparison.md").write_text("\n".join(lines))
    return report
