#!/usr/bin/env bash
# Strategy run (morning|evening|submit|inputs): optional collector pull (rsync -a keeps the directory mtimes
# that FW001 completion proofs depend on), then the daily run. A failed pull stops here.
# Morning needs the pull too: the trade date's FW001 decision is sealed on the collector before 08:59.
set -euo pipefail

when=${1:-}
if [[ "$when" != morning && "$when" != evening && "$when" != submit && "$when" != inputs ]]; then
    printf '%s\n' 'Usage: run.sh morning|evening|submit|inputs' >&2
    exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/../.." && pwd)
python=${HQA_PYTHON:-"$project_root/venv/bin/python"}
cd -- "$project_root"

if [[ -n "${HQA_COLLECTOR_HOST:-}" && -n "${HQA_COLLECTOR_LOCAL_DIR:-}" ]]; then
    printf '%s\n' 'Set only one of HQA_COLLECTOR_HOST and HQA_COLLECTOR_LOCAL_DIR' >&2
    exit 1
fi
if [[ -n "${HQA_COLLECTOR_LOCAL_DIR:-}" ]]; then
    # Collector on the same host (lab hqa-server): copy its frozen data tree read-only into this
    # checkout, with the same folders and rsync -a semantics as pull_collector_data.sh, so file and
    # directory mtimes (FW001 completion proofs) are preserved. The collector tree is never written.
    if [[ -n "${HQA_DATA_DIR:-}" && "$(realpath -m -- "$HQA_DATA_DIR")" != "$(realpath -m -- "$project_root/data")" ]]; then
        printf 'HQA_DATA_DIR (%s) must be %s when HQA_COLLECTOR_LOCAL_DIR is set\n' "$HQA_DATA_DIR" "$project_root/data" >&2
        exit 1
    fi
    source_dir=${HQA_COLLECTOR_LOCAL_DIR%/}
    mkdir -p -- "$project_root/data"
    if ! rsync -a --itemize-changes \
            "$source_dir/disclosures" "$source_dir/market" "$source_dir/ops" "$source_dir/forward" \
            "$source_dir/fundamentals" "$source_dir/reference" "$project_root/data/"; then
        printf '%s\n' "local collector data copy failed; the $when run did not start" >&2
        exit 1
    fi
elif [[ -n "${HQA_COLLECTOR_HOST:-}" ]]; then
    # pull_collector_data.sh always copies into <project>/data; the run must read the same tree.
    if [[ -n "${HQA_DATA_DIR:-}" && "$(realpath -m -- "$HQA_DATA_DIR")" != "$(realpath -m -- "$project_root/data")" ]]; then
        printf 'HQA_DATA_DIR (%s) must be %s when HQA_COLLECTOR_HOST is set\n' "$HQA_DATA_DIR" "$project_root/data" >&2
        exit 1
    fi
    pull=("$project_root/scripts/ops/pull_collector_data.sh" --execute "$HQA_COLLECTOR_HOST")
    if [[ -n "${HQA_COLLECTOR_SSH_KEY:-}" ]]; then
        pull+=("$HQA_COLLECTOR_SSH_KEY")
    fi
    if ! bash "${pull[@]}"; then
        printf '%s\n' "collector data pull failed; the $when run did not start" >&2
        exit 1
    fi
else
    printf '%s\n' 'HQA_COLLECTOR_HOST is not set; using the local data directory as is' >&2
fi

case "$when" in
    submit) exec "$python" -m scripts.ops.strategy_runtime submit-due --execute ;;
    inputs) exec "$python" -m scripts.ops.strategy_runtime submit-due --inputs-only --execute ;;
    *) exec "$python" -m scripts.ops.strategy_runtime daily --when "$when" --execute ;;
esac
