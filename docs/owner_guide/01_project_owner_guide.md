# VeriAgent 项目 Owner 总说明书

## 这份文档解决什么问题

这不是代码索引，而是项目负责人需要长期保持的系统心智模型。阅读后应能区分两套 runtime、说明它们为什么存在、指出当前完成边界，并知道哪些目录是产品代码、测试、实验输入或历史产物。

## 1. 项目一句话介绍

### 外层 VeriAgent

外层 VeriAgent 是一个面向 ProofWriter OWA 的**可验证检索与故障恢复研究 runtime**：它先规划目标及其显式补集的证据检索，利用本地检索和前向推理产生候选答案，再由 Auditor 验证证明或 Unknown 覆盖证书，由 Controller 在 `STOP / RETRIEVE / REPLAN / ROLLBACK / CONTINUE` 中选择下一步。

关键入口：

- `runner/agent_loop.py::VeriAgentRunner`
- `planner/deterministic.py::DeterministicPlanner`
- `auditor/deterministic.py::DeterministicAuditor`
- `controller/policy.py::DeterministicController`
- `tools/proof.py::ProofVerifier`

它解决的问题是：检索型推理系统不能只给一个标签，还要说明证据是否充分、证明能否机器验证，以及工具失败后能否恢复。

### Olist Data Agent

Olist Data Agent 是一个独立的、面向电商分析的**受约束数据问答 pipeline**：LLM 负责指标选择、候选 SQL 和最终表达；确定性代码负责 Metric Contract、表和 SQL 边界、DuckDB 只读执行、可选 Cross-check、安全停止及 Trace。

关键入口：

- `data_agent/runner/minimal_runner.py::DataAgentRunner`
- `data_agent/tools/`
- `data_agent/llm/`
- `data_agent/trace/`
- `scripts/data_agent/run_openpai_qwen_smoke.py`

它解决的问题是：SQL 能运行并不代表业务分析正确。系统需要显式控制指标口径、grain、状态范围、时间字段和一对多 join 风险，并留下可审计证据。

### 两者的真实关系

两者在同一个 Python 包和研究仓库中，但当前是**并列子系统，不是已经融合的一套 Agent**。

已经共享的只有工程层面的理念：

- 确定性验证优先于模型自信；
- 关键步骤生成结构化事件；
- 将“无法安全回答”与普通错误区分；
- 为未来审计、恢复和离线训练保留轨迹。

当前没有连接的部分：

- Data Agent 不调用外层 `DeterministicPlanner`；
- Data Agent 不调用外层 `DeterministicAuditor`；
- Data Agent 不调用外层 `DeterministicController`；
- Data Agent 没有使用外层 `CheckpointStore` 或 rollback；
- Data Agent 的 `audit_* / controller_decision / rollback_* / sql_repair_generated` 只是预留事件名；
- ProofWriter 的 proof verifier 不验证 SQL 或业务指标。

面试时最安全的表述是：

> 仓库先实现了面向符号推理的 VeriAgent 外层研究框架，后来新增了一个隔离的 Olist Data Agent，用相同的“模型生成、确定性验证、全过程留痕”原则验证数据分析场景。当前 Data Agent 是线性最小闭环，尚未接入外层 recovery loop。

## 2. 整体架构图

### 仓库级架构

```text
                         ┌───────────────────────────────┐
                         │      VeriAgent Repository      │
                         └───────────────────────────────┘
                                        │
                    ┌───────────────────┴───────────────────┐
                    │                                       │
        ┌───────────▼────────────┐              ┌───────────▼────────────┐
        │ ProofWriter VeriAgent   │              │ Olist Data Agent        │
        │ Planner                 │              │ Metric Contract          │
        │ Retrieval               │              │ Tool Registry            │
        │ Answerer / Auditor      │              │ SQL + DuckDB              │
        │ Controller / Rollback   │              │ Cross-check / Trace       │
        └─────────────────────────┘              └──────────────────────────┘
                    │                                       │
             ProofWriter tasks                         Olist six tables
```

