from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from ..schemas import (
    AgentPlan,
    AuditReport,
    BudgetState,
    EvidenceItem,
    Label,
    ProofStep,
    RetrievalRequest,
    Task,
    ToolResult,
    TraceEvent,
    VerifiedCandidate,
    to_jsonable,
)


@dataclass
class AgentState:
    task: Task
    plan: AgentPlan
    pending_requests: list[RetrievalRequest] = field(default_factory=list)
    completed_request_keys: set[str] = field(default_factory=set)
    evidence_ledger: dict[str, EvidenceItem] = field(default_factory=dict)
    candidate_answer: Optional[Label] = None
    candidate_reasoning: str = ""
    candidate_proof: list[ProofStep] = field(default_factory=list)
    verified_candidate: Optional[VerifiedCandidate] = None
    latest_audit: Optional[AuditReport] = None
    latest_tool_result: Optional[ToolResult] = None
    last_checkpoint_id: Optional[str] = None
    retry_counts: dict[str, int] = field(default_factory=dict)
    action_history: list[str] = field(default_factory=list)
    budget: BudgetState = field(default_factory=BudgetState)
    status: str = "RUNNING"
    events: list[TraceEvent] = field(default_factory=list)
    fault_detected: bool = False
    recovery_attempted: bool = False

    def state_hash(self) -> str:
        payload = {
            "task_id": self.task.task_id,
            "plan_revision": self.plan.revision,
            "pending": [r.key for r in self.pending_requests],
            "completed": sorted(self.completed_request_keys),
            "evidence": sorted(self.evidence_ledger),
            "candidate": self.candidate_answer.value if self.candidate_answer else None,
            "verified": self.verified_candidate.answer.value if self.verified_candidate else None,
            "status": self.status,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def add_requests(self, requests: list[RetrievalRequest]) -> int:
        existing = {r.key for r in self.pending_requests} | self.completed_request_keys
        added = 0
        for request in requests:
            if request.key not in existing:
                self.pending_requests.append(request)
                existing.add(request.key)
                added += 1
        return added

    def serializable_snapshot(self) -> dict:
        return to_jsonable(self)
