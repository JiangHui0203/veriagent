# Olist Data Agent V0 Metric Contract

本合同只适用于 `olistbr/brazilian-ecommerce` Kaggle dataset version 2 的六表数据底座。默认业务月份统一使用 `orders.order_purchase_timestamp`；除 `order_count` 外，下列商业指标默认只统计 `order_status = 'delivered'`。

## 固定指标

| Metric | Display name | Grain / formula | Default scope |
|---|---|---|---|
| `order_count` | Order Count | order grain；`COUNT(DISTINCT orders.order_id)` | 全部已下单订单，不静默增加 delivered 条件 |
| `payment_value` | Customer Payment Value | 先按 `order_payments.order_id` 汇总 `SUM(payment_value)`，再与订单关联后求和 | delivered |
| `avg_order_payment` | Average Payment per Order | 先得到每单 `order_payment_value`，再 `AVG(order_payment_value)` | delivered |
| `item_revenue` | Item Sales Value | item grain；`SUM(order_items.price)` | delivered |
| `freight_value` | Freight Value | item grain；`SUM(order_items.freight_value)` | delivered |
| `unique_customers` | Unique Customers | `orders.customer_id = customers.customer_id` 后，`COUNT(DISTINCT customers.customer_unique_id)` | delivered |

## 强制语义与安全规则

- `payment_value` 是客户支付记录合计；`item_revenue` 仅是商品行价格合计，展示名必须使用 **Item Sales Value**。二者不是同一个指标。
- “revenue” 不得默认解释成会计收入；当前六表不支持可靠的净收入，因为没有完整退款台账。遇到净收入问题应报告数据缺口。
- `customer_id` 是订单与客户表的连接键；跨订单识别真实消费者必须使用 `customer_unique_id`，二者不可互换。
- `orders → order_items` 和 `orders → order_payments` 都可能是一对多。禁止在 raw item/payment 联表结果上直接汇总支付金额、商品金额或运费。
- 同时需要 item 与 payment 指标时，两侧都必须先汇总到 `order_id` 粒度，再进行连接。
- `AVG(payment_value)` 是 payment-record 平均值，不是 Average Payment per Order，默认禁止用作 `avg_order_payment`。
- 若问题没有指定其他时间语义，月份一律取购买月份；不得静默切换到批准、发货、签收或预计送达时间。
- delivered 条件不得静默应用于 `order_count`；若需要 delivered 订单数，必须明确标为 `delivered_order_count`。

## 验证最低要求

数值结果至少验证：指标定义、目标 grain、连接键与一对多关系、时间字段、状态范围。payment 与 item 同时参与时，必须检查 raw join row multiplication，并以 order-grain 聚合结果作为安全结果。
