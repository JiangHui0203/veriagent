from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable


def build_controller_samples(
    events: Iterable[dict],
    successful_only: bool = True,
) -> list[dict]:
    """Convert append-only traces into Controller state/action examples.

    Gold labels are never read. When ``successful_only`` is enabled, the
    runtime's strict task-success flag is used only as a trajectory filter.
    """

    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        grouped[str(event["run_id"])].append(event)

    samples: list[dict] = []
    for run_id, run_events in grouped.items():
        run_events.sort(key=lambda row: int(row.get("event_id", 0)))
        final_events = [x for x in run_events if x.get("event_type") == "run_finished"]
        successful = bool(
            final_events and final_events[-1].get("payload", {}).get("task_success")
        )
        if successful_only and not successful:
            continue

        latest_plan = None
        latest_tool_result = None
        latest_audit = None
        action_history: list[dict] = []
        for event in run_events:
            event_type = event.get("event_type")
            payload = event.get("payload", {})
            if event_type == "plan_created":
                latest_plan = payload
            elif event_type == "tool_returned":
                latest_tool_result = payload.get("result")
            elif event_type == "audit_completed":
                latest_audit = payload.get("audit")
            elif event_type == "controller_decision":
                decision = payload.get("decision", {})
                samples.append(
                    {
                        "sample_id": f"{run_id}:{event.get('event_id')}",
                        "run_id": run_id,
                        "task_id": event.get("task_id"),
                        "input": {
                            "plan": latest_plan,
                            "latest_tool_result": latest_tool_result,
                            "latest_audit": latest_audit,
                            "action_history": list(action_history),
                        },
                        "output": {
                            "action": decision.get("action"),
                            "reason": decision.get("reason"),
                            "request": decision.get("request"),
                            "checkpoint_id": decision.get("checkpoint_id"),
                        },
                        "metadata": {
                            "trajectory_success": successful,
                            "source": "veriagent_runtime",
                        },
                    }
                )
                action_history.append(
                    {
                        "action": decision.get("action"),
                        "reason": decision.get("reason"),
                    }
                )
    return samples


def export_controller_sft(
    trace_input: str | Path,
    output: str | Path,
    successful_only: bool = True,
) -> int:
    events = []
    with Path(trace_input).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                events.append(json.loads(line))
    samples = build_controller_samples(events, successful_only=successful_only)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
    return len(samples)

