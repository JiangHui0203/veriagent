from __future__ import annotations

import duckdb
import pytest

from veriagent.evaluation.memory_metrics import paired_outcomes, summarize_group
from veriagent.evaluation.memory_scoring import reference_values


def case(task_id, passed, **overrides):
    return {
        "task_id": task_id, "run_id": task_id, "repeat": 1, "passed": passed,
        "false_accept": False, "answerable": True, "analysis_succeeded": True,
        "invalid_table": False, "failure_category": None if passed else "SQL_EXECUTION_ERROR",
        "llm_requests": 2, "prompt_tokens": 80, "completion_tokens": 20,
        "llm_latency_ms": 60, "end_to_end_ms": 100, "memory_hits": 0,
        **overrides,
    }


def test_paired_metrics_count_harm_as_well_as_help_and_charge_for_failures():
    groups = {
        "without_memory": summarize_group([case("a", True), case("b", False), case("c", True), case("d", False)]),
        "with_memory": summarize_group([case("a", True), case("b", True), case("c", False), case("d", False)]),
    }
    paired = paired_outcomes(groups)
    assert (paired["helped"], paired["harmed"], paired["both_passed"], paired["both_failed"]) == (1, 1, 1, 1)
    assert paired["net_benefit_rate"] == 0
    # All four attempts cost 100 tokens, despite only two being correct.
    assert groups["with_memory"]["cost_per_correct_completion"]["total_tokens"] == 200
    assert groups["with_memory"]["cost_per_correct_completion"]["llm_requests"] == 4
    assert groups["with_memory"]["cost_per_correct_completion"]["end_to_end_ms"] == 200


def test_all_failed_cost_is_undefined_and_missing_old_timers_are_not_zero():
    all_failed = summarize_group([case("a", False)])
    assert all(value is None for value in all_failed["cost_per_correct_completion"].values())
    legacy = summarize_group([case("a", True, end_to_end_ms=None)])
    assert legacy["cost_per_correct_completion"]["end_to_end_ms"] is None
    assert legacy["end_to_end_latency"]["p95_ms"] is None
    assert legacy["stability"]["repeated_tasks"] == 0


def test_recurrence_excludes_unreached_analysis_and_unsupported_tasks():
    result = summarize_group([
        case("a", False, invalid_table=True, failure_category="INVALID_TABLE"),
        case("b", True),
        case("c", False, analysis_succeeded=False, failure_category="LLM_API_ERROR"),
        case("d", True, answerable=False),
    ])
    recurrence = result["known_error_recurrence"]["invalid_table"]
    assert recurrence["count"] == 1
    assert recurrence["opportunities"] == 2
    assert recurrence["excluded_before_analysis"] == 1
    assert recurrence["rate"] == 0.5
    assert recurrence["run_ids"] == ["a"]


def test_repetitions_report_mixed_outcomes_without_inflating_task_count():
    result = summarize_group([
        case("a", True, end_to_end_ms=10),
        case("a", False, repeat=2, run_id="a2", end_to_end_ms=30),
        case("b", True, end_to_end_ms=20),
        case("b", True, repeat=2, run_id="b2", end_to_end_ms=40),
    ])
    assert result["stability"]["repeated_tasks"] == 2
    assert result["stability"]["mixed_outcomes"] == 1
    assert result["stability"]["always_passed"] == 1
    assert result["end_to_end_latency"]["p50_ms"] == 20
    assert result["end_to_end_latency"]["p95_ms"] == 40
    assert result["by_repeat"] == [{"repeat": 1, "passed": 2, "total": 2}, {"repeat": 2, "passed": 1, "total": 2}]


def test_missing_or_duplicate_pairs_cannot_produce_net_benefit():
    groups = {"without_memory": {"tasks": [case("a", True)]}, "with_memory": {"tasks": []}}
    with pytest.raises(ValueError, match="same task/repeat"):
        paired_outcomes(groups)
    groups["with_memory"]["tasks"] = [case("a", True), case("a", False)]
    with pytest.raises(ValueError, match="Duplicate"):
        paired_outcomes(groups)


def test_v3_references_respect_explicit_scope_and_do_not_multiply_joined_amounts(tmp_path):
    database = tmp_path / "reference.duckdb"
    with duckdb.connect(str(database)) as con:
        con.execute("CREATE TABLE orders(order_id VARCHAR, order_status VARCHAR, order_purchase_timestamp TIMESTAMP)")
        con.execute("CREATE TABLE order_payments(order_id VARCHAR, payment_value DECIMAL(18,2))")
        con.execute("CREATE TABLE order_items(order_id VARCHAR, price DECIMAL(18,2), freight_value DECIMAL(18,2))")
        for month in range(1, 11):
            con.execute("INSERT INTO orders VALUES (?, 'delivered', ?), (?, 'canceled', ?)",
                        [f"d{month}", f"2017-{month:02d}-01", f"c{month}", f"2017-{month:02d}-02"])
            con.execute("INSERT INTO order_payments VALUES (?, 10), (?, 20), (?, 99)",
                        [f"d{month}", f"d{month}", f"c{month}"])
            con.execute("INSERT INTO order_items VALUES (?, 2, 1), (?, 3, 2), (?, 50, 5)",
                        [f"d{month}", f"d{month}", f"c{month}"])
    values = reference_values(database, "v3")
    assert [values[key]["order_count"] for key in ("EM21", "EM22", "EM23")] == ["2", "1", "1"]
    assert [values[key]["payment_value"] for key in ("EM24", "EM25", "EM26")] == ["30.00", "129.00", "30.00"]
    for key in ("EM27", "EM28", "EM29"):
        assert values[key] == {"payment_value": "30.00", "item_revenue": "5.00", "freight_value": "3.00"}
    assert all(values[key] is None for key in ("EM30", "EM31", "EM32"))