### Data Agent 一次请求

```text
User Question
      │
      ▼
DataAgentRunner ─────────► TraceWriter
      │
      ▼
LLM Analysis Selection
      │  AnalysisSelection {analysis, metrics, needed_tables}
      ▼
Deterministic Metric Contract
      │
      ├── unsupported ──► STOP_WITH_DATA_GAP ──► Trace + Summary
      │
      ▼
Schema Inspection Tools
      │  contract required tables ∪ model tables
      ▼
LLM SQL Generation
      │  SQLGeneration {sql, reason}
      ▼
SQL Safety Check
      │  one SELECT/WITH; forbidden keywords; max rows
      ▼
Read-only DuckDB
      ▼
Optional Cross-check
      ▼
LLM Final Answer
      │  FinalAnswer {answer, key_values, limitations}
      ▼
trace.jsonl + run_summary.json
      ▼
Offline Gold Scorer（runtime 完成后才读取 Gold）
```

这里的验证必须谨慎理解：当前 runner 验证的是 SQL 成功执行以及可选 Cross-check 通过，不是完整 SQL 业务语义证明。

## 3. Repository Map

项目根在 `.../final/veriagent/`，但测试和结果目录位于其父级 `.../final/`。以下只保留 Owner 需要理解的目录。

### `veriagent/`

外层 ProofWriter runtime 的主体，包括 Planner、Retriever、Answerer、Auditor、Controller、State、Proof Tools、Baseline、Evaluation 和 CLI。

分类：**runtime 源码**。

### `veriagent/data_agent/`

Olist Data Agent runtime：线性 runner、LLM client、五个工具、DuckDB 连接协议和 trace。

分类：**runtime 源码**。阅读入口是 `data_agent/runner/minimal_runner.py`。

### `veriagent/datasets/`

包含 Olist 原始 CSV、六表 DuckDB、profiling 产物、runtime tasks、Gold JSONL 和 canonical/cross-check SQL。

分类：**数据与评估资产**。

```text
datasets/olistbr/
├── ecommerce.duckdb
├── *.csv
├── profiling/
└── tasks/v0/
    ├── tasks_v0.jsonl      runtime 可读
    ├── gold_v0.jsonl       仅离线验证/评分
    └── sql/                Gold、Cross-check、诊断 SQL
```

### `veriagent/knowledge/`

存放面向 Data Agent 的精简 Metric Contract 文档。分类：**知识和设计合同**。

注意：当前 runtime 实际查询的是 `data_agent/tools/metric_search.py` 中的 `METRICS` Python 字典，不会解析 Markdown。Markdown 是人工可读合同，不是唯一机器真相。

### `veriagent/scripts/`

离线构建与运维入口，包括构建 DuckDB、生成 profiling、验证 Gold、本地 mock smoke、OpenPAI Qwen smoke 和离线评分。

分类：**数据准备、部署和评估脚本**，不是单个在线请求路径的一部分。

### `tests/`（位于 `.../final/tests/`）

包含 ProofWriter 与 Data Agent 测试、FakeLLM 和固定 mock cases。分类：**自动化测试**。

最重要的 Data Agent 测试：

- `test_data_agent_tools_v0.py`
- `test_minimal_data_agent_v0.py`
- `test_olist_data_foundation.py`
- `test_olist_gold_v0.py`
- `test_openpai_qwen_smoke_v0.py`

### `results/`（位于 `.../final/results/`）

存放运行输出。Data Agent 当前可见的是 `results/data_agent/mock_traces/`。分类：**实验结果和演示资产**。

现有 mock trace 可以证明 pipeline 和工具曾生成预期产物，不能证明真实 Qwen smoke 已执行成功。当前没有可确认的 `qwen3_8b_smoke_v0` 真实结果目录。

### 历史研究产物

