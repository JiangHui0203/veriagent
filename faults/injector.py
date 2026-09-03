from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum

from ..schemas import ToolResult


class FaultKind(str, Enum):
    NONE = "none"
    TIMEOUT_ONCE = "timeout_once"
    DROP_TOP_RESULT_ONCE = "drop_top_result_once"
    MALFORMED_RESULT_ONCE = "malformed_result_once"
    PLAN_OMISSION_ONCE = "plan_omission_once"


@dataclass(frozen=True)
class FaultScenario:
    fault_id: str
    kind: FaultKind
    trigger_call: int = 1
    request_key: str | None = None


class FaultInjector:
    def __init__(self, scenario: FaultScenario | None = None) -> None:
        self.scenario = scenario
        self.call_count = 0
        self.triggered = False

    def apply(self, result: ToolResult) -> ToolResult:
        self.call_count += 1
        scenario = self.scenario
        if (
            scenario is None
            or scenario.kind in {FaultKind.NONE, FaultKind.PLAN_OMISSION_ONCE}
            or self.triggered
        ):
            return result
        if self.call_count != scenario.trigger_call:
            return result
        if scenario.request_key and result.metadata.get("request_key") != scenario.request_key:
            return result

        self.triggered = True
        mutated = copy.deepcopy(result)
        mutated.fault_id = scenario.fault_id

        if scenario.kind == FaultKind.TIMEOUT_ONCE:
            mutated.ok = False
            mutated.items = []
            mutated.error_type = "InjectedTimeout"
            mutated.error_message = "injected transient tool timeout"
            mutated.retryable = True
        elif scenario.kind == FaultKind.DROP_TOP_RESULT_ONCE:
            if mutated.items:
                mutated.items = mutated.items[1:]
            mutated.metadata["returned"] = len(mutated.items)
            mutated.metadata["fault"] = "top result removed"
        elif scenario.kind == FaultKind.MALFORMED_RESULT_ONCE:
            mutated.ok = False
            mutated.items = []
            mutated.error_type = "MalformedToolResult"
            mutated.error_message = "injected malformed tool payload"
            mutated.retryable = True
        return mutated

    def apply_plan(self, plan, requests, target_polarity: str):
        """Inject a missing target-complement branch without exposing gold labels."""
        scenario = self.scenario
        if (
            scenario is None
            or scenario.kind != FaultKind.PLAN_OMISSION_ONCE
            or self.triggered
        ):
            return plan, requests
        self.triggered = True
        kept_requests = [r for r in requests if r.polarity == target_polarity]
        kept_keys = {r.key for r in kept_requests}
        plan.steps = [
            step
            for step in plan.steps
            if step.request is None or step.request.key in kept_keys
        ]
        return plan, kept_requests
