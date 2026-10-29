"""Promote bounded lessons only from independently scored, complete runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..tools.metric_search import METRICS, requests_unsupported_metric
from ..trace import load_trace
from .store import ExperienceMemory, digest


def learn_from_run(run_dir: Path, memory_path: Path) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    memory = ExperienceMemory.load(memory_path)
    report = {"run_dir": str(run_dir), "accepted": [], "skipped": [], "changed_sources": 0}
    score_path = run_dir / "offline_score.json"
    summary_path = run_dir / "smoke_summary.json"
    if not score_path.is_file() or not summary_path.is_file():
        report["skipped"].append({"reason": "completed_runtime_and_offline_score_required"})
        return report
    score = json.loads(score_path.read_text())
    batch = json.loads(summary_path.read_text())
    if batch.get("status") not in {"COMPLETED", "COMPLETED_WITH_FAILURES"}:
        report["skipped"].append({"reason": "runtime_incomplete"})
        return report
    runtime_tasks = {task["task_id"]: task for task in batch.get("tasks", [])}
    for scored in score.get("tasks", []):
        task_id = scored.get("task_id", "")
        if not isinstance(task_id, str) or not task_id or Path(task_id).name != task_id or task_id in {".", ".."}:
            raise ValueError("Invalid task ID in offline score")
        reason = None
        rules = []
        if scored.get("passed") is not True or not scored.get("checks") or not all(v is True for v in scored["checks"].values()):
            report["skipped"].append({"task_id": task_id, "reason": "offline_checks_not_passed"})
            continue
        trace_path = run_dir / task_id / "trace.jsonl"
        task_summary = json.loads((run_dir / task_id / "run_summary.json").read_text())
        events = load_trace(trace_path)
        status = task_summary.get("status")
        if (task_summary.get("task_id") != task_id
                or task_summary.get("model") != score.get("model")
                or runtime_tasks.get(task_id, {}).get("status") != status
                or not events or events[0].event_type != "run_started"
                or events[-1].event_type != "run_finished"
                or events[-1].data.get("status") != status
                or task_summary.get("event_count") != len(events)
                or any(e.event_type == "run_failed" for e in events)):
            report["skipped"].append({"task_id": task_id, "reason": "inconsistent_runtime_evidence"})
            continue
        question_events = [e for e in events if e.event_type == "question_received"]
        if len(question_events) != 1:
            report["skipped"].append({"task_id": task_id, "reason": "question_evidence_missing"})
            continue
        question = question_events[0].data["question"]
        if status == "SUCCESS":
            required = {"status", "metric_selection", "sql_numeric", "final_numeric"}
            validated = any(e.event_type == "answer_validation_completed" and e.data.get("matched") is True for e in events)
            verified = any(e.event_type == "verification_completed" and e.data.get("final_values_match") is True for e in events)
            if not required <= set(scored["checks"]) or not validated or not verified:
                reason = "numeric_verification_missing"
            else:
                for event in events:
                    if event.event_type == "metric_search_completed" and event.status == "success":
                        metric = event.data.get("tool_result", {}).get("result", {}).get("metric_id")
                        if metric in METRICS:
                            rules.append("metric:" + metric)
        elif status == "STOP_WITH_DATA_GAP":
            required = {"status", "no_numeric_answer", "no_sql"}
            gap = any(e.event_type == "verification_completed" and e.data.get("outcome") == "DATA_GAP_CONFIRMED" for e in events)
            if (not required <= set(scored["checks"]) or not gap
                    or not requests_unsupported_metric(question)
                    or task_summary.get("final_answer", {}).get("key_values") != {}
                    or any(e.event_type in {"sql_generated", "sql_execution_completed"} for e in events)):
                reason = "data_gap_verification_missing"
            else:
                rules.append("unsupported:net_revenue")
        else:
            reason = "failed_runs_are_not_positive_experience"
        if reason or not rules:
            report["skipped"].append({"task_id": task_id, "reason": reason or "no_supported_lesson"})
            continue
        source = {
            "run_dir": str(run_dir), "task_id": task_id, "model": task_summary["model"],
            "status": status, "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
            "score_sha256": hashlib.sha256(score_path.read_bytes()).hexdigest(),
            "question_sha256": digest(question), "checks": scored["checks"],
        }
        for rule in sorted(set(rules)):
            report["changed_sources"] += int(memory.add_evidence(rule, source))
            report["accepted"].append({"task_id": task_id, "rule_id": rule})
    if report["changed_sources"]:
        memory.save(memory_path)
    report["entry_count"] = len(memory.entries)
    report["active_entry_count"] = memory.active_entry_count
    report["snapshot_id"] = memory.snapshot_id
    return report
