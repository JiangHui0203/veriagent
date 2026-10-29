"""Frozen questions for memory ablations; no answers or scoring SQL here."""

MEMORY_TASKS = (
    {"task_id": "EM01", "question": "2017 年 12 月所有状态的订单一共有多少个？"},
    {"task_id": "EM02", "question": "2017 年 12 月 delivered 订单的 Customer Payment Value 是多少？"},
    {"task_id": "EM03", "question": "2017 年 10 月 delivered 订单中，客户支付金额、商品销售额和运费各是多少？"},
    {"task_id": "EM04", "question": "2018 年 1 月的净收入是多少？"},
)

MEMORY_TASK_SUITES = {
    "v1": MEMORY_TASKS,
    "v2": (
        {"task_id": "EM11", "question": "2018 年 6 月所有状态的订单一共有多少个？"},
        {"task_id": "EM12", "question": "2018 年 5 月 delivered 订单的 Customer Payment Value 是多少？"},
        {"task_id": "EM13", "question": "2018 年 2 月 delivered 订单中，客户支付金额、商品销售额和运费各是多少？"},
        {"task_id": "EM14", "question": "2018 年 3 月的净收入是多少？"},
    ),
    "v3": (
        {"task_id": "EM21", "question": "2017 年 7 月下单的订单总数是多少？包含所有订单状态。"},
        {"task_id": "EM22", "question": "2017 年 8 月下单且状态为 delivered 的订单数是多少？"},
        {"task_id": "EM23", "question": "2017 年 9 月下单且状态为 canceled 的订单数是多少？"},
        {"task_id": "EM24", "question": "2017 年 1 月的 Customer Payment Value 是多少？请按指标默认口径统计。"},
        {"task_id": "EM25", "question": "统计 2017 年 2 月下单的所有状态订单的客户支付金额；本题明确包含所有状态。"},
        {"task_id": "EM26", "question": "按下单月份统计，2017 年 3 月 delivered 订单的支付金额是多少？"},
        {"task_id": "EM27", "question": "2017 年 4 月下单的 delivered 订单，客户支付金额、商品销售额和运费分别是多少？"},
        {"task_id": "EM28", "question": "For delivered orders purchased in May 2017, report Customer Payment Value, Item Sales Value and Freight Value."},
        {"task_id": "EM29", "question": "请分别给出 2017 年 6 月下单且已送达（delivered）的订单的支付金额、商品销售额及运费。"},
        {"task_id": "EM30", "question": "2017 年 7 月的净收入是多少？"},
        {"task_id": "EM31", "question": "What was net revenue for orders purchased in August 2017?"},
        {"task_id": "EM32", "question": "请计算 2018 年 4 月扣除退款后的净收入。"},
    ),
}
