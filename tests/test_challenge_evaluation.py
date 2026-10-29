from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import duckdb
import pytest

from veriagent.data_agent.db import read_only_connection
from veriagent.data_agent.llm import LLMGenerationResult
from veriagent.data_agent.memory import ExperienceMemory
from veriagent.data_agent.memory.store import digest
from veriagent.evaluation.challenge_catalog import build_catalog, load_tasks, file_sha256, write_json
from veriagent.evaluation.challenge_reference import PythonReference, query_values, reference_sql
from veriagent.evaluation.challenge_scoring import score_challenges
from veriagent.evaluation.challenge_tasks import SCENARIOS, Scenario, validate_splits
from veriagent.evaluation.memory_scoring import _matches
from veriagent.scripts.data_agent.run_challenge_eval import run_challenges
from veriagent.data_agent.memory.store import REVIEWED_RULES
from veriagent.data_agent.tools.sql_executor import execute_sql
from veriagent.data_agent.trace import load_trace
from veriagent.evaluation.failure_memory import build_failure_memory


@pytest.fixture
def source(tmp_path):
    database = tmp_path / "data.duckdb"
    with duckdb.connect(str(database)) as con:
        con.execute("CREATE TABLE orders(order_id VARCHAR, customer_id VARCHAR, order_status VARCHAR, order_purchase_timestamp TIMESTAMP, order_approved_at TIMESTAMP, order_delivered_customer_date TIMESTAMP)")
        con.execute("CREATE TABLE customers(customer_id VARCHAR, customer_unique_id VARCHAR, customer_state VARCHAR)")
        con.execute("CREATE TABLE order_payments(order_id VARCHAR, payment_value DECIMAL(18,2), payment_type VARCHAR, payment_installments INTEGER)")
        con.execute("CREATE TABLE order_items(order_id VARCHAR, price DECIMAL(18,2), freight_value DECIMAL(18,2))")
        con.execute("INSERT INTO customers VALUES ('a1','u1','SP'),('a2','u1','SP'),('b','u2','MG'),('c','u3','SP'),('d','u4','SP')")
        con.execute("""INSERT INTO orders VALUES
            ('o1','a1','delivered','2017-01-01','2017-01-02','2017-02-01'),
            ('o2','a2','delivered','2017-01-31','2017-02-01','2017-02-10'),
            ('o3','b','canceled','2017-01-15','2017-01-16',NULL),
            ('o4','c','delivered','2017-02-01','2017-02-02','2017-02-15'),
            ('o5','d','delivered','2017-01-01',NULL,'2017-01-31')""")
        con.execute("INSERT INTO order_payments VALUES ('o1',10,'voucher',1),('o1',20,'credit_card',3),('o2',40,'credit_card',6),('o3',5,'voucher',1),('o3',5,'credit_card',3),('o4',100,'credit_card',3)")
        con.execute("INSERT INTO order_items VALUES ('o1',5,1),('o1',25,2),('o2',40,4),('o3',4,1),('o3',6,1),('o4',80,20),('o5',7,1)")
    scenarios = (
        Scenario("D1", "dev", "payment_grain", ("avg_order_payment",), "2017-01-01", "2017-02-01", payments_min=2),
        Scenario("D2", "dev", "customer_identity", ("unique_customers",), "2017-01-01", "2017-02-01"),
        Scenario("D3", "dev", "join_fanout", ("payment_value", "item_revenue", "freight_value"), "2017-01-01", "2017-02-01", items_min=2),
        Scenario("D4", "dev", "time_scope", ("order_count",), "2017-01-01", "2017-02-01", scope="all", time_mutation="approval_time"),
        Scenario("T1", "test", "payment_grain", ("avg_order_payment", "payment_value"), "2017-01-01", "2017-02-01", state="SP"),
        Scenario("T2", "test", "customer_identity", ("unique_customers", "order_count"), "2017-01-01", "2017-02-01", scope="all", state="SP"),
        Scenario("T3", "test", "join_fanout", ("payment_value", "item_revenue", "freight_value"), "2017-01-01", "2017-02-01", scope="all"),
        Scenario("T4", "test", "time_scope", ("payment_value",), "2017-01-01", "2017-02-01", delivered_start="2017-02-01", delivered_end="2017-02-05", time_mutation="ignore_delivery"),
    )
    catalog = tmp_path / "catalog"
    build_catalog(database, catalog, scenarios)
    return database, catalog, scenarios


def test_fixed_catalog_has_24_unique_grouped_scenarios():
    validate_splits()
    assert len(SCENARIOS) == 24
    assert all(sum(s.split == split for s in SCENARIOS) == 12 for split in ("dev", "test"))
    original = SCENARIOS[0]
    with pytest.raises(ValueError, match="group crosses"):
        validate_splits((original, replace(original, split="test", task_id="X", start="2016-01-01")))


