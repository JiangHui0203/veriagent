"""Runtime-only deterministic checks for the three-task integration smoke."""

from __future__ import annotations

from typing import Optional


DA02_PAYMENT_CROSS_CHECK_SQL = """
WITH eligible_orders AS (
    SELECT order_id
    FROM orders
    WHERE order_status = 'delivered'
      AND order_purchase_timestamp >= TIMESTAMP '2017-11-01'
      AND order_purchase_timestamp < TIMESTAMP '2017-12-01'
)
SELECT ROUND(SUM(payment_value), 2) AS payment_value
FROM order_payments
WHERE order_id IN (SELECT order_id FROM eligible_orders)
"""


def smoke_cross_check_sql(task_id: str) -> Optional[str]:
    """Return an independent query without embedding any expected value."""

    return DA02_PAYMENT_CROSS_CHECK_SQL if task_id == "DA02" else None
