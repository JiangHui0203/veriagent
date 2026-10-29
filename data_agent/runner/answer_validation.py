"""Fail-closed checks for the V0 single-row numeric answer protocol."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


COUNT_METRICS = frozenset({"order_count", "unique_customers"})


def _number(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("Answer values and SQL values must be numbers")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("Invalid numeric value") from error
    if not result.is_finite():
        raise ValueError("Non-finite numeric values are not supported")
    return result


def verify_answer_values(
    final_answer: dict[str, Any],
    sql_result: dict[str, Any],
    metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return canonical SQL values, or reject unsupported/mismatched output.

    This checks result-to-answer fidelity, not SQL business semantics. Multi-row
    answers need a separate mapping contract and are explicitly unsupported.
    """
    rows = sql_result.get("rows", [])
    columns = sql_result.get("columns", [])
    if sql_result.get("truncated") or len(rows) != 1 or not columns:
        raise ValueError("Answer verification requires one complete aggregate row")
    if len(set(columns)) != len(columns) or len(rows[0]) != len(columns):
        raise ValueError("SQL result columns must be unique and match the row")
    expected_keys = {metric["metric_id"] for metric in metrics}
    if set(columns) != expected_keys:
        raise ValueError("SQL columns must use the selected metric IDs as aliases")
    values = final_answer.get("key_values")
    if not isinstance(values, dict) or set(values) != expected_keys:
        raise ValueError("Final key_values must contain exactly the SQL metric columns")

    canonical = {}
    for column, sql_value in zip(columns, rows[0]):
        expected, actual = _number(sql_value), _number(values[column])
        tolerance = Decimal(0) if column in COUNT_METRICS else Decimal("0.01")
        if column in COUNT_METRICS and expected != expected.to_integral_value():
            raise ValueError(f"Count result is not an integer: {column}")
        if abs(actual - expected) > tolerance:
            raise ValueError(f"Final answer disagrees with SQL result: {column}")
        canonical[column] = int(expected) if column in COUNT_METRICS else float(expected)
    return canonical


def render_verified_answer(values: dict[str, Any], metrics: list[dict[str, Any]]) -> str:
    """Render numbers from SQL so free-form model prose cannot change them."""
    names = {metric["metric_id"]: metric["display_name"] for metric in metrics}
    parts = []
    for key, value in values.items():
        formatted = f"{value:,}" if key in COUNT_METRICS else f"{value:,.2f}"
        parts.append(f"{names[key]} = {formatted}")
    return "查询结果：" + "；".join(parts) + "。"
