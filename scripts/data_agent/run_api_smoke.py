"""Run DA01/DA02/DA10 via a configured API, then score the completed artifacts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
import json
import urllib.error
import urllib.request


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(REPOSITORY_ROOT.parent))

from veriagent.data_agent.llm import LLMConfig
from veriagent.data_agent.llm.environment import read_environment
from veriagent.data_agent.memory import DEFAULT_MEMORY_PATH, ExperienceMemory, learn_from_run
from veriagent.scripts.data_agent.run_openpai_qwen_smoke import (
    DEFAULT_DATABASE,
    DEFAULT_TASKS_FILE,
    run_smoke,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=REPOSITORY_ROOT / ".env")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--check-config", action="store_true", help="Validate configuration without an API call")
    parser.add_argument("--check-api", action="store_true", help="Check API authentication via GET /models without inference")
    parser.add_argument("--memory-file", type=Path, default=DEFAULT_MEMORY_PATH)
    parser.add_argument("--no-memory", action="store_true", help="Disable experience retrieval and learning")
    args = parser.parse_args()
    try:
        values = read_environment(args.env_file)
        config = LLMConfig.from_env(values)
        if config.api_key.lower() in {"", "empty", "your-api-key", "replace-me"}:
            raise ValueError(f"Set LLM_API_KEY locally in {args.env_file}; do not send it in chat")
        for path in (DEFAULT_DATABASE, DEFAULT_TASKS_FILE):
            if not path.is_file():
                raise FileNotFoundError(f"Required data asset missing: {path}")
    except (OSError, ValueError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    print(f"Model: {config.model}; endpoint: {config.base_url}; API key: configured", flush=True)
    if args.check_config:
        return 0
    if args.check_api:
        request = urllib.request.Request(
            config.base_url + "/models",
            headers={"Authorization": "Bearer " + config.api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            if not isinstance(payload.get("data"), list):
                raise ValueError("API returned an unexpected model-list response")
            print("API authentication: OK")
            return 0
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read(8192))
                detail = str(payload.get("error", {}).get("message", ""))[:500]
            except (ValueError, AttributeError):
                detail = "Non-JSON response; check API endpoint and proxy configuration"
            print(f"API HTTP {error.code}: {detail.replace(config.api_key, '[REDACTED]')}")
            error.close()
            return 2
        except (OSError, ValueError) as error:
            print(str(error).replace(config.api_key, "[REDACTED]"))
            return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output_root = args.output_root or REPOSITORY_ROOT / "results" / f"api_smoke_{stamp}"
    print(f"Results: {output_root.resolve()}", flush=True)
    try:
        memory = None if args.no_memory else ExperienceMemory.load(args.memory_file)
        if memory is not None:
            print(f"Experience memory: {memory.active_entry_count} active entries", flush=True)
        run_smoke(
            tasks_file=DEFAULT_TASKS_FILE,
            database=DEFAULT_DATABASE,
            output_root=output_root,
            environ=values,
            execution_backend="local-api",
            smoke_id="api_smoke_v0",
            experience_memory=memory,
        )
        # Standard answers are read only after the runtime has completed.
        from veriagent.scripts.data_agent.score_qwen_smoke import DEFAULT_GOLD_FILE, score_smoke

        score = score_smoke(results_root=output_root, gold_file=DEFAULT_GOLD_FILE)
        if not args.no_memory:
            learning = learn_from_run(output_root, args.memory_file)
            (output_root / "memory_learning.json").write_text(json.dumps(learning, ensure_ascii=False, indent=2) + "\n")
            print(f"Experience learning: {len(learning['accepted'])} verified lesson(s), {learning['changed_sources']} new evidence source(s)")
    except (OSError, ValueError) as error:
        message = str(error).replace(config.api_key, "[REDACTED]")
        print(f"Run error: {message}", file=sys.stderr)
        return 2
    return 0 if score["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
