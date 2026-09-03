# 7 天项目 Owner 学习路线

## 学习假设

- Python 基础一般；
- 工程经验有限；
- 对 Agent、Tool、Prompt、RAG 有概念；
- 每天投入 1.5–2.5 小时；
- 目标不是背代码，而是形成“输入—责任—输出—失败证据”思维。

每天都做一张纸的总结，只写：模块目标、输入、输出、调用者、失败点、当前限制。

## Day 1：建立两套架构的地图

### 阅读文件

1. `docs/owner_guide/01_project_owner_guide.md`
2. `../README_VERIAGENT.md`（项目父目录）
3. `data_agent/__init__.py`
4. `runner/agent_loop.py:27-160`
5. `data_agent/runner/minimal_runner.py:22-50`

### 理解目标

- 能区分 ProofWriter VeriAgent 与 Olist Data Agent；
- 知道外层是循环型恢复框架，Data Agent 是线性 pipeline；
- 能指出哪些模块没有连接；
- 能识别 runtime、tests、datasets、results、历史实验。

### 不要做

- 不要读所有 dataclass；
- 不要进入 `run_code/` 历史脚本；
- 不要尝试运行 OpenPAI。

### 当天产出

手画两张图：仓库双系统图、Data Agent 请求图。

### 自测问题

1. Data Agent 调用了哪个外层 Controller？正确答案是没有。
2. 两套系统真正共享的是什么？
3. 哪些目录是 runtime，哪些是结果？
4. 为什么不能用现有 mock trace 证明真实 Qwen 已验证？

## Day 2：读懂 Minimal Runner

### 阅读文件

1. `docs/owner_guide/02_runtime_flow.md`
2. `data_agent/runner/minimal_runner.py`
3. `data_agent/runner/smoke_contract.py`
4. `../tests/fixtures/data_agent_mock_cases.py`

### 理解目标

- 按顺序说出十个阶段；
- 理解 `_llm_generate/_failed/_safe_stop`；
- 理解 DA02 与 DA10 分支；
- 知道 Cross-check 由参数控制；
- 知道 SUCCESS 的验证边界。

### 阅读方法

先只标出每个外部调用：

```text
LLM → Registry → LLM → Registry → Registry? → LLM → Trace
```

第二遍再看每个失败后的 return，理解为何 runner 不会继续。

### 当天产出

用自己的话写一份 DA02 事件序列，以及 DA10 为什么只有一次 LLM call。

### 自测问题

1. model tables 和 contract tables 如何合并？
2. metric 未找到与 unsupported 有何区别？
3. final key value 抄错后谁会发现？
4. 什么条件下 final LLM 不会被调用？

## Day 3：掌握 Tools 和确定性边界

### 阅读文件

1. `docs/owner_guide/03_module_learning_guide.md` 的 Level 2
2. `data_agent/schemas.py`
3. `data_agent/tools/registry.py`
4. `data_agent/tools/metric_search.py`
5. `data_agent/tools/schema_inspector.py`
6. `data_agent/tools/sql_executor.py`
7. `data_agent/tools/join_checker.py`
8. `data_agent/tools/cross_checker.py`
9. `../tests/test_data_agent_tools_v0.py`

### 理解目标

- 区分 tool capability 和 invocation policy；
- 理解 ToolResult；
- 知道 SQL safety 与 semantic correctness 的差别；
- 能解释 join multiplication；
- 能解释 Cross-check 的独立性假设。

### 不要做

- 不必逐字符背 SQL mask state machine；
- 不要因为工具已注册就推断 runner 会调用；
- 不要把 `retryable` 当成已有 retry。

### 当天产出

制作五工具表：问题、输入、输出、错误类型、是否进入当前 runner。

### 自测问题

1. 哪些 SQL 会被本地拒绝？
2. 一条只读 SQL 为什么仍可能业务错误？
3. join checker 当前由谁调用？
4. Cross-check shape 为什么需要 key 和 numeric columns？

## Day 4：理解 DuckDB、Contract 和 Gold

### 阅读文件

1. `data_agent/db.py`
2. `scripts/data_agent/olist_common.py`
3. `scripts/data_agent/build_olist_duckdb.py`
4. `knowledge/metric_definitions.md`
5. `datasets/olistbr/tasks/v0/tasks_v0.jsonl`
6. `datasets/olistbr/tasks/v0/gold_v0.jsonl`
7. `datasets/olistbr/tasks/v0/sql/DA02_gold.sql`
8. `datasets/olistbr/tasks/v0/sql/DA02_crosscheck.sql`
9. `datasets/olistbr/tasks/v0/sql/DA09_gold.sql`
10. `datasets/olistbr/tasks/v0/sql/DA09_naive_join.sql`

### 理解目标

- 记住六表及其 grain；
- 理解六个指标的时间、scope 和公式；
- 区分 Contract 与 Gold；
- 理解 DA09 为何高估；
- 知道 build 是离线写操作，runtime 是只读。

### 当天产出

画出 orders、customers、items、payments、products、translation 的 join 图，并在每条边标注 1:1、1:N 或维表映射。

### 自测问题

