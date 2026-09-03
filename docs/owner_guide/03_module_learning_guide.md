# 模块学习指南：按理解难度读代码

## 使用方法

不要按文件夹字母顺序阅读。每一层先回答“它在系统边界中负责什么”，再读关键函数。看到实现细节时始终区分：模型建议、确定性控制、离线评估。

## Level 0：先辨认两套系统

### 模块：外层 VeriAgent

**解决什么问题**：ProofWriter 中的证据检索、可验证证明、Unknown 覆盖和工具故障恢复。

**为什么需要**：单次模型回答无法证明检索完整，也无法在 timeout/dropout 后恢复。

**输入**：结构化 `Task`，内含 world、question 和 gold label（gold label 用于运行结果评估，不作为推理证据）。

**输出**：`RunResult`，包含 answer、proof validity、coverage、action counts、fault/recovery 状态和 events。

**被谁调用**：`cli.py` 的 run 命令及 baseline/实验代码。

**出问题如何定位**：从 `VeriAgentRunner.run()` 循环看 state、latest audit、controller decision、pending requests、budget 和最后状态。

**面试如何解释**：这是“可恢复的检索推理 Agent”；不要把它的 Planner/Auditor/Controller 说成 Data Agent 已经使用的模块。

关键文件：

- `runner/agent_loop.py`
- `planner/deterministic.py`
- `auditor/deterministic.py`
- `controller/policy.py`
- `tools/proof.py`

阅读重点：`run()` 的 while loop、Controller action protocol、Unknown coverage。

不要关注：初次阅读不必展开所有 dataclass、unification 细节和 baseline prompt。

## Level 1：理解 Data Agent 主流程

### 模块：Minimal Runner

**解决什么问题**：把三个 LLM 阶段、五类工具能力和 trace 串成一个确定顺序的最小闭环。

**为什么需要**：没有统一 runner 时，prompt、SQL、验证和结果写盘会散落在实验脚本里，难以测试和审计。

**输入**：注入的 LLM client、tool registry、trace root，以及每次运行的 run ID、task ID、question、可选 Cross-check SQL。

**输出**：`AgentRunResult` 和两份运行文件。

**被谁调用**：本地 mock smoke、OpenPAI real smoke、集成测试。

**出问题如何定位**：先看 `_llm_generate/_failed/_safe_stop` 三个公共分支，再沿 `run()` 中的 event step 找最后一个完成阶段。

**面试如何解释**：它是 dependency-injected linear orchestrator，不是 ReAct 式无限工具循环，也不是外层 recovery controller。

关键文件和函数：

- `data_agent/runner/minimal_runner.py`
  - `DataAgentRunner.__init__`
  - `_llm_generate`
  - `_failed`
  - `_safe_stop`
  - `run`
- `data_agent/runner/smoke_contract.py::smoke_cross_check_sql`

阅读重点：

1. LLM 调用与确定性调用如何交替；
2. DA10 为什么只调用一次 LLM；
3. required tables 如何与 model tables 合并；
4. Cross-check 为什么由调用参数控制；
5. 什么条件下写 SUCCESS、FAILED、STOP_WITH_DATA_GAP。

不要关注：第一次不要逐条记 event 文案，也不要纠结 dataclass 语法。

自测：

- 如果 LLM 为净收入选择 `payment_value`，谁覆盖它？
- 如果 final answer 抄错 SQL 数字，runtime 会不会失败？
- 没传 `cross_check_sql` 时，verification 的证据是什么？

## Level 2：理解能力与安全边界

### 模块：Tool Registry 与 ToolResult

**解决什么问题**：给所有工具统一名称、说明、参数 schema、handler 和错误结构。

**为什么需要**：runner 不应为每类工具写不同异常协议；未来模型或 controller 也需要可枚举工具描述。

**输入**：tool name 和 arguments object。

**输出**：统一 `ToolResult {tool, ok, result, error_type, error, retryable}`。

**被谁调用**：DataAgentRunner、smoke scripts、工具测试。

