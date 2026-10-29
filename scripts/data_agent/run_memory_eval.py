"""Compare frozen experience retrieval against no memory on held-out questions."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(REPOSITORY_ROOT.parent))

from veriagent.data_agent import DataAgentRunner
from veriagent.data_agent.db import DEFAULT_DATABASE_PATH
from veriagent.data_agent.llm import LLMClient, LLMConfig
from veriagent.data_agent.llm.environment import read_environment
from veriagent.data_agent.memory import DEFAULT_MEMORY_PATH, ExperienceMemory
from veriagent.data_agent.memory.store import digest
from veriagent.data_agent.tools.registry import create_registry
from veriagent.data_agent.trace import load_trace
from veriagent.evaluation.memory_tasks import MEMORY_TASK_SUITES


def run_evaluation(*, llm_client, memory: ExperienceMemory, database: Path, output_root: Path,
                   suite: str = "v1", repeats: int = 1) -> dict:
    if isinstance(repeats, bool) or not isinstance(repeats, int) or not 1 <= repeats <= 20:
        raise ValueError("repeats must be an integer between 1 and 20")
    if not memory.active_entry_count:
        raise ValueError("Build verified experience memory before running the comparison")
    if not Path(database).is_file():
        raise ValueError("Evaluation database is missing")
    tasks = MEMORY_TASK_SUITES[suite]
    training_questions = {source["question_sha256"] for entry in memory.entries for source in entry["sources"]}
    if any(digest(task["question"]) in training_questions for task in tasks):
        raise ValueError("Evaluation questions overlap memory sources; choose a frozen training-only snapshot")
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=False)
    memory.save(output_root / "memory_snapshot.json")
    manifest = {
        "status": "RUNNING", "model": llm_client.model, "suite": suite, "tasks": list(tasks),
        "repeats": repeats, "runs": [], "tasks_sha256": digest(list(tasks)),
        "memory_snapshot_id": memory.snapshot_id, "learning_during_evaluation": False,
        "database": str(Path(database).resolve()),
        "generation": {key: getattr(getattr(llm_client, "config", None), key, None)
                       for key in ("temperature", "max_tokens", "thinking", "allow_json_repair")},
    }
    manifest_path = output_root / "evaluation_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    runners = {
        name: DataAgentRunner(
            llm_client=llm_client, tool_registry=create_registry(database),
            trace_root=output_root / name, execution_backend="local-api",
            experience_memory=memory if name == "with_memory" else None,
        ) for name in ("without_memory", "with_memory")
    }
    # Flip the first condition across both tasks and repetitions. Each run starts
    # a fresh conversation; no evaluation result becomes training evidence.
    try:
        for repeat in range(1, repeats + 1):
            for index, task in enumerate(tasks):
                order = ("without_memory", "with_memory") if (index + repeat - 1) % 2 == 0 else ("with_memory", "without_memory")
                for group in order:
                    run_id = task["task_id"] if repeats == 1 else f"{task['task_id']}_r{repeat:02d}"
                    print(f"{group}/{run_id}: RUNNING", flush=True)
                    started = time.perf_counter()
                    result = runners[group].run(run_id=run_id, **task)
                    manifest["runs"].append({
                        "group": group, "task_id": task["task_id"], "repeat": repeat,
                        "run_id": run_id, "status": result.status,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                    })
                    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
                    print(f"{group}/{run_id}: {result.status}", flush=True)
                    # Preserve the failed trace, but do not spend the remaining
                    # experiment budget on a broken API connection or credential.
                    failures = [e for e in load_trace(result.trace_path) if e.event_type == "run_failed"]
                    if failures and failures[-1].data.get("error_type") == "LLM_API_ERROR":
                        raise OSError("Evaluation stopped after LLM API failure: " + failures[-1].data["error"])
        if memory.snapshot_id != manifest["memory_snapshot_id"]:
            raise ValueError("Memory changed during evaluation")
    except (OSError, ValueError, KeyboardInterrupt) as error:
        manifest["status"] = "ABORTED"
        manifest["abort_type"] = type(error).__name__
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        raise
    manifest["status"] = "COMPLETED"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    # Import the independent reference scorer only after every runtime finishes.
    from veriagent.evaluation.memory_scoring import score_memory_evaluation

    return score_memory_evaluation(output_root, database)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=REPOSITORY_ROOT / ".env")
    parser.add_argument("--memory-file", type=Path, default=DEFAULT_MEMORY_PATH)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--suite", choices=sorted(MEMORY_TASK_SUITES), default="v1")
    parser.add_argument("--repeats", type=int, default=1, help="Independent repetitions per question and condition (1-20)")
    args = parser.parse_args()
    values = read_environment(args.env_file)
    config = LLMConfig.from_env(values, temperature=0.0, max_tokens=1024, timeout_seconds=90.0)
    if config.api_key.lower() in {"", "empty", "your-api-key", "replace-me"}:
        print("Configure LLM_API_KEY locally in .env", file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output = args.output_root or REPOSITORY_ROOT / "results" / f"memory_eval_{stamp}"
    print(f"Model: {config.model}; results: {output.resolve()}", flush=True)
    try:
        report = run_evaluation(
            llm_client=LLMClient(config), memory=ExperienceMemory.load(args.memory_file),
            database=args.database, output_root=output,
            suite=args.suite, repeats=args.repeats,
        )
    except (OSError, ValueError) as error:
        print(str(error).replace(config.api_key, "[REDACTED]"), file=sys.stderr)
        return 2
    for name, group in report["groups"].items():
        print(f"{name}: {group['passed']}/{group['total']}, false accepts: {group['false_accepts']}, memory hits: {group['memory_hits']}")
    paired = report["paired_outcomes"]
    print(f"Memory helped: {paired['helped']}; harmed: {paired['harmed']}; net: {paired['net_helped']}/{paired['pairs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
