# 面试防守材料

## 使用方式

每题只给“回答核心”，不是背诵稿。回答时先说真实架构，再说设计价值，最后主动交代边界。禁止把预留事件、注册工具或未来方向描述为已经上线。

## A. 项目介绍

### A1. 用一句话介绍项目

**回答核心**：仓库包含一个 ProofWriter 可验证检索恢复框架，以及一个独立的 Olist 数据问答 pipeline；共同原则是让模型生成候选，让确定性代码验证关键边界并记录 trace。

**不要编造**：两者尚未融合成同一个 Agent。

### A2. 你在项目中解决的核心问题是什么？

**回答核心**：不是让模型“能写 SQL”，而是降低可执行但业务错误的 SQL 风险，特别是指标口径、grain、时间、状态和一对多 join。

### A3. 这个项目最有价值的工程点是什么？

**回答核心**：LLM 与确定性控制分层、Gold/runtime 隔离、不可回答安全停止、结构化 trace。可用 DA10 和 DA09 举例。

### A4. 当前项目完成到什么程度？

**回答核心**：Data Agent 已有六表 DuckDB、六指标 contract、五工具、三阶段 LLM、线性 runner、trace、mock/real client 和 OpenPAI smoke 脚本；真实 Qwen 结果当前不可确认，SQL semantic validator 和 recovery 未接入。

### A5. 为什么仓库看起来有两套架构？

**回答核心**：外层 VeriAgent研究 ProofWriter proof/recovery；Olist Data Agent 验证数据场景。后者复用了设计理念，没有复用外层控制循环。

## B. Agent 架构

### B1. 这算 Agent 还是 workflow？

**回答核心**：当前 Data Agent 更准确是受约束的 agentic workflow/linear pipeline；有模型决策和工具能力，但没有自主循环 control policy。外层 VeriAgent 才有显式 Planner/Auditor/Controller loop。

### B2. Runner 的职责是什么？

**回答核心**：定义阶段顺序、错误分支、安全停止、工具调用和 trace；它不负责具体 SQL 执行或 HTTP 细节。

### B3. Tool Registry 有什么价值？

**回答核心**：统一工具描述、参数和错误协议，使 runner/未来 controller 不依赖具体实现。

**不要编造**：注册不等于模型会自动选择工具。

### B4. Data Agent 为什么没有直接复用外层 Controller？

**回答核心**：ProofWriter Controller 依据 proof validity 和 evidence coverage；SQL 场景需要 metric、grain、join、result consistency 等新的 audit signals，不能直接套用。

### B5. Recovery 应该怎么加？

**回答核心**：先实现能分类失败的 SQL semantic auditor，再为可修复错误定义 bounded repair/retry/checkpoint；不能先让 LLM 无限重写 SQL。

## C. LLM 设计

### C1. 为什么分三次调用 LLM？

**回答核心**：分离指标理解、SQL 生成和结果表达，能为每阶段提供最小上下文、独立 schema 和明确失败分类。

### C2. Prompt 中为什么不要求完整思维链？

**回答核心**：系统只需要 brief rationale 和结构化字段，关键判断应由 contract/tool result 支撑，而不是依赖不可验证的长推理文本。

### C3. Structured output 能保证答案正确吗？

**回答核心**：不能；它保证格式和基本字段类型。业务正确性由 contract、执行和 Cross-check 部分约束，当前仍缺完整 semantic validation。

### C4. JSON repair 如何设计？

**回答核心**：仅在 parse 失败时追加一次“返回合法 JSON”的修复请求；schema 失败和 API 错误不会无界重试。

### C5. FakeLLM 和 Real LLM 如何对齐？

**回答核心**：共享 `generate_json(messages, response_type)`，都返回 `LLMGenerationResult`；Fake 队列驱动并记录 calls，Real 通过 HTTP 调 OpenAI-compatible endpoint。

### C6. 为什么选择 OpenAI-compatible？

**回答核心**：按协议解耦 runner 与具体 Qwen server/厂商；通过 base URL、model、key 和 server command 注入部署差异。

## D. Text-to-SQL

### D1. 为什么普通 Text-to-SQL 不够？

**回答核心**：SQL syntax 正确不代表 metric semantics 正确。举 `AVG(payment_value)` 和 raw items/payments join 例子。

### D2. 如何防止模型使用错误指标？

**回答核心**：模型选择后必须经过冻结 Metric Contract；净收入还会根据原 question 强制触发 unsupported 检查。

### D3. 如何防止危险 SQL？

**回答核心**：单 statement、SELECT/WITH allow rule、forbidden keywords、只读 DuckDB、max rows。

**当前限制**：没有 SQL 表级 allowlist、query timeout 或完整 parser/AST policy。

### D4. 如何验证 generated SQL？

**回答核心**：当前有安全检查、执行和可选独立 Cross-check；完整 metric/grain/join semantic validator 尚未实现。

### D5. Cross-check 为什么有效？

**回答核心**：用不同查询路径计算同一小型数值并在容差内比较，降低单实现错误风险。

**不要编造**：不是所有任务都有 Cross-check；OpenPAI smoke 仅 DA02。

### D6. 如果两条 SQL 都错了怎么办？

**回答核心**：Cross-check 只能在独立错误假设下增加信心，不能替代 contract/semantic audit；两条 SQL 共用错误口径可能同时通过。

## E. 数据库问题

### E1. 为什么用 DuckDB？

**回答核心**：Olist 是本地分析数据，DuckDB 提供明确类型、SQL 能力、单文件可重复性和只读连接，适合 smoke 与离线验证。

### E2. 六张表是什么？

**回答核心**：customers、orders、order_items、order_payments、products、category_translation；其他原始 CSV 不进入 V0 数据库。

