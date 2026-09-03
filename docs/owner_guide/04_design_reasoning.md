# 设计推理：为什么系统这样拆

## 1. 为什么不是直接 Text-to-SQL？

### 设计背景

Text-to-SQL 常把问题理解、指标口径、表选择、SQL 和答案表达合并为一次生成。SQL 可能语法正确，却在业务上错误：例如对 payment records 直接求平均、把所有 placed orders 静默限制为 delivered，或 raw join items 和 payments 后重复金额。

### 工程考虑

项目把风险拆成多个可观察阶段：

```text
metric selection → contract → schema → candidate SQL
→ safety check → execution → optional cross-check → answer
```

这样能区分“模型不懂指标”“SQL 不安全”“SQL 执行失败”“结果不一致”“答案抄错”五种问题。

代价是 pipeline 更长，且当前 semantic validator 尚未完成；Prompt 中写了合同不代表系统已经证明 SQL 遵守合同。

### 面试回答版本

> 我们不是把 Text-to-SQL 完全交给模型，而是把模型当候选生成器。业务指标先进入确定性 contract，SQL 再经过只读执行和可选独立对账。原因是可执行 SQL 仍可能在 grain、join 和 scope 上错误，数据分析需要验证语义而不只是验证 syntax。

## 2. 为什么需要 Metric Contract？

### 设计背景

“订单量”“收入”“平均每单支付”并不是天然唯一的数据库表达。没有统一定义时，不同模型、prompt 或工程师会产生不同口径。

### 工程考虑

Contract 同时冻结：

- display name；
- meaning；
- grain；
- default scope；
- time field；
- required tables；
- critical rules；
- unsupported concepts。

它还能补齐模型漏掉的必要表，并阻止净收入被替换成 payment value。

当前风险是机器读取的 `METRICS` 字典和两份 Markdown 合同可能漂移。Owner 修改指标时必须同步代码、合同、Gold SQL 和测试。

### 面试回答版本

> Metric Contract 是业务语义的机器可执行 allowlist。它把“模型觉得这个词什么意思”转换成固定 grain、时间、scope 和连接规则，使模型替换不再决定最终口径。

## 3. 为什么 LLM 不能直接访问数据库？

### 设计背景

如果模型拥有任意数据库连接，它可以生成写操作、查询无关表、返回过量数据，或者在失败时反复尝试不可控 SQL。

### 工程考虑

当前设计中模型只看到必要 schema 和前一步工具结果。数据库能力被包在工具层：

- schema inspector 有六表 allowlist；
- connection 使用 `read_only=True`；
- SQL 只允许单条 SELECT/WITH；
- 结果最多返回 200 行。

需要诚实说明：SQL Executor 本身尚未做 SQL 表级 allowlist，也没有 query timeout/complexity budget。

### 面试回答版本

> LLM 没有数据库凭据和连接对象，只能提出 SQL 字符串。执行权限由本地工具持有，因此 prompt 被忽略时仍有只读权限和语句边界作为最后防线。

## 4. 为什么需要 Tool Layer？

### 设计背景

模型、数据库和 runner 的错误形式不同。如果直接耦合，runner 会充满特定异常和返回结构，未来更换模型或数据库也困难。

### 工程考虑

Tool Layer 提供：

- 稳定名称和描述；
- argument schema；
- handler 注入；
- 统一 `ToolResult`；
- 明确 retryable 元数据；
- 可单独测试。

它也让工具能力与工具 policy 分离。join checker “存在”不等于 runner “会调用”，这正是能力层与控制层的区别。

当前 registry 没有通用 JSON Schema validator，主要依赖 Python handler 参数和内部检查。

### 面试回答版本

> Tool Layer 是模型与真实系统能力之间的 anti-corruption layer。模型提出意图，registry 将它变成统一的受控调用和错误协议；Controller 或 runner 决定何时使用工具。

## 5. 为什么需要 Trace？

### 设计背景

数据 Agent 的最终答案可能正确，但过程靠错误 SQL“碰巧”得到；也可能 SQL 正确而最终 LLM 抄错。只记录最终文本无法区分。

### 工程考虑

Trace 保存：

- 原问题；
- LLM stage 和 usage；
- metric resolution；
- schema；
- generated SQL；
- tool input/output；
- Cross-check；
- safe stop；
- final answer 和状态。

它支持故障定位、离线评分、模型比较和未来 SFT/recovery 数据构建。

当前限制：没有 schema version、state hash 或完整 lifecycle validator；Loader 能“加载”不等于能重放工具并证明状态相同。

### 面试回答版本

> Trace 不是附属日志，而是验证证据。它把一次答案拆成可检查事件，使我们能回答“模型选了什么指标、执行了什么 SQL、验证了什么、在哪一步停止”。

## 6. 为什么 FakeLLM 存在？

### 设计背景

真实 LLM 有网络、配额、随机性和模型版本变化。如果所有测试都依赖它，工程回归与模型波动无法区分。

### 工程考虑

FakeLLM：

- 与 real client 共享 `generate_json(messages, response_type)`；
- 队列返回固定响应；
- 记录 calls 和 call count；
- 仍使用 structured schema；
- 可以稳定验证 DA10 只调用一次 LLM、DA02 一定执行 Cross-check。

