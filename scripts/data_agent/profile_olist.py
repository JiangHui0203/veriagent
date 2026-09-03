#!/usr/bin/env python3
"""Profile the Olist V0 DuckDB foundation and write bounded analytical outputs."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

import duckdb

from olist_common import DATABASE_NAME, DEFAULT_DATA_DIR, TABLE_DEFINITIONS


OUTPUT_FILES = (
    "profiling_summary.md",
    "monthly_metrics.csv",
    "category_metrics.csv",
    "state_metrics.csv",
    "payment_type_metrics.csv",
    "cardinality_summary.csv",
    "data_quality_summary.csv",
)


MONTHLY_SQL = """
WITH payment_by_order AS (
    SELECT order_id, SUM(payment_value) AS order_payment_value
    FROM order_payments
    GROUP BY order_id
),
item_by_order AS (
    SELECT
        order_id,
        SUM(price) AS order_item_sales_value,
        SUM(freight_value) AS order_freight_value
    FROM order_items
    GROUP BY order_id
)
SELECT
    strftime(o.order_purchase_timestamp, '%Y-%m') AS purchase_month,
    COUNT(DISTINCT o.order_id) AS order_count,
    COUNT(DISTINCT CASE WHEN o.order_status = 'delivered' THEN o.order_id END)
        AS delivered_order_count,
    COALESCE(SUM(CASE WHEN o.order_status = 'delivered'
                      THEN p.order_payment_value ELSE 0 END), 0)
        AS payment_value,
    AVG(CASE WHEN o.order_status = 'delivered'
             THEN p.order_payment_value END) AS avg_order_payment,
    COALESCE(SUM(CASE WHEN o.order_status = 'delivered'
                      THEN i.order_item_sales_value ELSE 0 END), 0)
        AS item_sales_value,
    COALESCE(SUM(CASE WHEN o.order_status = 'delivered'
                      THEN i.order_freight_value ELSE 0 END), 0)
        AS freight_value,
    COUNT(DISTINCT CASE WHEN o.order_status = 'delivered'
                        THEN c.customer_unique_id END) AS unique_customers
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
LEFT JOIN payment_by_order p ON o.order_id = p.order_id
LEFT JOIN item_by_order i ON o.order_id = i.order_id
GROUP BY 1
ORDER BY 1
"""


CATEGORY_SQL = """
SELECT
    p.product_category_name,
    t.product_category_name_english,
    SUM(i.price) AS item_sales_value,
    COUNT(*) AS item_count,
    COUNT(DISTINCT i.order_id) AS distinct_order_count
FROM order_items i
JOIN orders o ON i.order_id = o.order_id
JOIN products p ON i.product_id = p.product_id
LEFT JOIN category_translation t
    ON p.product_category_name = t.product_category_name
WHERE o.order_status = 'delivered'
GROUP BY 1, 2
ORDER BY item_sales_value DESC NULLS LAST
"""


STATE_SQL = """
WITH payment_by_order AS (
    SELECT order_id, SUM(payment_value) AS order_payment_value
    FROM order_payments
    GROUP BY order_id
),
customer_base AS (
    SELECT customer_state, COUNT(*) AS customer_record_count
    FROM customers
    GROUP BY customer_state
)
SELECT
    c.customer_state,
    b.customer_record_count,
    COUNT(DISTINCT o.order_id) AS delivered_order_count,
    COUNT(DISTINCT c.customer_unique_id) AS unique_customers,
    COALESCE(SUM(p.order_payment_value), 0) AS payment_value,
    AVG(p.order_payment_value) AS avg_order_payment
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
JOIN customer_base b ON c.customer_state = b.customer_state
LEFT JOIN payment_by_order p ON o.order_id = p.order_id
WHERE o.order_status = 'delivered'
GROUP BY 1, 2
ORDER BY payment_value DESC NULLS LAST
"""


PAYMENT_TYPE_SQL = """
SELECT
    payment_type,
    COUNT(*) AS payment_record_count,
    COUNT(DISTINCT order_id) AS distinct_order_count,
    SUM(payment_value) AS payment_value
