from __future__ import annotations

from collections import Counter

from ..schemas import ActionDecision, ActionType, AuditReport
from ..state.models import AgentState


class DeterministicController:
    def __init__(
        self,
        max_retries_per_request: int = 2,
        loop_window: int = 6,
        recovery_enabled: bool = True,
    ) -> None:
        self.max_retries_per_request = max_retries_per_request
        self.loop_window = loop_window
        self.recovery_enabled = recovery_enabled

    def decide(self, state: AgentState, audit: AuditReport) -> ActionDecision:
        if audit.is_step_valid and audit.verification is not None and audit.verification.valid:
            return ActionDecision(ActionType.STOP, audit.verification.reason)

        if state.budget.exhausted:
            return ActionDecision(ActionType.STOP, "execution budget exhausted")

        if audit.recommended_action == ActionType.ROLLBACK:
            if not self.recovery_enabled:
                return ActionDecision(ActionType.STOP, "recovery disabled after tool failure")
            latest = state.latest_tool_result
            request_key = latest.metadata.get("request_key", "unknown") if latest else "unknown"
            retries = state.retry_counts.get(request_key, 0)
            if state.last_checkpoint_id and retries < self.max_retries_per_request:
                return ActionDecision(
                    ActionType.ROLLBACK,
                    f"recover from retryable tool failure ({request_key})",
                    checkpoint_id=state.last_checkpoint_id,
                )
            return ActionDecision(ActionType.REPLAN, f"retry limit reached for {request_key}")

        recent = state.action_history[-self.loop_window :]
        if len(recent) >= self.loop_window:
            counts = Counter(recent)
            if counts.most_common(1)[0][1] >= self.loop_window - 1:
                return ActionDecision(ActionType.REPLAN, "repeated action loop detected")

        if audit.recommended_action == ActionType.RETRIEVE:
            if state.pending_requests:
                return ActionDecision(
                    ActionType.RETRIEVE,
                    audit.findings[0].message if audit.findings else "retrieve planned evidence",
                    request=state.pending_requests[0],
                )
            return ActionDecision(ActionType.REPLAN, "retrieval requested but plan queue is empty")

        if audit.recommended_action == ActionType.REPLAN:
            return ActionDecision(ActionType.REPLAN, "audit found an uncovered planning gap")

        return ActionDecision(ActionType.CONTINUE, "continue current verified plan")