def test_references_agree_with_hand_computed_values_and_missing_payment_denominator(source):
    database, catalog, scenarios = source
    refs = json.loads((catalog / "references.json").read_text())
    assert float(refs["D1"]["values"]["avg_order_payment"]) == 30
    assert refs["D2"]["values"] == {"unique_customers": "2"}
    assert refs["D3"]["values"] == {"payment_value": "30.00", "item_revenue": "30.00", "freight_value": "3.00"}
    assert refs["D4"]["values"] == {"order_count": "4"}
    # Three delivered orders, but only two have payments: denominator is two.
    assert float(refs["T1"]["values"]["avg_order_payment"]) == 35
    assert refs["T1"]["evidence"]["orders_without_payments"] == 1
    assert all(all(ref["checks"].values()) for ref in refs.values())
    s = replace(scenarios[0], payments_min=0, state="SP", payment_type="credit_card", installments_min=3,
                repeat_customer=True, metrics=("unique_customers", "payment_value", "avg_order_payment"))
    expected = {"unique_customers": 1, "payment_value": 70, "avg_order_payment": 35}
    with read_only_connection(database) as con:
        assert query_values(con, reference_sql(s)) == expected
    assert PythonReference(database).evaluate(s)[0] == expected


def test_non_discriminating_task_is_rejected(source, tmp_path):
    database, _, scenarios = source
    # February cohort has exactly one payment, so raw AVG is not a useful trap.
    scenario = replace(scenarios[0], start="2017-02-01", end="2017-03-01", payments_min=0)
    with pytest.raises(ValueError, match="mutation_changes_result"):
        build_catalog(database, tmp_path / "bad", (scenario,))
    assert not (tmp_path / "bad").exists()


def test_catalog_refuses_changed_questions_and_changed_database(source):
    database, catalog, _ = source
    original = (catalog / "dev.jsonl").read_bytes()
    (catalog / "dev.jsonl").write_bytes(original + b"\n")
    with pytest.raises(ValueError, match="questions changed"):
        load_tasks(catalog, "dev", database)
    (catalog / "dev.jsonl").write_bytes(original)
    with duckdb.connect(str(database)) as con:
        con.execute("UPDATE customers SET customer_state='RJ' WHERE customer_id='d'")
    with pytest.raises(ValueError, match="Database or metric contract changed"):
        load_tasks(catalog, "dev", database)


class ScriptedModel:
    model = "challenge-test-model"

    def __init__(self, scenarios):
        self.scenarios = {s.question: s for s in scenarios if s.split == "dev"}
        self.calls = []

    def generate_json(self, messages, response_type):
        payload = json.loads(messages[-1]["content"])
        self.calls.append(payload)
        s = self.scenarios[payload["question"]]  # Fails if a held-out task leaks in.
        assert not {"reference_sql", "values", "mutation_sql", "expected", "gold"} & payload.keys()
        if response_type.__name__ == "AnalysisSelection":
            response = {"analysis": "Select metrics.", "metrics": list(s.metrics), "needed_tables": ["customers", "orders", "order_payments", "order_items"]}
        elif response_type.__name__ == "SQLGeneration":
            response = {"sql": reference_sql(s, "raw_join" if s.family == "join_fanout" else None), "reason": "Compute metrics."}
        else:
            result = payload["sql_result"]
            response = {"answer": "Results", "key_values": dict(zip(result["columns"], result["rows"][0])), "limitations": []}
        return LLMGenerationResult(ok=True, data=response_type.from_dict(response).to_dict(), prompt_tokens=100, completion_tokens=20)


def test_baseline_scores_semantic_errors_after_runtime_and_keeps_test_unqueried(source, tmp_path, monkeypatch):
    database, catalog, scenarios = source
    output = tmp_path / "baseline"
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path.parent == catalog and path.name in {"references.json", "specifications.json", "test.jsonl"}:
            state = json.loads((output / "evaluation_manifest.json").read_text())["status"]
            assert state == "COMPLETED", "Offline references read before runtime completed"
            assert path.name != "test.jsonl", "Held-out questions read by development evaluation"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    model = ScriptedModel(scenarios)
    report = run_challenges(llm_client=model, catalog=catalog, database=database, output=output, repeats=2)
    group = report["groups"]["without_memory"]
    assert group["passed"] == 6 and group["total"] == 8 and group["false_accepts"] == 2
    assert group["cost_per_correct_completion"]["total_tokens"] == 480
    assert "paired_outcomes" not in report
    assert len(model.calls) == 24
    review = json.loads((output / "failure_review.json").read_text())
    assert len(review["cases"]) == 2
    assert all(row["matches_known_mutation_values"] for row in review["cases"])
    assert all(not row["eligible_for_memory"] for row in review["cases"])
    assert not (output / "memory_snapshot.json").exists()
    manifest_path = output / "evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["runs"][4]["run_id"] = manifest["runs"][0]["run_id"]
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="distinct trace directories"):
        score_challenges(output, database)


