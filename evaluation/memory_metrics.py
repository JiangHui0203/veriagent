"""Descriptive paired metrics; repeated trials are not independent new tasks."""

from __future__ import annotations

from collections import Counter, defaultdict
import math
from typing import Any


def ratio(numerator: float, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def latency_distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "measured_runs": len(values),
        "mean_ms": ratio(sum(values), len(values)),
        "p50_ms": ordered[math.ceil(len(values) * 0.50) - 1] if values else None,
        "p95_ms": ordered[math.ceil(len(values) * 0.95) - 1] if values else None,
        "percentile_method": "nearest_rank",
    }


def summarize_group(cases: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(case["passed"] for case in cases)
    totals = {key: round(sum(case[key] for case in cases), 2) for key in (
        "llm_requests", "prompt_tokens", "completion_tokens", "llm_latency_ms", "memory_hits",
    )}
    totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
    elapsed = [case["end_to_end_ms"] for case in cases if case["end_to_end_ms"] is not None]
    totals["end_to_end_ms"] = round(sum(elapsed), 2) if len(elapsed) == len(cases) else None
    cost = {key: ratio(totals[key], passed) if totals[key] is not None else None
            for key in ("total_tokens", "llm_requests", "llm_latency_ms", "end_to_end_ms")}
    # INVALID_TABLE is a historically observed schema/analysis failure. Count
    # only answerable tasks that actually reached a successful analysis output.
    at_risk = [case for case in cases if case["answerable"]]
    opportunities = [case for case in at_risk if case["analysis_succeeded"]]
    recurring = [case for case in opportunities if case["invalid_table"]]
    recurrence = {
        "invalid_table": {
            "description": "Invalid table name in analysis/schema inspection (previously payments)",
            "count": len(recurring), "opportunities": len(opportunities),
            "excluded_before_analysis": len(at_risk) - len(opportunities),
            "rate": ratio(len(recurring), len(opportunities)),
            "run_ids": [case["run_id"] for case in recurring],
        },
    }
    by_task = defaultdict(list)
    by_repeat = defaultdict(list)
    for case in cases:
        by_task[case["task_id"]].append(case)
        by_repeat[case["repeat"]].append(case)
    task_stability = [{
        "task_id": task_id, "runs": len(trials),
        "passed": sum(trial["passed"] for trial in trials),
        "pass_rate": ratio(sum(trial["passed"] for trial in trials), len(trials)),
    } for task_id, trials in by_task.items()]
    eligible_stability = [row for row in task_stability if row["runs"] >= 2]
    return {
        "passed": passed, "total": len(cases),
        "false_accepts": sum(case["false_accept"] for case in cases), **totals,
        "cost_per_correct_completion": cost,
        "end_to_end_latency": latency_distribution(elapsed),
        "known_error_recurrence": recurrence,
        "failure_categories": dict(Counter(case["failure_category"] for case in cases if not case["passed"])),
        "memory_task_coverage": ratio(sum(case["memory_hits"] > 0 for case in cases), len(cases)),
        "stability": {
            "repeated_tasks": len(eligible_stability),
            "always_passed": sum(row["passed"] == row["runs"] for row in eligible_stability),
            "always_failed": sum(row["passed"] == 0 for row in eligible_stability),
            "mixed_outcomes": sum(0 < row["passed"] < row["runs"] for row in eligible_stability),
            "tasks": task_stability,
        },
        "by_repeat": [{"repeat": repeat, "passed": sum(case["passed"] for case in trials),
                       "total": len(trials)} for repeat, trials in sorted(by_repeat.items())],
        "tasks": cases,
    }


def paired_outcomes(groups: dict[str, dict[str, Any]]) -> dict[str, Any]:
    indexed = {}
    for name in ("without_memory", "with_memory"):
        cases = groups[name]["tasks"]
        indexed[name] = {(case["task_id"], case["repeat"]): case for case in cases}
        if len(indexed[name]) != len(cases):
            raise ValueError("Duplicate task/repeat pair in evaluation")
    if indexed["without_memory"].keys() != indexed["with_memory"].keys():
        raise ValueError("Both memory conditions must have exactly the same task/repeat pairs")
    pairs = []
    for key, before in indexed["without_memory"].items():
        after = indexed["with_memory"][key]
        outcome = {
            (False, True): "helped", (True, False): "harmed",
            (True, True): "both_passed", (False, False): "both_failed",
        }[(bool(before["passed"]), bool(after["passed"]))]
        pairs.append({"task_id": key[0], "repeat": key[1], "outcome": outcome})
    counts = Counter(pair["outcome"] for pair in pairs)
    net = counts["helped"] - counts["harmed"]
    return {
        "pairs": len(pairs), **{key: counts[key] for key in ("helped", "harmed", "both_passed", "both_failed")},
        "net_helped": net, "net_benefit_rate": ratio(net, len(pairs)),
        "details": pairs,
    }
