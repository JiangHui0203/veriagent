# Olist 挑战题与候选记忆对照

本轮从本地 Olist 真实数据、现有指标合同和 DA03/DA08/DA09 等题目的错误模式出发，构建一套独立的单行数值挑战题。没有下载外部基准，也没有把人为构造的错误 SQL 当作真实模型失败。

## 来源与覆盖

题目配方在 `evaluation/challenge_tasks.py` 中固定，共 24 题，开发集 12 题、保留测试集 12 题。每组在四个家族各有 3 题：

| 家族 | 挑战内容 | 典型错误对照 |
|---|---|---|
| payment_grain | 多次支付、voucher 订单、分期资格、每单平均值 | 直接对支付记录取平均 |
| customer_identity | 独立消费者、重复购买者、商品/支付资格组合 | 把 `customer_id` 当作消费者身份 |
| join_fanout | 多商品、多支付、满足某类支付条件的整单金额 | raw items/payments 关联后直接汇总 |
| time_scope | 购买与签收时间交集、跨月边界、不同指标的状态口径 | 静默统一 delivered、漏签收条件、改用批准时间 |

所有题只要求已支持的指标与单行数值结果，不要求 Top-K、多行输出或未定义的派生指标。涉及某种支付方式时，题目明确区分“订单资格条件”与“这些订单全部支付记录的金额”，避免答案依赖含糊解释。

分组在真实模型运行前确定。`group_key` 排除仅有日期、州值变化的近重复跨组；测试集采用不同的条件组合。两组仍共享错误家族、数据库和题目表达模板，因此这里只能测量领域内场景迁移，不能宣称跨数据库泛化。

## 题目如何验证

`build_challenge_set.py` 不调用模型，逐题检查：

1. 目标数据子集非空。
2. SQL 标准答案与独立 Python 计算一致。Python 直接读取原始订单、客户、支付和商品记录，以集合去重、Decimal 求和及订单级分母计算，不复用 SQL 的筛选或聚合结果。
3. 该题指定的一种典型错误 SQL 与正确结果存在超过评分容差的差异。若数据不能区分这种错误，建集失败，不会悄悄收录。

计数必须精确一致，金额和均值的绝对容差为 0.01。存在没有支付记录的订单时，平均每单支付的分母只包括有支付记录的订单。

构建产物包括 `dev.jsonl`、`test.jsonl`、离线配方、参考结果、错误对照 SQL 与结果、数据证据和可读题目目录。manifest 记录题目/参考文件哈希、数据库文件 SHA256、指标合同哈希及目录编号。数据或合同变化后必须重新验证；原目录不覆盖。

题目是否“对模型更难”最终由开发集基线判断。典型错误对照只能证明题目有区分度，不能证明模型实际会犯这种错误。

## 运行方式

在仓库目录执行：

```bash
# 建集与独立校验，不调用 API；输出新的 results/challenge_set_<timestamp>/
.venv/bin/python scripts/data_agent/build_challenge_set.py

# 首先只运行开发集基线：12 题，各重复 2 次，正常约 72 次模型请求
.venv/bin/python scripts/data_agent/run_challenge_eval.py \
  --catalog results/challenge_set_<timestamp> --split dev --mode baseline --repeats 2

# 显式复核失败后，重放证据并创建独立候选；plan 必须绑定该轮复核文件的哈希
.venv/bin/python scripts/data_agent/build_failure_memory.py \
  --run-dir results/challenge_eval_<dev-timestamp> \
  --plan evaluation/reviews/<reviewed-plan>.json \
  --memory-file memory/<new-candidate>.json

# 完成开发阶段、冻结候选记忆后，才使用保留测试集比较两种条件
.venv/bin/python scripts/data_agent/run_challenge_eval.py \
  --catalog results/challenge_set_<timestamp> --split test --mode paired \
  --memory-file memory/<frozen-candidate>.json --repeats 3
```

默认模式关闭经验记忆；开发集基线阶段不更新模型提示、指标合同或守卫以迎合结果。所有模式均不自动学习。配对模式复制记忆快照并拒绝源问题与评测题完全相同。

运行时只读取所选题目的 ID 与问题，模型仍通过原有指标查询和 schema 工具获取上下文。SQL 标准答案、错误 SQL、参考数值和家族标签都不发送给模型。评分器在该轮所有运行完成后才读取参考文件，并验证题目、轨迹身份、条件和快照一致性。

