"""Build isolated candidate memory from explicitly reviewed development pairs.

This verifies review provenance and replays both SQL statements. Root-cause
review and lesson authoring remain manual; a failed output is never a positive
example and held-out runs are not accepted as sources.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..data_agent.memory.store import ExperienceMemory, REVIEWED_RULES, digest
from ..data_agent.tools.sql_executor import execute_sql
from ..data_agent.trace import load_trace
from .challenge_catalog import file_sha256, load_tasks, write_json
from .challenge_reference import equal_values
from .memory_scoring import _matches


def _values(result) -> dict:
    if not result.ok or result.result["truncated"] or len(result.result["rows"]) != 1:
        return {}
    return dict(zip(result.result["columns"], result.result["rows"][0]))


def _evidence(run_dir, manifest, task, reference, reviewed, *, positive):
    run_id = reviewed["run_id"]
    if not isinstance(run_id, str) or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("Invalid reviewed run ID")
    runs = [r for r in manifest["runs"] if r["run_id"] == run_id and r["group"] == "without_memory"]
    if len(runs) != 1 or runs[0]["task_id"] != task["task_id"]:
        raise ValueError("Reviewed run is missing from development manifest")
    path = run_dir / "without_memory" / run_id
    trace_path = path / "trace.jsonl"
    if file_sha256(trace_path) != reviewed["trace_sha256"]:
        raise ValueError("Reviewed trace changed")
    events = load_trace(trace_path)
    summary = json.loads((path / "run_summary.json").read_text())
    questions = [e.data["question"] for e in events if e.event_type == "question_received"]
    sql = [e.data["sql"] for e in events if e.event_type == "sql_generated"]
    terminal_ok = bool(events) and (
        events[-1].event_type == "run_failed" if summary["status"] == "FAILED"
        else events[-1].event_type == "run_finished" and events[-1].data.get("status") == summary["status"]
    )
    if (questions != [task["question"]] or sql != [reviewed["sql"]]
            or summary["task_id"] != task["task_id"] or summary["run_id"] != run_id
            or summary["model"] != manifest["model"] or summary["status"] != runs[0]["status"]
            or summary["event_count"] != len(events)
            or not terminal_ok or events[0].event_type != "run_started"
            or events[0].data.get("memory_enabled") is not False):
        raise ValueError("Reviewed runtime evidence is inconsistent")
    if positive:
        selected = {e.data["tool_result"]["result"]["metric_id"] for e in events
                    if e.event_type == "metric_search_completed" and e.status == "success"}
        executed = [e.data["tool_result"] for e in events if e.event_type == "sql_execution_completed"]
        valid = any(e.event_type == "answer_validation_completed" and e.data.get("matched") is True for e in events)
        verified = any(e.event_type == "verification_completed" and e.data.get("final_values_match") is True for e in events)
        if (summary["status"] != "SUCCESS" or selected != set(reference["metrics"])
                or not valid or not verified or any(e.event_type == "run_failed" for e in events)
                or not _matches(summary.get("final_answer", {}).get("key_values", {}), reference["values"])
                or len(executed) != 1 or not executed[0]["ok"]
                or executed[0]["result"]["truncated"] or len(executed[0]["result"]["rows"]) != 1
                or not _matches(dict(zip(executed[0]["result"]["columns"], executed[0]["result"]["rows"][0])), reference["values"])):
            raise ValueError("Successful counterpart lacks verified correct runtime evidence")
    return summary, events


def build_failure_memory(*, run_dir: Path, plan_path: Path, database: Path, output: Path) -> dict:
    """Validate every selected pair before writing a new, separate memory file."""
    run_dir, output = Path(run_dir).resolve(), Path(output).resolve()
    report_path = output.with_suffix(".promotion.json")
    if output.exists() or report_path.exists():
        raise ValueError("Candidate output must be new; existing memory is never overwritten")
    manifest_path = run_dir / "evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if (manifest["status"] != "COMPLETED" or manifest["split"] != "dev"
            or manifest["mode"] != "baseline" or manifest["conditions"] != ["without_memory"]
            or manifest["learning_during_evaluation"] is not False):
        raise ValueError("Only completed development baselines may supply candidate memory")
    plan = json.loads(plan_path.read_text())
    if plan.get("status") != "APPROVED_FOR_CANDIDATE" or not plan.get("reviews"):
        raise ValueError("An explicit reviewed promotion plan is required")
    catalog = Path(manifest["catalog"])
    catalog_manifest, tasks = load_tasks(catalog, "dev", database)
    if (catalog_manifest["catalog_id"] != manifest["catalog_id"]
            or plan["catalog_id"] != manifest["catalog_id"] or manifest["tasks"] != tasks):
        raise ValueError("Reviewed catalog or questions differ from frozen development data")
    for name in ("references.json", "specifications.json"):
        if file_sha256(catalog / name) != catalog_manifest["files"][name]:
            raise ValueError("Frozen reference files changed")
    review_path = run_dir / "failure_analysis.json"
    if file_sha256(review_path) != plan["review_sha256"]:
        raise ValueError("Manual review changed after approval")
    review = json.loads(review_path.read_text())
    if review["status"] != "REVIEWED_CANDIDATES_NOT_PROMOTED" or review["source_split"] != "dev":
        raise ValueError("Manual development review is required")
    references = json.loads((catalog / "references.json").read_text())
    tasks = {task["task_id"]: task for task in tasks}
    findings = {row["task_id"]: row for row in review["findings"]}
    memory = ExperienceMemory()
    accepted = []
    for selection in plan["reviews"]:
        task_id, rule_id = selection["task_id"], selection["rule_id"]
        if task_id not in tasks or rule_id not in REVIEWED_RULES:
            raise ValueError("Review must select a development task and an approved failure rule")
        finding = findings[task_id]
        pair = {row["run_id"]: row for row in finding["paired_evidence"]}
        bad, good = pair[selection["failed_run_id"]], pair[selection["successful_run_id"]]
        if (bad["run_id"] == good["run_id"] or bad["matches_independent_reference"] is not False
                or good["matches_independent_reference"] is not True or not good["replay_ok"]):
            raise ValueError("Review needs a failed and a successful counterpart")
        reference = references[task_id]
        if (reference["split"] != "dev" or reference["question_sha256"] != digest(tasks[task_id]["question"])
                or not all(reference["checks"].values())
                or not equal_values(reference["values"], reference["python_values"])):
            raise ValueError("Independent development reference is invalid")
        ref_replay = execute_sql(reference["reference_sql"], database_path=database)
        if not _matches(_values(ref_replay), reference["values"]):
            raise ValueError("Frozen reference replay differs")
        bad_summary, bad_events = _evidence(run_dir, manifest, tasks[task_id], reference, bad, positive=False)
        _evidence(run_dir, manifest, tasks[task_id], reference, good, positive=True)
        bad_replay = execute_sql(bad["sql"], database_path=database)
        good_replay = execute_sql(good["sql"], database_path=database)
        if _matches(_values(bad_replay), reference["values"]) or not _matches(_values(good_replay), reference["values"]):
            raise ValueError("SQL replay does not confirm a failed/successful contrast")
        if bad_replay.ok != bad["replay_ok"] or bad_replay.error_type != bad["error_type"]:
            raise ValueError("Reviewed failure does not reproduce")
        if rule_id == "reviewed:child_row_eligibility":
            if not bad_replay.ok or bad_summary["status"] != "SUCCESS":
                raise ValueError("Child-count review requires a reproduced semantic false accept")
        else:
            if (bad_replay.ok or bad_summary["status"] != "FAILED"
                    or 'Binder Error: Referenced table' not in (bad_replay.error or "")
                    or not any(e.event_type == "run_failed" and e.data.get("error_type") == "SQL_EXECUTION_ERROR" for e in bad_events)):
                raise ValueError("Alias review requires a reproduced missing-alias binder error")
        source = {"kind": "manually_reviewed_failure_contrast", "source_split": "dev",
                  "run_dir": str(run_dir), "catalog_id": manifest["catalog_id"],
                  "group_key": reference["group_key"], "task_id": task_id, "model": manifest["model"],
                  "question_sha256": reference["question_sha256"], "trace_sha256": good["trace_sha256"],
                  "failed_trace_sha256": bad["trace_sha256"], "failed_run_id": bad["run_id"],
                  "successful_run_id": good["run_id"], "review_sha256": plan["review_sha256"],
                  "plan_sha256": file_sha256(plan_path), "manifest_sha256": file_sha256(manifest_path),
                  "checks": {"manual_review_bound_to_traces": True, "failed_sql_reproduced": True,
                             "successful_sql_matches_independent_reference": True,
                             "successful_runtime_verified": True}}
        if not memory.add_evidence(rule_id, source):
            raise ValueError("Duplicate reviewed evidence")
        accepted.append({"task_id": task_id, "rule_id": rule_id, "failed_run_id": bad["run_id"],
                         "successful_run_id": good["run_id"]})
    report = {"status": "FROZEN_CANDIDATE", "memory_path": str(output), "snapshot_id": memory.snapshot_id,
              "active_entry_count": memory.active_entry_count, "accepted": accepted,
              "source_split": "dev", "catalog_id": manifest["catalog_id"],
              "retrieval_policy": "SQL stage only; child item/payment row-count wording; both approved rules; no historical answers",
              "default_memory_modified": False, "automatic_root_cause_learning": False,
              "evaluation_plan": {"split": "test", "mode": "paired", "repeats": 3,
                                  "primary": "paired correct completions", "secondary": ["false accepts", "tokens per correct completion", "latency", "stability"],
                                  "no_candidate_changes_after_test": True}}
    memory.save(output)
    write_json(report_path, report)
    return report
