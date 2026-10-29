"""Offline references: SQL aggregation versus Python over raw source rows."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal

from ..data_agent.db import read_only_connection
from .challenge_tasks import Scenario

COUNT_METRICS = {"order_count", "unique_customers"}


def equal_values(actual: dict, expected: dict) -> bool:
    if actual.keys() != expected.keys():
        return False
    for key, value in actual.items():
        if value is None or expected[key] is None or isinstance(value, bool):
            return False
        left, right = Decimal(str(value)), Decimal(str(expected[key]))
        if not left.is_finite() or not right.is_finite():
            return False
        if abs(left - right) > (Decimal(0) if key in COUNT_METRICS else Decimal("0.01")):
            return False
    return True


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def reference_sql(s: Scenario, mutation: str | None = None) -> str:
    if mutation not in {None, "payment_record_average", "customer_record_identity", "raw_join",
                        "force_delivered", "ignore_delivery", "approval_time"}:
        raise ValueError("Unknown reference mutation")
    field = "order_approved_at" if mutation == "approval_time" else "order_purchase_timestamp"
    predicates = [f"o.{field} >= TIMESTAMP {_quote(s.start)}", f"o.{field} < TIMESTAMP {_quote(s.end)}"]
    if s.scope == "delivered" or mutation == "force_delivered":
        predicates.append("o.order_status = 'delivered'")
    if s.state:
        predicates.append(f"c.customer_state = {_quote(s.state)}")
    for table, minimum in (("order_payments", s.payments_min), ("order_items", s.items_min)):
        if minimum:
            predicates.append(f"o.order_id IN (SELECT order_id FROM {table} GROUP BY order_id HAVING COUNT(*) >= {minimum})")
    if s.payment_type:
        extra = f" AND payment_installments >= {s.installments_min}" if s.installments_min else ""
        predicates.append(f"o.order_id IN (SELECT order_id FROM order_payments WHERE payment_type = {_quote(s.payment_type)}{extra})")
    if s.delivered_start and mutation != "ignore_delivery":
        predicates.extend([f"o.order_delivered_customer_date >= TIMESTAMP {_quote(s.delivered_start)}",
                           f"o.order_delivered_customer_date < TIMESTAMP {_quote(s.delivered_end)}"])
    repeat_filter = " WHERE customer_unique_id IN (SELECT customer_unique_id FROM base GROUP BY customer_unique_id HAVING COUNT(DISTINCT order_id) >= 2)" if s.repeat_customer else ""
    ctes = f"""WITH base AS (
        SELECT o.*, c.customer_unique_id FROM orders o JOIN customers c ON o.customer_id = c.customer_id
        WHERE {' AND '.join(predicates)}
    ), eligible AS (SELECT * FROM base{repeat_filter}),
    p AS (SELECT order_id, SUM(payment_value) total FROM order_payments GROUP BY order_id),
    i AS (SELECT order_id, SUM(price) price, SUM(freight_value) freight FROM order_items GROUP BY order_id)
    """
    if mutation == "raw_join":
        expressions = {"payment_value": "SUM(p.payment_value)", "item_revenue": "SUM(i.price)", "freight_value": "SUM(i.freight_value)"}
        return ctes + "SELECT " + ", ".join(f"{expressions[m]} AS {m}" for m in s.metrics) + " FROM eligible e JOIN order_payments p USING(order_id) JOIN order_items i USING(order_id)"
    selections = []
    for metric in s.metrics:
        scope = " WHERE order_status = 'delivered'" if s.scope == "mixed" and metric != "order_count" else ""
        ids = f"SELECT order_id FROM eligible{scope}"
        if metric == "order_count":
            expr = f"SELECT COUNT(DISTINCT order_id) FROM eligible{scope}"
        elif metric == "unique_customers":
            identity = "customer_id" if mutation == "customer_record_identity" else "customer_unique_id"
            expr = f"SELECT COUNT(DISTINCT {identity}) FROM eligible{scope}"
        elif metric == "avg_order_payment":
            expr = f"SELECT AVG(total) FROM p WHERE order_id IN ({ids})"
            if mutation == "payment_record_average":
                expr = f"SELECT AVG(payment_value) FROM order_payments WHERE order_id IN ({ids})"
        elif metric == "payment_value":
            expr = f"SELECT SUM(total) FROM p WHERE order_id IN ({ids})"
        elif metric in {"item_revenue", "freight_value"}:
            expr = f"SELECT SUM({'price' if metric == 'item_revenue' else 'freight'}) FROM i WHERE order_id IN ({ids})"
        else:
            raise ValueError("Unsupported challenge metric")
        selections.append(f"({expr}) AS {metric}")
    return ctes + "SELECT " + ",\n".join(selections)


def query_values(connection, sql: str) -> dict:
    cursor = connection.execute(sql)
    names = [column[0] for column in cursor.description]
    rows = cursor.fetchall()
    if len(rows) != 1:
        raise ValueError("Challenge references must return one aggregate row")
    return dict(zip(names, rows[0]))


class PythonReference:
    """Independent filtering, identity sets and Decimal sums; no gold SQL reuse."""

    def __init__(self, database):
        self.payments = defaultdict(list)
        self.items = defaultdict(list)
        with read_only_connection(database) as con:
            customers = con.execute("SELECT customer_id, customer_unique_id, customer_state FROM customers").fetchall()
            self.customers = {row[0]: row[1:] for row in customers}
            self.orders = con.execute("SELECT order_id, customer_id, order_status, order_purchase_timestamp, order_delivered_customer_date FROM orders").fetchall()
            if len(self.customers) != len(customers) or len({row[0] for row in self.orders}) != len(self.orders):
                raise ValueError("Source primary keys are not unique")
            for order_id, value, kind, installments in con.execute("SELECT order_id, payment_value, payment_type, payment_installments FROM order_payments").fetchall():
                self.payments[order_id].append((value, kind, installments))
            for order_id, price, freight in con.execute("SELECT order_id, price, freight_value FROM order_items").fetchall():
                self.items[order_id].append((price, freight))

    def evaluate(self, s: Scenario) -> tuple[dict, dict]:
        start, end = datetime.fromisoformat(s.start), datetime.fromisoformat(s.end)
        delivery = (datetime.fromisoformat(s.delivered_start), datetime.fromisoformat(s.delivered_end)) if s.delivered_start else None
        eligible = []
        for row in self.orders:
            order_id, customer_id, status, purchased, delivered = row
            customer = self.customers.get(customer_id)
            if customer is None or purchased is None or not start <= purchased < end:
                continue
            if s.scope == "delivered" and status != "delivered":
                continue
            if s.state and customer[1] != s.state:
                continue
            payments, items = self.payments[order_id], self.items[order_id]
            if len(payments) < s.payments_min or len(items) < s.items_min:
                continue
            if s.payment_type and not any(kind == s.payment_type and (installments or 0) >= s.installments_min for _, kind, installments in payments):
                continue
            if delivery and (delivered is None or not delivery[0] <= delivered < delivery[1]):
                continue
            eligible.append(row)
        if s.repeat_customer:
            counts = Counter(self.customers[row[1]][0] for row in eligible)
            eligible = [row for row in eligible if counts[self.customers[row[1]][0]] >= 2]
        values = {}
        for metric in s.metrics:
            selected = [row for row in eligible if s.scope != "mixed" or metric == "order_count" or row[2] == "delivered"]
            if metric == "order_count":
                value = len({row[0] for row in selected})
            elif metric == "unique_customers":
                value = len({self.customers[row[1]][0] for row in selected})
            elif metric in {"payment_value", "avg_order_payment"}:
                totals = [sum((p[0] for p in self.payments[row[0]]), Decimal(0)) for row in selected if self.payments[row[0]]]
                value = sum(totals, Decimal(0)) if totals else None
                if metric == "avg_order_payment" and totals:
                    value /= len(totals)
            elif metric in {"item_revenue", "freight_value"}:
                amounts = [item[0 if metric == "item_revenue" else 1] for row in selected for item in self.items[row[0]]]
                value = sum(amounts, Decimal(0)) if amounts else None
            else:
                raise ValueError("Unsupported challenge metric")
            values[metric] = value
        return values, {
            "eligible_orders": len(eligible),
            "distinct_consumers": len({self.customers[row[1]][0] for row in eligible}),
            "multi_payment_orders": sum(len(self.payments[row[0]]) > 1 for row in eligible),
            "multi_item_orders": sum(len(self.items[row[0]]) > 1 for row in eligible),
            "orders_without_payments": sum(not self.payments[row[0]] for row in eligible),
        }


def mutation_for(s: Scenario) -> str:
    return {"payment_grain": "payment_record_average", "customer_identity": "customer_record_identity",
            "join_fanout": "raw_join", "time_scope": s.time_mutation}[s.family]