**出问题如何定位**：看 `UNKNOWN_TOOL / INVALID_ARGUMENT / TOOL_ERROR / INVALID_TOOL_RESULT`；注意 argument schema 当前主要是描述，registry 没有通用 JSON Schema validator。

**面试如何解释**：Tool layer 将 LLM 与数据库能力隔离，并提供稳定错误协议；它不是“工具注册后模型就会自动使用”。

关键文件：`data_agent/schemas.py`、`data_agent/tools/registry.py`。

### 模块：Metric Search

**解决什么问题**：把自然语言或 metric ID 映射为冻结指标合同，拒绝已知不支持指标。

**为什么需要**：Text-to-SQL 最大风险往往不是 SQL syntax，而是业务口径漂移。

**输入**：指标查询字符串。

**输出**：metric ID、定义、grain、time field、scope、required tables、critical rules，或确定性错误。

**被谁调用**：Runner Step 3；也可被工具 smoke 单独调用。

**出问题如何定位**：检查 normalize 后的 query、aliases、`UNSUPPORTED_ALIASES` 和最长匹配选择。

**面试如何解释**：这是机器可执行的 semantic allowlist；当前并非向量 RAG，也不读取 Markdown。

关键文件：`data_agent/tools/metric_search.py`。

不要关注：不要把简单字符串匹配包装成语义搜索或 RAG retrieval。

### 模块：Schema Inspector

**解决什么问题**：给 SQL 生成提供真实列名和类型，同时避免直接把全表数据交给模型。

**为什么需要**：减少 hallucinated column，并限制模型上下文和数据暴露。

**输入**：一张表名和可选 DB path。

**输出**：row count、column names/types。

**被谁调用**：Runner Step 4。

**出问题如何定位**：先看六表 allowlist，再看 DuckDB 路径和 `information_schema`。

**面试如何解释**：模型不持有数据库连接，只看到最小必要 schema。

关键文件：`data_agent/db.py`、`data_agent/tools/schema_inspector.py`。

### 模块：SQL Executor

**解决什么问题**：拒绝明显危险 SQL，在只读 DuckDB 中执行并限制输出行数。

**为什么需要**：prompt 约束不能替代执行权限和代码检查。

**输入**：SQL、max rows、可选 DB path。

**输出**：columns、rows、returned rows、truncated，或结构化错误。

**被谁调用**：Runner、Cross-check、工具测试。

**出问题如何定位**：区分 `SQL_NOT_READ_ONLY` 与 `SQL_EXECUTION_ERROR`；前者是本地规则拒绝，后者是 DuckDB 执行错误。

**面试如何解释**：这是 syntax/safety guard，不是 business semantic verifier。

关键文件和函数：

- `data_agent/tools/sql_executor.py::_mask_literals_and_comments`
- `validate_read_only_sql`
- `execute_sql`

不要关注：不需要逐字符背 mask state machine；理解它为何避免注释/字符串误判即可。

### 模块：Join Checker

**解决什么问题**：测量两表连接键是否唯一、join relationship 和右侧 fanout 风险。

**为什么需要**：orders→items 和 orders→payments 都是一对多，raw join 会扩大行数和金额。

**输入**：left/right table、left/right key。

**输出**：row counts、unique keys、duplicate groups、joined rows、unmatched keys、relationship、multiplication risk。

**被谁调用**：工具 smoke 和测试；**当前不被 DataAgentRunner 调用**。

**出问题如何定位**：检查 allowlist、列是否存在和 relationship 的方向定义。

**面试如何解释**：能力已实现，但自动 policy 尚未接入；不要声称每条生成 SQL 都通过 cardinality 检查。

关键文件：`data_agent/tools/join_checker.py`。

### 模块：Cross Checker

**解决什么问题**：比较两条独立小型数值聚合 SQL，而不是相信一次生成结果。

**为什么需要**：同一业务值用不同查询路径得到一致结果，可以降低单条 SQL 实现错误风险。

**输入**：sql_a、sql_b、绝对/相对容差。

