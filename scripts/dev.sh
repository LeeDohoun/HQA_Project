#!/usr/bin/env bash
# ==========================================
# HQA — start backend, frontend, ai-server together.
#
# Usage:  ./scripts/dev.sh
# Stop:   Ctrl-C (kills all three children)
#
# Logs are prefixed [ai] [be] [fe] and streamed to stdout.
# Per-service log files: logs/dev/{ai,be,fe}.log
# ==========================================

set -uo pipefail
set -m  # enable job control so each child gets its own process group

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ── Configurable paths / ports ────────────────────────────────────────────
AI_PORT="${AI_PORT:-8001}"
BE_PORT="${BE_PORT:-8000}"
FE_PORT="${FE_PORT:-3000}"

VENV="${HQA_VENV:-$HOME/Desktop/school/capstone/HQA_project_2/HQA_Project/venv}"
LOG_DIR="$ROOT/logs/dev"
mkdir -p "$LOG_DIR"

# Backend env (Spring needs JDBC URL, not asyncpg). Pulled from .env-be when
# present, with sane fallbacks for local Postgres/Redis.
if [[ -f "$ROOT/.env-be" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env-be"
  set +a
fi
export DATABASE_URL="${DATABASE_URL_JDBC:-jdbc:postgresql://localhost:5432/hqa}"
export DATABASE_USERNAME="${DATABASE_USERNAME:-$USER}"
export DATABASE_PASSWORD="${DATABASE_PASSWORD:-}"
export REDIS_HOST="${REDIS_HOST:-localhost}"
export REDIS_PORT="${REDIS_PORT:-6379}"
export AI_SERVER_URL="${AI_SERVER_URL:-http://localhost:$AI_PORT}"
export ENV="${ENV:-local}"

# ── Pre-flight ────────────────────────────────────────────────────────────
fail() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
info() { printf '\033[36m• %s\033[0m\n' "$*"; }

[[ -x "$VENV/bin/uvicorn" ]] || fail "venv missing or no uvicorn at $VENV (set HQA_VENV=...)"
command -v mvn >/dev/null || fail "mvn not on PATH"
command -v node >/dev/null || fail "node not on PATH"
[[ -d "$ROOT/frontend/node_modules" ]] || (cd "$ROOT/frontend" && info "npm install (first run)" && npm install --no-audit --no-fund)

for port in "$AI_PORT" "$BE_PORT" "$FE_PORT"; do
  if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "port $port already in use — kill it or change AI_PORT/BE_PORT/FE_PORT"
  fi
done

# ── Process tracking + cleanup ────────────────────────────────────────────
PIDS=()
SHUTDOWN_DONE=0
shutdown() {
  [[ "$SHUTDOWN_DONE" == 1 ]] && return
  SHUTDOWN_DONE=1
  echo
  info "shutting down…"
  # Signal each child's *process group* so wrappers (bash -c, npm) take
  # their actual servers (mvn, next, uvicorn) down with them. With `set -m`
  # each backgrounded `(...)` becomes its own pgid == pid, so -PID works.
  for pid in "${PIDS[@]}"; do
    [[ -n "$pid" ]] && kill -TERM "-$pid" 2>/dev/null || true
  done
  sleep 2
  for pid in "${PIDS[@]}"; do
    [[ -n "$pid" ]] && kill -KILL "-$pid" 2>/dev/null || true
  done
  wait 2>/dev/null
  info "all stopped."
}
trap shutdown INT TERM EXIT

# Stream a child's stdout+stderr through `sed` to add a colored prefix.
# Tee to a per-service log so failures can be inspected after the fact.
# The subshell + `set -m` ensures the whole pipeline is one process group.
launch() {
  local tag="$1" color="$2" logfile="$3"; shift 3
  local prefix
  prefix=$(printf '\033[%sm[%s]\033[0m' "$color" "$tag")
  ( "$@" 2>&1 | tee "$logfile" | sed -u "s|^|$prefix |" ) &
  PIDS+=($!)
}

info "starting all 3 servers (Ctrl-C to stop)"
info "  ai → http://localhost:$AI_PORT      log: $LOG_DIR/ai.log"
info "  be → http://localhost:$BE_PORT      log: $LOG_DIR/be.log"
info "  fe → http://localhost:$FE_PORT      log: $LOG_DIR/fe.log"
echo

# AI server (FastAPI / uvicorn from sibling venv)
PYTHONPATH="$ROOT" launch ai 36 "$LOG_DIR/ai.log" \
  "$VENV/bin/uvicorn" ai_server.app:app --host 127.0.0.1 --port "$AI_PORT"

# Backend (Spring Boot)
launch be 33 "$LOG_DIR/be.log" \
  bash -c "cd '$ROOT/backend' && PORT=$BE_PORT mvn -B -q spring-boot:run"

# Frontend (Next.js dev)
launch fe 35 "$LOG_DIR/fe.log" \
  bash -c "cd '$ROOT/frontend' && PORT=$FE_PORT npm run dev"

# Wait until any child exits, then trigger cleanup. Portable across
# macOS bash 3.2 (no `wait -n`).
while :; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      info "child $pid exited — tearing down"
      exit 1
    fi
  done
  sleep 1
done
