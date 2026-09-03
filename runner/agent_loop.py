from __future__ import annotations

import uuid
from collections import Counter

from ..answerer import DeterministicAnswerer
from ..auditor import DeterministicAuditor
from ..controller import DeterministicController
from ..faults import FaultInjector
from ..planner import DeterministicPlanner
from ..retrieval import LocalRetriever
from ..schemas import (
    ActionType,
    BudgetState,
    EventType,
    Label,
    RunResult,
    Task,
    TraceEvent,
    VerifiedCandidate,
    to_jsonable,
)
from ..state import AgentState, CheckpointStore, JsonlTraceStore
from ..tools import ProofVerifier, RetrievalTool


class VeriAgentRunner:
    method_name = "veriagent"

    def __init__(
        self,
        planner: DeterministicPlanner | None = None,
        auditor: DeterministicAuditor | None = None,
        controller: DeterministicController | None = None,
        verifier: ProofVerifier | None = None,
        answerer: DeterministicAnswerer | None = None,
        fault_injector: FaultInjector | None = None,
        trace_store: JsonlTraceStore | None = None,
        max_steps: int = 40,
        max_tool_calls: int = 30,
        method_name: str = "veriagent",
    ) -> None:
        self.verifier = verifier or ProofVerifier()
        self.answerer = answerer or DeterministicAnswerer(self.verifier)
        self.planner = planner or DeterministicPlanner()
        self.auditor = auditor or DeterministicAuditor(self.verifier)
        self.controller = controller or DeterministicController()
        self.fault_injector = fault_injector or FaultInjector()
        self.trace_store = trace_store
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.method_name = method_name

    def _record(
        self,
        state: AgentState,
        run_id: str,
        event_type: EventType,
        payload: dict,
        state_hash_before: str | None = None,
    ) -> None:
        event = TraceEvent(
            run_id=run_id,
            task_id=state.task.task_id,
            event_id=len(state.events) + 1,
            event_type=event_type,
            payload=to_jsonable(payload),
            state_hash_before=state_hash_before,
            state_hash_after=state.state_hash(),
        )
        state.events.append(event)
        if self.trace_store is not None:
            self.trace_store.append(event)

    def _apply_verification(self, state: AgentState) -> None:
        audit = state.latest_audit
        if audit is None or audit.verification is None:
            return
        verification = audit.verification
        if not audit.is_step_valid or not verification.valid:
            return
        evidence_ids = {step.evidence_id for step in verification.proof}
        if verification.label == Label.UNKNOWN:
            evidence_ids = set(state.evidence_ledger)
        state.verified_candidate = VerifiedCandidate(
            answer=verification.label,
            proof=verification.proof,
            evidence_ids=evidence_ids,
            verification_reason=verification.reason,
            coverage_complete=verification.coverage_complete,
        )

    def run(self, task: Task) -> RunResult:
        run_id = str(uuid.uuid4())
        plan, initial_requests = self.planner.create(task)
        plan, initial_requests = self.fault_injector.apply_plan(
            plan,
            initial_requests,
            task.question.atom.polarity,
        )
        state = AgentState(
            task=task,
            plan=plan,
            pending_requests=list(initial_requests),
            budget=BudgetState(self.max_steps, self.max_tool_calls),
        )
        checkpoints = CheckpointStore()
        retrieval_tool = RetrievalTool(LocalRetriever(task.world))

        self._record(state, run_id, EventType.RUN_STARTED, {"method": self.method_name})
        self._record(state, run_id, EventType.PLAN_CREATED, {"plan": plan})

        while state.status == "RUNNING":
            proposal = self.answerer.propose(state)
            if (
                state.candidate_answer != proposal.answer
                or state.candidate_proof != proposal.proof
            ):
                state.candidate_answer = proposal.answer
                state.candidate_reasoning = proposal.reasoning
                state.candidate_proof = proposal.proof
                self._record(
                    state,
                    run_id,
                    EventType.CANDIDATE_UPDATED,
                    {"proposal": proposal},
                )
            audit = self.auditor.audit(state)
            state.latest_audit = audit
            scenario = self.fault_injector.scenario
            if (
                scenario is not None
                and self.fault_injector.triggered
                and audit.primary_error.value == "PLAN_MISSING_BRANCH"
            ):
                state.fault_detected = True
            self._apply_verification(state)
            self._record(state, run_id, EventType.AUDIT_COMPLETED, {"audit": audit})

            decision = self.controller.decide(state, audit)
            action_key = decision.action.value
            if decision.request is not None:
                action_key += f":{decision.request.key}"
            state.action_history.append(action_key)
            state.budget.steps_used += 1
            self._record(
                state,
                run_id,
                EventType.CONTROLLER_DECISION,
                {"decision": decision},
            )

            if decision.action == ActionType.STOP:
                if state.verified_candidate is not None:
                    state.status = "COMPLETED"
                elif state.budget.exhausted:
                    state.status = "BUDGET_EXHAUSTED"
                else:
                    state.status = "STOPPED_UNVERIFIED"
                break

            if decision.action == ActionType.RETRIEVE:
                request = decision.request
                if request is None:
                    state.status = "INVALID_CONTROLLER_ACTION"
                    break

                before = state.state_hash()
                checkpoint_id = checkpoints.create(state, f"before retrieval {request.key}")
                state.last_checkpoint_id = checkpoint_id
                self._record(
                    state,
                    run_id,
                    EventType.CHECKPOINT_CREATED,
                    {"checkpoint_id": checkpoint_id, "request_key": request.key},
                    before,
                )

                for index, pending in enumerate(state.pending_requests):
                    if pending.key == request.key:
                        state.pending_requests.pop(index)
                        break

                self._record(
                    state,
                    run_id,
                    EventType.TOOL_REQUESTED,
                    {"request": request},
                )
                result = retrieval_tool.execute(request)
                result = self.fault_injector.apply(result)
                state.budget.tool_calls_used += 1
                state.latest_tool_result = result
                if result.fault_id:
                    state.fault_detected = True
                self._record(state, run_id, EventType.TOOL_RETURNED, {"result": result})

                if result.ok:
                    added_ids = []
                    for item in result.items:
                        if item.evidence_id not in state.evidence_ledger:
                            state.evidence_ledger[item.evidence_id] = item
                            added_ids.append(item.evidence_id)
                    if added_ids:
                        self._record(
                            state,
                            run_id,
                            EventType.EVIDENCE_ADDED,
                            {"evidence_ids": added_ids},
                        )
                    self.planner.expand_from_evidence(state, result.items)

                    total = int(result.metadata.get("total_matches", len(result.items)))
                    returned = int(result.metadata.get("returned", len(result.items)))
                    if returned >= total:
                        state.completed_request_keys.add(request.key)
                    else:
                        # An incomplete page or injected dropout is observable as
                        # missing coverage and is retried with a larger budget.
                        state.add_requests(
                            [
                                type(request)(
                                    request.evidence_type,
                                    request.predicate,
                                    request.polarity,
                                    query=request.query,
                                    top_k=max(request.top_k * 2, total),
                                )
                            ]
                        )
                        state.recovery_attempted = True
                continue

            if decision.action == ActionType.ROLLBACK:
                failed = state.latest_tool_result
                checkpoint_id = decision.checkpoint_id
                if not checkpoints.has(checkpoint_id):
                    state.status = "ROLLBACK_FAILED"
                    break
                request_key = (
                    str(failed.metadata.get("request_key", "unknown"))
                    if failed is not None
                    else "unknown"
                )
                current_events = state.events
                fault_detected = state.fault_detected
                steps_used = state.budget.steps_used
                tool_calls_used = state.budget.tool_calls_used
                action_history = list(state.action_history)
                retry_counts = dict(state.retry_counts)
                state = checkpoints.restore(str(checkpoint_id), current_events)
                state.budget.steps_used = steps_used
                state.budget.tool_calls_used = tool_calls_used
                state.action_history = action_history
                state.retry_counts = retry_counts
                state.retry_counts[request_key] = state.retry_counts.get(request_key, 0) + 1
                state.recovery_attempted = True
                state.fault_detected = fault_detected
                state.latest_tool_result = None
                self._record(
                    state,
                    run_id,
                    EventType.ROLLBACK_COMPLETED,
                    {"checkpoint_id": checkpoint_id, "request_key": request_key},
                )
                continue

            if decision.action == ActionType.REPLAN:
                if state.fault_detected:
                    state.recovery_attempted = True
                added = self.planner.replan(state, audit)
                state.latest_tool_result = None
                self._record(
                    state,
                    run_id,
                    EventType.PLAN_CREATED,
                    {"revision": state.plan.revision, "new_requests": added},
                )
                if added == 0 and not state.pending_requests:
                    state.status = "REPLAN_EXHAUSTED"
                    break
                continue

            # CONTINUE is a no-op in the deterministic runtime: the next audit
            # observes the same verified state. It remains part of the public
            # action protocol for future LLM reasoning steps.
            state.latest_tool_result = None

        answer = (
            state.verified_candidate.answer
            if state.verified_candidate is not None
            else state.candidate_answer or Label.INVALID
        )
        proof_valid = False
        coverage_complete = False
        if state.verified_candidate is not None:
            if answer in {Label.TRUE, Label.FALSE}:
                expected = task.question.atom if answer == Label.TRUE else task.question.atom.complement()
                proof_valid, _ = self.verifier.validate_proof_steps(
                    task.world,
                    state.verified_candidate.proof,
                    expected,
                )
            elif answer == Label.UNKNOWN:
                proof_valid = state.verified_candidate.coverage_complete
                coverage_complete = state.verified_candidate.coverage_complete

        is_correct = answer == task.question.label
        task_success = is_correct and proof_valid and state.status == "COMPLETED"
        scenario = self.fault_injector.scenario
        fault_id = scenario.fault_id if scenario and self.fault_injector.triggered else None
        recovery_success = bool(
            fault_id and state.fault_detected and state.recovery_attempted and task_success
        )
        action_counts = Counter(x.split(":", 1)[0] for x in state.action_history)
        tool_latency_ms = sum(
            float(event.payload.get("result", {}).get("latency_ms", 0.0) or 0.0)
            for event in state.events
            if event.event_type == EventType.TOOL_RETURNED
        )

        self._record(
            state,
            run_id,
            EventType.RUN_FINISHED,
            {
                "status": state.status,
                "answer": answer,
                "task_success": task_success,
            },
        )
        return RunResult(
            run_id=run_id,
            task_id=task.task_id,
            method=self.method_name,
            answer=answer,
            gold_label=task.question.label,
            status=state.status,
            is_correct=is_correct,
            task_success=task_success,
            proof_valid=proof_valid,
            coverage_complete=coverage_complete,
            retrieved_evidence_ids=sorted(state.evidence_ledger),
            action_counts=dict(action_counts),
            tool_calls=state.budget.tool_calls_used,
            steps=state.budget.steps_used,
            latency_ms=tool_latency_ms,
            fault_id=fault_id,
            fault_detected=state.fault_detected,
            recovery_attempted=state.recovery_attempted,
            recovery_success=recovery_success,
            events=state.events,
        )
