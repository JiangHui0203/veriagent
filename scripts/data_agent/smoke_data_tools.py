"""Minimal deterministic smoke runner for the Olist V0 tool layer."""

from __future__ import annotations

from pathlib import Path

from veriagent.data_agent import registry


PROJECT_DIR = Path(__file__).resolve().parents[2]
SQL_DIR = PROJECT_DIR / "datasets" / "olistbr" / "tasks" / "v0" / "sql"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sql_file(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


def main() -> None:
    metric = registry.call(
        "search_metric_definition", {"query": "average order payment"}
    )
    require(metric.ok, metric.error or "metric lookup failed")
    require(metric.result["metric_id"] == "avg_order_payment", "wrong metric")
    require(
        any("aggregate payments to order_id" in rule for rule in metric.result["critical_rules"]),
        "missing order-grain rule",
    )
    print("metric search           PASS")

    unsupported = registry.call(
        "search_metric_definition", {"query": "net revenue"}
    )
    require(not unsupported.ok, "net revenue must not be supported")
    require(unsupported.error_type == "UNSUPPORTED_METRIC", "wrong unsupported result")
    require(
        unsupported.result["expected_action"] == "STOP_WITH_DATA_GAP",
        "missing data-gap action",
    )
    print("unsupported metric      PASS")

    orders_schema = registry.call("inspect_schema", {"table_name": "orders"})
    payments_schema = registry.call(
        "inspect_schema", {"table_name": "order_payments"}
    )
    require(orders_schema.ok and orders_schema.result["row_count"] == 99_441, "orders schema failed")
    require(
        payments_schema.ok and payments_schema.result["row_count"] == 103_886,
        "payments schema failed",
    )
    print("schema inspection       PASS")

    da01 = registry.call(
        "execute_sql",
        {
            "sql": """
                SELECT COUNT(DISTINCT order_id) AS order_count
                FROM orders
                WHERE order_purchase_timestamp >= TIMESTAMP '2017-11-01'
                  AND order_purchase_timestamp < TIMESTAMP '2017-12-01'
            """
        },
    )
    require(da01.ok and da01.result["rows"][0][0] == 7_544, "DA01 SQL failed")
    print("SQL execution           PASS")

    items_cardinality = registry.call(
        "check_join_cardinality",
        {"left_table": "orders", "right_table": "order_items", "left_key": "order_id"},
    )
    payments_cardinality = registry.call(
        "check_join_cardinality",
        {
            "left_table": "orders",
            "right_table": "order_payments",
            "left_key": "order_id",
        },
    )
    for result in (items_cardinality, payments_cardinality):
        require(result.ok, result.error or "cardinality check failed")
        require(result.result["relationship"] == "ONE_TO_MANY", "wrong relationship")
        require(result.result["multiplication_risk"], "missing multiplication risk")
    print("join cardinality        PASS")

    da02 = registry.call(
        "cross_check",
        {
            "sql_a": sql_file("DA02_gold.sql"),
            "sql_b": sql_file("DA02_crosscheck.sql"),
            "absolute_tolerance": 0.01,
        },
    )
    require(da02.ok and da02.result["matched"], "DA02 cross-check failed")
    require(
        abs(float(da02.result["result_a"]["rows"][0][0]) - 1_153_528.05) <= 0.01,
        "DA02 value mismatch",
    )
    print("cross-check             PASS")

    safe = registry.call("execute_sql", {"sql": sql_file("DA09_gold.sql")})
    naive = registry.call("execute_sql", {"sql": sql_file("DA09_naive_join.sql")})
    require(safe.ok and naive.ok, "DA09 SQL execution failed")
    safe_payment = float(safe.result["rows"][0][0])
    naive_payment = float(naive.result["rows"][0][1])
    require(abs(safe_payment - 1_153_528.05) <= 0.01, "DA09 safe value mismatch")
    require(abs(naive_payment - 1_548_682.69) <= 0.01, "DA09 naive value mismatch")
    require(naive_payment != safe_payment, "DA09 naive result must differ")
    inflation = 100.0 * (naive_payment - safe_payment) / safe_payment
    print("DA09 naive-risk smoke   PASS")
    print(
        f"safe={safe_payment:,.2f} naive={naive_payment:,.2f} "
        f"inflation={inflation:.4f}%"
    )


if __name__ == "__main__":
    main()
