"""Freeze and independently validate the local Olist challenge scenarios."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT.parent))

from veriagent.data_agent.db import DEFAULT_DATABASE_PATH
from veriagent.evaluation.challenge_catalog import build_catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output = args.output_root or ROOT / "results" / f"challenge_set_{stamp}"
    result = build_catalog(args.database, output)
    print(f"Validated catalog: {output.resolve()}")
    print(f"dev: {result['splits']['dev']['tasks']}; test: {result['splits']['test']['tasks']}")
    print(f"Catalog ID: {result['catalog_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
