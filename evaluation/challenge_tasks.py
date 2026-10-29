"""Versioned, hand-authored scenarios. No model results influence the split."""

from __future__ import annotations

from dataclasses import asdict, dataclass

VERSION = "olist_challenge_v1"


@dataclass(frozen=True)
class Scenario:
    task_id: str
    split: str
    family: str
    metrics: tuple[str, ...]
    start: str
    end: str
    scope: str = "delivered"
    state: str | None = None
    payments_min: int = 0
    items_min: int = 0
    payment_type: str | None = None
    installments_min: int = 0
    repeat_customer: bool = False
    delivered_start: str | None = None
    delivered_end: str | None = None
    time_mutation: str | None = None

    @property
    def group_key(self) -> tuple:
        # Group out simple paraphrases/date/state substitutions. Same error
        # families occur in both splits, with different predicate combinations.
        return (self.family, self.metrics, self.scope, bool(self.state), bool(self.payments_min),
                bool(self.items_min), self.payment_type, bool(self.installments_min),
                self.repeat_customer, bool(self.delivered_start))

    @property
    def question(self) -> str:
        parts = [f"统计购买时间从 {self.start}（含）到 {self.end}（不含）的订单。"]
        if self.scope == "delivered":
            parts.append("仅考虑状态为 delivered 的订单。")
        elif self.scope == "all":
            parts.append("本题明确包含全部订单状态，所有所求指标均使用这个范围。")
        elif self.scope == "mixed":
            parts.append("订单数包含全部状态；客户支付金额仅统计其中 delivered 订单。")
        else:
            raise ValueError("Unsupported scenario scope")
        if self.state:
            parts.append(f"客户所在州为 {self.state}。")
        if self.payments_min:
            parts.append(f"只考虑至少有 {self.payments_min} 条支付记录的订单。")
        if self.items_min:
            parts.append(f"只考虑至少有 {self.items_min} 条商品明细的订单。")
        if self.payment_type:
            constraint = f"且该记录的分期数至少为 {self.installments_min}" if self.installments_min else ""
            parts.append(f"订单须有至少一条 {self.payment_type} 类型的支付记录{constraint}；所求支付指标包含这些订单的全部支付记录。")
        if self.delivered_start:
            parts.append(f"同时要求实际签收时间从 {self.delivered_start}（含）到 {self.delivered_end}（不含）。")
        if self.repeat_customer:
            parts.append("在满足上述全部条件的订单范围内，只保留至少下过两单的真实消费者对应的订单。")
        names = {"avg_order_payment": "平均每单支付金额", "payment_value": "客户支付金额",
                 "unique_customers": "独立真实消费者数量", "order_count": "订单数",
                 "item_revenue": "商品销售额", "freight_value": "运费"}
        parts.append("请给出" + "、".join(names[metric] for metric in self.metrics) + "。")
        return "".join(parts)

    def task(self) -> dict:
        return {"task_id": self.task_id, "question": self.question}

    def specification(self) -> dict:
        return {**asdict(self), "group_key": self.group_key, "question": self.question}


P = ("avg_order_payment",)
C = ("unique_customers",)
J = ("payment_value", "item_revenue", "freight_value")

# Development and held-out recipes are fixed together, before baseline inference.
SCENARIOS = (
    Scenario("CP01", "dev", "payment_grain", P, "2017-01-01", "2017-07-01", payments_min=2),
    Scenario("CP02", "dev", "payment_grain", P, "2017-01-01", "2018-01-01", state="SP"),
    Scenario("CP03", "dev", "payment_grain", P, "2017-01-01", "2018-01-01", payment_type="voucher"),
    Scenario("CC01", "dev", "customer_identity", C, "2017-01-01", "2018-01-01"),
    Scenario("CC02", "dev", "customer_identity", C + ("order_count",), "2017-01-01", "2018-01-01", state="SP", repeat_customer=True),
    Scenario("CC03", "dev", "customer_identity", C, "2017-01-01", "2018-01-01", items_min=2),
    Scenario("CJ01", "dev", "join_fanout", J, "2017-01-01", "2017-07-01", items_min=2),
    Scenario("CJ02", "dev", "join_fanout", J, "2017-01-01", "2017-07-01", payments_min=2, state="SP"),
    Scenario("CJ03", "dev", "join_fanout", ("payment_value", "freight_value"), "2017-07-01", "2018-01-01", items_min=2, payment_type="voucher"),
    Scenario("CT01", "dev", "time_scope", ("order_count", "payment_value"), "2017-11-25", "2017-12-05", scope="mixed", time_mutation="force_delivered"),
    Scenario("CT02", "dev", "time_scope", ("payment_value",), "2017-03-01", "2017-04-01", delivered_start="2017-04-01", delivered_end="2017-05-01", time_mutation="ignore_delivery"),
    Scenario("CT03", "dev", "time_scope", ("order_count",), "2017-12-25", "2018-01-05", scope="all", time_mutation="approval_time"),
    Scenario("CP11", "test", "payment_grain", P + ("payment_value",), "2018-01-01", "2018-07-01", payments_min=2, state="MG"),
    Scenario("CP12", "test", "payment_grain", P, "2018-01-01", "2018-07-01", items_min=2, payment_type="credit_card", installments_min=3),
    Scenario("CP13", "test", "payment_grain", P, "2018-01-01", "2018-07-01", state="SP", payments_min=2, payment_type="voucher"),
    Scenario("CC11", "test", "customer_identity", C + ("order_count",), "2018-01-01", "2018-07-01", scope="all", state="SP"),
    Scenario("CC12", "test", "customer_identity", C, "2018-01-01", "2018-07-01", payment_type="credit_card", repeat_customer=True),
    Scenario("CC13", "test", "customer_identity", C + ("payment_value",), "2018-01-01", "2018-07-01", items_min=2, repeat_customer=True),
    Scenario("CJ11", "test", "join_fanout", J, "2018-01-01", "2018-07-01", items_min=2, payments_min=2),
    Scenario("CJ12", "test", "join_fanout", J, "2018-01-01", "2018-07-01", state="MG", payment_type="credit_card", installments_min=6),
    Scenario("CJ13", "test", "join_fanout", J, "2018-01-01", "2018-07-01", scope="all", items_min=2, payment_type="voucher"),
    Scenario("CT11", "test", "time_scope", ("order_count", "payment_value"), "2018-01-01", "2018-07-01", scope="mixed", state="RJ", items_min=2, time_mutation="force_delivered"),
    Scenario("CT12", "test", "time_scope", P + ("payment_value",), "2018-05-01", "2018-06-01", state="SP", delivered_start="2018-06-01", delivered_end="2018-07-01", time_mutation="ignore_delivery"),
    Scenario("CT13", "test", "time_scope", ("order_count", "unique_customers"), "2018-01-01", "2018-07-01", scope="all", payments_min=2, time_mutation="force_delivered"),
)


def validate_splits(scenarios=SCENARIOS) -> None:
    if len({s.task_id for s in scenarios}) != len(scenarios):
        raise ValueError("Duplicate challenge task ID")
    groups = {}
    for scenario in scenarios:
        if scenario.split not in {"dev", "test"}:
            raise ValueError("Unknown challenge split")
        previous = groups.setdefault(scenario.group_key, scenario.split)
        if previous != scenario.split:
            raise ValueError("Scenario group crosses development/test split")
    if len({s.question for s in scenarios}) != len(scenarios):
        raise ValueError("Duplicate challenge question")
