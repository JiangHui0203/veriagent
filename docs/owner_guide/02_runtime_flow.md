# Olist Data Agent Runtime 全流程

## 这份文档解决什么问题

这份文档以 DA02 为主案例，回答“一条问题经过谁、输入输出是什么、在哪里会失败”。主代码是 `data_agent/runner/minimal_runner.py::DataAgentRunner.run`。

## 0. 调用边界

Runner 输入：

```text
run_id:          可读且可作为目录名的运行标识
task_id:         任务标识，例如 DA02
question:        原始用户问题
cross_check_sql: 可选的独立验证 SQL
```

Runner 输出 `AgentRunResult`：

```text
run_id, task_id, model, status, final_answer, trace_path, summary_path
```

DA02 问题：

> 2017 年 11 月 delivered 订单的 Customer Payment Value 是多少？

OpenPAI smoke 通过 `scripts/data_agent/run_openpai_qwen_smoke.py::run_smoke` 调用；本地 mock 通过 `scripts/data_agent/smoke_minimal_agent.py` 或测试 fixture 调用。

## Step 1：Question Input

### 谁执行

`DataAgentRunner.run` 和 `TraceWriter`。

### 输入与处理

- `run_id="DA02"`
- `task_id="DA02"`
- 原始问题
- DA02 独立 Cross-check SQL

Runner 创建 `<trace_root>/DA02/trace.jsonl` 和 `run_summary.json`，发出 `run_started`、`question_received`。

### 输出

初始化后的 TraceWriter，运行状态为 `RUNNING`。

### 失败情况

- run ID 不符合简单标识正则；
- 输出目录无权限；
- trace 文件无法创建；
- 上层没有检查输出冲突时，旧 trace 可能被截断重写。

### 代码位置

- `data_agent/runner/minimal_runner.py:176-211`
- `data_agent/trace/writer.py:16-38`

## Step 2：LLM Metric Selection

### 谁执行

`analysis_messages()` 构造 prompt，注入的 LLM client 执行 `generate_json()`，`AnalysisSelection` 验证结构。

### 输入

- 原始问题；
- 六个可用 metric ID 和 display name；
- 输出 schema：`analysis / metrics / needed_tables`。

### 输出

```json
{
  "analysis": "Need delivered Customer Payment Value for November 2017.",
  "metrics": ["payment_value"],
  "needed_tables": ["orders", "order_payments"]
}
```

Trace 产生 `llm_request_started(stage=analysis)`、`llm_request_completed`、`analysis_generated`。

### 失败情况

- endpoint、timeout、API response shape 错误；
- 两次响应都不是合法 JSON；
- metrics 为空或字段类型不符。

失败进入 `_failed()`，不会继续查库。

### 代码位置

- `data_agent/llm/prompts.py:20-36`
- `data_agent/llm/schemas.py:9-33`
- `data_agent/llm/client.py:123-210`
- `data_agent/runner/minimal_runner.py:213-239`

## Step 3：Metric Contract Validation

### 谁执行

`ToolRegistry.call("search_metric_definition", ...)`，最终调用 `search_metric_definition()`。

### 输入

LLM 选择的指标字符串 `payment_value`。

### 输出

```text
metric_id:       payment_value
grain:           payment to order
time_field:      order_purchase_timestamp
default_scope:   delivered
required_tables: orders, order_payments
critical_rules:
  - payments 与另一一对多表连接前先聚合到 order_id
  - payment value 不是 accounting/net revenue
```

### 失败情况

- `UNSUPPORTED_METRIC`：已知不支持，进入安全停止；
- `METRIC_NOT_FOUND`：没有匹配的冻结指标，当前作为普通失败；
- registry 参数或 handler 错误。

LLM 的指标选择只是建议。系统是否允许计算、使用什么时间和 grain，由 contract 决定。

### 代码位置

- `data_agent/tools/metric_search.py:12-187`
- `data_agent/tools/registry.py:92-105`
- `data_agent/runner/minimal_runner.py:241-302`

## Step 4：Schema Inspection

### 谁执行

Runner 计算表集合，`inspect_schema()` 查询 DuckDB `information_schema`。

### 输入

```text
analysis.needed_tables ∪ 每个 resolved metric 的 required_tables
```

模型漏掉 `order_payments` 时 contract 会补上；模型编造 `refunds` 时 allowlist 会拒绝。

