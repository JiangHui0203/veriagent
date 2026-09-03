from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .baselines import (
    DirectFullContextBaseline,
    DirectThinkBaseline,
    FixedWorkflowBaseline,
    ReActLLMBaseline,
    ReActSymbolicBaseline,
    StandardRAGBaseline,
    StandardRAGLLMBaseline,
)
from .data.proofwriter import iter_proofwriter_tasks, task_to_record
from .evaluation.metrics import evaluate_by_method
from .faults import FaultInjector, FaultKind, FaultScenario
from .llm import OpenAICompatibleClient
from .runner import VeriAgentRunner
from .schemas import RunResult, Task, to_jsonable
from .sft import export_controller_sft
from .state import JsonlTraceStore
from .tools import ProofVerifier


OFFLINE_METHODS = {
    "direct-full",
    "standard-rag",
    "react",
    "fixed",
    "veriagent",
    "veriagent-auditor",
    "veriagent-recovery",
}
LLM_METHODS = {"direct-think-llm", "standard-rag-llm", "react-llm"}


def _write_json(path: str | Path, payload: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)


def _llm_client_from_env() -> OpenAICompatibleClient:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    model = os.getenv("LLM_MODEL", "deepseek-chat")
    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    input_cost = float(os.getenv("LLM_INPUT_COST_PER_MILLION", "0"))
    output_cost = float(os.getenv("LLM_OUTPUT_COST_PER_MILLION", "0"))
    if not api_key:
        raise RuntimeError("LLM_API_KEY or DEEPSEEK_API_KEY is required for LLM baselines")
    return OpenAICompatibleClient(
        api_key=api_key,
        model=model,
        base_url=base_url,
        input_cost_per_million=input_cost,
        output_cost_per_million=output_cost,
    )


