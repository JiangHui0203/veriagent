# 本机 API 最小闭环

范围是 DA01、DA02、DA10：订单量、delivered 订单支付金额，以及因缺少退款台账而停止的净收入问题。运行阶段不读取标准答案；三题结束后才执行离线评分。

## 安装与密钥

在 `veriagent/` 目录执行：

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

首次使用时，将 `.env.example` 复制为 `.env`，执行 `chmod 600 .env`，在本机编辑器填写 `LLM_API_KEY`。已有 `.env` 时保留原文件。不要把密钥填入 Python 源码、命令行参数或聊天。

```dotenv
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_API_KEY=
LLM_THINKING=disabled
```

`.env` 已被 Git 忽略；`.env.example` 仅保存空密钥模板。配置读取不执行 shell，也不展开密钥中的 `$`。进程中同名环境变量优先于 `.env`。

模型名按用户指定保留。根据 [DeepSeek 官方说明](https://api-docs.deepseek.com/quick_start/agent_integrations/)，旧名称 `deepseek-v4-flash` 当前路由到更新的 Flash 模型；可用 `deepseek-flash` 调用当前版本。此流程通过 `LLM_THINKING=disabled` 关闭思考模式，使用短 JSON 输出。

## 运行

```bash
# 只检查配置和数据文件，不联网、不输出密钥
.venv/bin/python scripts/data_agent/run_api_smoke.py --check-config

# GET /models 验证认证，不调用模型生成
.venv/bin/python scripts/data_agent/run_api_smoke.py --check-api

# 真正执行模型调用、SQL、验证、留痕和离线评分
.venv/bin/python scripts/data_agent/run_api_smoke.py
```

正常运行约有 7 次模型请求；无效 JSON 最多额外修复一次。实际模型请求产生服务商 API 用量。默认每次生成独立输出目录，不覆盖历史结果。退出码：0 为全部评分通过，1 为运行或评分未通过，2 为配置或入口错误。

当前入口默认启用本机经验记忆：从 `memory/experiences.json` 检索合格经验，评分完成后再追加合格来源。`--no-memory` 同时关闭本次检索和学习。记忆提炼、版本及冻结对比实验见 [经验记忆说明](experience_memory.md)。

需要本机已有 `datasets/olistbr/ecommerce.duckdb`、`tasks/v0/tasks_v0.jsonl` 和离线评分用的 `gold_v0.jsonl`。这些数据资产未纳入 Git。`.env` 位置可用 `--env-file` 指定，输出位置可用 `--output-root` 指定。

## 验收与产物

| 任务 | 预期 |
|---|---|
| DA01 | Order Count = 7,544，SUCCESS |
| DA02 | Customer Payment Value = 1,153,528.05，独立交叉验证通过，SUCCESS |
| DA10 | STOP_WITH_DATA_GAP，无数值、不执行 SQL |

`results/api_smoke_<timestamp>/` 包含每题的 `trace.jsonl`、`run_summary.json`，以及整轮的 `smoke_summary.json`、`offline_score.json`。密钥不进入提示词或运行配置输出；SQL 连接禁止外部文件和网络访问。

最终数值验证只支持一行数值聚合，SQL 别名必须与所选 metric ID 一致。计数精确匹配，其他金额指标绝对误差最多 0.01。匹配后从 SQL 结果生成展示文本；不一致、空值、截断或多行结果会明确失败。该验证不证明 SQL 的时间范围、状态和 join 语义；十题真实模型评估、自动修复与恢复循环仍属于后续工作。

## 本地测试

```bash
.venv/bin/python -B -m pytest -c pytest.ini -q -p no:cacheprovider tests
.venv/bin/python scripts/data_agent/validate_v0_gold.py
```

第一条使用临时 DuckDB 和脚本化模型响应，验证凭据保护、数值错误拦截和端到端评分；不调用真实模型。第二条离线核对现有十道题的 Gold 与数据库结果，不代表十题真实模型运行。

若遇到 HTTP 401，在 `.env` 中核对或替换密钥，再运行 `--check-api`。网络限制、HTTP 429 和服务端错误会保留在 trace 中，排查后使用新输出目录复跑。