### 输出

每张表的真实表名、行数、列名和 DuckDB 类型，不返回业务数据行。

### 失败情况

- 表不在六表 allowlist；
- DuckDB 文件或表不存在；
- 数据库连接错误。

### 代码位置

- `data_agent/db.py:17-26,47-73`
- `data_agent/tools/schema_inspector.py:12-55`
- `data_agent/runner/minimal_runner.py:304-360`

## Step 5：SQL Generation

### 谁执行

`sql_generation_messages()`、LLM client、`SQLGeneration` schema。

### 输入

- 原始问题；
- resolved metric definitions；
- 相关表 schema；
- “一条只读 SELECT/WITH，遵守 grain/time/scope”的要求。

### 输出

正确形态：

```sql
WITH payment_by_order AS (
    SELECT order_id, SUM(payment_value) AS order_payment_value
    FROM order_payments
    GROUP BY order_id
)
SELECT ROUND(SUM(p.order_payment_value), 2) AS payment_value
FROM orders o
JOIN payment_by_order p ON o.order_id = p.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= TIMESTAMP '2017-11-01'
  AND o.order_purchase_timestamp < TIMESTAMP '2017-12-01'
```

以及简短 `reason`。此时只能称为候选 SQL，不能称为已验证 SQL。

### 失败情况

- LLM API/JSON/schema 失败；
- SQL 为空；
- reason 类型错误。

### 代码位置

- `data_agent/llm/prompts.py:39-57`
- `data_agent/llm/schemas.py:36-52`
- `data_agent/runner/minimal_runner.py:362-384`

## Step 6：SQL Safety Check

### 谁执行

`execute_sql()` 内部调用 `validate_read_only_sql()`。

### 输入

候选 SQL 和 `max_rows=200`。

### 检查

1. SQL 是非空字符串；
2. 屏蔽字符串和注释后分析；
3. 只能有一个 statement；
4. 第一个关键字必须是 SELECT 或 WITH；
5. 必须包含 SELECT；
6. 拒绝 INSERT、UPDATE、DELETE、DROP、ALTER、CREATE、COPY、IMPORT、ATTACH、INSTALL、LOAD、CALL 等；
7. max_rows 必须是正整数且不能是 bool。

### 失败情况

返回 `SQL_NOT_READ_ONLY`，runner 结束为 `FAILED`。

### 能防止与不能防止

能防止显式写操作和多 statement，但不能证明 grain、join、时间、scope 或计算复杂度正确，也没有 SQL 表级 allowlist。

### 代码位置

- `data_agent/tools/sql_executor.py:13-105`

## Step 7：DuckDB Execution

### 谁执行

`execute_sql()` 和 `read_only_connection()`。

### 处理与输出

- 以 `read_only=True` 打开 DuckDB；
- 执行 SQL；
- `fetchmany(max_rows + 1)`；
- 返回 columns、rows、returned_rows、truncated。

```json
{
  "columns": ["payment_value"],
  "rows": [[1153528.05]],
  "returned_rows": 1,
  "truncated": false
}
```

### 失败情况

SQL 语法、表列、类型、数据库访问或执行错误，统一为 `SQL_EXECUTION_ERROR`。

### 代码位置

- `data_agent/db.py:29-44`
- `data_agent/tools/sql_executor.py:107-155`
- `data_agent/runner/minimal_runner.py:386-419`

## Step 8：Cross Check

### 谁执行

仅当调用方传入 `cross_check_sql` 时，Runner 调用 `cross_check()`。DA02 独立 SQL 位于 `data_agent/runner/smoke_contract.py`。

### 输入

- `sql_a`：模型生成 SQL；
- `sql_b`：独立固定 SQL；
- absolute tolerance `0.01`；
- relative tolerance `1e-6`。

DA02 的 sql_b 先找 eligible order IDs，再用 `IN` 汇总 payment，没有嵌入期望数字。

### 输出

两侧结果、每个数值的绝对/相对差、容差和 `matched`。

### 失败情况

- 任一 SQL 失败；
- 结果超过 200 行；
- 列数、key set 或数值 shape 不匹配；
- 数值超出容差。

不匹配时不会进入 final-answer LLM。

### 当前边界

