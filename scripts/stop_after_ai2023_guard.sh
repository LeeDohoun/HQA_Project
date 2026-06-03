#!/usr/bin/env zsh
set -u

cd /Users/leedohoun/Desktop/HQA_Project || exit 1

LOG="experiment_results/backtesting/agent_architecture_validation/batch_logs/ai2023_stop_guard.log"
STATE="experiment_results/backtesting/agent_architecture_validation/uncontaminated_4agent_runs_ai2023/supervisor-state.json"
QUEUE_SCREEN="hqa_ai2023_semicon2024"
SEMICON_PATTERN="uncontaminated_4agent_runs_[s]emiconductor2024"
POLL_SECONDS="${POLL_SECONDS:-10}"

mkdir -p "$(dirname "$LOG")"

log_line() {
  print -r -- "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
}

read_returncode() {
  .venv/bin/python - "$STATE" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
try:
    payload = json.loads(path.read_text())
except Exception:
    print("unknown")
else:
    returncode = payload.get("returncode")
    print("None" if returncode is None else returncode)
PY
}

semiconductor_process_running() {
  .venv/bin/python <<'PY'
import subprocess
import sys

try:
    output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
except Exception:
    sys.exit(1)

target_root = "uncontaminated_4agent_runs_semiconductor2024"
target_scripts = (
    "supervise_uncontaminated_4agent_run.py",
    "run_uncontaminated_4agent_backtests.py",
    "proof_validation.py",
)
for line in output.splitlines():
    if target_root not in line:
        continue
    if any(script in line for script in target_scripts):
        sys.exit(0)
sys.exit(1)
PY
}

log_line "guard start: stop after AI 2023"

while true; do
  if semiconductor_process_running; then
    log_line "semiconductor process detected; stopping queue"
    pkill -f "$SEMICON_PATTERN" >/dev/null 2>&1 || true
    screen -S "$QUEUE_SCREEN" -X quit >/dev/null 2>&1 || true
    exit 0
  fi

  if [[ -f "$STATE" ]]; then
    rc="$(read_returncode)"
    if [[ "$rc" != "None" && "$rc" != "unknown" ]]; then
      log_line "AI 2023 finished with returncode=$rc; stopping queue"
      screen -S "$QUEUE_SCREEN" -X quit >/dev/null 2>&1 || true
      pkill -f "$SEMICON_PATTERN" >/dev/null 2>&1 || true
      exit 0
    fi
  fi

  sleep "$POLL_SECONDS"
done
