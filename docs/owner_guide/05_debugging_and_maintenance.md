# 调试与维护手册

## 使用原则

调试时不要先改 prompt。按层排查：

```text
Environment
→ Input/Config
→ LLM Protocol
→ Metric Contract
→ Schema
→ SQL Safety
→ DuckDB Execution
→ Cross-check
→ Final Answer
→ Trace/Summary
→ Offline Score
```

每次只回答三个问题：最后一个成功事件是什么、下一步预期事件是什么、失败是确定性拒绝还是外部异常。

## 1. 先认识输出文件

单任务目录应包含：

```text
<output_root>/<task_id>/
├── trace.jsonl
└── run_summary.json
```

OpenPAI smoke 还应包含：

```text
<output_root>/
├── server.log
├── smoke_summary.json
├── offline_score.json
├── DA01/
├── DA02/
└── DA10/
```

证据优先级：

1. 平台/job 状态：容器是否启动；
2. `server.log`：模型服务是否 ready；
3. `smoke_summary.json`：每题 runtime 状态；
4. task trace：精确失败阶段；
5. task summary：最终返回；
6. `offline_score.json`：业务和 Gold 对比。

## 2. LLM 失败

### 现象 A：没有 `llm_request_completed`

可能原因：

- endpoint 不可达；
- server 尚未启动；
- 请求长期阻塞；
- 进程被平台终止；
- trace 写入在请求前后失败。

排查顺序：

1. 看 `server.log` 是否出现加载成功；
2. 看 `/models` readiness 是否通过；
3. 核对 `LLM_BASE_URL` 是否包含正确 `/v1`；
4. 核对 `LLM_MODEL` 是否与 `/models` ID 精确一致；
5. 看 trace 是否至少有 `llm_request_started`；
6. 看平台超时/OOM，而不是先改 prompt。

代码位置：`data_agent/llm/client.py::_http_transport/_request`。

### 现象 B：`LLM_API_ERROR`

可能原因：

- connection refused、DNS、timeout；
- API response 不是 object；
- 没有 choices；
- message 没有字符串 content；
- backend 不完全兼容 OpenAI chat completions。

排查顺序：

1. 查看 event 的 `error` 原文；
2. 确认失败 stage 是 analysis、sql_generation 还是 final_answer；
3. 对照服务端 log 的同一时间；
4. 核对 endpoint 路径、model、key；
5. 再判断是否需要兼容 backend response。

不要做：把所有 API 错误归为“模型能力不行”。

### 现象 C：`JSON_PARSE_ERROR`

可能原因：

- 模型返回解释性文字；
- JSON 未闭合；
- backend 忽略 `response_format`；
- 第一次和一次 repair 都失败。

排查顺序：

1. 看 `raw_text`；
2. 看 `attempts` 是否为 2；
3. 检查 system prompt 是否仍要求 JSON-only；
4. 用相同 backend 检查 `response_format` 支持；
5. 判断是 prompt compliance 还是 client parser 问题。

当前行为：只修复 JSON parse 一次；schema 错误不进行 repair。

### 现象 D：`JSON_SCHEMA_ERROR`

可能原因：

- analysis 为空；
- metrics 不是非空字符串列表；
- SQL 为空；
- key_values 不是 object；
- limitations 不是字符串列表。

排查顺序：

1. 确认 response type；
2. 看 raw JSON；
3. 对照 `data_agent/llm/schemas.py`；
4. 判断 schema 是否太严，还是模型输出确实不符合协议；
5. 不要绕过 schema 直接继续运行。

## 3. SQL 失败

### 现象 A：`SQL_NOT_READ_ONLY`

可能原因：

- 多 statement；
- 开头不是 SELECT/WITH；
- 包含 forbidden keyword；
- SQL/comment/string 未闭合；
- max_rows 非正整数。

排查顺序：

1. 读 `sql_generated.data.sql`；
2. 看错误是 statement、first keyword 还是 forbidden keyword；
3. 确认不是 prompt 要求模型执行 DDL；
4. 对照 `sql_executor.py::validate_read_only_sql`；
5. 修复生成协议，而不是放宽只读规则。

### 现象 B：`SQL_EXECUTION_ERROR`

可能原因：

- hallucinated table/column；
- SQL 方言不兼容 DuckDB；
- alias、group by 或类型错误；
- DB 文件/表异常；
- 查询本身资源消耗过高。

排查顺序：

