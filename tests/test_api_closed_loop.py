from __future__ import annotations

import json
import io
from decimal import Decimal
from pathlib import Path
import urllib.error

import duckdb
import pytest

from veriagent.data_agent import DataAgentRunner
from veriagent.data_agent.llm import LLMClient, LLMConfig, LLMGenerationResult
from veriagent.data_agent.llm.environment import read_environment
from veriagent.data_agent.runner.answer_validation import verify_answer_values
from veriagent.data_agent.runner.smoke_contract import DA02_PAYMENT_CROSS_CHECK_SQL
from veriagent.data_agent.tools.registry import create_registry
from veriagent.data_agent.tools.sql_executor import execute_sql
from veriagent.data_agent.trace import load_trace
from veriagent.scripts.data_agent import run_openpai_qwen_smoke as smoke
from veriagent.scripts.data_agent.score_qwen_smoke import score_smoke


class QueueClient:
    model = "test-model"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def generate_json(self, messages, response_type):
        self.calls.append(messages)
        result = response_type.from_dict(next(self.responses)).to_dict()
        return LLMGenerationResult(ok=True, data=result)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "sample.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE TABLE orders(order_id VARCHAR, order_status VARCHAR, order_purchase_timestamp TIMESTAMP)")
        connection.execute("INSERT INTO orders VALUES ('a', 'delivered', '2017-11-01'), ('b', 'canceled', '2017-11-15'), ('c', 'delivered', '2017-10-01')")
        connection.execute("CREATE TABLE order_payments(order_id VARCHAR, payment_value DECIMAL(18, 2))")
        connection.execute("INSERT INTO order_payments VALUES ('a', 10.00), ('a', 20.25), ('b', 50.00), ('c', 100.00)")
    return path


def responses(metric, sql, value):
    return [
        {"analysis": "Compute the requested metric.", "metrics": [metric], "needed_tables": []},
        {"sql": sql, "reason": "Use purchase month and metric scope."},
        {"answer": "A deliberately wrong prose number: 999999.", "key_values": {metric: value}, "limitations": []},
    ]


ORDER_SQL = "SELECT COUNT(DISTINCT order_id) AS order_count FROM orders WHERE order_purchase_timestamp >= TIMESTAMP '2017-11-01' AND order_purchase_timestamp < TIMESTAMP '2017-12-01'"
PAYMENT_SQL = """
WITH payments AS (
    SELECT order_id, SUM(payment_value) AS amount FROM order_payments GROUP BY order_id
)
SELECT SUM(amount) AS payment_value FROM orders o JOIN payments p ON o.order_id = p.order_id
WHERE o.order_status = 'delivered'
AND o.order_purchase_timestamp >= TIMESTAMP '2017-11-01'
AND o.order_purchase_timestamp < TIMESTAMP '2017-12-01'
"""


def test_dotenv_is_literal_and_environment_takes_priority(tmp_path):
    path = tmp_path / ".env"
    path.write_text('LLM_MODEL=file-model\nLLM_API_KEY="key-${UNDEFINED}-$literal"\n')
    config = read_environment(path, {"LLM_MODEL": "process-model"})
    assert config["LLM_MODEL"] == "process-model"
    assert config["LLM_API_KEY"] == "key-${UNDEFINED}-$literal"


def test_client_hides_key_and_sends_thinking_setting():
    config = LLMConfig.from_env({
        "LLM_BASE_URL": "https://api.deepseek.com",
        "LLM_MODEL": "deepseek-v4-flash",
        "LLM_API_KEY": "unit-test-secret",
        "LLM_THINKING": "disabled",
    })
    assert "unit-test-secret" not in repr(config)

    def transport(url, payload, headers, timeout):
        assert url == "https://api.deepseek.com/chat/completions"
        assert payload["thinking"] == {"type": "disabled"}
        assert headers["Authorization"] == "Bearer unit-test-secret"
        raise urllib.error.URLError("rejected unit-test-secret")

    result = LLMClient(config, transport).generate_json([])
    assert not result.ok
    assert "unit-test-secret" not in result.error
    assert "[REDACTED]" in result.error


def test_http_authentication_error_is_informative_and_redacted(monkeypatch):
    def reject(*args, **kwargs):
        body = json.dumps({"error": {"message": "Invalid unit-test-secret"}}).encode()
        raise urllib.error.HTTPError("https://api.example.com", 401, "Unauthorized", {}, io.BytesIO(body))

    monkeypatch.setattr("urllib.request.urlopen", reject)
    result = LLMClient(LLMConfig("https://api.example.com", "unit-test-secret", "test")).generate_json([])
    assert not result.ok and "HTTP 401" in result.error
    assert "unit-test-secret" not in result.error


def test_generated_sql_cannot_read_local_credentials(database, tmp_path):
    secret = tmp_path / "private.env"
    secret.write_text("LLM_API_KEY=unit-test-secret")
    sql_path = str(secret).replace("'", "''")
    result = execute_sql(f"SELECT content FROM read_text('{sql_path}')", database_path=database)
    assert not result.ok
    assert result.error_type == "SQL_EXECUTION_ERROR"
    assert "unit-test-secret" not in str(result.to_dict())


@pytest.mark.parametrize("url", [
    "http://api.deepseek.com",
    "https://api.deepseek.com?api_key=secret",
    "https://user:secret@api.deepseek.com",
])
def test_reject_unsafe_credential_transport(url):
    with pytest.raises(ValueError):
        LLMConfig(url, "test-key", "test-model")