FROM order_payments
GROUP BY payment_type
ORDER BY payment_value DESC NULLS LAST
"""


def query(connection: duckdb.DuckDBPyConnection, sql: str) -> tuple[list[str], list[tuple]]:
    cursor = connection.execute(sql)
    columns = [column[0] for column in cursor.description]
    return columns, cursor.fetchall()


def scalar(connection: duckdb.DuckDBPyConnection, sql: str) -> Any:
    return connection.execute(sql).fetchone()[0]


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def format_number(value: Any, decimals: int = 2) -> str:
    if value is None:
        return ""
    if isinstance(value, (Decimal, float)):
        return f"{float(value):,.{decimals}f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(format_number(value) for value in row) + " |")
    return "\n".join(lines)


def collect_data_quality(connection: duckdb.DuckDBPyConnection) -> list[tuple]:
    rows: list[tuple] = []

    uniqueness_checks = (
        ("orders", ("order_id",)),
        ("customers", ("customer_id",)),
        ("products", ("product_id",)),
        ("order_items", ("order_id", "order_item_id")),
        ("order_payments", ("order_id", "payment_sequential")),
        ("category_translation", ("product_category_name",)),
    )
    for table_name, columns in uniqueness_checks:
        group_columns = ", ".join(columns)
        total = scalar(connection, f"SELECT COUNT(*) FROM {table_name}")
        duplicates = scalar(
            connection,
            f"""
            SELECT COALESCE(SUM(group_count - 1), 0)
            FROM (
                SELECT COUNT(*) AS group_count
                FROM {table_name}
                GROUP BY {group_columns}
                HAVING COUNT(*) > 1
            ) duplicate_groups
            """,
        )
        rows.append(
            (
                "uniqueness",
                table_name,
                "+".join(columns),
                total,
                duplicates,
                ratio(duplicates, total),
            )
        )

    foreign_keys = (
        ("orders", "customer_id", "customers", "customer_id"),
        ("order_items", "order_id", "orders", "order_id"),
        ("order_payments", "order_id", "orders", "order_id"),
        ("order_items", "product_id", "products", "product_id"),
        (
            "products",
            "product_category_name",
            "category_translation",
            "product_category_name",
        ),
    )
    for child, child_key, parent, parent_key in foreign_keys:
        total = scalar(connection, f"SELECT COUNT(*) FROM {child}")
        unmatched = scalar(
            connection,
            f"""
            SELECT COUNT(*)
            FROM {child} child
            LEFT JOIN {parent} parent
                ON child.{child_key} = parent.{parent_key}
            WHERE child.{child_key} IS NOT NULL
              AND parent.{parent_key} IS NULL
            """,
        )
        rows.append(
            (
                "foreign_key",
                child,
                f"{child_key}->{parent}.{parent_key}",
                total,
                unmatched,
                ratio(unmatched, total),
            )
        )

    null_checks = (
        ("orders", "order_id"),
        ("order_items", "order_id"),
        ("order_payments", "order_id"),
        ("customers", "customer_id"),
        ("orders", "customer_id"),
        ("customers", "customer_unique_id"),
        ("orders", "order_status"),
        ("orders", "order_purchase_timestamp"),
        ("order_items", "product_id"),
        ("products", "product_id"),
        ("order_items", "price"),
        ("order_items", "freight_value"),
        ("order_payments", "payment_value"),
        ("products", "product_category_name"),
    )
    for table_name, column_name in null_checks:
        total, null_count = connection.execute(
            f"SELECT COUNT(*), COUNT(*) FILTER (WHERE {column_name} IS NULL) "
            f"FROM {table_name}"
        ).fetchone()
        rows.append(
            (
                "null",
                table_name,
                column_name,
                total,
                null_count,
                ratio(null_count, total),
            )
        )

    return rows


def collect_cardinality(connection: duckdb.DuckDBPyConnection) -> tuple[list[tuple], dict[str, Any]]:
    order_count = scalar(connection, "SELECT COUNT(*) FROM orders")
    item_distribution = connection.execute(
        """
        WITH item_counts AS (
            SELECT o.order_id, COUNT(i.order_item_id) AS record_count
            FROM orders o
            LEFT JOIN order_items i ON o.order_id = i.order_id
            GROUP BY o.order_id
        )
        SELECT record_count, COUNT(*) AS orders
        FROM item_counts
        GROUP BY record_count
        ORDER BY record_count
        """
    ).fetchall()
    payment_distribution = connection.execute(
        """
        WITH payment_counts AS (
            SELECT o.order_id, COUNT(p.payment_sequential) AS record_count
            FROM orders o
            LEFT JOIN order_payments p ON o.order_id = p.order_id
            GROUP BY o.order_id
        )
        SELECT record_count, COUNT(*) AS orders
        FROM payment_counts
        GROUP BY record_count
        ORDER BY record_count
        """
    ).fetchall()

    multi_item_orders = sum(count for record_count, count in item_distribution if record_count > 1)
    multi_payment_orders = sum(
        count for record_count, count in payment_distribution if record_count > 1
    )
    both_multi = scalar(
        connection,
        """
        WITH item_counts AS (
            SELECT order_id, COUNT(*) AS item_count
            FROM order_items GROUP BY order_id
        ),
        payment_counts AS (
            SELECT order_id, COUNT(*) AS payment_count
            FROM order_payments GROUP BY order_id
        )
        SELECT COUNT(*)
        FROM item_counts i
        JOIN payment_counts p ON i.order_id = p.order_id
        WHERE i.item_count > 1 AND p.payment_count > 1
        """,
    )
    orders_items_rows = scalar(
        connection,
        "SELECT COUNT(*) FROM orders o JOIN order_items i ON o.order_id = i.order_id",
    )
    orders_payments_rows = scalar(
        connection,
        "SELECT COUNT(*) FROM orders o JOIN order_payments p ON o.order_id = p.order_id",
    )
    raw_join_rows = scalar(
        connection,
        """
        SELECT COUNT(*)
        FROM orders o
        JOIN order_items i ON o.order_id = i.order_id
        JOIN order_payments p ON o.order_id = p.order_id
        """,
    )
    safe_payment = scalar(
        connection,
        """
        WITH payment_by_order AS (
            SELECT order_id, SUM(payment_value) AS order_payment_value
            FROM order_payments GROUP BY order_id
        )
        SELECT SUM(p.order_payment_value)
        FROM orders o
        JOIN payment_by_order p ON o.order_id = p.order_id
        WHERE o.order_status = 'delivered'
        """,
    )
    naive_payment = scalar(
        connection,
        """
        SELECT SUM(p.payment_value)
        FROM orders o
        JOIN order_items i ON o.order_id = i.order_id
        JOIN order_payments p ON o.order_id = p.order_id
        WHERE o.order_status = 'delivered'
        """,
    )
    payment_difference = naive_payment - safe_payment
    payment_inflation = float(payment_difference / safe_payment) if safe_payment else 0.0

    rows: list[tuple] = []
    rows.extend(
        (
            "item_count_distribution",
            "orders_by_item_record_count",
            str(record_count),
            count,
            ratio(count, order_count),
        )
        for record_count, count in item_distribution
    )
    rows.extend(
        (
            "payment_count_distribution",
            "orders_by_payment_record_count",
            str(record_count),
            count,
            ratio(count, order_count),
        )
        for record_count, count in payment_distribution
    )
    summary_values = (
        ("order_cardinality", "multi_item_orders", "orders", multi_item_orders, ratio(multi_item_orders, order_count)),
        ("order_cardinality", "multi_payment_orders", "orders", multi_payment_orders, ratio(multi_payment_orders, order_count)),
        ("order_cardinality", "multi_item_and_multi_payment_orders", "orders", both_multi, ratio(both_multi, order_count)),
        ("join_rows", "orders_join_order_items", "rows", orders_items_rows, None),
        ("join_rows", "orders_join_order_payments", "rows", orders_payments_rows, None),
        ("join_rows", "raw_orders_items_payments", "rows", raw_join_rows, None),
        ("payment_comparison", "safe_delivered_payment_total", "native_amount", safe_payment, None),
        ("payment_comparison", "naive_delivered_payment_total", "native_amount", naive_payment, None),
        ("payment_comparison", "naive_minus_safe", "native_amount", payment_difference, None),
        ("payment_comparison", "relative_inflation", "ratio", payment_inflation, payment_inflation),
    )
    rows.extend(summary_values)

    values = {
        "order_count": order_count,
        "multi_item_orders": multi_item_orders,
        "multi_payment_orders": multi_payment_orders,
        "both_multi": both_multi,
        "orders_items_rows": orders_items_rows,
        "orders_payments_rows": orders_payments_rows,
        "raw_join_rows": raw_join_rows,
        "safe_payment": safe_payment,
        "naive_payment": naive_payment,
        "payment_difference": payment_difference,
        "payment_inflation": payment_inflation,
    }
    return rows, values


def month_index(month: str) -> int:
    parsed = datetime.strptime(month, "%Y-%m")
    return parsed.year * 12 + parsed.month


def phenomenon_candidates(
    monthly_rows: list[tuple],
    category_rows: list[tuple],
    state_rows: list[tuple],
    cardinality: dict[str, Any],
    min_date: Any,
    max_date: Any,
) -> list[str]:
    peak_orders = max(monthly_rows, key=lambda row: row[1])
    peak_payment = max(monthly_rows, key=lambda row: row[3])
    monthly_changes = []
    for previous, current in zip(monthly_rows, monthly_rows[1:]):
        if (
            month_index(current[0]) - month_index(previous[0]) == 1
            and previous[1] >= 1_000
            and current[1] >= 1_000
        ):
            change = (current[1] - previous[1]) / previous[1]
            monthly_changes.append((change, previous, current))
    largest_gain = max(monthly_changes, key=lambda row: row[0])
    largest_drop = min(monthly_changes, key=lambda row: row[0])

    category_total = sum((row[2] or Decimal("0")) for row in category_rows)
    top_category = category_rows[0]
    category_label = top_category[1] or top_category[0] or "missing category"
    top_category_share = float(top_category[2] / category_total) if category_total else 0.0

    state_total = sum((row[4] or Decimal("0")) for row in state_rows)
    top_state = state_rows[0]
    top_state_share = float(top_state[4] / state_total) if state_total else 0.0

    return [
        f"数据覆盖 {min_date} 至 {max_date}；首尾月份都不是完整自然月，边界月份不宜直接作完整月同比。",
        f"全状态订单量峰值出现在 {peak_orders[0]}，共 {peak_orders[1]:,} 单；delivered payment value 峰值出现在 {peak_payment[0]}，为 {format_number(peak_payment[3])}。",
        f"连续月份订单量最大上升为 {largest_gain[1][0]}→{largest_gain[2][0]}（{largest_gain[0]:.1%}），最大下降为 {largest_drop[1][0]}→{largest_drop[2][0]}（{largest_drop[0]:.1%}）。",
        f"delivered 范围内，最高 Item Sales Value 品类是 {category_label}，金额 {format_number(top_category[2])}，占全部品类 Item Sales Value 的 {top_category_share:.1%}。",
        f"delivered 范围内，州 {top_state[0]} 的 Customer Payment Value 最高，为 {format_number(top_state[4])}，占州级合计的 {top_state_share:.1%}。",
        f"{cardinality['multi_item_orders']:,} 个订单包含多条 item，{cardinality['multi_payment_orders']:,} 个订单包含多条 payment，二者同时发生的订单有 {cardinality['both_multi']:,} 个。",
        f"raw items+payments join 产生 {cardinality['raw_join_rows']:,} 行；直接汇总会把 delivered payment 从 {format_number(cardinality['safe_payment'])} 放大到 {format_number(cardinality['naive_payment'])}（+{cardinality['payment_inflation']:.2%}）。",
    ]


def build_summary(
    connection: duckdb.DuckDBPyConnection,
    table_counts: list[tuple],
    data_quality_rows: list[tuple],
    cardinality: dict[str, Any],
    monthly_rows: list[tuple],
    category_rows: list[tuple],
    state_rows: list[tuple],
    payment_type_rows: list[tuple],
) -> str:
    min_date, max_date = connection.execute(
        "SELECT MIN(order_purchase_timestamp), MAX(order_purchase_timestamp) FROM orders"
    ).fetchone()
    status_rows = connection.execute(
        """
        SELECT order_status, COUNT(*) AS orders,
               COUNT(*) * 1.0 / SUM(COUNT(*)) OVER () AS share
        FROM orders
        GROUP BY order_status
        ORDER BY orders DESC
        """
    ).fetchall()
    delivered_orders = next(count for status, count, _ in status_rows if status == "delivered")
    untranslated_categories = scalar(
        connection,
        """
        SELECT COUNT(DISTINCT p.product_category_name)
        FROM products p
        LEFT JOIN category_translation t
          ON p.product_category_name = t.product_category_name
        WHERE p.product_category_name IS NOT NULL
          AND t.product_category_name IS NULL
        """,
    )
    products_without_category = scalar(
        connection,
        "SELECT COUNT(*) FROM products WHERE product_category_name IS NULL",
    )
    nonzero_quality = [row for row in data_quality_rows if row[4] > 0]
    phenomena = phenomenon_candidates(
        monthly_rows, category_rows, state_rows, cardinality, min_date, max_date
    )

    top_categories = [
        (
            row[1] or row[0] or "missing category",
            row[2],
            row[3],
            row[4],
        )
        for row in category_rows[:10]
    ]
    top_states = [(row[0], row[2], row[3], row[4], row[5]) for row in state_rows[:10]]
    payment_rows = [(row[0], row[1], row[2], row[3]) for row in payment_type_rows]

    quality_lines = [
        f"- `{row[1]}.{row[2]}` ({row[0]}): {row[4]:,} / {row[3]:,}"
        for row in nonzero_quality
    ]
    if not quality_lines:
        quality_lines = ["- 本轮指定的唯一性、外键与关键 NULL 检查均未发现问题。"]

    lines = [
        "# Olist V0 Profiling Summary",
        "",
        "- 数据集：`olistbr/brazilian-ecommerce`",
        "- Kaggle dataset version: `2`",
        "- Profiling run: `olist_v0`",
        f"- 订单购买时间范围：`{min_date}` 至 `{max_date}`",
        "",
        "## 六表规模",
        "",
        markdown_table(("table", "rows"), table_counts),
        "",
        "## 订单状态",
        "",
        markdown_table(
            ("order_status", "order_count", "share"),
            [(status, count, f"{share:.2%}") for status, count, share in status_rows],
        ),
        "",
        f"Delivered share: {delivered_orders / cardinality['order_count']:.2%}",
        "",
        "## 数据质量",
        "",
        *quality_lines,
        f"- 无法映射英文名的非空 product category：{untranslated_categories:,} 个；product category 为空的产品：{products_without_category:,} 个。",
        "",
        "## Join cardinality",
        "",
        f"- Multi-item orders: {cardinality['multi_item_orders']:,} ({cardinality['multi_item_orders'] / cardinality['order_count']:.2%})",
        f"- Multi-payment orders: {cardinality['multi_payment_orders']:,} ({cardinality['multi_payment_orders'] / cardinality['order_count']:.2%})",
        f"- Both multi-item and multi-payment: {cardinality['both_multi']:,}",
        f"- Orders join items: {cardinality['orders_items_rows']:,} rows",
        f"- Orders join payments: {cardinality['orders_payments_rows']:,} rows",
        f"- Raw orders + items + payments: {cardinality['raw_join_rows']:,} rows",
        "",
        "## Safe vs naive delivered payment",
        "",
        f"- Safe order-grain total: {format_number(cardinality['safe_payment'])}",
        f"- Naive raw-join total: {format_number(cardinality['naive_payment'])}",
        f"- Difference: {format_number(cardinality['payment_difference'])}",
        f"- Relative inflation: {cardinality['payment_inflation']:.2%}",
        "",
        "## Payment type distribution",
        "",
        markdown_table(
            ("payment_type", "records", "orders", "payment_value"), payment_rows
        ),
        "",
        "## Top delivered categories",
        "",
        markdown_table(
            ("category", "item_sales_value", "items", "orders"), top_categories
        ),
        "",
        "## Top delivered customer states",
        "",
        markdown_table(
            ("state", "delivered_orders", "unique_customers", "payment_value", "avg_order_payment"),
            top_states,
        ),
        "",
        "## 后续问题设计的证据候选",
        "",
        *[f"- {item}" for item in phenomena],
        "",
        "说明：月度、品类和州级商业指标默认使用 delivered 订单；`order_count` 单独保留全部已下单订单。",
    ]
    return "\n".join(lines) + "\n"


def profile(data_dir: Path) -> dict[str, Any]:
    data_dir = data_dir.resolve()
    database_path = data_dir / DATABASE_NAME
    if not database_path.is_file():
        raise FileNotFoundError(
            f"DuckDB database not found: {database_path}. Run build_olist_duckdb.py first."
        )
    output_dir = data_dir / "profiling"
    output_dir.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        missing_tables = set(TABLE_DEFINITIONS) - existing_tables
        if missing_tables:
            raise RuntimeError("Missing DuckDB tables: " + ", ".join(sorted(missing_tables)))

        table_counts = [
            (table_name, scalar(connection, f"SELECT COUNT(*) FROM {table_name}"))
            for table_name in TABLE_DEFINITIONS
        ]
        data_quality_rows = collect_data_quality(connection)
        cardinality_rows, cardinality = collect_cardinality(connection)
        monthly_columns, monthly_rows = query(connection, MONTHLY_SQL)
        category_columns, category_rows = query(connection, CATEGORY_SQL)
        state_columns, state_rows = query(connection, STATE_SQL)
        payment_columns, payment_rows = query(connection, PAYMENT_TYPE_SQL)

        write_csv(output_dir / "monthly_metrics.csv", monthly_columns, monthly_rows)
        write_csv(output_dir / "category_metrics.csv", category_columns, category_rows)
        write_csv(output_dir / "state_metrics.csv", state_columns, state_rows)
        write_csv(output_dir / "payment_type_metrics.csv", payment_columns, payment_rows)
        write_csv(
            output_dir / "cardinality_summary.csv",
            ("section", "metric", "bucket_or_unit", "value", "percentage"),
            cardinality_rows,
        )
        write_csv(
            output_dir / "data_quality_summary.csv",
            (
                "check_type",
                "table",
                "column_or_relation",
                "total_rows",
                "issue_count",
                "issue_rate",
            ),
            data_quality_rows,
        )
        summary = build_summary(
            connection,
            table_counts,
            data_quality_rows,
            cardinality,
            monthly_rows,
            category_rows,
            state_rows,
            payment_rows,
        )
        (output_dir / "profiling_summary.md").write_text(summary, encoding="utf-8")
    finally:
        connection.close()

    return cardinality


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory containing ecommerce.duckdb",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cardinality = profile(args.data_dir)
    print(f"multi-item orders: {cardinality['multi_item_orders']:,}")
    print(f"multi-payment orders: {cardinality['multi_payment_orders']:,}")
    print(f"safe payment total: {format_number(cardinality['safe_payment'])}")
    print(f"naive payment total: {format_number(cardinality['naive_payment'])}")
    print(f"inflation: {cardinality['payment_inflation']:.2%}")
    print(f"profiling outputs: {len(OUTPUT_FILES)} files")


if __name__ == "__main__":
    main()