**输出**：matched、两侧结果、差异、逐值 comparisons。

**被谁调用**：Runner 的可选 Step 8；当前 OpenPAI smoke 只对 DA02 使用。

**出问题如何定位**：先看两侧 SQL 是否成功和 truncated，再看 shape mismatch，最后看数值容差。

**面试如何解释**：这是冗余验证，不是 Gold lookup；但独立性取决于 sql_b 的构造方式。

关键文件：`data_agent/tools/cross_checker.py`、`data_agent/runner/smoke_contract.py`。

## Level 3：理解模型接口

### 模块：Prompts 与 Structured Output

**解决什么问题**：把开放式模型输出压缩为三个小型、可验证的协议阶段。

**为什么需要**：一次长回答难以区分 metric、SQL、final wording 的错误来源。

**输入**：question、available/resolved metrics、schemas、SQL result、Cross-check result。

**输出**：messages 和三个 typed dict。

**被谁调用**：Minimal Runner。

**出问题如何定位**：看 trace 中 stage，再检查对应 messages 构造和 `from_dict` 规则。

**面试如何解释**：结构化输出约束格式，确定性工具约束业务和执行；两者不能相互替代。

关键文件：`data_agent/llm/prompts.py`、`data_agent/llm/schemas.py`。

阅读重点：每个阶段模型看到了什么、没看到什么。

不要关注：不要把 prompt 中的 `analysis` 当作完整 chain-of-thought；系统明确只要求 brief rationale。

### 模块：Real LLM Client

**解决什么问题**：通过统一 OpenAI-compatible HTTP 协议接入本地 Qwen 或远端服务。

**为什么需要**：runner 不绑定 vLLM、特定云 SDK 或某个模型厂商。

**输入**：LLMConfig、messages、response type。

**输出**：`LLMGenerationResult`，含 data/raw text/error/tokens/latency/attempts。

**被谁调用**：Runner；OpenPAI smoke 用 `from_env()` 构建。

**出问题如何定位**：依次检查 base URL、`/chat/completions`、model ID、API response shape、raw text、JSON parse 和 schema。

**面试如何解释**：标准协议降低部署耦合；不代表所有兼容服务对 `response_format` 的行为完全一致。

关键文件：`data_agent/llm/client.py`。

### 模块：FakeLLM

**解决什么问题**：不使用网络和 GPU，确定性驱动 runner 的各个分支。

**为什么需要**：模型随机性不应掩盖 runner、工具和 trace 的回归错误。

**输入**：预设 responses 队列。

**输出**：同 real client 的 `LLMGenerationResult` 接口，并记录 calls。

**被谁调用**：测试和 mock smoke。

**出问题如何定位**：检查 response 是否耗尽、response type schema 和 call count。

**面试如何解释**：Fake 证明工程协议可重复，不证明真实模型遵循 prompt。

关键文件：`../tests/fixtures/fake_llm.py`、`data_agent_mock_cases.py`。

## Level 4：理解可观察性

### 模块：Trace Protocol

**解决什么问题**：把问题、LLM stage、工具输入输出、安全停止和最终状态变成可机器读取的事件。

**为什么需要**：普通文本日志难以离线评分、统计、回放和比较不同运行。

**输入**：event type、step、status、summary、data、可选 role/tool。

**输出**：逐行 JSON event 和 compact run summary。

**被谁调用**：Runner；OpenPAI runner/scorer；测试。

**出问题如何定位**：Writer 负责顺序写，Loader 检查 JSON、ID、step；再人工检查 lifecycle 和 summary 一致性。

**面试如何解释**：当前是 replay-ready audit log，不是完整 state-machine replay。Data Agent trace 没有 state hash，外层 VeriAgent trace 才有状态哈希设计。

关键文件：`data_agent/trace/events.py`、`writer.py`、`loader.py`。

阅读重点：事件字段、`json_safe`、写入和加载不变量。

不要关注：不要把 `EVENT_TYPES` 中预留的 recovery 事件说成已经有实现。

