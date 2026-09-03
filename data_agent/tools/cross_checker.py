"""Independent SQL-result comparison for small numeric aggregates."""

from __future__ import annotations

import numbers
from pathlib import Path
from typing import Any, Optional

from ..schemas import ToolResult
from .sql_executor import execute_sql


def _is_numeric(value: Any) -> bool:
    return isinstance(value, numbers.Number) and not isinstance(value, bool)


def _difference(left: Any, right: Any) -> tuple[float, float]:
    left_value = float(left)
    right_value = float(right)
    absolute = abs(left_value - right_value)
    denominator = max(abs(left_value), abs(right_value))
    relative = absolute / denominator if denominator else 0.0
    return absolute, relative


def _align_numeric_values(
    result_a: dict[str, Any], result_b: dict[str, Any]
) -> tuple[list[tuple[str, Any, Any]], Optional[str]]:
    columns_a = result_a["columns"]
    columns_b = result_b["columns"]
    rows_a = result_a["rows"]
    rows_b = result_b["rows"]
    if len(columns_a) != len(columns_b):
        return [], "different column counts"
    if len(rows_a) == 1 and len(rows_b) == 1:
        if not all(_is_numeric(value) for value in (*rows_a[0], *rows_b[0])):
            return [], "one-row comparison requires numeric columns"
        return [
            (
                columns_a[index] if columns_a[index] == columns_b[index] else f"column_{index}",
                rows_a[0][index],
                rows_b[0][index],
            )
            for index in range(len(columns_a))
        ], None
    if len(rows_a) != len(rows_b) or not rows_a:
        return [], "different row counts"
    if columns_a != columns_b:
        return [], "keyed aggregate columns differ"

    numeric_indices = [
        index
        for index in range(len(columns_a))
        if all(_is_numeric(row[index]) for row in rows_a + rows_b)
    ]
    key_indices = [index for index in range(len(columns_a)) if index not in numeric_indices]
    if not numeric_indices or not key_indices:
        return [], "multi-row comparison requires explicit key and numeric columns"

    def keyed(rows: list[tuple]) -> tuple[dict[tuple, tuple], bool]:
        mapping = {}
        for row in rows:
            key = tuple(row[index] for index in key_indices)
            if key in mapping:
                return {}, False
            mapping[key] = tuple(row[index] for index in numeric_indices)
        return mapping, True

    keyed_a, unique_a = keyed(rows_a)
    keyed_b, unique_b = keyed(rows_b)
    if not unique_a or not unique_b:
        return [], "aggregate keys are not unique"
    if set(keyed_a) != set(keyed_b):
        return [], "aggregate key sets differ"

    aligned = []
    for key in sorted(keyed_a, key=repr):
        for position, column_index in enumerate(numeric_indices):
            aligned.append(
                (
                    f"{key}:{columns_a[column_index]}",
                    keyed_a[key][position],
                    keyed_b[key][position],
                )
            )
    return aligned, None


def cross_check(
    sql_a: str,
    sql_b: str,
    absolute_tolerance: float = 0.01,
    relative_tolerance: float = 1e-6,
    database_path: Optional[Path] = None,
) -> ToolResult:
    if (
        not _is_numeric(absolute_tolerance)
        or not _is_numeric(relative_tolerance)
        or absolute_tolerance < 0
        or relative_tolerance < 0
    ):
        return ToolResult(
            tool="cross_check",
            ok=False,
            error_type="INVALID_ARGUMENT",
            error="tolerances must be non-negative",
            retryable=False,
        )
    result_a = execute_sql(sql_a, max_rows=200, database_path=database_path)
    result_b = execute_sql(sql_b, max_rows=200, database_path=database_path)
    if not result_a.ok or not result_b.ok:
        errors = [result.error for result in (result_a, result_b) if not result.ok]
        return ToolResult(
            tool="cross_check",
            ok=False,
            result={"result_a": result_a.to_dict(), "result_b": result_b.to_dict()},
            error_type="CROSS_CHECK_SQL_ERROR",
            error="; ".join(error for error in errors if error),
            retryable=any(result.retryable for result in (result_a, result_b)),
        )
    if result_a.result["truncated"] or result_b.result["truncated"]:
        return ToolResult(
            tool="cross_check",
            ok=False,
            result={"result_a": result_a.result, "result_b": result_b.result},
            error_type="CROSS_CHECK_TRUNCATED",
            error="cross-check inputs exceeded the 200-row comparison limit",
            retryable=False,
        )

    aligned, shape_error = _align_numeric_values(result_a.result, result_b.result)
    if shape_error is not None:
        return ToolResult(
            tool="cross_check",
            ok=False,
            result={"result_a": result_a.result, "result_b": result_b.result},
            error_type="CROSS_CHECK_SHAPE_MISMATCH",
            error=shape_error,
            retryable=False,
        )

    comparisons = []
    matched = True
    max_absolute = 0.0
    max_relative = 0.0
    for label, value_a, value_b in aligned:
        absolute, relative = _difference(value_a, value_b)
        within_tolerance = absolute <= absolute_tolerance + relative_tolerance * max(
            abs(float(value_a)), abs(float(value_b))
        )
        matched = matched and within_tolerance
        max_absolute = max(max_absolute, absolute)
        max_relative = max(max_relative, relative)
        comparisons.append(
            {
                "value": label,
                "a": value_a,
                "b": value_b,
                "absolute_difference": absolute,
                "relative_difference": relative,
                "matched": within_tolerance,
            }
        )

    return ToolResult(
        tool="cross_check",
        ok=True,
        result={
            "matched": matched,
            "result_a": result_a.result,
            "result_b": result_b.result,
            "absolute_difference": max_absolute,
            "relative_difference": max_relative,
            "tolerance": {
                "absolute": absolute_tolerance,
                "relative": relative_tolerance,
            },
            "comparisons": comparisons,
        },
    )
