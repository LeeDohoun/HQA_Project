#!/usr/bin/env bash
# Kill servers started by scripts/dev.sh (ai/be/fe).
# Targets whatever is LISTENing on the dev ports — works even if
# dev.sh exited uncleanly and orphaned its children.

set -uo pipefail

AI_PORT="${AI_PORT:-8001}"
BE_PORT="${BE_PORT:-8000}"
FE_PORT="${FE_PORT:-3000}"

info() { printf '\033[36m• %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }

kill_port() {
  local name="$1" port="$2"
  local pids
  pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -z "$pids" ]]; then
    info "$name (port $port): nothing listening"
    return
  fi
  info "$name (port $port): killing $(echo "$pids" | tr '\n' ' ')"
  # TERM the process group so wrappers (bash -c, npm, mvn) drop their children too.
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    local pgid
    pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
    if [[ -n "$pgid" ]]; then
      kill -TERM "-$pgid" 2>/dev/null || true
    else
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done <<< "$pids"

  sleep 2

  # Anything still listening on this port gets SIGKILL.
  pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    warn "$name (port $port): force-killing $(echo "$pids" | tr '\n' ' ')"
    while read -r pid; do
      [[ -n "$pid" ]] || continue
      local pgid
      pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
      if [[ -n "$pgid" ]]; then
        kill -KILL "-$pgid" 2>/dev/null || true
      else
        kill -KILL "$pid" 2>/dev/null || true
      fi
    done <<< "$pids"
  fi
}

kill_port ai "$AI_PORT"
kill_port be "$BE_PORT"
kill_port fe "$FE_PORT"

# Also clean up any lingering dev.sh process itself.
dev_pids=$(pgrep -f "scripts/dev.sh" 2>/dev/null || true)
if [[ -n "$dev_pids" ]]; then
  info "dev.sh wrapper: killing $(echo "$dev_pids" | tr '\n' ' ')"
  echo "$dev_pids" | xargs -n1 kill -TERM 2>/dev/null || true
fi

info "done."
