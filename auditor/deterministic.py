from __future__ import annotations

from ..schemas import (
    ActionType,
    AuditFinding,
    AuditReport,
    ErrorType,
    Label,
    Severity,
    VerificationResult,
)
from ..state.models import AgentState
from ..tools.proof import ProofVerifier


class DeterministicAuditor:
    def __init__(
        self,
        verifier: ProofVerifier | None = None,
        adaptive_stop: bool = True,
    ) -> None:
        self.verifier = verifier or ProofVerifier()
        self.adaptive_stop = adaptive_stop

    def audit(self, state: AgentState) -> AuditReport:
        latest_tool = state.latest_tool_result
        if latest_tool is not None and not latest_tool.ok:
            return AuditReport(
                is_step_valid=False,
                findings=[
                    AuditFinding(
                        ErrorType.TOOL_FAILURE,
                        Severity.CRITICAL,
                        latest_tool.error_message or "tool execution failed",
                    )
                ],
                recommended_action=ActionType.ROLLBACK,
                confidence=100,
            )

        retrieved_ids = set(state.evidence_ledger)
        target = state.task.question.atom
        required_initial_keys = {
            f"fact:{target.predicate}:{target.polarity}",
            f"rule:{target.predicate}:{target.polarity}",
            f"fact:{target.complement().predicate}:{target.complement().polarity}",
            f"rule:{target.complement().predicate}:{target.complement().polarity}",
        }
        planned_keys = {
            step.request.key
            for step in state.plan.steps
            if step.request is not None
        }
        missing_plan_keys = sorted(required_initial_keys - planned_keys)
        if missing_plan_keys:
            _, missing_signatures = self.verifier.coverage(
                state.task.world,
                state.task.question,
                retrieved_ids,
                state.completed_request_keys,
            )
            return AuditReport(
                is_step_valid=False,
                findings=[
                    AuditFinding(
                        ErrorType.PLAN_MISSING_BRANCH,
                        Severity.CRITICAL,
                        f"initial plan omits required target/complement branches: {missing_plan_keys}",
                    )
                ],
                recommended_action=ActionType.REPLAN,
                confidence=100,
                verification=VerificationResult(
                    valid=False,
                    label=Label.UNKNOWN,
                    reason="plan coverage is incomplete",
                    missing_signatures=missing_signatures,
                ),
            )

        inferred = self.verifier.infer_label(
            state.evidence_ledger.values(),
            state.task.question,
        )

        if state.candidate_answer is not None and state.candidate_answer != inferred.label:
            return AuditReport(
                is_step_valid=False,
                findings=[
                    AuditFinding(
                        ErrorType.ANSWER_DRIFT,
                        Severity.CRITICAL,
                        (
                            f"candidate {state.candidate_answer.value} disagrees with "
                            f"deterministic evidence state {inferred.label.value}"
                        ),
                    )
                ],
                recommended_action=ActionType.ROLLBACK,
                confidence=100,
                verification=inferred,
            )

        if inferred.label in {Label.TRUE, Label.FALSE} and inferred.valid:
            verified = self.verifier.verify_claim(
                state.task.world,
                state.task.question,
                retrieved_ids,
                inferred.label,
            )
            if not self.adaptive_stop:
                complete, missing = self.verifier.coverage(
                    state.task.world,
                    state.task.question,
                    retrieved_ids,
                    state.completed_request_keys,
                )
                if not complete:
                    verified.valid = False
                    verified.reason = "fixed-stop policy waits for complete relevant coverage"
                    verified.missing_signatures = missing
                    action = ActionType.RETRIEVE if state.pending_requests else ActionType.REPLAN
                    return AuditReport(
                        is_step_valid=False,
                        findings=[
                            AuditFinding(
                                ErrorType.RETRIEVAL_MISS,
                                Severity.WARNING,
                                verified.reason,
                            )
                        ],
                        recommended_action=action,
                        confidence=100,
                        verification=verified,
                    )
            return AuditReport(
                is_step_valid=verified.valid,
                findings=[],
                recommended_action=ActionType.STOP,
                confidence=100,
                verification=verified,
            )

        if inferred.label == Label.INVALID:
            return AuditReport(
                is_step_valid=False,
                findings=[
                    AuditFinding(
                        ErrorType.EVIDENCE_CONFLICT,
                        Severity.CRITICAL,
                        inferred.reason,
                    )
                ],
                recommended_action=ActionType.REPLAN,
                confidence=100,
                verification=inferred,
            )

        complete, missing = self.verifier.coverage(
            state.task.world,
            state.task.question,
            retrieved_ids,
            state.completed_request_keys,
        )
        unknown_verification = VerificationResult(
            valid=complete,
            label=Label.UNKNOWN,
            reason=(
                "target and complement are underivivable with complete relevant coverage"
                if complete
                else "unknown is unsafe because relevant evidence coverage is incomplete"
            ),
            coverage_complete=complete,
            missing_signatures=missing,
        )

        if complete:
            return AuditReport(
                is_step_valid=True,
                findings=[],
                recommended_action=ActionType.STOP,
                confidence=100,
                verification=unknown_verification,
            )

        action = ActionType.RETRIEVE if state.pending_requests else ActionType.REPLAN
        return AuditReport(
            is_step_valid=False,
            findings=[
                AuditFinding(
                    ErrorType.RETRIEVAL_MISS,
                    Severity.CRITICAL,
                    f"relevant signatures remain uncovered: {missing}",
                )
            ],
            recommended_action=action,
            confidence=100,
            verification=unknown_verification,
        )
