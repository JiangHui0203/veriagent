#!/usr/bin/env python3
"""Validate the Olist Data Agent V0 runtime tasks and Gold packages."""

from __future__ import annotations

import argparse
import json
import numbers
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from olist_common import DATABASE_NAME, DEFAULT_DATA_DIR


EXPECTED_TASK_IDS = [f"DA{index:02d}" for index in range(1, 11)]
RUNTIME_FIELDS = {"task_id", "question", "task_type"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise AssertionError(f"{path.name}:{line_number} is invalid JSON") from error
    return records


def query(connection: duckdb.DuckDBPyConnection, sql_path: Path) -> list[dict[str, Any]]:
    sql = sql_path.read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    ).lstrip()
    if not executable.upper().startswith(("SELECT", "WITH")):
        raise AssertionError(f"{sql_path.name} is not a read-only SELECT/WITH query")
    if re.search(r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|COPY|ATTACH)\b", executable, re.I):
        raise AssertionError(f"{sql_path.name} contains a forbidden modifying statement")

    cursor = connection.execute(sql)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def tolerance(gold: dict[str, Any]) -> float:
    values = gold.get("numeric_tolerance", {})
    return max(
        float(values.get("money_absolute", 0.01)),
        float(values.get("percentage_point_absolute", 0.01)),
    )


def compare(actual: Any, expected: Any, allowed_error: float, label: str) -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise AssertionError(f"{label}: expected an object")
        for key, value in expected.items():
            if key not in actual:
                raise AssertionError(f"{label}: missing key {key}")
            compare(actual[key], value, allowed_error, f"{label}.{key}")
        return
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise AssertionError(f"{label}: list length mismatch")
        for index, (actual_item, expected_item) in enumerate(zip(actual, expected)):
            compare(actual_item, expected_item, allowed_error, f"{label}[{index}]")
        return
    if isinstance(expected, numbers.Number) and not isinstance(expected, bool):
        if actual is None or abs(float(actual) - float(expected)) > allowed_error:
            raise AssertionError(f"{label}: got {actual}, expected {expected}")
        return
    if actual != expected:
        raise AssertionError(f"{label}: got {actual!r}, expected {expected!r}")