@pytest.mark.parametrize("candidate", [31, True, "30.25", float("nan"), float("inf"), None])
def test_answer_validator_rejects_wrong_or_invalid_values(candidate):
    with pytest.raises(ValueError):
        verify_answer_values(
            {"key_values": {"payment_value": candidate}},
            {"columns": ["payment_value"], "rows": [[Decimal("30.25")]], "truncated": False},
            [{"metric_id": "payment_value"}],
        )


def test_money_tolerance_uses_decimal_and_returns_sql_value():
    sql = {"columns": ["payment_value"], "rows": [[Decimal("30.25")]], "truncated": False}
    metrics = [{"metric_id": "payment_value"}]
    assert verify_answer_values({"key_values": {"payment_value": 30.26}}, sql, metrics) == {"payment_value": 30.25}
    with pytest.raises(ValueError):
        verify_answer_values({"key_values": {"payment_value": 30.27}}, sql, metrics)


@pytest.mark.parametrize("sql,values", [
    ({"columns": ["order_count"], "rows": [[2]], "truncated": True}, {"order_count": 2}),
    ({"columns": ["order_count"], "rows": [[2], [3]]}, {"order_count": 2}),
    ({"columns": ["order_count"], "rows": [[2]]}, {}),
    ({"columns": ["order_count"], "rows": [[2]]}, {"order_count": 2, "extra": 99}),
    ({"columns": ["order_count"], "rows": [[2]]}, {"order_count": 2.001}),
    ({"columns": ["made_up_alias"], "rows": [[2]]}, {"made_up_alias": 2}),
])
def test_incomplete_or_ambiguous_answers_cannot_pass(sql, values):
    with pytest.raises(ValueError):
        verify_answer_values({"key_values": values}, sql, [{"metric_id": "order_count"}])


def test_wrong_final_amount_fails_after_successful_crosscheck(database, tmp_path):
    client = QueueClient(responses("payment_value", PAYMENT_SQL, 999))
    result = DataAgentRunner(
        llm_client=client,
        tool_registry=create_registry(database),
        trace_root=tmp_path / "traces",
    ).run(
        run_id="wrong-final", task_id="DA02",
        question="2017 年 11 月 delivered 订单支付金额是多少？",
        cross_check_sql=DA02_PAYMENT_CROSS_CHECK_SQL,
    )
    events = load_trace(result.trace_path)
    assert result.status == "FAILED"
    assert result.final_answer["key_values"] == {}
    assert any(e.event_type == "cross_check_completed" and e.status == "success" for e in events)
    assert events[-1].data["error_type"] == "FINAL_ANSWER_MISMATCH"
    assert smoke._failure_category(events, result.status) == "FINAL_ANSWER_FAILURE"
    assert not any(e.event_type == "run_finished" for e in events)


def test_mock_end_to_end_with_real_sql_and_offline_scoring(database, tmp_path, monkeypatch):
    tasks_file = tmp_path / "tasks.jsonl"
    tasks = [
        {"task_id": "DA01", "question": "2017 年 11 月订单量？"},
        {"task_id": "DA02", "question": "2017 年 11 月 delivered 订单支付金额？"},
        {"task_id": "DA10", "question": "2017 年 11 月净收入？"},
    ]
    tasks_file.write_text("\n".join(json.dumps(task) for task in tasks))
    scripted = responses("order_count", ORDER_SQL, 2) + responses("payment_value", PAYMENT_SQL, 30.25)
    # Try to substitute payment_value for the unsupported business metric.
    scripted += [{"analysis": "Use payments.", "metrics": ["payment_value"], "needed_tables": []}]
    client = QueueClient(scripted)
    monkeypatch.setattr(smoke.LLMClient, "from_env", lambda **kwargs: client)
    output = tmp_path / "output"
    summary = smoke.run_smoke(
        tasks_file=tasks_file, database=database, output_root=output,
        execution_backend="local-api", smoke_id="api_smoke_v0",
    )
    assert summary["execution_backend"] == "local-api"
    assert summary["status"] == "COMPLETED"
    assert len(client.calls) == 7
    for task_id in ("DA01", "DA02"):
        saved = json.loads((output / task_id / "run_summary.json").read_text())
        assert "999999" not in saved["final_answer"]["answer"]
        assert saved["final_answer"]["key_values"]
    events = load_trace(output / "DA10" / "trace.jsonl")
    assert not any(e.event_type == "sql_execution_completed" for e in events)

    # Only create standard answers after runtime finishes: it cannot have read them.
    gold_file = tmp_path / "gold.jsonl"
    gold_file.write_text("\n".join(json.dumps(record) for record in [
        {"task_id": "DA01", "gold_values": {"order_count": 2}, "required_metrics": ["order_count"]},
        {"task_id": "DA02", "gold_values": {"payment_value": 30.25}, "required_metrics": ["payment_value"]},
        {"task_id": "DA10"},
    ]))
    score = score_smoke(results_root=output, gold_file=gold_file)
    assert score["all_passed"] and score["passed"] == 3
    assert score["score_id"] == "api_smoke_v0_offline"
    with pytest.raises(FileExistsError):
        smoke.run_smoke(tasks_file=tasks_file, database=database, output_root=output)
