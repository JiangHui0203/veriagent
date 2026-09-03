"""Exact, alias, and simple text retrieval over the frozen V0 metric contract."""

from __future__ import annotations

import re
from typing import Any

from ..db import METRIC_CONTRACT_PATH
from ..schemas import ToolResult


METRICS: dict[str, dict[str, Any]] = {
    "order_count": {
        "display_name": "Order Count",
        "definition": "Number of distinct orders placed in the requested purchase period.",
        "grain": "order",
        "time_field": "order_purchase_timestamp",
        "default_scope": "all placed orders",
        "required_tables": ["orders"],
        "critical_rules": [
            "use COUNT(DISTINCT order_id)",
            "do not silently apply a delivered filter",
        ],
        "aliases": ["orders", "order count", "placed orders", "订单量", "订单数"],
    },
    "payment_value": {
        "display_name": "Customer Payment Value",
        "definition": "Total customer payment amount for delivered orders in the purchase period.",
        "grain": "payment to order",
        "time_field": "order_purchase_timestamp",
        "default_scope": "delivered",
        "required_tables": ["orders", "order_payments"],
        "critical_rules": [
            "aggregate payments to order_id before joining another one-to-many table",
            "do not treat payment value as accounting or net revenue",
        ],
        "aliases": [
            "payment",
            "customer payment",
            "customer payment value",
            "支付金额",
            "客户支付金额",
        ],
    },
    "avg_order_payment": {
        "display_name": "Average Payment per Order",
        "definition": "Average total customer payment per delivered order with payment records.",
        "grain": "order",
        "time_field": "order_purchase_timestamp",
        "default_scope": "delivered",
        "required_tables": ["orders", "order_payments"],
        "critical_rules": [
            "aggregate payments to order_id before averaging",
            "do not use AVG(payment_value) at payment-record grain",
        ],
        "aliases": [
            "average order payment",
            "average payment per order",
            "avg order payment",
            "平均每单支付",
            "平均订单支付",
        ],
    },
    "item_revenue": {
        "display_name": "Item Sales Value",
        "definition": "Total order-item price for delivered orders; it is not accounting revenue.",
        "grain": "order item",
        "time_field": "order_purchase_timestamp",
        "default_scope": "delivered",
        "required_tables": ["orders", "order_items"],
        "critical_rules": [
            "sum order_items.price at item grain",
            "display as Item Sales Value, not accounting revenue",
        ],
        "aliases": ["item sales", "item sales value", "item revenue", "商品销售额"],
    },
    "freight_value": {
        "display_name": "Freight Value",
        "definition": "Total freight amount recorded on order items for delivered orders.",
        "grain": "order item",
        "time_field": "order_purchase_timestamp",
        "default_scope": "delivered",
        "required_tables": ["orders", "order_items"],
        "critical_rules": [
            "sum order_items.freight_value",
            "do not multiply freight through a payment-table join",
        ],
        "aliases": ["freight", "freight value", "shipping value", "运费"],
    },
    "unique_customers": {
        "display_name": "Unique Customers",
        "definition": "Distinct real customers associated with delivered orders.",
        "grain": "customer",
        "time_field": "order_purchase_timestamp",
        "default_scope": "delivered",
        "required_tables": ["orders", "customers"],
        "critical_rules": [
            "join orders to customers with customer_id",
            "count DISTINCT customer_unique_id",
        ],
        "aliases": ["unique customer", "unique customers", "customer count", "独立客户"],
    },
}

UNSUPPORTED_ALIASES = (
    "net revenue",
    "net_revenue",
    "净收入",
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower().replace("_", " ").replace("-", " "))


def requests_unsupported_metric(text: str) -> bool:
    if not isinstance(text, str):
        return False
    normalized = _normalize(text)
    return any(_normalize(alias) in normalized for alias in UNSUPPORTED_ALIASES)


def search_metric_definition(query: str) -> ToolResult:
    if not isinstance(query, str) or not query.strip():
        return ToolResult(
            tool="search_metric_definition",
            ok=False,
            error_type="INVALID_ARGUMENT",
            error="query must be a non-empty string",
            retryable=False,
        )
    normalized_query = _normalize(query)
    if requests_unsupported_metric(normalized_query):
        reason = (
            "Net revenue is unsupported in Olist V0: the six-table database has no "
            "complete refund ledger and no frozen net revenue metric. "
            "payment_value and item_revenue are not net revenue."
        )
        return ToolResult(
            tool="search_metric_definition",
            ok=False,
            result={
                "query": query,
                "supported": False,
                "expected_action": "STOP_WITH_DATA_GAP",
                "contract_path": str(METRIC_CONTRACT_PATH),
            },
            error_type="UNSUPPORTED_METRIC",
            error=reason,
            retryable=False,
        )

    candidates = []
    for metric_id, definition in METRICS.items():
        aliases = [metric_id, *definition["aliases"]]
        for alias in aliases:
            normalized_alias = _normalize(alias)
            if normalized_query == normalized_alias or (
                f" {normalized_alias} " in f" {normalized_query} "
            ):
                candidates.append((len(normalized_alias), metric_id))

    if not candidates:
        return ToolResult(
            tool="search_metric_definition",
            ok=False,
            result={"query": query, "supported": False},
            error_type="METRIC_NOT_FOUND",
            error="No frozen V0 metric matched the query.",
            retryable=True,
        )

    metric_id = max(candidates)[1]
    definition = METRICS[metric_id]
    result = {
        "metric_id": metric_id,
        "display_name": definition["display_name"],
        "definition": definition["definition"],
        "grain": definition["grain"],
        "time_field": definition["time_field"],
        "default_scope": definition["default_scope"],
        "required_tables": list(definition["required_tables"]),
        "critical_rules": list(definition["critical_rules"]),
        "supported": True,
        "contract_path": str(METRIC_CONTRACT_PATH),
    }
    return ToolResult(tool="search_metric_definition", ok=True, result=result)