def test_api_failure_aborts_before_reading_any_answers(source, tmp_path):
    database, catalog, _ = source

    class Offline:
        model = "offline"
        calls = 0

        def generate_json(self, *args, **kwargs):
            self.calls += 1
            return LLMGenerationResult(ok=False, error_type="LLM_API_ERROR", error="Offline")

    model = Offline()
    output = tmp_path / "aborted"
    with pytest.raises(OSError, match="API failure"):
        run_challenges(llm_client=model, catalog=catalog, database=database, output=output)
    assert model.calls == 1
    assert json.loads((output / "evaluation_manifest.json").read_text())["status"] == "ABORTED"
    assert not (output / "challenge_score.json").exists()
    with pytest.raises(ValueError, match="Complete challenge runtime"):
        score_challenges(output, database)


def test_paired_mode_freezes_memory_and_rejects_training_question_overlap(source, tmp_path):
    database, catalog, scenarios = source
    memory = ExperienceMemory()
    memory.add_evidence("metric:avg_order_payment", {"question_sha256": digest("unit-test training question"),
                                                   "trace_sha256": "unit-test synthetic provenance"})
    before = memory.snapshot_id
    report = run_challenges(llm_client=ScriptedModel(scenarios), catalog=catalog, database=database,
                            output=tmp_path / "paired", mode="paired", repeats=1, memory=memory)
    assert memory.snapshot_id == before
    assert report["paired_outcomes"]["pairs"] == 4
    assert report["paired_outcomes"]["both_failed"] == 1
    assert report["groups"]["with_memory"]["memory_hits"] > 0
    memory.entries[0]["sources"][0]["question_sha256"] = digest(scenarios[0].question)
    with pytest.raises(ValueError, match="overlap"):
        run_challenges(llm_client=ScriptedModel(scenarios), catalog=catalog, database=database,
                       output=tmp_path / "overlap", mode="paired", memory=memory)


def test_unique_customer_counts_have_no_monetary_tolerance():
    assert not _matches({"unique_customers": 3.005}, {"unique_customers": "3"})
    assert _matches({"unique_customers": 3}, {"unique_customers": "3"})


@pytest.fixture
def reviewed_source(source, tmp_path):
    database, catalog, scenarios = source

    class ContrastingModel(ScriptedModel):
        def __init__(self):
            super().__init__(scenarios)
            self.sql_counts = {}

        def generate_json(self, messages, response_type):
            payload = json.loads(messages[-1]["content"])
            s = self.scenarios[payload["question"]]
            if response_type.__name__ != "SQLGeneration":
                return super().generate_json(messages, response_type)
            count = self.sql_counts.get(s.task_id, 0)
            self.sql_counts[s.task_id] = count + 1
            sql = reference_sql(s)
            if count == 0 and s.task_id == "D3":
                sql = """SELECT COALESCE(SUM(p.payment_value), 0) AS payment_value,
                    COALESCE(SUM(i.price), 0) AS item_revenue, COALESCE(SUM(i.freight_value), 0) AS freight_value
                    FROM orders o JOIN order_payments p USING(order_id) JOIN order_items i USING(order_id)
                    WHERE o.order_id IN (SELECT order_id FROM orders GROUP BY order_id HAVING COUNT(*) >= 2)"""
            if count == 0 and s.task_id == "D1":
                sql = "SELECT SUM(missing_alias.payment_value) AS avg_order_payment FROM orders o"
            return LLMGenerationResult(ok=True, data=response_type.from_dict({"sql": sql, "reason": "Test contrast"}).to_dict())

    run_dir = tmp_path / "reviewed_run"
    run_challenges(llm_client=ContrastingModel(), catalog=catalog, database=database, output=run_dir, repeats=2)
    findings, selections = [], []
    for task_id, rule_id in (("D3", "reviewed:child_row_eligibility"), ("D1", "reviewed:alias_scope")):
        evidence = []
        for repeat in (1, 2):
            run_id = f"{task_id}_r{repeat:02d}"
            trace = run_dir / "without_memory" / run_id / "trace.jsonl"
            sql = next(e.data["sql"] for e in load_trace(trace) if e.event_type == "sql_generated")
            result = execute_sql(sql, database_path=database)
            evidence.append({"run_id": run_id, "trace_sha256": file_sha256(trace), "sql": sql,
                             "replay_ok": result.ok, "matches_independent_reference": repeat == 2,
                             "error_type": result.error_type})
        findings.append({"task_id": task_id, "paired_evidence": evidence})
        selections.append({"task_id": task_id, "rule_id": rule_id,
                           "failed_run_id": f"{task_id}_r01", "successful_run_id": f"{task_id}_r02"})
    review_path = run_dir / "failure_analysis.json"
    write_json(review_path, {"status": "REVIEWED_CANDIDATES_NOT_PROMOTED", "source_split": "dev", "findings": findings})
    plan_path = tmp_path / "promotion_plan.json"
    write_json(plan_path, {"status": "APPROVED_FOR_CANDIDATE", "review_sha256": file_sha256(review_path),
                           "catalog_id": json.loads((catalog / "manifest.json").read_text())["catalog_id"],
                           "reviews": selections})
    return database, run_dir, plan_path, tmp_path / "candidate.json"


