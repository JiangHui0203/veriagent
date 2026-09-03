from __future__ import annotations

from collections import Counter
import re
from statistics import mean

from ..schemas import Label, RunResult, Task
from ..tools import ProofVerifier


def _safe_mean(values: list[float]) -> float:
    return mean(values) if values else 0.0


def _mean_or_none(values: list[float]) -> float | None:
    return mean(values) if values else None


def _gold_evidence_sets(task: Task, verifier: ProofVerifier) -> list[set[str]]:
    if task.question.label == Label.UNKNOWN:
        return [verifier.required_evidence_ids(task.world, task.question)]
    annotated: list[set[str]] = []
    for proof in task.question.gold_proofs:
        local_ids = set(re.findall(r"(?:triple|rule)\d+", proof))
        if local_ids:
            annotated.append({f"{task.world.world_id}:{x}" for x in local_ids})
    if annotated:
        return annotated
    full = verifier.infer_label(task.world.evidence.values(), task.question)
    return [{step.evidence_id for step in full.proof}]


def evaluate_results(results: list[RunResult], tasks: dict[str, Task]) -> dict:
    verifier = ProofVerifier()
    retrieval_recalls: list[float] = []
    retrieval_precisions: list[float] = []
    tool_success_flags: list[float] = []
    injected = [r for r in results if r.fault_id]

    for result in results:
        task = tasks[result.task_id]
        gold_sets = _gold_evidence_sets(task, verifier)
        gold_union = set().union(*gold_sets) if gold_sets else set()
        retrieved = set(result.retrieved_evidence_ids)
        retrieval_recalls.append(
            max(
                (len(gold & retrieved) / len(gold) if gold else 1.0)
                for gold in gold_sets
            )
            if gold_sets
            else 1.0
        )
        retrieval_precisions.append(
            len(gold_union & retrieved) / len(retrieved) if retrieved else 0.0
        )
        for event in result.events:
            if event.event_type.value == "tool_returned":
                tool_success_flags.append(1.0 if event.payload.get("result", {}).get("ok") else 0.0)

    action_counts: Counter[str] = Counter()
    for result in results:
        action_counts.update(result.action_counts)

    return {
        "count": len(results),
        "answer_accuracy": _safe_mean([float(r.is_correct) for r in results]),
        "task_success_rate": _safe_mean([float(r.task_success) for r in results]),
        "proof_valid_rate": _safe_mean([float(r.proof_valid) for r in results]),
        "unknown_coverage_rate": _mean_or_none(
            [float(r.coverage_complete) for r in results if r.answer == Label.UNKNOWN]
        ),
        "evidence_recall": _safe_mean(retrieval_recalls),
        "evidence_precision": _safe_mean(retrieval_precisions),
        "tool_call_success_rate": _mean_or_none(tool_success_flags),
        "avg_tool_calls": _safe_mean([float(r.tool_calls) for r in results]),
        "avg_steps": _safe_mean([float(r.steps) for r in results]),
        "avg_prompt_tokens": _safe_mean([float(r.prompt_tokens) for r in results]),
        "avg_completion_tokens": _safe_mean([float(r.completion_tokens) for r in results]),
        "avg_total_tokens": _safe_mean([float(r.total_tokens) for r in results]),
        "avg_latency_ms": _safe_mean([float(r.latency_ms) for r in results]),
        "avg_cost_usd": _safe_mean([float(r.cost_usd) for r in results]),
        "status_counts": dict(Counter(r.status for r in results)),
        "answer_counts": dict(Counter(r.answer.value for r in results)),
        "action_counts": dict(action_counts),
        "fault_count": len(injected),
        "fault_detection_rate": _mean_or_none([float(r.fault_detected) for r in injected]),
        "recovery_attempt_rate": _mean_or_none([float(r.recovery_attempted) for r in injected]),
        "recovery_success_rate": _mean_or_none([float(r.recovery_success) for r in injected]),
    }


def evaluate_by_method(results: list[RunResult], tasks: dict[str, Task]) -> dict[str, dict]:
    methods = sorted({r.method for r in results})
    return {
        method: evaluate_results([r for r in results if r.method == method], tasks)
        for method in methods
    }
