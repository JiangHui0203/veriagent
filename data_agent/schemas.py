"""Small shared result protocol for the Olist data tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass
class ToolResult:
    tool: str
    ok: bool
    result: Any = None
    error_type: Optional[str] = None
    error: Optional[str] = None
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