def test_reviewed_failure_promotion_is_isolated_and_retrieval_scoped(reviewed_source):
    database, run_dir, plan_path, output = reviewed_source
    report = build_failure_memory(run_dir=run_dir, plan_path=plan_path, database=database, output=output)
    assert report["active_entry_count"] == 2
    memory = ExperienceMemory.load(output)
    assert {e["rule_id"] for e in memory.entries} == set(REVIEWED_RULES)
    question = "至少有 2 条商品明细的订单的支付金额？"
    assert not memory.retrieve(question, stage="analysis")
    assert not memory.retrieve("订单数是多少？", stage="sql_generation", metric_ids=["order_count"])
    retrieved = memory.retrieve(question, stage="sql_generation", metric_ids=["payment_value"])
    assert len(retrieved) == 2
    assert not any(word in json.dumps(retrieved) for word in ("2017", "trace_sha256", "D3_r01"))
    assert all(e["sources"][0]["source_split"] == "dev" for e in memory.entries)
    assert report["evaluation_plan"]["no_candidate_changes_after_test"]
    with pytest.raises(ValueError, match="never overwritten"):
        build_failure_memory(run_dir=run_dir, plan_path=plan_path, database=database, output=output)


@pytest.mark.parametrize("tamper", ["test_split", "review", "trace", "false_success", "replay"])
def test_promotion_rejects_heldout_or_inconsistent_evidence(reviewed_source, tamper):
    database, run_dir, plan_path, output = reviewed_source
    if tamper == "test_split":
        path = run_dir / "evaluation_manifest.json"
        data = json.loads(path.read_text())
        data["split"] = "test"
        write_json(path, data)
    elif tamper == "review":
        path = run_dir / "failure_analysis.json"
        path.write_text(path.read_text() + "\n")
    elif tamper == "trace":
        path = run_dir / "without_memory" / "D3_r01" / "trace.jsonl"
        path.write_text(path.read_text() + "\n")
    elif tamper == "false_success":
        path = run_dir / "without_memory" / "D3_r02" / "run_summary.json"
        data = json.loads(path.read_text())
        data["final_answer"]["key_values"]["payment_value"] = 999
        write_json(path, data)
    else:
        path = run_dir / "failure_analysis.json"
        data = json.loads(path.read_text())
        data["findings"][0]["paired_evidence"][0]["replay_ok"] = False
        write_json(path, data)
        plan = json.loads(plan_path.read_text())
        plan["review_sha256"] = file_sha256(path)
        write_json(plan_path, plan)
    reasons = {"test_split": "Only completed development", "review": "Manual review changed",
               "trace": "Reviewed trace changed", "false_success": "Successful counterpart",
               "replay": "failure does not reproduce"}
    with pytest.raises(ValueError, match=reasons[tamper]):
        build_failure_memory(run_dir=run_dir, plan_path=plan_path, database=database, output=output)
    assert not output.exists()


def test_reviewed_rule_versions_do_not_invalidate_metric_memory(monkeypatch):
    memory = ExperienceMemory()
    memory.add_evidence("metric:payment_value", {"trace_sha256": "metric-source"})
    metric_entry = json.loads(json.dumps(memory.entries[0]))
    memory.add_evidence("reviewed:alias_scope", {"trace_sha256": "reviewed-source"})
    assert memory.active_entry_count == 2
    monkeypatch.setitem(REVIEWED_RULES, "reviewed:alias_scope", "Revised approved instruction")
    reloaded = ExperienceMemory(memory.entries)
    assert reloaded.active_entry_count == 1
    assert metric_entry in reloaded.entries
    assert reloaded.retrieve("支付金额", stage="analysis")[0]["rule_id"] == "metric:payment_value"
