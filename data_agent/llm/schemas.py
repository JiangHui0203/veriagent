"""Structured-response schemas for the minimal Data Agent LLM protocol."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class AnalysisSelection:
    analysis: str
    metrics: list[str]
    needed_tables: list[str]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AnalysisSelection":
        analysis = payload.get("analysis")
        metrics = payload.get("metrics")
        needed_tables = payload.get("needed_tables")
        if not isinstance(analysis, str) or not analysis.strip():
            raise ValueError("analysis must be a non-empty string")
        if not isinstance(metrics, list) or not metrics or not all(
            isinstance(item, str) and item.strip() for item in metrics
        ):
            raise ValueError("metrics must be a non-empty list of strings")
        if not isinstance(needed_tables, list) or not all(
            isinstance(item, str) and item.strip() for item in needed_tables
        ):
            raise ValueError("needed_tables must be a list of strings")
        return cls(analysis=analysis, metrics=metrics, needed_tables=needed_tables)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SQLGeneration:
    sql: str
    reason: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SQLGeneration":
        sql = payload.get("sql")
        reason = payload.get("reason")
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("sql must be a non-empty string")
        if not isinstance(reason, str):
            raise ValueError("reason must be a string")
        return cls(sql=sql, reason=reason)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FinalAnswer:
    answer: str
    key_values: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FinalAnswer":
        answer = payload.get("answer")
        key_values = payload.get("key_values", {})
        limitations = payload.get("limitations", [])
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("answer must be a non-empty string")
        if not isinstance(key_values, dict):
            raise ValueError("key_values must be an object")
        if not isinstance(limitations, list) or not all(
            isinstance(item, str) for item in limitations
        ):
            raise ValueError("limitations must be a list of strings")
        return cls(answer=answer, key_values=key_values, limitations=limitations)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LLMGenerationResult:
    ok: bool
    data: Optional[dict[str, Any]] = None
    raw_text: Optional[str] = None
    error_type: Optional[str] = None
    error: Optional[str] = None
    retryable: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    attempts: int = 1