def cmd_prepare(args: argparse.Namespace) -> int:
    tasks = list(
        iter_proofwriter_tasks(
            args.input,
            limit_worlds=args.limit_worlds,
            questions_per_world=args.questions_per_world,
        )
    )
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for task in tasks:
            handle.write(json.dumps(task_to_record(task), ensure_ascii=False) + "\n")
    print(f"prepared {len(tasks)} tasks -> {target}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    verifier = ProofVerifier()
    total = 0
    mismatches = []
    for task in iter_proofwriter_tasks(
        args.input,
        limit_worlds=args.limit_worlds,
        questions_per_world=args.questions_per_world,
    ):
        total += 1
        predicted = verifier.infer_label(task.world.evidence.values(), task.question).label
        if predicted != task.question.label:
            mismatches.append(
                {
                    "task_id": task.task_id,
                    "gold": task.question.label.value,
                    "predicted": predicted.value,
                }
            )
    print(json.dumps({"count": total, "mismatches": mismatches[:20]}, ensure_ascii=False, indent=2))
    return 1 if mismatches else 0


def _build_runner(
    method: str,
    args: argparse.Namespace,
    trace_store: JsonlTraceStore | None,
):
    if method == "direct-full":
        return DirectFullContextBaseline()
    if method == "standard-rag":
        return StandardRAGBaseline(top_k=args.top_k)
    if method == "react":
        return ReActSymbolicBaseline(top_k=args.top_k, max_tool_calls=args.max_tool_calls)
    if method == "fixed":
        return FixedWorkflowBaseline(top_k=max(args.top_k, 50))
    if method in {"veriagent", "veriagent-auditor", "veriagent-recovery"}:
        scenario = None
        if args.fault != FaultKind.NONE.value:
            scenario = FaultScenario(
                fault_id=f"{args.fault}-call-{args.fault_trigger_call}",
                kind=FaultKind(args.fault),
                trigger_call=args.fault_trigger_call,
            )
        adaptive_stop = method == "veriagent"
        recovery_enabled = method in {"veriagent", "veriagent-recovery"}
        from .auditor import DeterministicAuditor
        from .controller import DeterministicController

        return VeriAgentRunner(
            auditor=DeterministicAuditor(adaptive_stop=adaptive_stop),
            controller=DeterministicController(recovery_enabled=recovery_enabled),
            fault_injector=FaultInjector(scenario),
            trace_store=trace_store,
            max_steps=args.max_steps,
            max_tool_calls=args.max_tool_calls,
            method_name=method,
        )

    client = _llm_client_from_env()
    if method == "direct-think-llm":
        return DirectThinkBaseline(client)
    if method == "standard-rag-llm":
        return StandardRAGLLMBaseline(client, top_k=args.top_k)
    if method == "react-llm":
        return ReActLLMBaseline(client, max_steps=args.max_steps, top_k=args.top_k)
    raise ValueError(f"unknown method: {method}")


def cmd_run(args: argparse.Namespace) -> int:
    methods = [x.strip() for x in args.methods.split(",") if x.strip()]
    unknown = set(methods) - OFFLINE_METHODS - LLM_METHODS
    if unknown:
        raise ValueError(f"unknown methods: {sorted(unknown)}")

    tasks = list(
        iter_proofwriter_tasks(
            args.input,
            limit_worlds=args.limit_worlds,
            questions_per_world=args.questions_per_world,
        )
    )
    task_map: dict[str, Task] = {task.task_id: task for task in tasks}
    trace_store = None
    if args.trace_output:
        trace_path = Path(args.trace_output)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text("", encoding="utf-8")
        trace_store = JsonlTraceStore(trace_path)

    results: list[RunResult] = []
    for task in tasks:
        for method in methods:
            runner = _build_runner(method, args, trace_store)
            results.append(runner.run(task))

    summary = evaluate_by_method(results, task_map)
    serialized_results = []
    for result in results:
        row = to_jsonable(result)
        if not args.include_events:
            row["events"] = []
        serialized_results.append(row)

    output = {
        "meta": {
            "input": args.input,
            "methods": methods,
            "limit_worlds": args.limit_worlds,
            "questions_per_world": args.questions_per_world,
            "top_k": args.top_k,
            "max_steps": args.max_steps,
            "max_tool_calls": args.max_tool_calls,
            "fault": args.fault,
        },
        "summary": summary,
        "results": serialized_results,
    }
    _write_json(args.output, output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"saved {len(results)} runs -> {args.output}")
    return 0


def cmd_export_sft(args: argparse.Namespace) -> int:
    count = export_controller_sft(
        args.trace_input,
        args.output,
        successful_only=not args.include_failed,
    )
    print(f"exported {count} Controller samples -> {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="veriagent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", required=True, help="ProofWriter meta-*.jsonl path")
    common.add_argument("--limit-worlds", type=int, default=None)
    common.add_argument("--questions-per-world", type=int, default=None)

    prepare = subparsers.add_parser("prepare", parents=[common])
    prepare.add_argument("--output", required=True)
    prepare.set_defaults(func=cmd_prepare)

    validate = subparsers.add_parser("validate", parents=[common])
    validate.set_defaults(func=cmd_validate)

    run = subparsers.add_parser("run", parents=[common])
    run.add_argument(
        "--methods",
        default="direct-full,standard-rag,react,fixed,veriagent",
    )
    run.add_argument("--output", required=True)
    run.add_argument("--trace-output", default=None)
    run.add_argument("--include-events", action="store_true")
    run.add_argument("--top-k", type=int, default=8)
    run.add_argument("--max-steps", type=int, default=40)
    run.add_argument("--max-tool-calls", type=int, default=30)
    run.add_argument(
        "--fault",
        choices=[x.value for x in FaultKind],
        default=FaultKind.NONE.value,
    )
    run.add_argument("--fault-trigger-call", type=int, default=1)
    run.set_defaults(func=cmd_run)

    export_sft = subparsers.add_parser("export-sft")
    export_sft.add_argument("--trace-input", required=True)
    export_sft.add_argument("--output", required=True)
    export_sft.add_argument("--include-failed", action="store_true")
    export_sft.set_defaults(func=cmd_export_sft)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
