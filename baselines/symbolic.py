from __future__ import annotations

import uuid
from collections import Counter, deque

from ..retrieval import LocalRetriever
from ..schemas import (
    EvidenceType,
    Label,
    RetrievalRequest,
    RunResult,
    Task,
)
from ..tools import ProofVerifier


def _result(
    task: Task,
    method: str,
    answer: Label,
    retrieved_ids: set[str],
    tool_calls: int,
    proof_valid: bool,
    coverage_complete: bool,
    action_counts: Counter[str] | None = None,
) -> RunResult:
    correct = answer == task.question.label
    success = correct and proof_valid
    return RunResult(
        run_id=str(uuid.uuid4()),
        task_id=task.task_id,
        method=method,
        answer=answer,
        gold_label=task.question.label,
        status="COMPLETED",
        is_correct=correct,
        task_success=success,
        proof_valid=proof_valid,
        coverage_complete=coverage_complete,
        retrieved_evidence_ids=sorted(retrieved_ids),
        action_counts=dict(action_counts or {}),
        tool_calls=tool_calls,
        steps=max(1, sum((action_counts or {}).values())),
    )


class DirectFullContextBaseline:
    """Deterministic information upper bound used to test the environment."""

    method_name = "direct_full_context_symbolic"

    def __init__(self, verifier: ProofVerifier | None = None) -> None:
        self.verifier = verifier or ProofVerifier()

    def run(self, task: Task) -> RunResult:
        inferred = self.verifier.infer_label(task.world.evidence.values(), task.question)
        proof_valid = inferred.valid and inferred.label != Label.UNKNOWN
        coverage_complete = inferred.label == Label.UNKNOWN
        if coverage_complete:
            proof_valid = True
        return _result(
            task,
            self.method_name,
            inferred.label,
            set(task.world.evidence),
            0,
            proof_valid,
            coverage_complete,
        )


class StandardRAGBaseline:
    method_name = "standard_rag_symbolic"

    def __init__(self, top_k: int = 8, verifier: ProofVerifier | None = None) -> None:
        self.top_k = top_k
        self.verifier = verifier or ProofVerifier()

    def run(self, task: Task) -> RunResult:
        response = LocalRetriever(task.world).search_text(task.question.text, self.top_k)
        ids = {x.evidence_id for x in response.items}
        inferred = self.verifier.infer_label(response.items, task.question)
        proof_valid = inferred.label in {Label.TRUE, Label.FALSE} and inferred.valid
        return _result(
            task,
            self.method_name,
            inferred.label,
            ids,
            1,
            proof_valid,
            False,
            Counter({"RETRIEVE": 1, "STOP": 1}),
        )


class ReActSymbolicBaseline:
    """Iterative retrieval without independent audit, rollback, or stop contract."""

    method_name = "react_symbolic"

    def __init__(self, top_k: int = 20, max_tool_calls: int = 20) -> None:
        self.top_k = top_k
        self.max_tool_calls = max_tool_calls
        self.verifier = ProofVerifier()

    def run(self, task: Task) -> RunResult:
        retriever = LocalRetriever(task.world)
        target = task.question.atom
        queue = deque([target.signature, target.complement().signature])
        seen_requests: set[str] = set()
        evidence = {}
        calls = 0

        while queue and calls < self.max_tool_calls:
            predicate, polarity = queue.popleft()
            for evidence_type in (EvidenceType.FACT, EvidenceType.RULE):
                request = RetrievalRequest(
                    evidence_type,
                    predicate,
                    polarity,
                    query=task.question.text,
                    top_k=self.top_k,
                )
                if request.key in seen_requests:
                    continue
                seen_requests.add(request.key)
                calls += 1
                response = retriever.search(request)
                for item in response.items:
                    evidence[item.evidence_id] = item
                    if item.rule is not None:
                        for premise in item.rule.body:
                            queue.append(premise.signature)

                inferred = self.verifier.infer_label(evidence.values(), task.question)
                if inferred.label in {Label.TRUE, Label.FALSE}:
                    return _result(
                        task,
                        self.method_name,
                        inferred.label,
                        set(evidence),
                        calls,
                        inferred.valid,
                        False,
                        Counter({"RETRIEVE": calls, "STOP": 1}),
                    )

        inferred = self.verifier.infer_label(evidence.values(), task.question)
        # ReAct's unsupported Unknown is intentionally not proof-valid.
        return _result(
            task,
            self.method_name,
            inferred.label,
            set(evidence),
            calls,
            inferred.label in {Label.TRUE, Label.FALSE} and inferred.valid,
            False,
            Counter({"RETRIEVE": calls, "STOP": 1}),
        )


class FixedWorkflowBaseline:
    method_name = "fixed_workflow_symbolic"

    def __init__(self, top_k: int = 50) -> None:
        self.top_k = top_k
        self.verifier = ProofVerifier()

    def run(self, task: Task) -> RunResult:
        retriever = LocalRetriever(task.world)
        evidence = {}
        calls = 0
        for predicate, polarity in sorted(
            self.verifier.required_signatures(task.world, task.question)
        ):
            for evidence_type in (EvidenceType.FACT, EvidenceType.RULE):
                request = RetrievalRequest(
                    evidence_type,
                    predicate,
                    polarity,
                    query=task.question.text,
                    top_k=self.top_k,
                )
                calls += 1
                for item in retriever.search(request).items:
                    evidence[item.evidence_id] = item

        inferred = self.verifier.infer_label(evidence.values(), task.question)
        coverage = inferred.label == Label.UNKNOWN
        proof_valid = inferred.valid and (inferred.label != Label.UNKNOWN or coverage)
        return _result(
            task,
            self.method_name,
            inferred.label,
            set(evidence),
            calls,
            proof_valid,
            coverage,
            Counter({"RETRIEVE": calls, "STOP": 1}),
        )

