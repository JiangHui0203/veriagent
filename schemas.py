from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Optional


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class Label(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"
    INVALID = "invalid"


class ActionType(StrEnum):
    CONTINUE = "CONTINUE"
    RETRIEVE = "RETRIEVE"
    REPLAN = "REPLAN"
    ROLLBACK = "ROLLBACK"
    STOP = "STOP"


class EvidenceType(StrEnum):
    FACT = "fact"
    RULE = "rule"


class ErrorType(StrEnum):
    NONE = "NONE"
    PLAN_MISSING_BRANCH = "PLAN_MISSING_BRANCH"
    RETRIEVAL_MISS = "RETRIEVAL_MISS"
    IRRELEVANT_EVIDENCE = "IRRELEVANT_EVIDENCE"
    UNSUPPORTED_STEP = "UNSUPPORTED_STEP"
    INVALID_RULE_APPLICATION = "INVALID_RULE_APPLICATION"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
    TOOL_FAILURE = "TOOL_FAILURE"
    ANSWER_DRIFT = "ANSWER_DRIFT"
    LOOP_DETECTED = "LOOP_DETECTED"
    STOP_UNSAFE = "STOP_UNSAFE"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class EventType(StrEnum):
    RUN_STARTED = "run_started"
    PLAN_CREATED = "plan_created"
    TOOL_REQUESTED = "tool_requested"
    TOOL_RETURNED = "tool_returned"
    EVIDENCE_ADDED = "evidence_added"
    CANDIDATE_UPDATED = "candidate_updated"
    AUDIT_COMPLETED = "audit_completed"
    CONTROLLER_DECISION = "controller_decision"
    CHECKPOINT_CREATED = "checkpoint_created"
    ROLLBACK_COMPLETED = "rollback_completed"
    RUN_FINISHED = "run_finished"


@dataclass(frozen=True, order=True)
class Atom:
    """Canonical ProofWriter atom: subject, relation, object, polarity."""

    subject: str
    relation: str
    object: str
    polarity: str = "+"

    def __post_init__(self) -> None:
        if self.polarity not in {"+", "-"}:
            raise ValueError(f"invalid polarity: {self.polarity}")

    @property
    def signature(self) -> tuple[str, str]:
        return (self.predicate, self.polarity)

    @property
    def predicate(self) -> str:
        """Logical predicate used for retrieval (e.g. red or chases)."""
        return self.object if self.relation == "is" else self.relation

    @property
    def key(self) -> str:
        return "|".join((self.subject, self.relation, self.object, self.polarity))

    def complement(self) -> "Atom":
        return Atom(
            subject=self.subject,
            relation=self.relation,
            object=self.object,
            polarity="-" if self.polarity == "+" else "+",
        )

    def render(self) -> str:
        negation = "not " if self.polarity == "-" else ""
        if self.relation == "is":
            return f"{self.subject} is {negation}{self.object}"
        return f"{self.subject} does {negation}{self.relation} {self.object}"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    body: tuple[Atom, ...]
    head: Atom
    text: str = ""


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    world_id: str
    evidence_type: EvidenceType
    text: str
    atom: Optional[Atom] = None
    rule: Optional[Rule] = None

    def __post_init__(self) -> None:
        if self.evidence_type == EvidenceType.FACT and self.atom is None:
            raise ValueError("fact evidence requires atom")
        if self.evidence_type == EvidenceType.RULE and self.rule is None:
            raise ValueError("rule evidence requires rule")

    @property
    def signature(self) -> tuple[str, str]:
        if self.atom is not None:
            return self.atom.signature
        assert self.rule is not None
        return self.rule.head.signature


@dataclass(frozen=True)
class Question:
    question_id: str
    text: str
    atom: Atom
    label: Label
    depth: Optional[int] = None
    strategy: Optional[str] = None
    gold_proofs: tuple[str, ...] = ()


@dataclass
class World:
    world_id: str
    theory: str
    evidence: dict[str, EvidenceItem]
    questions: dict[str, Question]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def facts(self) -> list[EvidenceItem]:
        return [x for x in self.evidence.values() if x.evidence_type == EvidenceType.FACT]

    @property
    def rules(self) -> list[EvidenceItem]:
        return [x for x in self.evidence.values() if x.evidence_type == EvidenceType.RULE]


@dataclass(frozen=True)
class Task:
    task_id: str
    world: World
    question: Question


@dataclass(frozen=True)
class RetrievalRequest:
    evidence_type: EvidenceType
    predicate: str
    polarity: str
    query: str = ""
    top_k: int = 20

    @property
    def key(self) -> str:
        return f"{self.evidence_type.value}:{self.predicate}:{self.polarity}"


@dataclass
class ToolResult:
    call_id: str
    tool: str
    ok: bool
    items: list[EvidenceItem] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    latency_ms: float = 0.0
    retryable: bool = False
    fault_id: Optional[str] = None


@dataclass
class PlanStep:
    step_id: str
    subgoal: str
    expected_action: ActionType
    request: Optional[RetrievalRequest] = None
    status: str = "pending"
    attempts: int = 0


@dataclass
class AgentPlan:
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    revision: int = 0


@dataclass(frozen=True)
class ProofStep:
    step_id: str
    conclusion: Atom
    evidence_id: str
    premise_step_ids: tuple[str, ...] = ()


@dataclass
class VerificationResult:
    valid: bool
    label: Label
    reason: str
    proof: list[ProofStep] = field(default_factory=list)
    coverage_complete: bool = False
    missing_signatures: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class VerifiedCandidate:
    answer: Label
    proof: list[ProofStep]
    evidence_ids: set[str]
    verification_reason: str
    coverage_complete: bool = False


@dataclass
class CandidateProposal:
    answer: Label
    reasoning: str
    proof: list[ProofStep] = field(default_factory=list)
    evidence_ids: set[str] = field(default_factory=set)


@dataclass
class AuditFinding:
    error_type: ErrorType
    severity: Severity
    message: str
    affected_step_ids: list[str] = field(default_factory=list)


@dataclass
class AuditReport:
    is_step_valid: bool
    findings: list[AuditFinding]
    recommended_action: ActionType
    confidence: int
    verification: Optional[VerificationResult] = None

    @property
    def primary_error(self) -> ErrorType:
        return self.findings[0].error_type if self.findings else ErrorType.NONE


@dataclass
class ActionDecision:
    action: ActionType
    reason: str
    request: Optional[RetrievalRequest] = None
    checkpoint_id: Optional[str] = None


@dataclass
class BudgetState:
    max_steps: int = 40
    max_tool_calls: int = 30
    steps_used: int = 0
    tool_calls_used: int = 0

    @property
    def exhausted(self) -> bool:
        return self.steps_used >= self.max_steps or self.tool_calls_used >= self.max_tool_calls


@dataclass
class TraceEvent:
    run_id: str
    task_id: str
    event_id: int
    event_type: EventType
    payload: dict[str, Any]
    state_hash_before: Optional[str] = None
    state_hash_after: Optional[str] = None


@dataclass
class RunResult:
    run_id: str
    task_id: str
    method: str
    answer: Label
    gold_label: Label
    status: str
    is_correct: bool
    task_success: bool
    proof_valid: bool
    coverage_complete: bool
    retrieved_evidence_ids: list[str]
    action_counts: dict[str, int]
    tool_calls: int
    steps: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    fault_id: Optional[str] = None
    fault_detected: bool = False
    recovery_attempted: bool = False
    recovery_success: bool = False
    events: list[TraceEvent] = field(default_factory=list)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, set):
        return sorted(to_jsonable(x) for x in value)
    if isinstance(value, tuple):
        return [to_jsonable(x) for x in value]
    if isinstance(value, list):
        return [to_jsonable(x) for x in value]
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    return value


def normalize_label(value: Any) -> Label:
    if value is True:
        return Label.TRUE
    if value is False:
        return Label.FALSE
    text = str(value).strip().lower()
    return {
        "true": Label.TRUE,
        "false": Label.FALSE,
        "unknown": Label.UNKNOWN,
    }.get(text, Label.INVALID)


def evidence_ids(items: Iterable[EvidenceItem]) -> set[str]:
    return {item.evidence_id for item in items}