def canonical_values(task_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if task_id in {"DA01", "DA02", "DA03", "DA04", "DA08", "DA09"}:
        if len(rows) != 1:
            raise AssertionError(f"{task_id}: canonical SQL must return one row")
        return rows[0]
    if task_id == "DA05":
        if len(rows) != 5:
            raise AssertionError("DA05: canonical SQL must return five contributors")
        first = rows[0]
        return {
            "july_total": first["july_total"],
            "august_total": first["august_total"],
            "total_delta": first["total_delta"],
            "total_pct_change": first["total_pct_change"],
            "top5_share_of_net_decline": first["top5_share_of_net_decline"],
            "top5_share_of_gross_category_declines": first[
                "top5_share_of_gross_category_declines"
            ],
            "top5_negative_contributors": [
                {
                    "rank": row["decline_rank"],
                    "category": row["category"],
                    "july": row["july_item_sales_value"],
                    "august": row["august_item_sales_value"],
                    "delta": row["delta"],
                }
                for row in rows
            ],
        }
    if task_id == "DA06":
        if len(rows) != 5:
            raise AssertionError("DA06: canonical SQL must return five contributors")
        first = rows[0]
        return {
            "october_total": first["october_total"],
            "november_total": first["november_total"],
            "total_increase": first["total_increase"],
            "top5_share_of_total_increase": first["top5_share_of_total_increase"],
            "top5_positive_contributors": [
                {
                    "rank": row["growth_rank"],
                    "state": row["customer_state"],
                    "october": row["october_order_count"],
                    "november": row["november_order_count"],
                    "delta": row["delta"],
                }
                for row in rows
            ],
        }
    if task_id == "DA07":
        if len(rows) != 3:
            raise AssertionError("DA07: canonical SQL must return three categories")
        return {
            "top_categories": [
                {
                    "rank": index,
                    "category": row["category"],
                    "item_sales_value": row["item_sales_value"],
                }
                for index, row in enumerate(rows, start=1)
            ]
        }
    raise AssertionError(f"No canonical result adapter for {task_id}")


def validate_crosscheck(
    task_id: str,
    rows: list[dict[str, Any]],
    gold_values: dict[str, Any],
    allowed_error: float,
) -> None:
    if task_id in {"DA01", "DA02", "DA07", "DA09"}:
        compare(canonical_values(task_id, rows), gold_values, allowed_error, f"{task_id}.crosscheck")
        return
    if task_id == "DA03":
        by_month = {str(row["purchase_month"])[:7]: row for row in rows}
        expected = {
            "2017-10": {
                "order_count": gold_values["oct_order_count"],
                "payment_value": gold_values["oct_payment_value"],
                "avg_order_payment": gold_values["oct_avg_order_payment"],
            },
            "2017-11": {
                "order_count": gold_values["nov_order_count"],
                "payment_value": gold_values["nov_payment_value"],
                "avg_order_payment": gold_values["nov_avg_order_payment"],
            },
        }
        compare(by_month, expected, allowed_error, "DA03.crosscheck")
        return
    if task_id == "DA04":
        by_month = {str(row["purchase_month"])[:7]: row for row in rows}
        expected = {
            "2018-07": {
                "delivered_order_count": gold_values["jul_delivered_order_count"],
                "payment_value": gold_values["jul_payment_value"],
                "avg_order_payment": gold_values["jul_avg_order_payment"],
                "item_sales_value": gold_values["jul_item_sales_value"],
                "freight_value": gold_values["jul_freight_value"],
            },
            "2018-08": {
                "delivered_order_count": gold_values["aug_delivered_order_count"],
                "payment_value": gold_values["aug_payment_value"],
                "avg_order_payment": gold_values["aug_avg_order_payment"],
                "item_sales_value": gold_values["aug_item_sales_value"],
                "freight_value": gold_values["aug_freight_value"],
            },
        }
        compare(by_month, expected, allowed_error, "DA04.crosscheck")
        return
    if task_id == "DA05":
        if len(rows) != 1:
            raise AssertionError("DA05 cross-check must return one row")
        row = rows[0]
        expected = {
            "july_total_direct": gold_values["july_total"],
            "august_total_direct": gold_values["august_total"],
            "direct_delta": gold_values["total_delta"],
            "july_total_from_categories": gold_values["july_total"],
            "august_total_from_categories": gold_values["august_total"],
            "sum_category_delta": gold_values["total_delta"],
        }
        compare(row, expected, allowed_error, "DA05.crosscheck")
        return
    if task_id == "DA06":
        if len(rows) != 1:
            raise AssertionError("DA06 cross-check must return one row")
        row = rows[0]
        expected = {
            "october_total_direct": gold_values["october_total"],
            "november_total_direct": gold_values["november_total"],
            "direct_increase": gold_values["total_increase"],
            "october_total_from_states": gold_values["october_total"],
            "november_total_from_states": gold_values["november_total"],
            "sum_state_delta": gold_values["total_increase"],
        }
        compare(row, expected, allowed_error, "DA06.crosscheck")
        return
    if task_id == "DA08":
        by_state = {row["customer_state"]: row for row in rows}
        expected = {
            "SP": {
                "delivered_order_count": gold_values["top_payment_state_order_count"],
                "payment_value": gold_values["top_payment_value"],
                "avg_order_payment": gold_values["top_payment_state_avg_order_payment"],
            },
            "PB": {
                "delivered_order_count": gold_values["top_avg_state_order_count"],
                "avg_order_payment": gold_values["highest_avg_order_payment"],
            },
        }
        compare(by_state, expected, allowed_error, "DA08.crosscheck")
        return
    raise AssertionError(f"No cross-check adapter for {task_id}")


def validate_naive_fixture(
    connection: duckdb.DuckDBPyConnection,
    task_dir: Path,
    gold: dict[str, Any],
    allowed_error: float,
) -> None:
    rows = query(connection, task_dir / gold["naive_sql_path"])
    if len(rows) != 1:
        raise AssertionError("DA09 naive SQL must return one row")
    row = rows[0]
    safe = gold["gold_values"]
    actual = dict(row)
    actual.update(
        {
            "payment_overstatement": row["naive_payment_value"] - Decimal(str(safe["payment_value"])),
            "payment_inflation_pct": 100.0
            * (float(row["naive_payment_value"]) - safe["payment_value"])
            / safe["payment_value"],
            "combined_overstatement": row["naive_item_sales_plus_freight"]
            - Decimal(str(safe["item_sales_plus_freight"])),
            "combined_inflation_pct": 100.0
            * (float(row["naive_item_sales_plus_freight"]) - safe["item_sales_plus_freight"])
            / safe["item_sales_plus_freight"],
        }
    )
    compare(actual, gold["naive_diagnostic"], allowed_error, "DA09.naive")
    if abs(float(row["naive_payment_value"]) - safe["payment_value"]) <= allowed_error:
        raise AssertionError("DA09 naive payment unexpectedly equals the safe result")


def validate_gold(data_dir: Path, emit: bool = True) -> list[str]:
    data_dir = data_dir.resolve()
    task_dir = data_dir / "tasks" / "v0"
    tasks = load_jsonl(task_dir / "tasks_v0.jsonl")
    gold_records = load_jsonl(task_dir / "gold_v0.jsonl")

    task_ids = [record.get("task_id") for record in tasks]
    gold_ids = [record.get("task_id") for record in gold_records]
    if task_ids != EXPECTED_TASK_IDS or gold_ids != EXPECTED_TASK_IDS:
        raise AssertionError("tasks_v0 and gold_v0 must each contain ordered DA01-DA10")
    if any(set(record) != RUNTIME_FIELDS for record in tasks):
        raise AssertionError("runtime task contains fields outside task_id/question/task_type")

    database_path = data_dir / DATABASE_NAME
    connection = duckdb.connect(str(database_path), read_only=True)
    passed: list[str] = []
    try:
        for gold in gold_records:
            task_id = gold["task_id"]
            allowed_error = tolerance(gold)
            if task_id == "DA10":
                if gold["answerability"] != "UNANSWERABLE_WITH_CURRENT_DATA":
                    raise AssertionError("DA10 must be unanswerable")
                if gold.get("expected_action") != "STOP_WITH_DATA_GAP":
                    raise AssertionError("DA10 must stop with a data gap")
                if gold.get("gold_sql_path") is not None or gold.get("gold_values"):
                    raise AssertionError("DA10 must not contain a numeric Gold answer")
                validation_rows = query(connection, task_dir / gold["validation_sql_path"])
                compare(
                    validation_rows,
                    [{"refund_like_column_count": 0}],
                    allowed_error,
                    "DA10.validation",
                )
            else:
                if gold["answerability"] != "ANSWERABLE":
                    raise AssertionError(f"{task_id} must be answerable")
                canonical_path = gold.get("gold_sql_path")
                crosscheck_path = gold.get("crosscheck_sql_path")
                if not canonical_path or not crosscheck_path:
                    raise AssertionError(f"{task_id} is missing canonical or cross-check SQL")
                canonical_rows = query(connection, task_dir / canonical_path)
                actual_values = canonical_values(task_id, canonical_rows)
                compare(actual_values, gold["gold_values"], allowed_error, f"{task_id}.gold")
                crosscheck_rows = query(connection, task_dir / crosscheck_path)
                validate_crosscheck(
                    task_id, crosscheck_rows, gold["gold_values"], allowed_error
                )
                if task_id == "DA09":
                    validate_naive_fixture(connection, task_dir, gold, allowed_error)
            passed.append(task_id)
            if emit:
                print(f"{task_id} PASS")
    finally:
        connection.close()

    if emit:
        print(f"\n{len(passed)}/10 gold tasks validated")
    return passed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory containing ecommerce.duckdb and tasks/v0",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validate_gold(args.data_dir)


if __name__ == "__main__":
    main()
