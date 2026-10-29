"""Replay explicitly reviewed development failures and freeze candidate lessons."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT.parent))

from veriagent.data_agent.db import DEFAULT_DATABASE_PATH
from veriagent.evaluation.failure_memory import build_failure_memory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--memory-file", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    args = parser.parse_args()
    try:
        report = build_failure_memory(run_dir=args.run_dir, plan_path=args.plan,
                                      database=args.database, output=args.memory_file)
    except (OSError, ValueError, KeyError) as error:
        print(f"Candidate promotion stopped: {error}", file=sys.stderr)
        return 2
    print(f"Frozen {report['active_entry_count']} candidate rules: {args.memory_file.resolve()}")
    print(f"Snapshot: {report['snapshot_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