1. 为什么 order_count 不默认 delivered？
2. customer_id 与 customer_unique_id 各做什么？
3. 为什么 payment value 不是 net revenue？
4. runtime 读取哪份合同？
5. 为什么 Gold SQL 可以存在仓库中但不能进入 runner？

## Day 5：理解 LLM 协议和 Fake/Real 分工

### 阅读文件

1. `docs/owner_guide/04_design_reasoning.md`
2. `data_agent/llm/prompts.py`
3. `data_agent/llm/schemas.py`
4. `data_agent/llm/client.py`
5. `../tests/fixtures/fake_llm.py`
6. `../tests/test_minimal_data_agent_v0.py:97-179`

### 理解目标

- 知道每个 prompt 给模型什么、没有给什么；
- 理解 JSON object、parser 和 schema 三层；
- 理解一次 repair 的触发条件；
- 理解 dependency injection；
- 不把 Fake 结果当模型能力结果。

### 当天产出

写一张 Fake/Real 对照表：构造方式、共同接口、网络、tokens、随机性、能证明什么、不能证明什么。

### 自测问题

1. `response_format` 与 dataclass schema 有什么区别？
2. schema error 会不会 repair？
3. endpoint 失败为什么不应该改 metric prompt？
4. 为什么 real client 不绑定特定 Qwen SDK？

## Day 6：理解 Trace、调试和 OpenPAI

### 阅读文件

1. `data_agent/trace/events.py`
2. `data_agent/trace/writer.py`
3. `data_agent/trace/loader.py`
4. `docs/owner_guide/05_debugging_and_maintenance.md`
5. `scripts/data_agent/run_openpai_qwen_smoke_job.sh`
6. `scripts/data_agent/run_openpai_qwen_smoke.py`
7. `scripts/data_agent/score_qwen_smoke.py`
8. `../tests/test_openpai_qwen_smoke_v0.py`
9. `../results/data_agent/mock_traces/DA02/trace.jsonl`

### 理解目标

- 根据 event sequence 定位失败；
- 区分 trace、summary、server log、offline score；
- 理解 OpenPAI 五层故障模型；
- 知道 job YAML 不在仓库内；
- 知道 trace loader 当前验证什么、不验证什么。

### 当天产出

选 DA02 trace，逐事件标注“LLM/Tool/Control/Evidence”；再假设分别在 analysis、SQL、Cross-check、final 阶段失败，写出最后应看到的事件。

### 自测问题

1. 没有任何 task trace 时先查哪里？
2. server ready 但模型 ID 不匹配会怎样？
3. 为什么 offline scorer 必须最后运行？
4. trace 能加载是否等于运行可信？
5. 当前旧 artifact 和 Writer 字段可能有什么差异？

## Day 7：面试复盘与 Owner 决策演练

### 阅读文件

1. `docs/owner_guide/06_interview_defense.md`
2. 回顾前六天笔记
3. `runner/agent_loop.py:93-160,234-287`
4. `data_agent/runner/minimal_runner.py:176-538`

### 理解目标

- 能做 3 分钟项目介绍；
- 能诚实说明两套架构和未连接部分；
- 面对追问先给证据，再给局限和未来计划；
- 能决定一个新需求应改 Contract、Tool、Runner、Trace 还是 Scorer。

### 三次口头演练

#### 演练 1：三分钟介绍

结构：问题背景 30 秒、架构 60 秒、DA02/DA10 例子 60 秒、完成边界和下一步 30 秒。

#### 演练 2：白板请求流程

不看材料画出：

```text
Question → Analysis → Contract → Schema → SQL → Safety
→ DuckDB → Cross-check → Answer → Trace → Offline Score
```

#### 演练 3：诚实防守

连续回答：

- join checker 是否自动使用？
- semantic validation 是否完成？
- recovery 是否接入？
- real Qwen 是否有可确认结果？
- SUCCESS 是否等价业务正确？

### 当天产出

写一页 Owner 决策表：

| 新需求 | 首先检查的模块 |
|---|---|
| 新业务指标 | Contract + Gold |
| 新表 | DB schema + allowlist + Contract |
| SQL 业务错误 | Semantic validator/Auditor |
| 模型格式错误 | Prompt + Schema + Client |
| 新恢复动作 | Auditor + Controller + Trace |
| OpenPAI 启动错误 | Job/Image/Server |
| 评分错误 | Trace + Scorer + Gold |

### 最终自测问题

1. 项目为什么不是普通 Text-to-SQL？
2. 为什么不能说 Data Agent 已有 rollback？
3. 哪些事实由 runtime 验证，哪些只由 offline scorer 验证？
4. 如果要优先完成一项工程增强，为什么应先做 SQL semantic validator？
5. 如果面试官指出当前限制，你能否给出具体代码证据而不是泛泛认同？

## 七天结束后的推荐持续节奏

每周选择一条真实或 mock trace，完成一次：

```text
问题口径复述
→ 预测 metric/schema
→ 审查 generated SQL grain
→ 检查 Cross-check
→ 对照 final answer
→ 写出一个未覆盖风险
```

当你可以在不运行模型的情况下，从代码和 trace 判断“系统为何成功、成功证明了什么、还没有证明什么”，就已经具备独立维护这个项目的 Owner 能力。
