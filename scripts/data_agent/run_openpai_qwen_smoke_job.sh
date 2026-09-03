#!/usr/bin/env bash
# OpenPAI-container entrypoint. The confirmed OpenAI-compatible server command
# must be supplied by the selected image; this script does not install a backend.

set -Eeuo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/mnt/userdata/jianghui/code/lot/datasets/final}"
MODEL_PATH="${MODEL_PATH:-/mnt/userdata/jianghui/models/Qwen3-8B}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${PROJECT_ROOT}/results/data_agent/qwen3_8b_smoke_v0}"
TASKS_FILE="${TASKS_FILE:-${PROJECT_ROOT}/veriagent/datasets/olistbr/tasks/v0/tasks_v0.jsonl}"
DATABASE="${DATABASE:-${PROJECT_ROOT}/veriagent/datasets/olistbr/ecommerce.duckdb}"
SERVER_READY_TIMEOUT_SEC="${SERVER_READY_TIMEOUT_SEC:-600}"
LLM_BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:8000/v1}"
LLM_API_KEY="${LLM_API_KEY:-EMPTY}"
LLM_MODEL="${LLM_MODEL:-Qwen3-8B}"
QWEN_SERVER_CMD="${QWEN_SERVER_CMD:-}"

export MODEL_PATH LLM_BASE_URL LLM_API_KEY LLM_MODEL
export PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

if [[ -z "${QWEN_SERVER_CMD}" ]]; then
  echo "ERROR: QWEN_SERVER_CMD is required from a confirmed OpenPAI image." >&2
  exit 2
fi
if ! [[ "${SERVER_READY_TIMEOUT_SEC}" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: SERVER_READY_TIMEOUT_SEC must be a positive integer." >&2
  exit 2
fi

missing=()
[[ -d "${PROJECT_ROOT}/veriagent" ]] || missing+=("project")
[[ -d "${MODEL_PATH}" ]] || missing+=("model")
[[ -f "${TASKS_FILE}" ]] || missing+=("tasks")
[[ -f "${DATABASE}" ]] || missing+=("database")
if (( ${#missing[@]} > 0 )); then
  echo "ERROR: inaccessible OpenPAI inputs: ${missing[*]}" >&2
  exit 3
fi
python -c 'import duckdb' || {
  echo "ERROR: the selected OpenPAI image does not provide duckdb." >&2
  exit 3
}

if [[ -e "${OUTPUT_ROOT}/smoke_summary.json" ]] \
  || [[ -e "${OUTPUT_ROOT}/DA01" ]] \
  || [[ -e "${OUTPUT_ROOT}/DA02" ]] \
  || [[ -e "${OUTPUT_ROOT}/DA10" ]]; then
  echo "ERROR: refusing to overwrite existing real smoke outputs: ${OUTPUT_ROOT}" >&2
  exit 4
fi
mkdir -p "${OUTPUT_ROOT}"
SERVER_LOG="${OUTPUT_ROOT}/server.log"

server_pid=""
cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  if [[ -n "${server_pid}" ]] && kill -0 "${server_pid}" 2>/dev/null; then
    kill -TERM -- "-${server_pid}" 2>/dev/null || kill "${server_pid}" 2>/dev/null || true
    wait "${server_pid}" 2>/dev/null || true
  fi
  exit "${exit_code}"
}
trap cleanup EXIT INT TERM

write_server_failure() {
  local message="$1"
  python - "${OUTPUT_ROOT}" "${LLM_MODEL}" "${message}" <<'PY'
import json
import sys
from pathlib import Path

output_root = Path(sys.argv[1])
payload = {
    "smoke_id": "qwen3_8b_smoke_v0",
    "model": sys.argv[2],
    "execution_backend": "OpenPAI",
    "status": "FAILED",
    "failure_category": "SERVER_START_FAILURE",
    "error": sys.argv[3],
    "tasks": [],
}
(output_root / "smoke_summary.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
PY
}

echo "Starting confirmed OpenAI-compatible Qwen server; log: ${SERVER_LOG}"
setsid bash -lc "exec ${QWEN_SERVER_CMD}" >"${SERVER_LOG}" 2>&1 &
server_pid=$!

ready=0
for ((second = 1; second <= SERVER_READY_TIMEOUT_SEC; second++)); do
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    write_server_failure "server process exited before readiness"
    echo "ERROR: Qwen server exited before becoming ready." >&2
    tail -n 120 "${SERVER_LOG}" >&2 || true
    exit 5
  fi

  if python - "${LLM_BASE_URL}" "${LLM_API_KEY}" "${LLM_MODEL}" >/dev/null 2>&1 <<'PY'
import json
import sys
import urllib.request

base_url, api_key, expected_model = sys.argv[1:]
headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
request = urllib.request.Request(base_url.rstrip("/") + "/models", headers=headers)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(request, timeout=2) as response:
    payload = json.loads(response.read().decode("utf-8"))
models = payload.get("data", [])
model_ids = [item.get("id") for item in models if isinstance(item, dict)]
if response.status != 200 or expected_model not in model_ids:
    raise RuntimeError(f"expected model {expected_model!r}; available={model_ids!r}")
PY
  then
    ready=1
    echo "Qwen endpoint ready after ${second}s."
    break
  fi

  if (( second % 30 == 0 )); then
    echo "Qwen server still loading (${second}s); latest log:"
    tail -n 8 "${SERVER_LOG}" || true
  fi
  sleep 1
done

if [[ "${ready}" -ne 1 ]]; then
  write_server_failure "endpoint readiness timeout after ${SERVER_READY_TIMEOUT_SEC}s"
  echo "ERROR: Qwen endpoint readiness timed out." >&2
  tail -n 120 "${SERVER_LOG}" >&2 || true
  exit 5
fi

python -m veriagent.scripts.data_agent.run_openpai_qwen_smoke \
  --tasks-file "${TASKS_FILE}" \
  --database "${DATABASE}" \
  --output-root "${OUTPUT_ROOT}"

python -m veriagent.scripts.data_agent.score_qwen_smoke \
  --results-root "${OUTPUT_ROOT}" \
  --gold-file "${PROJECT_ROOT}/veriagent/datasets/olistbr/tasks/v0/gold_v0.jsonl"

echo "OpenPAI Qwen3-8B smoke completed: ${OUTPUT_ROOT}"