API 错误会使运行标为 `ABORTED`，保留已有轨迹并停止后续调用；不对不完整的一轮生成正式成绩。已完成评估的进程退出码为 0，即使个别题失败，具体失败应查看评分报告。

## 结果与后续经验入口

每次实验保存在独立 `results/challenge_eval_<timestamp>/`：

- 每道题、每次重复、每种条件的 trace 与运行汇总。
- `challenge_score.json` / `.md`：离线正确性、错误放行、家族通过数、Token、耗时、单位成功成本及稳定性；配对模式另有净收益。
- 开发集专有的 `failure_review.json`：失败 SQL、实际值、参考值、轨迹哈希、检查结果，以及是否碰巧与典型错误对照值一致。

失败记录的状态始终是 `REQUIRES_REVIEW`，`eligible_for_memory=false`。数值与某个错误对照相同只是诊断线索，不自动认定原因。后续需要复核真实错误、验证修正方法，才可以形成候选经验；保留测试集的失败不作为本轮学习材料。默认经验构建脚本也不会扫描这些实验目录。

## 本机首次结果（2026-10-26）

题集：[24 道题目及数据证据](../results/challenge_set_20261026T071641_710355Z/catalog.md)，其中 [开发集问题](../results/challenge_set_20261026T071641_710355Z/dev.jsonl) 与 [保留测试集问题](../results/challenge_set_20261026T071641_710355Z/test.jsonl) 均只有 ID 和问题。全部 24 题通过独立参考及典型错误区分验证。

请求模型 `deepseek-v4-flash`，开发集关闭记忆，12 题各重复 2 次。[基线报告](../results/challenge_eval_20261026T072815_498927Z/challenge_score.md)：

| 指标 | 结果 |
|---|---:|
| 正确完成 | 22/24 |
| 第一轮 / 第二轮 | 10/12、12/12 |
| 错误答案被运行时接受 | 1 次 |
| SQL 执行失败 | 1 次 |
| 两次均通过 / 有波动的不同题目 | 10 / 2 |
| 模型请求 / 总 Token | 71 / 46,717 |
| 每次正确完成 Token | 2,123.5 |
| 每次正确完成完整耗时（包含失败消耗） | 4.56 秒 |

两条失败都来自多表题，但实际原因并非预设的 raw join 金额倍增：

- `CJ01_r01` 在订单表按唯一 `order_id` 分组后要求 `COUNT(*) >= 2`，错误地将商品明细数量判断放到了父表。错误筛选为空，COALESCE 将三个金额变为 0，运行时接受了这个错误答案。正确范围有 1,336 单，支付金额为 310,201.75。
- `CJ03_r01` 在没有定义 `oi` 的作用域中引用 `oi.order_item_id`，被 DuckDB 拒绝。

[失败复核](../results/challenge_eval_20261026T072815_498927Z/failure_analysis.md)记录了具体原因和两次 SQL 的证据。第二次运行对应的正确 SQL 已重新执行，并与冻结参考一致；这属于相同配置下的生成波动，不能解释为学习效果。

完成开发集基线时，这两条线索尚未写入记忆，12 道测试题尚未进行模型推理。首个受沙箱网络限制而中断的尝试保留在 `results/challenge_eval_20261026T072531_845280Z/`，未计入正式成绩。

## 经复核的候选记忆 v1

用户授权继续后，代理根据上述开发集错误复核编写两条固定经验。根因分析与经验编写是显式开发工作，程序负责校验和提取，并未实现无人监督的根因学习。

- `reviewed:child_row_eligibility`：商品或支付记录数条件应在对应子表中按订单分组；按唯一订单表分组不能计量子表行数。
- `reviewed:alias_scope`：查询条件中的别名必须在当前作用域可见；将子表计数放在包含该子表的 CTE 或相关子查询中。

两条规则仅在 SQL 生成阶段、问题包含商品/支付行数表述时检索。规则不携带历史日期、答案或 SQL；每次使用当前问题的约束。候选文件只含这两条新经验，避免与旧的三条指标记忆混在一起测量。

构建器 `evaluation/failure_memory.py` 要求已完成的开发集无记忆基线，核对固定题集、复核文件和轨迹哈希；重放错误与正确 SQL，验证正确重跑的运行时数值校验、最终答案与独立参考。它拒绝测试集来源和覆盖已有记忆文件。根因结论由复核计划绑定，SQL 重放验证结果差异，不能单凭重放自动证明所有根因判断。

