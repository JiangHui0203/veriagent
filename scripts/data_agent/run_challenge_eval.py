"""Run a frozen challenge split; default to development baseline without memory."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT.parent))

from veriagent.data_agent import DataAgentRunner
from veriagent.data_agent.db import DEFAULT_DATABASE_PATH
from veriagent.data_agent.llm import LLMClient, LLMConfig
from veriagent.data_agent.llm.environment import read_environment
from veriagent.data_agent.memory import DEFAULT_MEMORY_PATH, ExperienceMemory
from veriagent.data_agent.memory.store import digest
from veriagent.data_agent.tools.registry import create_registry
from veriagent.data_agent.trace import load_trace
from veriagent.evaluation.challenge_catalog import load_tasks, write_json


def run_challenges(*, llm_client, catalog: Path, database: Path, output: Path, split="dev",
                   mode="baseline", repeats=2, memory: ExperienceMemory | None = None) -> dict:
    if mode not in {"baseline", "paired"} or isinstance(repeats, bool) or not isinstance(repeats, int) or not 1 <= repeats <= 20:
        raise ValueError("Use baseline/paired mode and 1-20 repetitions")
    catalog_manifest, tasks = load_tasks(catalog, split, database)
    if mode == "paired":
        if memory is None or not memory.active_entry_count:
            raise ValueError("Paired evaluation requires active verified experience memory")
        training = {source["question_sha256"] for entry in memory.entries for source in entry["sources"]}
        if any(digest(task["question"]) in training for task in tasks):
            raise ValueError("Challenge questions overlap memory training sources")
    else:
        memory = None
    output.mkdir(parents=True, exist_ok=False)
    if memory is not None:
        memory.save(output / "memory_snapshot.json")
    conditions = ["without_memory", "with_memory"] if mode == "paired" else ["without_memory"]
    manifest = {"status": "RUNNING", "model": llm_client.model, "catalog": str(catalog.resolve()),
                "catalog_id": catalog_manifest["catalog_id"], "split": split, "mode": mode,
                "repeats": repeats, "conditions": conditions, "tasks": tasks, "runs": [],
                "learning_during_evaluation": False,
                "memory_snapshot_id": memory.snapshot_id if memory is not None else None,
                "generation": {key: getattr(getattr(llm_client, "config", None), key, None)
                               for key in ("temperature", "max_tokens", "thinking", "allow_json_repair")}}
    manifest_path = output / "evaluation_manifest.json"
    write_json(manifest_path, manifest)
    runners = {group: DataAgentRunner(llm_client=llm_client, tool_registry=create_registry(database),
                                      trace_root=output / group, execution_backend="local-api",
                                      experience_memory=memory if group == "with_memory" else None) for group in conditions}
    try:
        for repeat in range(1, repeats + 1):
            for index, task in enumerate(tasks):
                order = conditions if (index + repeat - 1) % 2 == 0 else list(reversed(conditions))
                for group in order:
                    run_id = f"{task['task_id']}_r{repeat:02d}"
                    print(f"{group}/{run_id}: RUNNING", flush=True)
                    started = time.perf_counter()
                    result = runners[group].run(run_id=run_id, **task)
                    manifest["runs"].append({"task_id": task["task_id"], "run_id": run_id, "repeat": repeat,
                                             "group": group, "status": result.status,
                                             "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)})
                    write_json(manifest_path, manifest)
                    print(f"{group}/{run_id}: {result.status}", flush=True)
                    errors = [e.data for e in load_trace(result.trace_path) if e.event_type == "run_failed"]
                    if errors and errors[-1].get("error_type") == "LLM_API_ERROR":
                        raise OSError("Evaluation stopped after LLM API failure: " + errors[-1]["error"])
        if memory is not None and memory.snapshot_id != manifest["memory_snapshot_id"]:
            raise ValueError("Memory changed during challenge evaluation")
    except (OSError, ValueError, KeyboardInterrupt) as error:
        manifest.update(status="ABORTED", abort_type=type(error).__name__)
        write_json(manifest_path, manifest)
        raise
    manifest["status"] = "COMPLETED"
    write_json(manifest_path, manifest)
    # Scoring and expected values are only loaded after every run has finished.
    from veriagent.evaluation.challenge_scoring import score_challenges

    return score_challenges(output, database)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--memory-file", type=Path, default=DEFAULT_MEMORY_PATH)
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--mode", choices=("baseline", "paired"), default="baseline")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    config = LLMConfig.from_env(read_environment(args.env_file), temperature=0.0, max_tokens=1024, timeout_seconds=90.0)
    if config.api_key.lower() in {"", "empty", "your-api-key", "replace-me"}:
        print("Configure LLM_API_KEY locally in .env", file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output = args.output_root or ROOT / "results" / f"challenge_eval_{stamp}"
    print(f"Model: {config.model}; split: {args.split}; mode: {args.mode}; results: {output.resolve()}", flush=True)
    try:
        report = run_challenges(llm_client=LLMClient(config), catalog=args.catalog, database=args.database,
                                output=output, split=args.split, mode=args.mode, repeats=args.repeats,
                                memory=ExperienceMemory.load(args.memory_file) if args.mode == "paired" else None)
    except (OSError, ValueError) as error:
        print(str(error).replace(config.api_key, "[REDACTED]"), file=sys.stderr)
        return 2
    for name, group in report["groups"].items():
        print(f"{name}: {group['passed']}/{group['total']}; false accepts: {group['false_accepts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