1. 看前面的 `schema_inspection_completed`；
2. 对照 generated SQL 中每个表列；
3. 区分 syntax、binder、catalog、conversion 错误；
4. 在确认数据路径正确后，才单独复现 SQL；
5. 检查 prompt 是否把真实 schema 传给模型。

### 现象 C：SQL 成功但结果可疑

可能原因：

- 错误时间字段；
- delivered/all-order scope 错误；
- `COUNT(*)` 在一对多 join 后膨胀；
- 直接 `AVG(payment_value)`；
- raw item/payment join；
- NULL/unmapped category 被静默丢弃。

排查顺序：

1. 回到 metric contract，而不是先看最终文案；
2. 写出目标 grain；
3. 标出每次 join 前后的 grain；
4. 看是否同时引入 items 和 payments；
5. 用 join checker 测量 pairwise cardinality；
6. 找对应 Gold SQL 只做离线对照，不把它传回 runtime；
7. 检查是否存在独立 Cross-check。

重要：当前 runner 不自动进行这套 semantic audit。

### 现象 D：Cross-check 失败

可能原因：

- 主 SQL 错；
- 验证 SQL 错；
- 两者 scope/grain 不同；
- 结果 shape 不可比较；
- 超过 200 行；
- 差异超过容差。

排查顺序：

1. 两侧 `result_a/result_b` 是否都执行成功；
2. 是否 truncated；
3. columns、row count、key set 是否一致；
4. 比较过滤条件、时间边界和 NULL 处理；
5. 再看 absolute/relative difference；
6. 不要为了通过测试盲目扩大 tolerance。

## 4. 数据问题

### 现象 A：`ModuleNotFoundError: duckdb`

真实原因：代码直接依赖 DuckDB，但当前 `pyproject.toml` 主依赖列表没有声明 `duckdb`。此外 `data_agent/__init__.py` eager import 了 DB 工具，因此只想导入部分子模块也可能被阻塞。

排查顺序：

1. 确认正在使用哪个 Python environment；
2. 在受控环境中检查 DuckDB 是否由镜像提供；
3. 核对项目依赖声明与镜像实际依赖；
4. 不在生产/共享环境临时安装后假装依赖问题已解决；
5. 后续单独规划依赖声明修复。

本指南生成过程中未安装依赖。

### 现象 B：DuckDB 找不到或表缺失

可能原因：

- `DATABASE` 路径错误；
- CSV 未完整同步；
- build script 未运行或中断；
- 指向另一个版本数据库；
- 建库时行数验证失败并 rollback。

排查顺序：

1. 确认 resolved database path；
2. 检查六表是否存在；
3. 对照 `olist_common.py::EXPECTED_ROW_COUNTS`；
4. 检查源 CSV；
5. 需要重建时先备份/明确目标，因为 build 会 drop/recreate 六表。

### 现象 C：Contract 与结果不一致

可能原因：

- Python `METRICS` 与 Markdown 不一致；
- Gold SQL 未随合同更新；
- aliases 未覆盖新表达；
- default scope/time field 被改动一处但未同步。

排查顺序：

1. 确认 runtime 实际使用 `metric_search.py`；
2. 对照 `knowledge/metric_definitions.md`；
3. 对照 `datasets/metric_definitions.md`；
4. 对照 Gold records 和 SQL；
5. 对照 metric/tool/gold tests；
6. 把差异记录为合同迁移，不做静默修补。

### 现象 D：Join 数值膨胀

高风险结构：

```text
orders
├── order_items      1:N
└── order_payments   1:N
```

同一个 order 有 M 个 item、N 个 payment 时，raw join 产生 M×N 行。

排查顺序：

1. 查 generated SQL 是否同时 raw join 两侧；
2. 调用 join checker 检查 pairwise relationship；
3. 确认两侧是否先 `GROUP BY order_id`；
4. 对照 DA09 safe/naive fixture；
5. 重新对账，而不是只加 DISTINCT 掩盖问题。

## 5. Trace 问题

### 现象 A：trace 不能加载

可能原因：

- 非法 JSON 行；
- event ID 重复、不连续或不是从 1 开始；
- step 逆序；
- 中途写盘只留下半行。

排查顺序：

1. 根据 Loader 报错行号看原始行；
2. 检查最后一行是否因进程终止不完整；
3. 检查 run ID 是否被重复使用；
4. 对比 summary event_count；
5. 不要手工改 trace 后继续当原始实验证据。

### 现象 B：trace 能加载但逻辑不完整

Loader 当前不会检查：

- event type 是否属于 EVENT_TYPES；
- lifecycle 是否从 started 到 finished/failed；
- LLM started/completed 是否成对；
- summary 与最后事件是否一致；
- model/backend/schema version；
- 内容是否被篡改。

