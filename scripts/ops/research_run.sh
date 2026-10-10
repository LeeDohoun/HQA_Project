#!/usr/bin/env bash
set -euo pipefail

fail() { printf '%s\n' "$*" >&2; exit 1; }
checkout=/srv/hqa-research
[[ $(id -u) == 0 ]] || fail 'Run as root (sudo).'
if [[ "$#" -eq 0 || "$1" == --help || "$1" == -h ]]; then
    printf '%s\n' 'Usage: research_run.sh <hc002|hc003|hi001|r001|hf001|bb001|lh001|lh002> [args...]' \
        '       research_run.sh status' \
        'Runs the experiment with --execute. For LH use: lh001 screening --experiment LH002'
    exit 0
fi
units() {
    systemctl list-units --type=service --all --no-legend --plain --no-pager 'hqa-research-*' "$@" |
        awk '{print $1}'
}
if [[ "$1" == status ]]; then
    [[ "$#" -eq 1 ]] || fail 'status takes no arguments.'
    unit_list=$(units)
    while IFS= read -r unit; do
        [[ -n "$unit" ]] || continue
        systemctl show "$unit" --no-pager \
            --property=Id,ActiveState,SubState,Result,ExecMainCode,ExecMainStatus
    done <<< "$unit_list"
    exit 0
fi
module=$1
shift
case "$module" in hc002|hc003|hi001|r001|hf001|bb001|lh001|lh002) ;; *) fail 'Unknown experiment module.' ;; esac
[[ -d "$checkout/.git" && -x "$checkout/venv/bin/python" && ! -L "$checkout" ]] || fail 'Research checkout/venv is missing; run setup first.'
[[ -L "$checkout/data" && -d "$checkout/data" ]] || fail 'Research data volume link is missing.'
data_directory=$(readlink -f -- "$checkout/data")
[[ "$data_directory" == */hqa-research-data ]] || fail 'Unexpected research data link.'
volume=$(dirname -- "$data_directory")
mountpoint -q -- "$volume" || fail 'Research data volume is not mounted.'
device=$(findmnt -n -o MAJ:MIN --mountpoint "$volume")
root_device=$(findmnt -n -o MAJ:MIN --target /)
[[ -n "$device" && "$device" != "$root_device" ]] || fail 'Research data must be on a separate filesystem.'
[[ ! -e "$checkout/.env" && ! -e "$checkout/.env-ai" ]] || fail 'Private project environment files are forbidden in the research checkout.'
dirty=$(runuser -u hqares -- env -i HOME="$checkout" PATH=/usr/bin:/bin \
    git -C "$checkout" status --porcelain=v1 --untracked-files=all -- . \
    ':(exclude)data' \
    ':(glob,exclude)research/experiments/*/results/**' \
    ':(glob,exclude)research/experiments/*/diagnostics/**' \
    ':(exclude)research/experiments/registry.csv' \
    ':(exclude)research/experiments/holdout_ledger.jsonl')
[[ -z "$dirty" ]] || fail "Research checkout is dirty outside result/registry/ledger/data paths: $dirty"

# One run at a time: per-unit limits cannot reserve collectors' resources if
# several research units each take the entire budget.
exec 9>"$checkout/runs/.launch.lock"
flock -n 9 || fail 'Another research launch is in progress.'
[[ -z $(units --state=running,activating,deactivating) ]] || fail 'A research run is already in progress. Use status.'
total_bytes=$(free -b | awk '/^Mem:/ {print $2}')
cores=$(nproc --all)
[[ "$total_bytes" =~ ^[0-9]+$ && "$cores" =~ ^[0-9]+$ ]] || fail 'Could not measure machine memory/CPU.'
reserve_bytes=$((600 * 1024 * 1024))
(( total_bytes > reserve_bytes && cores >= 2 )) || fail 'Need more than 600 MiB RAM and at least two CPU cores.'
memory_max=$((total_bytes - reserve_bytes))
cpu_quota=$(((cores - 1) * 100))
unit="hqa-research-$module-$(date -u +%Y%m%dT%H%M%S)-$$"
log="$checkout/runs/$unit.log"
install -d -o hqares -g hqares -m 0750 "$checkout/runs"
install -o hqares -g hqares -m 0640 /dev/null "$log"
printf 'Unit: %s\nMemoryMax=%s bytes; CPUQuota=%s%% (600 MiB and one core reserved)\n' "$unit" "$memory_max" "$cpu_quota"
# Keep successful units exited/active and failed units loaded for status. No
# --wait, --pipe or --collect: systemd owns the process after SSH disconnects.
systemd-run --unit="$unit" --service-type=exec \
    -p User=hqares -p Group=hqares -p WorkingDirectory="$checkout" \
    -p RemainAfterExit=yes -p MemoryMax="$memory_max" -p CPUQuota="$cpu_quota%" \
    -p NoNewPrivileges=true -p Nice=10 -p UMask=0027 \
    -p StandardOutput="append:$log" -p StandardError="append:$log" \
    /usr/bin/env -i HOME="$checkout" USER=hqares LOGNAME=hqares \
    PATH="$checkout/venv/bin:$checkout/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    HQA_DATA_DIR="$checkout/data" TZ=Asia/Seoul PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    OPENAI_API_KEY=offline-disabled OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
    LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false \
    "$checkout/venv/bin/python" -m "backtesting.experiments.$module" --execute "$@"
printf 'Follow: sudo tail -f %q\nStatus: sudo bash %q status\n' "$log" "$checkout/scripts/ops/research_run.sh"