父级 `run_code/`、`data/results*`、`proxy/`、`sais/`、`tau_sweep/` 包含大量论文实验、批处理脚本和历史结果。维护 Data Agent 主流程时，不应从这些目录开始阅读，也不要将其中某个旧脚本误认成当前 runtime。

## 4. 当前完成能力

### 已经实现

#### 外层 VeriAgent

- ProofWriter 数据解析和本地检索；
- 目标及显式补集的确定性计划；
- 前向推理和 proof verification；
- Unknown 的相关证据覆盖检查；
- Auditor 和 Controller；
- checkpoint、rollback、replan；
- fault injection；
- symbolic/LLM baselines；
- trace 和 controller SFT 导出。

#### Olist Data Agent

- 六表 DuckDB 和只读连接；
- 六个冻结指标；
- 五个确定性工具；
- 三阶段 JSON-only LLM protocol；
- FakeLLM 与 OpenAI-compatible real client；
- DA01/DA02/DA10 最小集成流程；
- 净收入问题的 `STOP_WITH_DATA_GAP`；
- DA02 独立 SQL Cross-check；
- JSONL event trace 与 run summary；
- OpenPAI server readiness、输出防覆盖、运行后离线评分流程；
- 十个 Gold tasks 及 canonical/cross-check SQL。

### 尚未实现或尚未接入

- Data Agent 的 Planner/Auditor/Controller/Checkpoint/Rollback；
- SQL 业务语义的确定性 validator；
- 自动从问题或 SQL 触发 join cardinality checker；
- 自动生成独立 Cross-check SQL；
- 除 DA02 smoke 外的 runtime cross-check 覆盖；
- final `key_values` 与 SQL result 的 runtime 确定性一致性检查；
- SQL 复杂度、执行时间和内存预算；
- 完整 trace schema version、hash chain 或状态 replay；
- Data Agent 面向用户的服务/API/UI；
- 仓库内 OpenPAI job YAML；平台资源配置仍在仓库外；
- 可确认的真实 Qwen smoke 产物。

### 未来方向

合理的演进顺序是：

1. 补 SQL 语义验证和 final-value 一致性验证；
2. 将 join checker 变成按风险触发的强制步骤；
3. 明确 Cross-check 生成和信任边界；
4. 给 trace 增加协议版本和更严格 lifecycle 校验；
5. 再接入 Auditor/Controller/SQL repair/rollback；
6. 最后考虑在线服务或交互式 Demo。

不建议先做“大模型自主循环”，因为当前最薄弱的环节不是模型不会继续思考，而是系统还不能确定性证明候选 SQL 遵守了业务合同。

## 5. 文档与实现差异

| 文档或设计印象 | 当前真实实现 |
|---|---|
| Data Agent 已融入 VeriAgent recovery loop | 未融入；是独立线性 runner |
| 所有注册工具都会被 Agent 自动使用 | join checker 已注册，但 runner 不调用 |
| `verification_completed` 表示答案完全验证 | 只确认 SQL 成功和可选 Cross-check；final value 仍可能不一致 |
| Metric Contract Markdown 是 runtime source of truth | runtime 使用 Python `METRICS` 字典 |
| Cross-check 是每题标准步骤 | 参数可选；OpenPAI smoke 仅 DA02 使用 |
| Trace 已支持完整 replay | 当前 loader 只检查 JSON、event ID 和 step 顺序 |
| OpenPAI 流程包含完整 job 配置 | 仓库只有容器内 entrypoint，没有 job YAML |
| 安装项目即可运行 Data Agent | `pyproject.toml` 未声明 DuckDB 主依赖 |

## 6. Owner 的判断原则

面对任何新功能，先问四个问题：

1. 这是模型建议，还是可机器验证的事实？
2. 失败时系统会返回错误、数据缺口，还是一个看似合理的数字？
3. 这一步是否进入 trace，未来能否重建决策依据？
4. 测试是否证明 runtime 没有读取 Gold 或其他答案信息？

只要这四个问题没有明确答案，就不应把功能描述为“可验证”。
