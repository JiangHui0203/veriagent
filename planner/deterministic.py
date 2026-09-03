from __future__ import annotations

from ..schemas import (
    ActionType,
    AgentPlan,
    AuditReport,
    EvidenceItem,
    EvidenceType,
    PlanStep,
    RetrievalRequest,
    Task,
)
from ..state.models import AgentState


class DeterministicPlanner:
    """Backward-signature planner used by the reproducible MVP."""

    def __init__(self, top_k: int = 20) -> None:
        self.top_k = top_k
        self._step_counter = 0

    def _request_pair(self, predicate: str, polarity: str, query: str) -> list[RetrievalRequest]:
        return [
            RetrievalRequest(EvidenceType.FACT, predicate, polarity, query=query, top_k=self.top_k),
            RetrievalRequest(EvidenceType.RULE, predicate, polarity, query=query, top_k=self.top_k),
        ]

    def _steps_for(self, requests: list[RetrievalRequest]) -> list[PlanStep]:
        steps = []
        for request in requests:
            self._step_counter += 1
            steps.append(
                PlanStep(
                    step_id=f"s{self._step_counter}",
                    subgoal=(
                        f"Retrieve {request.evidence_type.value}s for "
                        f"predicate={request.predicate}, polarity={request.polarity}"
                    ),
                    expected_action=ActionType.RETRIEVE,
                    request=request,
                )
            )
        return steps

    def create(self, task: Task) -> tuple[AgentPlan, list[RetrievalRequest]]:
        target = task.question.atom
        opposite = target.complement()
        requests = self._request_pair(target.predicate, target.polarity, task.question.text)
        requests += self._request_pair(opposite.predicate, opposite.polarity, task.question.text)
        plan = AgentPlan(
            goal=(
                f"Determine whether {target.render()} or its explicit complement is derivable, "
                "then produce a verified proof or completeness certificate"
            ),
            steps=self._steps_for(requests),
        )
        return plan, requests

    def expand_from_evidence(self, state: AgentState, items: list[EvidenceItem]) -> int:
        requests: list[RetrievalRequest] = []
        for item in items:
            if item.rule is None:
                continue
            for premise in item.rule.body:
                requests.extend(
                    self._request_pair(
                        premise.predicate,
                        premise.polarity,
                        premise.render(),
                    )
                )
        added = state.add_requests(requests)
        if added:
            state.plan.steps.extend(self._steps_for(requests))
        return added

    def replan(self, state: AgentState, audit: AuditReport) -> int:
        state.plan.revision += 1
        requests: list[RetrievalRequest] = []
        if audit.verification is not None:
            for predicate, polarity in audit.verification.missing_signatures:
                requests.extend(
                    self._request_pair(
                        predicate,
                        polarity,
                        f"missing predicate {predicate}",
                    )
                )

        if not requests and state.latest_tool_result is not None:
            request_key = state.latest_tool_result.metadata.get("request_key")
            for step in state.plan.steps:
                if step.request is not None and step.request.key == request_key:
                    old = step.request
                    requests.append(
                        RetrievalRequest(
                            old.evidence_type,
                            old.predicate,
                            old.polarity,
                            query=old.query,
                            top_k=max(old.top_k * 2, self.top_k),
                        )
                    )

        added = state.add_requests(requests)
        if added:
            state.plan.steps.extend(self._steps_for(requests))
        return added

