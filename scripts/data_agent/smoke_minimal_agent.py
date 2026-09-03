"""Run DA01, DA02, and DA10 locally with mock-only LLM responses."""

from __future__ import annotations

from pathlib import Path

from tests.fixtures.data_agent_mock_cases import MOCK_CASES
from tests.fixtures.fake_llm import FakeLLMClient
from veriagent.data_agent import DataAgentRunner, registry
from veriagent.data_agent.trace import load_trace


REPO_ROOT = Path(__file__).resolve().parents[3]
TRACE_ROOT = REPO_ROOT / "results" / "data_agent" / "mock_traces"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_case(task_id: str):
    case = MOCK_CASES[task_id]
    fake = FakeLLMClient(case["responses"])
    runner = DataAgentRunner(
        llm_client=fake,
        tool_registry=registry,
        trace_root=TRACE_ROOT,
    )
    result = runner.run(
        run_id=task_id,
        task_id=task_id,
        question=case["question"],
        cross_check_sql=case["cross_check_sql"],
    )
    return result, fake


def main() -> None:
    da01, _ = run_case("DA01")
    require(da01.status == "SUCCESS", "DA01 did not succeed")
    require(da01.final_answer["key_values"]["order_count"] == 7_544, "DA01 value mismatch")
    print("DA01 mock agent      PASS")

    da02, _ = run_case("DA02")
    require(da02.status == "SUCCESS", "DA02 did not succeed")
    require(
        abs(da02.final_answer["key_values"]["payment_value"] - 1_153_528.05) <= 0.01,
        "DA02 value mismatch",
    )
    print("DA02 mock agent      PASS")
    da02_events = load_trace(da02.trace_path)
    cross_events = [event for event in da02_events if event.event_type == "cross_check_completed"]
    require(
        len(cross_events) == 1
        and cross_events[0].data["tool_result"]["result"]["matched"] is True,
        "DA02 cross-check did not match",
    )
    print("DA02 cross-check     PASS")

    da10, fake10 = run_case("DA10")
    require(da10.status == "STOP_WITH_DATA_GAP", "DA10 did not stop safely")
    require(da10.final_answer["key_values"] == {}, "DA10 fabricated a numeric value")
    da10_events = load_trace(da10.trace_path)
    require(fake10.call_count == 1, "DA10 requested SQL or final-answer generation")
    require(
        not any(event.event_type in {"sql_generated", "sql_execution_completed"} for event in da10_events),
        "DA10 executed SQL",
    )
    print("DA10 safe stop       PASS")

    for result in (da01, da02, da10):
        events = load_trace(result.trace_path)
        require(events[0].event_type == "run_started", "trace does not start correctly")
        require(events[-1].event_type == "run_finished", "trace does not finish correctly")
        require(all(event.data is not None for event in events), "trace data missing")
    print("trace validation     PASS")


if __name__ == "__main__":
    main()
