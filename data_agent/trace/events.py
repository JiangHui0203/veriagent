"""Stable event schema for Data Agent execution replay."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional


EVENT_TYPES = frozenset(
    {
        "run_started",
        "question_received",
        "metric_search_started",
        "metric_search_completed",
        "schema_inspection_started",
        "schema_inspection_completed",
        "llm_request_started",
        "llm_request_completed",
        "analysis_generated",
        "sql_generated",
        "tool_call_started",
        "tool_call_completed",
        "sql_execution_completed",
        "cross_check_started",
        "cross_check_completed",
        "data_gap_detected",
        "answer_generated",
        "verification_completed",
        "run_finished",
        "run_failed",
        "audit_started",
        "audit_completed",
        "controller_decision",
        "rollback_started",
        "rollback_completed",
        "sql_repair_generated",
    }
)


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return json_safe(value.to_dict())
    return value


@dataclass(frozen=True)
class TraceEvent:
    event_id: int
    event_type: str
    step: int
    status: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: Optional[str] = None
    role: Optional[str] = None
    tool: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, int) or self.event_id < 1:
            raise ValueError("event_id must be a positive integer")
        if not isinstance(self.event_type, str) or not self.event_type.strip():
            raise ValueError("event_type must be non-empty")
        if not isinstance(self.step, int) or self.step < 0:
            raise ValueError("step must be a non-negative integer")
        if not isinstance(self.status, str) or not self.status.strip():
            raise ValueError("status must be non-empty")
        if not isinstance(self.summary, str):
            raise ValueError("summary must be a string")
        if not isinstance(self.data, dict):
            raise ValueError("data must be an object")

    @classmethod
    def create(
        cls,
        *,
        event_id: int,
        event_type: str,
        step: int,
        status: str,
        summary: str,
        data: Optional[dict[str, Any]] = None,
        role: Optional[str] = None,
        tool: Optional[str] = None,
    ) -> "TraceEvent":
        return cls(
            event_id=event_id,
            event_type=event_type,
            step=step,
            status=status,
            summary=summary,
            data=json_safe(data or {}),
            timestamp=datetime.now(timezone.utc).isoformat(),
            role=role,
            tool=tool,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TraceEvent":
        required = {"event_id", "event_type", "step", "status", "summary", "data"}
        missing = required - set(payload)
        if missing:
            raise ValueError("trace event missing fields: " + ", ".join(sorted(missing)))
        return cls(
            event_id=payload["event_id"],
            event_type=payload["event_type"],
            step=payload["step"],
            status=payload["status"],
            summary=payload["summary"],
            data=payload["data"],
            timestamp=payload.get("timestamp"),
            role=payload.get("role"),
            tool=payload.get("tool"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value is not None}
