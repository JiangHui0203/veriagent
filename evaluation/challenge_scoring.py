"""Score completed challenge runs; keep failure hypotheses separate from facts."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

from ..data_agent.memory.store import digest
from ..data_agent.trace import load_trace
from .challenge_catalog import file_sha256, load_tasks, write_json
from .memory_metrics import paired_outcomes, summarize_group
from .memory_scoring import _matches


def score_challenges(output: Path, database: Path) -> dict:
    manifest = json.loads((output / "evaluation_manifest.json").read_text())
    if manifest["status"] != "COMPLETED":
        raise ValueError("Complete challenge runtime before reading references")
    catalog = Path(manifest["catalog"])
    catalog_manifest, tasks = load_tasks(catalog, manifest["split"], database)
    if catalog_manifest["catalog_id"] != manifest["catalog_id"] or tasks != manifest["tasks"]:
        raise ValueError("Challenge catalog differs from runtime snapshot")
    required = {(group, task["task_id"], repeat) for group in manifest["conditions"]
                for task in tasks for repeat in range(1, manifest["repeats"] + 1)}
    observed = {(run["group"], run["task_id"], run["repeat"]) for run in manifest["runs"]}
    if required != observed or len(observed) != len(manifest["runs"]):
        raise ValueError("Missing or duplicate challenge runs")
    if len({(run["group"], run["run_id"]) for run in manifest["runs"]}) != len(manifest["runs"]):
        raise ValueError("Repeated challenges must have distinct trace directories")
    if file_sha256(catalog / "references.json") != catalog_manifest["files"]["references.json"]:
        raise ValueError("Challenge references changed after freezing")
    if manifest["memory_snapshot_id"] is not None:
        snapshot = json.loads((output / "memory_snapshot.json").read_text())
        if digest(snapshot) != manifest["memory_snapshot_id"]:
            raise ValueError("Memory snapshot changed")
    references = json.loads((catalog / "references.json").read_text())
    groups = {}
    review = []
    for group in manifest["conditions"]:
        cases = []
        for run in (run for run in manifest["runs"] if run["group"] == group):
            task_id = run["task_id"]
            reference = references[task_id]
            path = output / group / run["run_id"]
            summary = json.loads((path / "run_summary.json").read_text())
            events = load_trace(path / "trace.jsonl")
            question = next(e.data["question"] for e in events if e.event_type == "question_received")
            start = events[0]
            if (summary["task_id"] != task_id or summary["run_id"] != run["run_id"]
                    or reference["split"] != manifest["split"] or digest(question) != reference["question_sha256"]
                    or start.event_type != "run_started"
                    or start.data.get("memory_enabled") != (group == "with_memory")
                    or start.data.get("memory_snapshot_id") != (manifest["memory_snapshot_id"] if group == "with_memory" else None)):
                raise ValueError("Challenge trace identity, question, or condition differs from manifest")
            sql_events = [e for e in events if e.event_type == "sql_execution_completed"]
            sql_values = {}
            if sql_events and sql_events[-1].status == "success":
                result = sql_events[-1].data["tool_result"]["result"]
                if len(result["rows"]) == 1 and not result["truncated"]:
                    sql_values = dict(zip(result["columns"], result["rows"][0]))
            answers = summary.get("final_answer", {}).get("key_values", {})
            selected = {e.data["tool_result"]["result"]["metric_id"] for e in events
                        if e.event_type == "metric_search_completed" and e.status == "success"}
            checks = {"status": summary["status"] == "SUCCESS", "metric_selection": selected == set(reference["metrics"]),
                      "sql_numeric": _matches(sql_values, reference["values"]),
                      "final_numeric": _matches(answers, reference["values"])}
            passed = all(checks.values())
            failures = [e.data for e in events if e.event_type == "run_failed"]
            failure = failures[-1] if failures else None
            calls = [e for e in events if e.event_type == "llm_request_completed"]
            case = {
                "task_id": task_id, "run_id": run["run_id"], "repeat": run["repeat"], "family": reference["family"],
                "passed": passed, "status": summary["status"], "checks": checks, "answerable": True,
                "analysis_succeeded": any(e.event_type == "analysis_generated" for e in events),
                "invalid_table": any(e.event_type == "schema_inspection_completed" and e.data["tool_result"].get("error_type") == "INVALID_TABLE" for e in events),
                "end_to_end_ms": run["elapsed_ms"], "false_accept": summary["status"] == "SUCCESS" and not passed,
                "llm_requests": sum(e.data.get("attempts", 1) for e in calls),
                "prompt_tokens": sum(e.data.get("prompt_tokens", 0) for e in calls),
                "completion_tokens": sum(e.data.get("completion_tokens", 0) for e in calls),
                "llm_latency_ms": round(sum(e.data.get("latency_ms", 0) for e in calls), 2),
                "memory_hits": sum(len(e.data.get("experiences", [])) for e in events if e.event_type == "memory_retrieved"),
                "failure": failure, "failure_category": None if passed else failure["error_type"] if failure else "OFFLINE_REFERENCE_MISMATCH",
                "final_key_values": answers,
            }
            cases.append(case)
            if not passed:
                generated = [e.data["sql"] for e in events if e.event_type == "sql_generated"]
                review.append({"task_id": task_id, "run_id": run["run_id"], "group": group,
                               "family": reference["family"], "checks": checks, "failure": failure,
                               "trace_sha256": file_sha256(path / "trace.jsonl"), "sql": generated[-1] if generated else None,
                               "observed_sql_values": sql_values, "expected_values": reference["values"],
                               "matches_known_mutation_values": _matches(sql_values, reference["mutation_values"]),
                               "known_mutation": reference["mutation"],
                               "diagnosis_status": "REQUIRES_REVIEW", "eligible_for_memory": False})
        groups[group] = summarize_group(cases)
        by_family = defaultdict(list)
        for case in cases:
            by_family[case["family"]].append(case)
        groups[group]["by_family"] = {family: {"passed": sum(c["passed"] for c in rows), "total": len(rows),
                                               "false_accepts": sum(c["false_accept"] for c in rows)}
                                     for family, rows in by_family.items()}
    report = {"model": manifest["model"], "catalog_id": manifest["catalog_id"], "split": manifest["split"],
              "mode": manifest["mode"], "unique_tasks": len(tasks), "repeats": manifest["repeats"],
              "learning_during_evaluation": False, "groups": groups,
              "limitations": ["Mutation witnesses show discriminative data, not observed model errors.",
                              "Matching a wrong reference value is a diagnostic hypothesis, not proof of SQL root cause.",
                              "Development results do not measure held-out memory benefit; no failed output is automatically learned.",
                              "Question families and database are shared; this is within-domain scenario transfer."]}
    if len(groups) == 2:
        report["paired_outcomes"] = paired_outcomes(groups)
    write_json(output / "challenge_score.json", report)
    if manifest["split"] == "dev":
        write_json(output / "failure_review.json", {"status": "UNREVIEWED", "promotion_allowed": False, "cases": review})
    lines = ["# 挑战题运行结果", "", f"模型：`{manifest['model']}`；分组：{manifest['split']}；模式：{manifest['mode']}。",
             f"{len(tasks)} 道不同题，每种条件各重复 {manifest['repeats']} 次；运行中不更新记忆。", "",
             "| 条件 | 正确完成 | 错误答案被接受 | 模型请求 | 总 Token |", "|---|---:|---:|---:|---:|"]
    for group, metrics in groups.items():
        lines.append(f"| {group} | {metrics['passed']}/{metrics['total']} | {metrics['false_accepts']} | {metrics['llm_requests']} | {metrics['total_tokens']} |")
    if "paired_outcomes" in report:
        paired = report["paired_outcomes"]
        lines += ["", f"配对结果：无记忆错、有记忆对 {paired['helped']} 对；无记忆对、有记忆错 {paired['harmed']} 对；"
                  f"两组均对 {paired['both_passed']} 对；均错 {paired['both_failed']} 对。",
                  f"净正确完成差：{paired['net_helped']}/{paired['pairs']}（{paired['net_benefit_rate']:.2%}）。"
                  "配对标签描述本次观察，不代表单次差异已被证明由记忆造成。"]
    lines += ["", "| 条件 | 每次正确完成 Token（含失败消耗） | 耗时 p50 / p95（秒） | 记忆覆盖 | 始终通过 / 有波动题数 |",
              "|---|---:|---:|---:|---:|"]
    for group, metrics in groups.items():
        cost = metrics["cost_per_correct_completion"]["total_tokens"]
        latency, stability = metrics["end_to_end_latency"], metrics["stability"]
        timing = f"{latency['p50_ms'] / 1000:.2f} / {latency['p95_ms'] / 1000:.2f}" if latency["measured_runs"] else "未记录"
        lines.append(f"| {group} | {cost if cost is not None else '无正确完成'} | {timing} | "
                     f"{metrics['memory_task_coverage']:.1%} | {stability['always_passed']} / {stability['mixed_outcomes']} |")
    lines += ["", "| 条件 | 错误家族 | 通过/总数 |", "|---|---|---:|"]
    for group, metrics in groups.items():
        for family, row in metrics["by_family"].items():
            lines.append(f"| {group} | {family} | {row['passed']}/{row['total']} |")
    lines += ["", "开发集基线不能证明测试集收益。测试集的重复仍来自相同题目，不能当成独立新题。"
              "典型错误 SQL 仅验证题目的区分度，不计入模型错误次数。本轮运行的失败输出未写入记忆。", ""]
    for row in review:
        lines.append(f"- {row['group']}/{row['run_id']}: {row['failure']['error_type'] if row['failure'] else 'OFFLINE_REFERENCE_MISMATCH'}；待复核。")
    (output / "challenge_score.md").write_text("\n".join(lines), encoding="utf-8")
    return report
