#!/usr/bin/env bash
# ==========================================
# HQA — start backend, frontend, ai-server, and local Ollama when configured.
#
# Usage:  ./scripts/dev.sh
# Stop:   Ctrl-C (kills all managed children)
#
# Logs are prefixed [ollama] [ai] [be] [fe] and streamed to stdout.
# Per-service log files: logs/dev/{ollama,ai,be,fe}.log
# ==========================================

set -uo pipefail
set -m  # enable job control so each child gets its own process group

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ── Configurable paths / ports ────────────────────────────────────────────
AI_PORT="${AI_PORT:-8001}"
BE_PORT="${BE_PORT:-8000}"
FE_PORT="${FE_PORT:-3000}"

VENV="${HQA_VENV:-$ROOT/venv}"
LOG_DIR="$ROOT/logs/dev"
mkdir -p "$LOG_DIR"

# Load the project .env so backend JVM properties and the AI process see the
# same local API keys when the stack is started through this script.
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

OLLAMA_LOCAL_BIN="$ROOT/.local/ollama-new/bin/ollama"
OLLAMA_MANAGED_HOST=""
OLLAMA_MANAGED_PORT=""
if [[ "${OLLAMA_BASE_URL:-}" =~ ^https?://(localhost|127\.0\.0\.1):([0-9]+)$ ]]; then
  OLLAMA_MANAGED_HOST="${BASH_REMATCH[1]}"
  OLLAMA_MANAGED_PORT="${BASH_REMATCH[2]}"
  [[ "$OLLAMA_MANAGED_HOST" == "localhost" ]] && OLLAMA_MANAGED_HOST="127.0.0.1"
fi

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
if [[ "${NEXT_PUBLIC_API_BASE+x}" == "x" ]]; then
  FRONTEND_API_BASE="$NEXT_PUBLIC_API_BASE"
else
  FRONTEND_API_BASE="http://localhost:$BE_PORT"
fi
export BACKEND_PROXY_TARGET="${BACKEND_PROXY_TARGET:-http://localhost:$BE_PORT}"

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

ensure_local_ollama() {
  [[ -n "$OLLAMA_MANAGED_PORT" ]] || return 0

  if lsof -iTCP:"$OLLAMA_MANAGED_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    info "ollama → ${OLLAMA_BASE_URL}      already running"
    return 0
  fi

  [[ -x "$OLLAMA_LOCAL_BIN" ]] || fail "OLLAMA_BASE_URL points to local port $OLLAMA_MANAGED_PORT but $OLLAMA_LOCAL_BIN is missing"

  info "ollama → ${OLLAMA_BASE_URL}      log: $LOG_DIR/ollama.log"
  launch ollama 34 "$LOG_DIR/ollama.log" \
    env OLLAMA_HOST="${OLLAMA_MANAGED_HOST}:${OLLAMA_MANAGED_PORT}" \
      OLLAMA_MODELS="${OLLAMA_MODELS:-$HOME/.ollama/models}" \
      "$OLLAMA_LOCAL_BIN" serve

  for _ in {1..30}; do
    if command -v curl >/dev/null && curl -fsS "$OLLAMA_BASE_URL/api/version" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  fail "ollama did not become healthy at $OLLAMA_BASE_URL"
}

ensure_local_ollama

info "starting dev stack (Ctrl-C to stop)"
info "  ai → http://localhost:$AI_PORT      log: $LOG_DIR/ai.log"
info "  be → http://localhost:$BE_PORT      log: $LOG_DIR/be.log"
info "  fe → http://localhost:$FE_PORT      log: $LOG_DIR/fe.log"
info "  fe api → ${FRONTEND_API_BASE}"
info "  fe proxy → ${BACKEND_PROXY_TARGET}"
[[ -n "${CORS_ORIGINS:-}" ]] && info "  cors → ${CORS_ORIGINS}"
[[ -n "${SESSION_COOKIE_SAME_SITE:-}" ]] && info "  session same-site → ${SESSION_COOKIE_SAME_SITE}"
[[ -n "${SESSION_COOKIE_SECURE:-}" ]] && info "  session secure → ${SESSION_COOKIE_SECURE}"
echo

# AI server (FastAPI / uvicorn from sibling venv)
PYTHONPATH="$ROOT" launch ai 36 "$LOG_DIR/ai.log" \
  "$VENV/bin/uvicorn" ai_server.app:app --host 127.0.0.1 --port "$AI_PORT"

# Backend (Spring Boot)
# spring-boot:run forks a JVM that does NOT inherit our shell's env vars,
# so pass everything Spring needs as -D system properties instead.
BE_JVM_ARGS="-DHQA_KIS_ENC_KEY=${HQA_KIS_ENC_KEY:-} \
-DDATABASE_URL=${DATABASE_URL} \
-DDATABASE_USERNAME=${DATABASE_USERNAME} \
-DDATABASE_PASSWORD=${DATABASE_PASSWORD} \
-DREDIS_HOST=${REDIS_HOST} \
-DREDIS_PORT=${REDIS_PORT} \
-DAI_SERVER_URL=${AI_SERVER_URL} \
-DCORS_ORIGINS=${CORS_ORIGINS:-http://localhost:$FE_PORT,http://localhost:8501} \
-DSESSION_COOKIE_SAME_SITE=${SESSION_COOKIE_SAME_SITE:-lax} \
-DSESSION_COOKIE_SECURE=${SESSION_COOKIE_SECURE:-false} \
-DENV=${ENV} \
-DKIS_APP_KEY=${KIS_APP_KEY:-} \
-DKIS_APP_SECRET=${KIS_APP_SECRET:-} \
-DKIS_ACCOUNT_NO=${KIS_ACCOUNT_NO:-}"
launch be 33 "$LOG_DIR/be.log" \
  bash -c 'cd "$1" && PORT="$2" mvn -B -q spring-boot:run -Dspring-boot.run.jvmArguments="$3"' _ "$ROOT/backend" "$BE_PORT" "$BE_JVM_ARGS"

# Frontend (Next.js dev)
launch fe 35 "$LOG_DIR/fe.log" \
  bash -c 'cd "$1" && PORT="$2" NEXT_PUBLIC_API_BASE="$3" BACKEND_PROXY_TARGET="$4" npm run dev' _ "$ROOT/frontend" "$FE_PORT" "$FRONTEND_API_BASE" "$BACKEND_PROXY_TARGET"

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
