#!/usr/bin/env bash
# Install the strategy-runtime systemd units. Prints the plan by default; copies only with
# --execute. Never enables or starts anything: the operator runs the printed commands.
set -euo pipefail

execute=false
target=${HQA_SYSTEMD_DIR:-/etc/systemd/system}
for argument in "$@"; do
    case "$argument" in
        --execute) execute=true ;;
        -h|--help)
            printf '%s\n' 'Usage: install.sh [--execute]  (HQA_SYSTEMD_DIR overrides /etc/systemd/system)'
            exit 0 ;;
        *) printf 'Unknown option: %s\n' "$argument" >&2; exit 2 ;;
    esac
done

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
units=("$script_dir"/systemd/*)
printf 'Target directory: %s\n' "$target"
for unit in "${units[@]}"; do
    printf 'install -m 0644 %s %s/\n' "$unit" "$target"
done
printf 'Environment file: /etc/hqa/strategy-runtime.env (template %s)\n' "$script_dir/strategy-runtime.env.example"

if [[ "$execute" == false ]]; then
    printf '%s\n' 'Dry run: nothing was copied. Re-run with --execute (as root) to copy the units.'
    exit 0
fi
if [[ "$target" == /etc/systemd/system && "$EUID" -ne 0 ]]; then
    printf '%s\n' 'Copying into /etc/systemd/system needs root.' >&2
    exit 1
fi
install -d "$target"
for unit in "${units[@]}"; do
    install -m 0644 "$unit" "$target/"
done
printf '%s\n' 'Copied. Not enabled. To enable after review:' \
    '  sudo systemctl daemon-reload' \
    '  sudo systemctl enable --now hqa-strategy-runtime-morning.timer hqa-strategy-runtime-evening.timer'