## Level 5：理解数据基础

### 模块：DuckDB Foundation

**解决什么问题**：把六个 CSV 固定为类型明确、可重复查询的本地数据库。

**为什么需要**：直接让运行时解析 CSV 会带来 schema inference、类型和性能不确定性。

**输入**：六个规定 CSV。

**输出**：`ecommerce.duckdb` 和 profile artifacts。

**被谁调用**：离线构建脚本；runtime 只读使用数据库。

**出问题如何定位**：检查源文件、预期行数、建库事务、表 schema、required joins 和 profile summary。

**面试如何解释**：DuckDB 适合本地分析与可重复 smoke；不是线上分布式数据仓库替代品。

关键文件：

- `scripts/data_agent/olist_common.py`
- `scripts/data_agent/build_olist_duckdb.py`
- `scripts/data_agent/profile_olist.py`
- `data_agent/db.py`

不要关注：初读不需要逐条分析 profile SQL。

### 模块：Metric Contract 与 Gold

**解决什么问题**：Contract 规定 runtime 允许怎样解释指标；Gold 规定离线评估期望什么。

**为什么需要**：两者必须分离，否则 runtime 会泄漏答案。

**输入**：Contract 的六个指标；tasks 的问题；离线阶段的 Gold records 和 SQL。

**输出**：runtime metric resolution、canonical expected values、cross-check 和 forbidden-shortcut 验证。

**被谁调用**：Metric Search 用代码字典；Gold validator/scorer 用 Gold 文件。

**出问题如何定位**：先判断是合同漂移还是数据漂移，再检查 canonical SQL、Cross-check SQL 和 tolerance。

**面试如何解释**：Contract 是运行规则，Gold 是评测答案；runtime 只能访问前者。

关键文件：

- `knowledge/metric_definitions.md`
- `data_agent/tools/metric_search.py`
- `datasets/olistbr/tasks/v0/tasks_v0.jsonl`
- `datasets/olistbr/tasks/v0/gold_v0.jsonl`
- `scripts/data_agent/validate_v0_gold.py`

## Level 6：理解部署与离线评分

### 模块：OpenPAI Smoke

**解决什么问题**：在确定镜像中启动外部 Qwen OpenAI-compatible server，跑固定三题并保存可审计结果。

**为什么需要**：Fake 不能验证真实模型的 JSON、metric selection 和 SQL generation 能力。

**输入**：project/model/tasks/database/output paths、server command、LLM endpoint/model/key。

**输出**：server log、每题 trace/summary、smoke summary、offline score。

**被谁调用**：OpenPAI 容器入口；job YAML/资源配置由仓库外平台提供。

**出问题如何定位**：先区分 job 未启动、容器启动失败、server readiness、runtime task、offline score 五层。

**面试如何解释**：脚本对 backend 保持中立，通过 `QWEN_SERVER_CMD` 注入服务命令；当前没有仓库内 job YAML，也没有可确认 real run 结果。

关键文件：

- `scripts/data_agent/run_openpai_qwen_smoke_job.sh`
- `scripts/data_agent/run_openpai_qwen_smoke.py`
- `scripts/data_agent/score_qwen_smoke.py`

阅读重点：输入预检、防覆盖、`/models` readiness、进程清理、runtime 完成后才读取 Gold。

不要关注：不要从其他 pilot 目录复制旧 OpenPAI 命令并假定适用于本流程。

## 最终自测

如果能不看代码回答以下问题，说明模块地图已经形成：

1. 哪个函数真正决定净收入必须停止？
2. 哪个模块只防写 SQL，不证明业务语义？
3. join checker 为什么存在但不能作为当前 runtime 的成功依据？
4. Fake 和 Real client 的共同最小接口是什么？
5. 为什么 tasks 可以进入 runtime，而 Gold 不可以？
6. summary 写 SUCCESS 时还可能漏掉哪类 final-answer 错误？
7. OpenPAI job 没有 trace 时，应该先查平台、server log 还是 SQL？