因此还要人工检查阶段序列。未来可增加 protocol version 和 lifecycle validator。

### 现象 C：现有 artifact 与当前代码字段不同

当前 Writer summary 会写 `execution_backend`，但旧 mock summary 可能没有。这说明 trace 协议缺少显式版本。

处理原则：

1. 不修改旧 artifact；
2. 记录它由旧版本产生；
3. 新 reader 对缺失可选字段保持明确兼容；
4. 新协议变更时增加 version，而不是靠猜测。

## 6. OpenPAI 问题

### 先分五层

```text
1. Job/YAML/资源层
2. Container/Image/Dependency 层
3. Model Server 层
4. Data Agent Runtime 层
5. Offline Scoring 层
```

仓库内没有 OpenPAI job YAML，因此第 1 层必须到平台任务配置中查，不能只看代码仓库。

### 现象 A：Job 根本没启动

可能原因：YAML、队列、GPU 资源、volume mount、image pull、权限。

排查顺序：

1. 平台 job 状态和 event；
2. job YAML/表单中的 image、GPU、mount、command；
3. `PROJECT_ROOT/MODEL_PATH` 是否映射到容器；
4. 容器 entrypoint 是否真的执行 shell script。

此时查 Data Agent trace 没有意义。

### 现象 B：entrypoint 立即退出 2/3/4

- exit 2：`QWEN_SERVER_CMD` 缺失或 timeout 参数非法；
- exit 3：project/model/tasks/database/duckdb 不可访问；
- exit 4：输出目录已有真实运行产物，脚本拒绝覆盖。

排查顺序：看 shell stderr，再核对环境变量和 mount。

### 现象 C：Server start/readiness failure

可能原因：

- `QWEN_SERVER_CMD` 与镜像不匹配；
- 模型路径错误；
- CUDA/OOM；
- 端口占用；
- `/models` 不可用；
- 返回 model ID 与 `LLM_MODEL` 不一致；
- 600 秒内未 ready。

排查顺序：

1. `server.log` 尾部；
2. server process 是否提前退出；
3. CUDA/OOM/权重文件；
4. 端口和 base URL；
5. `/models` payload；
6. expected model ID；
7. 只有确认模型确实需要更久时才调整 readiness timeout。

### 现象 D：Smoke 完成但 Offline Score 失败

可能原因：

- metric selection 错；
- SQL 数值错；
- DA02 Cross-check 未通过；
- final key value 抄错；
- DA10 执行了 SQL或返回数字；
- trace 缺失。

排查顺序：

1. `offline_score.json` 的 checks；
2. SQL numeric 与 final numeric 分开看；
3. metric selection；
4. Cross-check；
5. 最后回到 prompt/contract/SQL。

## 7. 维护变更清单

### 修改或新增 Metric

必须同步检查：

1. `data_agent/tools/metric_search.py::METRICS`；
2. `knowledge/metric_definitions.md`；
3. `datasets/metric_definitions.md`；
4. required table allowlist 是否足够；
5. runtime task；
6. Gold record；
7. canonical/cross-check SQL；
8. tool、Gold、runner 测试；
9. safe-stop/unsupported 行为。

### 修改 LLM Schema 或 Prompt

必须同步检查：

1. prompt `output_schema`；
2. dataclass `from_dict`；
3. Fake responses；
4. trace data；
5. scorer 读取路径；
6. real backend 是否支持相应 response format；
7. 旧 trace 兼容策略。

### 修改 Trace

必须同步检查：

1. Writer；
2. Loader；
3. Runner emit；
4. OpenPAI statistics；
5. Offline scorer；
6. Tests；
7. 协议版本与旧 artifact。

### 修改 OpenPAI 流程

必须分清：

- 仓库内 entrypoint；
- 仓库外 job YAML/平台配置；
- image 提供的依赖；
- external server command；
- output mount 和防覆盖策略。

## 8. 发布或合并前 Owner 检查

- 是否改了 runtime 读取范围，可能碰到 Gold？
- 是否把 prompt 建议误写成确定性验证？
- 是否新增工具但忘记 control policy？
- 是否新增事件但没更新 loader/scorer/tests？
- 是否会覆盖旧真实结果？
- 是否区分 FAILED 与 DATA_GAP？
- 是否记录当前未实现边界？
- 是否在受控环境运行了对应测试，而不是只看历史 mock artifact？

如果最后一项没有完成，只能说“代码和文档已更新，尚未完成运行验证”。
