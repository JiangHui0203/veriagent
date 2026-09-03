"""Minimal local registry for deterministic Olist Data Agent tools."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Callable, Optional

from ..schemas import ToolResult
from .cross_checker import cross_check
from .join_checker import check_join_cardinality
from .metric_search import search_metric_definition
from .schema_inspector import inspect_schema
from .sql_executor import execute_sql


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    argument_schema: dict[str, Any]
    handler: Callable[..., ToolResult]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "argument_schema": spec.argument_schema,
            }
            for spec in self._tools.values()
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult(
                tool=name,
                ok=False,
                error_type="UNKNOWN_TOOL",
                error=f"tool is not registered: {name}",
                retryable=False,
            )
        if not isinstance(arguments, dict):
            return ToolResult(
                tool=name,
                ok=False,
                error_type="INVALID_ARGUMENT",
                error="tool arguments must be an object",
                retryable=False,
            )
        try:
            result = spec.handler(**arguments)
        except TypeError as error:
            return ToolResult(
                tool=name,
                ok=False,
                error_type="INVALID_ARGUMENT",
                error=str(error),
                retryable=False,
            )
        except Exception as error:
            return ToolResult(
                tool=name,
                ok=False,
                error_type="TOOL_ERROR",
                error=str(error),
                retryable=True,
            )
        if not isinstance(result, ToolResult):
            return ToolResult(
                tool=name,
                ok=False,
                error_type="INVALID_TOOL_RESULT",
                error="registered handler did not return ToolResult",
                retryable=False,
            )
        return result


def create_registry(database_path: Optional[Path] = None) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="search_metric_definition",
            description="Search the frozen Olist V0 metric contract using exact, alias, or simple text matching.",
            argument_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=search_metric_definition,
        )
    )
    registry.register(
        ToolSpec(
            name="inspect_schema",
            description="Return row count and column names/types for one allowed Olist V0 table.",
            argument_schema={
                "type": "object",
                "properties": {"table_name": {"type": "string"}},
                "required": ["table_name"],
                "additionalProperties": False,
            },
            handler=partial(inspect_schema, database_path=database_path),
        )
    )
    registry.register(
        ToolSpec(
            name="execute_sql",
            description="Execute one read-only SELECT/WITH query and return at most max_rows rows.",
            argument_schema={
                "type": "object",
                "properties": {
                    "sql": {"type": "string"},
                    "max_rows": {"type": "integer", "minimum": 1, "default": 200},
                },
                "required": ["sql"],
                "additionalProperties": False,
            },
            handler=partial(execute_sql, database_path=database_path),
        )
    )
    registry.register(
        ToolSpec(
            name="check_join_cardinality",
            description="Measure pairwise join fanout and flag one-to-many or many-to-many multiplication risk.",
            argument_schema={
                "type": "object",
                "properties": {
                    "left_table": {"type": "string"},
                    "right_table": {"type": "string"},
                    "left_key": {"type": "string"},
                    "right_key": {"type": ["string", "null"]},
                },
                "required": ["left_table", "right_table", "left_key"],
                "additionalProperties": False,
            },
            handler=partial(check_join_cardinality, database_path=database_path),
        )
    )
    registry.register(
        ToolSpec(
            name="cross_check",
            description="Compare two independent, small numeric SQL aggregate results within explicit tolerances.",
            argument_schema={
                "type": "object",
                "properties": {
                    "sql_a": {"type": "string"},
                    "sql_b": {"type": "string"},
                    "absolute_tolerance": {"type": "number", "minimum": 0, "default": 0.01},
                    "relative_tolerance": {"type": "number", "minimum": 0, "default": 0.000001},
                },
                "required": ["sql_a", "sql_b"],
                "additionalProperties": False,
            },
            handler=partial(cross_check, database_path=database_path),
        )
    )
    return registry


registry = create_registry()
