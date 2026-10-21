#!/usr/bin/env bash
# OP001 readiness step on the competition PAPER account (2026-10-27..30 only; no data sync needed).
set -euo pipefail
step=${1:-}
if [[ "$step" != prewarm && "$step" != buy && "$step" != flatten && "$step" != verify ]]; then
    printf '%s\n' 'Usage: readiness.sh prewarm|buy|flatten|verify' >&2
    exit 2
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/../.." && pwd)
python=${HQA_PYTHON:-"$project_root/venv/bin/python"}
cd -- "$project_root"
exec "$python" -m scripts.ops.strategy_runtime readiness --strategy op001_readiness --step "$step" --execute
