"""Extract bounded experience rules from scored API smoke runs, without LLM calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(REPOSITORY_ROOT.parent))

from veriagent.data_agent.memory import DEFAULT_MEMORY_PATH, ExperienceMemory, learn_from_run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, action="append", help="Repeat to select source runs explicitly")
    parser.add_argument("--memory-file", type=Path, default=DEFAULT_MEMORY_PATH)
    args = parser.parse_args()
    runs = args.run_dir or sorted((REPOSITORY_ROOT / "results").glob("api_smoke_*"))
    reports = [learn_from_run(run, args.memory_file) for run in runs]
    memory = ExperienceMemory.load(args.memory_file)
    report = {"runs": reports, "entry_count": len(memory.entries),
              "active_entry_count": memory.active_entry_count, "snapshot_id": memory.snapshot_id}
    report_path = args.memory_file.with_name("learning_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Processed {len(runs)} runs; memory contains {memory.active_entry_count} active rule(s), {len(memory.entries)} total version(s).")
    print(f"Memory: {args.memory_file.resolve()}")
    print(f"Learning report: {report_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
