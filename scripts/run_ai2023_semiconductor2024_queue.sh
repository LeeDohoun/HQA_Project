#!/usr/bin/env zsh
set -euo pipefail

cd /Users/leedohoun/Desktop/HQA_Project

LOG_DIR="experiment_results/backtesting/agent_architecture_validation/batch_logs"
mkdir -p "$LOG_DIR"
QUEUE_LOG="$LOG_DIR/ai2023_semicon2024.log"
RUN_SEMICONDUCTOR_AFTER_AI="${RUN_SEMICONDUCTOR_AFTER_AI:-0}"

echo "$(date '+%Y-%m-%d %H:%M:%S') START AI 2023" >> "$QUEUE_LOG"
.venv/bin/python scripts/supervise_uncontaminated_4agent_run.py \
  --output-root experiment_results/backtesting/agent_architecture_validation/uncontaminated_4agent_runs_ai2023 \
  --theme AI \
  --periods validation_2023:20230101:20231231:validation \
  --stale-seconds 1800 \
  --poll-seconds 60 \
  --max-restarts 30 >> "$QUEUE_LOG" 2>&1

if [[ "$RUN_SEMICONDUCTOR_AFTER_AI" != "1" ]]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') STOP AFTER AI 2023" >> "$QUEUE_LOG"
  exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') START SEMICONDUCTOR 2024" >> "$QUEUE_LOG"
.venv/bin/python scripts/supervise_uncontaminated_4agent_run.py \
  --output-root experiment_results/backtesting/agent_architecture_validation/uncontaminated_4agent_runs_semiconductor2024 \
  --theme 반도체 \
  --theme-key 반도체 \
  --periods validation_2024:20240101:20241231:validation \
  --stale-seconds 1800 \
  --poll-seconds 60 \
  --max-restarts 40 >> "$QUEUE_LOG" 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S') ALL DONE" >> "$QUEUE_LOG"