本机复现命令：

```bash
.venv/bin/python scripts/data_agent/build_failure_memory.py \
  --run-dir results/challenge_eval_20261026T072815_498927Z \
  --plan evaluation/reviews/olist_candidate_v1.json \
  --memory-file memory/challenge_candidate_v1_copy.json
```

已冻结的 [候选记忆](../memory/challenge_candidate_v1.json) 与 [提取报告](../memory/challenge_candidate_v1.promotion.json) 分开保留。快照编号为 `e2837d4e8852bfdc5fe7346889d6bf5e71948814d75086e862fed4d705b0ee7d`。旧规则的版本与内容不受新增规则影响，默认 `memory/experiences.json` 没有被替换。

测试前固定方案：12 道保留题、每种条件各重复 3 次，按题目和轮次交替执行，比较“无记忆”和“仅两条候选记忆”。主指标为配对正确完成差，另报告错误放行、Token 成本、耗时和稳定性。测试过程中不修改候选、不把测试结果导入记忆。

## 候选 v1 的保留测试结果（2026-10-26）

完整结果见 [配对报告](../results/challenge_eval_20261026T074807_799473Z/challenge_score.md)，解释见 [阶段证据与结论](../results/challenge_eval_20261026T074807_799473Z/candidate_assessment.md) 和 [可审计 JSON](../results/challenge_eval_20261026T074807_799473Z/candidate_assessment.json)。两组共完成 72 次任务运行、190 次模型请求。

| 指标 | 无记忆 | 仅候选记忆 |
|---|---:|---:|
| 正确完成 | 29/36 | 30/36 |
| 错误答案被接受 | 0 | 0 |
| 分析阶段不存在的表名 `payments` | 7 | 6 |
| 进入 SQL 后正确完成 | 29/29 | 30/30 |
| 三次均通过的题数 | 7/12 | 9/12 |
| 三次均失败的题数 | 0/12 | 1/12 |
| 总 Token | 70,984 | 77,934 |
| 每次正确完成 Token，包含失败消耗 | 2,447.72 | 2,597.80 |
| 完整耗时 P50 / P95 | 4.72 / 5.61 秒 | 4.63 / 5.41 秒 |

配对观察为 3 对从错到对、2 对从对到错、27 对均对、4 对均错，净差为 +1/36。但不能将这个差异归功于候选经验：13 次失败都在 SQL 阶段经验检索之前，36 对首次分析提示的哈希全部相同。本次候选只影响 SQL 阶段，不会修复更早的表名错误。

8 道问题符合行数资格经验的检索条件；24 次潜在命中中，有记忆组有 6 次提前失败，其余 18 次各注入两条经验，总覆盖为 18/36。两组实际执行的 SQL 都通过独立参考评分，因而本轮没有观察到这两类目标错误复发，也没有产生证明候选能减少它们的对照证据。提前失败的记录不算作避免错误。

总 Token 增加 9.79%，单位正确完成 Token 增加 6.13%。12 道不同题的三次重复不能算成 36 道独立题；耗时受服务端波动影响，Token 不是费用账单。

决定：保留候选与证据，不替换默认记忆。下一步优先在开发阶段约束分析输出的准确表名，再用新保留题验证。当前测试集已经使用，不能修改候选后再把同一批题称为首次独立测试。本轮结果未导入经验库。

沙箱网络阻止的初始尝试保存在 `results/challenge_eval_20261026T074538_706351Z/`，状态为 `ABORTED`，未计入成绩。原默认记忆和候选快照均保持不变；新运行产物检查未发现本机 API key。相关回归检查共 110 项通过。

## 验证

```bash
.venv/bin/python -B -m pytest -c pytest.ini -q -p no:cacheprovider tests/test_challenge_evaluation.py
```

测试覆盖近重复分组隔离、手算参考值、缺失支付的平均分母、不可区分题拒绝、数据库/题目变更检测、运行时参考隔离、SQL 成功但语义错误的离线发现、重复轨迹完整性、API 中断，以及冻结记忆的配对运行。候选提取还测试开发/测试来源隔离、证据哈希、错误复现、正确重跑校验、检索范围和独立版本。脚本化模型仅验证实现。