### E3. order_count 为什么默认不加 delivered？

**回答核心**：contract 定义它为期间内 placed orders；delivered order count 是另一个业务语义，不能静默替换。

### E4. Average Payment per Order 为什么不能直接 AVG(payment_value)？

**回答核心**：payment 表是一笔支付记录粒度，一单可能多行；必须先按 order_id 汇总，再对订单总支付求平均。

### E5. 为什么 items 和 payments join 危险？

**回答核心**：两侧都是 1:N，同一 order 的 M items × N payments 会产生 M×N rows，使金额重复。

### E6. 如何计算 Unique Customers？

**回答核心**：orders 与 customers 用 customer_id join，跨订单去重用 customer_unique_id；两个 ID 角色不同。

## F. 工程问题

### F1. Trace 与日志有什么区别？

**回答核心**：Trace 有稳定 event fields 和阶段语义，可由 scorer/loader 读取；日志主要供人查看运行文本。

### F2. Trace 能完整 replay 吗？

**回答核心**：当前能重建事件和结果，Loader 检查 ID/step；不能重新执行并验证同一状态，没有 schema version/state hash。

### F3. 怎么防止 Gold leakage？

**回答核心**：runtime tasks 只含问题字段，runner 不引用 Gold；OpenPAI 完成 smoke summary 后 offline scorer 才打开 Gold。

### F4. 测试怎么分层？

**回答核心**：工具单测、数据 foundation、Gold validator、FakeLLM runner 集成、OpenPAI wrapper/scorer 测试；真实模型 smoke 不应放入常规单测。

### F5. OpenPAI 如何保证可重复？

**回答核心**：固定三题、temperature 0、模型 ID readiness、路径预检、防覆盖、server log、trace、运行后评分。

**当前限制**：仓库没有 job YAML，镜像和资源仍由平台外部配置。

### F6. 依赖管理有什么问题？

**回答核心**：代码依赖 DuckDB，但 `pyproject.toml` 主依赖为空；当前环境可能 import 失败。OpenPAI shell 有预检，但包元数据仍需修复。

### F7. 为什么优先 Replay Demo？

**回答核心**：项目价值是可审计流程；固定 trace 能稳定展示 metric、SQL、Cross-check 和 safe stop，不让 GPU/模型加载决定演示结果。

## G. 失败边界

### G1. DA10 是失败还是成功？

**回答核心**：它是预期的安全控制成功，状态为 `STOP_WITH_DATA_GAP`；不是 `FAILED`，因为数据确实不支持净收入。

### G2. SUCCESS 是否等于最终答案正确？

**回答核心**：不完全等价。Runner 当前没有确定性比较 final key_values 与 SQL result；离线 scorer 会检查真实 smoke。

### G3. `retryable=True` 会自动重试吗？

**回答核心**：不会。它目前只是错误元数据；JSON parse 有一次内部 repair，工具/API 没有 runner-level retry。

### G4. join checker 是否保护所有请求？

**回答核心**：否。工具已实现和注册，但 Minimal Runner 不调用；主要用于测试/诊断。

### G5. SQL semantic validation 完成了吗？

**回答核心**：没有。当前合同通过 prompt 传给模型，但没有 AST/规则系统证明生成 SQL 遵守 grain/time/scope/join。

### G6. Auditor/Controller/Recovery 接入 Data Agent 了吗？

**回答核心**：没有。Data Agent event protocol 有预留事件名，实际 runner 是线性流程；外层 VeriAgent 有 recovery，但服务于 ProofWriter。

### G7. 当前系统最大的风险是什么？

**回答核心**：可执行 SQL 或 SUCCESS 状态可能被误当成业务验证完成。最优先补的是 semantic validator、final-value consistency 和自动 join-risk policy。

## H. RAG 追问

### H1. Olist Data Agent 是 RAG 系统吗？

**回答核心**：当前不是典型向量 RAG。它会检索冻结 Metric Contract 和数据库 schema，但 metric search 是确定性字符串/alias 匹配，没有 embedding、vector store 或 learned retriever。

### H2. 外层 VeriAgent 与 RAG 有什么关系？

**回答核心**：外层有 predicate-aware local retrieval，并围绕检索覆盖、proof verification 和 recovery 建立 Agent loop。它可与 RAG 比较，但检索对象是结构化 facts/rules，不应描述为通用文档向量 RAG。

### H3. 为什么六个指标不需要向量检索？

**回答核心**：候选集合小、口径必须精确、错误匹配成本高。exact/alias allowlist 更容易审计；规模扩大后可以用语义召回找候选，但最终仍要落到确定性 metric ID 和合同。

### H4. 如果未来引入 RAG，应该检索什么？

**回答核心**：可以检索版本化指标合同、表说明、已审核 SQL 模式和数据质量说明；检索结果只能作为候选上下文，不能把历史 SQL 或 Gold 值直接当作答案。还要记录文档版本、retrieval evidence 和 coverage。

## 当前系统限制：面试必须主动保留

- join checker 已实现，但尚未自动进入 runner；
- SQL semantic validation 尚未完成；
- recovery/controller 尚未接入 Data Agent；
- Cross-check 是可选且 smoke 中仅 DA02 使用；
- final answer 与 SQL result 的 runtime 一致性未验证；
- Trace 不是完整 replay engine；
- `EVENT_TYPES` 中的 recovery 事件只是预留；
- SQL 没有计算预算和表级 allowlist；
- unsupported safe stop 主要显式覆盖净收入；
- DuckDB 未在项目主依赖中声明；
- 仓库没有 OpenPAI job YAML；
- 当前没有可确认的真实 Qwen smoke 结果。

能主动说出这些边界，不会削弱项目；相反，它证明你知道“已有能力、测试证据、设计目标”之间的区别。