- Cross-check 是可选参数；
- OpenPAI smoke 仅 DA02 使用；
- Runner 不会生成独立 SQL；
- 两条 SQL 同时包含同类错误时仍可能误通过。

### 代码位置

- `data_agent/tools/cross_checker.py:89-180`
- `data_agent/runner/smoke_contract.py:8-24`
- `data_agent/runner/minimal_runner.py:421-478`

## Step 9：Final Answer

### 谁执行

`final_answer_messages()`、LLM client、`FinalAnswer` schema。

### 输入

原始问题、resolved metrics、SQL result 和可选 Cross-check result。

### 输出

```json
{
  "answer": "2017 年 11 月 delivered 订单的 Customer Payment Value 为 1,153,528.05。",
  "key_values": {"payment_value": 1153528.05},
  "limitations": []
}
```

### 失败情况

API、JSON、schema 或字段类型错误。

### 关键限制

Runner 当前不比较 `key_values.payment_value` 与 SQL result。模型如果把最终数字抄错，runtime 仍可能写 `SUCCESS`；真实 smoke 的离线 scorer 才会发现。

### 代码位置

- `data_agent/llm/prompts.py:60-80`
- `data_agent/llm/schemas.py:55-77`
- `data_agent/runner/minimal_runner.py:480-508`

## Step 10：Trace Output

### 谁执行

`TraceWriter.emit()` 和 `TraceWriter.finalize()`。

### 输出

`trace.jsonl` 保存完整时序事件；`run_summary.json` 保存：

```text
run_id
task_id
model
execution_backend
status
final_answer
event_count
```

成功路径最后写 `answer_generated`、`verification_completed`、`run_finished`。

### 失败情况

- 中途写盘失败；
- trace JSON 被破坏；
- event IDs 重复、不连续或 step 逆序；
- summary 缺失。

OpenPAI wrapper 会尝试把未捕获异常归为 `TRACE_WRITE_FAILURE`。

### 代码位置

- `data_agent/trace/events.py`
- `data_agent/trace/writer.py`
- `data_agent/trace/loader.py`
- `data_agent/runner/minimal_runner.py:509-538`

## DA10：为什么不能回答净收入

DA10 问题：

> 2017 年 11 月的净收入是多少？

### 业务原因

六表包含 payment、item price、freight 等，但没有完整 refund ledger，也没有成本、税费或平台佣金口径。因此：

```text
payment_value != net revenue
item sales value != net revenue
```

### Runtime 如何阻止模型偷换

Runner 在第一次 LLM 指标选择后直接检查原始 question。如果包含 `net revenue / net_revenue / 净收入`，就把 metric query 强制改回原问题。

即使模型返回 `{"metrics":["payment_value"]}`，contract 仍返回 `UNSUPPORTED_METRIC`。事件序列是：

```text
analysis_generated
→ metric_search_completed(UNSUPPORTED_METRIC)
→ data_gap_detected
→ verification_completed(DATA_GAP_CONFIRMED)
→ run_finished(STOP_WITH_DATA_GAP)
```

不会发生 schema、SQL、Cross-check 或 final-answer 调用。

### STOP_WITH_DATA_GAP 的意义

- `FAILED`：系统本应完成，但 API、SQL、schema 或工具失败；
- `STOP_WITH_DATA_GAP`：系统正确识别数据不支持问题，并拒绝编造答案。

“知道不能回答”是可信 Data Agent 的成功控制结果。

### 当前限制

- 第一次 LLM analysis 仍必须先成功；
- 显式 unsupported alias 只覆盖净收入相关字符串；
- 其他合同外指标通常是 `METRIC_NOT_FOUND/FAILED`，不是统一数据缺口状态。

## 用 Trace 定位一次请求

按以下顺序看：

1. 是否有 `run_started`；
2. 最后一个 `llm_request_completed` 的 stage/ok/error_type；
3. `metric_search_completed` 是否 success；
4. 所有 `schema_inspection_completed` 是否 success；
5. `sql_generated` 是否符合合同；
6. `sql_execution_completed` 的 rows/truncated；
7. `cross_check_completed` 是否 matched；
8. `answer_generated.key_values` 是否与 SQL result 一致；
9. `verification_completed` 实际验证了哪一层；
10. 最终是 `run_finished` 还是 `run_failed`。

不要只看 summary 的一句 answer。Trace 才是 Owner 判断系统真实行为的主要证据。