它不能证明真实模型遵循 prompt，也不产生真实 latency/tokens。

### 面试回答版本

> FakeLLM 用来验证 orchestration contract，不用来替代模型评估。它把 runner 和工具测试变成确定性的；真实模型能力由隔离的 OpenPAI smoke 验证。

## 7. 为什么 Gold 不进入 runtime？

### 设计背景

如果 runtime 能读取期望数字、Gold SQL 或 forbidden shortcuts，smoke 看起来会非常稳定，但不能证明 Agent 自己完成了任务。

### 工程考虑

项目分离：

```text
tasks_v0.jsonl = task_id + question + task_type
gold_v0.jsonl  = expected values + checks + canonical SQL
```

Runner 源码不引用 Gold；OpenPAI 先写 `smoke_summary.json`，offline scorer 确认 runtime 完成后才打开 Gold。DA02 runtime Cross-check SQL 也没有嵌入期望值。

### 面试回答版本

> Contract 可以进入 runtime，因为它是业务规则；Gold 不能进入 runtime，因为它是答案。Gold 只在完整 trace 落盘后用于离线评分，这是防止 benchmark leakage 的基本边界。

## 8. 为什么采用 OpenAI-compatible Client？

### 设计背景

本地 Qwen、vLLM 类服务器和远端 API 往往都支持 OpenAI 风格 chat completions。把 runner 绑定某个 SDK 会把模型选择和部署环境耦合进业务代码。

### 工程考虑

当前 client 用标准 HTTP 协议，通过环境变量注入 base URL、key、model，并要求 JSON object。OpenPAI 通过外部 `QWEN_SERVER_CMD` 决定具体 backend。

收益：

- Runner 与服务实现解耦；
- 本地和 OpenPAI 使用同一接口；
- 测试可注入 transport。

限制：不同“兼容”服务对 `response_format`、usage、错误码和 model listing 仍可能不一致。

### 面试回答版本

> 我们依赖的是协议而不是厂商。这样可以在不修改 runner 的情况下替换本地 Qwen 或其他服务，同时保留统一 structured-output 和 trace 统计。

## 9. 为什么优先 Replay Demo，而不是实时 Demo？

### 设计背景

实时 Demo 同时依赖 GPU、镜像、模型加载、endpoint、网络、DuckDB 和 prompt 稳定性。任何基础设施故障都会掩盖真正想展示的工程设计。

### 工程考虑

现有 trace 已经包含问题、模型阶段、SQL、工具结果、Cross-check 和安全停止。优先用固定 DA01/DA02/DA10 trace 展示，可以稳定说明：

- 正常单指标流程；
- 带 Cross-check 的金额流程；
- 不可回答问题的 safe stop。

这不是说项目已经有完整 replay UI；当前只有结构化 artifact 和 loader。Replay Demo 更准确地说是“基于已落盘 trace 的确定性讲解/检查”。

实时 Demo 应在镜像、依赖、真实 smoke 和输出版本都稳定后再做。

### 面试回答版本

> 我会先展示 trace replay，因为项目核心卖点是可审计过程，而不是现场等模型加载。Replay 能稳定展示模型输入、SQL、验证和停止原因；实时运行作为补充，而不是让基础设施决定演示成败。

## 10. 为什么 Data Agent 当前是线性 pipeline？

### 设计背景

一个能自主循环的 Agent 看起来更完整，但如果没有明确 audit 条件，循环只会放大模型不确定性和成本。

### 工程考虑

线性 runner 先固定最小状态机和错误边界，便于：

- Fake/Real client 对齐；
- 每一步单测；
- 确认 Gold 隔离；
- 建立 trace protocol；
- 暴露未来 Auditor 应判断的条件。

外层 VeriAgent 的 recovery 架构提供了方向，但 SQL 场景需要新的 semantic audit 和 repair policy，不能直接复用 ProofWriter proof verifier。

### 面试回答版本

> 我们先做线性最小闭环，因为 recovery 的前提是能确定性判断“哪里错了”。在 SQL semantic validator 尚未完成前，贸然加入模型循环只会增加动作，不会增加可验证性。

## 设计决策总表

| 决策 | 获得的能力 | 付出的代价/剩余风险 |
|---|---|---|
| 分阶段 LLM | 易定位、易验证 | 更多请求和协议代码 |
| Metric Contract | 口径稳定 | 代码与文档可能漂移 |
| Tool Layer | 权限隔离、统一错误 | policy 仍需单独实现 |
| 只读 DuckDB | 本地可重复、安全 | 不是生产数仓，缺 query budget |
| Optional Cross-check | 冗余验证 | 覆盖有限、独立性需保证 |
| FakeLLM | 确定性回归 | 不代表真实模型能力 |
| Trace | 审计和离线评分 | 当前不是完整 replay protocol |
| Gold 隔离 | 评测可信 | runtime 不知道最终值是否正确 |
| OpenAI-compatible | backend 解耦 | 兼容实现仍有差异 |
| 线性 runner | 边界清晰 | 尚无自动 recovery |
