#!/usr/bin/env bash
set -euo pipefail

execute=false
arguments=()
for argument in "$@"; do
    case "$argument" in
        --execute) execute=true ;;
        -h|--help)
            printf '%s\n' 'Usage: sync_research_data.sh [--execute] user@host [ssh-key-path]'
            exit 0 ;;
        --*) printf 'Unknown option: %s\n' "$argument" >&2; exit 2 ;;
        *) arguments+=("$argument") ;;
    esac
done
if [[ "${#arguments[@]}" -lt 1 || "${#arguments[@]}" -gt 2 ]]; then
    printf '%s\n' 'Usage: sync_research_data.sh [--execute] user@host [ssh-key-path]' >&2
    exit 2
fi
host=${arguments[0]}
[[ "$host" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$ ]] || { printf '%s\n' 'Use user@host with an IPv4 address or DNS name.' >&2; exit 2; }
ssh_command='ssh -o BatchMode=yes'
if [[ "${#arguments[@]}" -eq 2 ]]; then
    ssh_key=$(realpath -e -- "${arguments[1]}")
    [[ -f "$ssh_key" && -r "$ssh_key" ]] || { printf '%s\n' 'SSH key must be readable.' >&2; exit 2; }
    quoted_key=${ssh_key//\"/\"\"}
    ssh_command+=" -o IdentitiesOnly=yes -i \"$quoted_key\""
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/../.." && pwd)
data_dir="$project_root/data"
[[ -d "$data_dir/market/krx_daily" && ! -L "$data_dir" ]] || { printf '%s\n' 'PC data/market/krx_daily history is required; data must be a local directory.' >&2; exit 1; }
sources=()
for relative in market/krx_daily fundamentals reference disclosures/dart_full/list market_context \
    research/lh001 research/lh002 disclosures/dart_buyback/structured \
    disclosures/dart_buyback/documents disclosures/business_text; do
    if [[ -d "$data_dir/$relative" && ! -L "$data_dir/$relative" ]]; then
        sources+=("$data_dir/./$relative/")
    else
        printf 'Not present (not transferred): data/%s\n' "$relative"
    fi
done
options=(-azR --itemize-changes --ignore-existing --no-links --rsync-path='sudo -n -u hqares rsync' -e "$ssh_command")
if [[ "$execute" == false ]]; then
    options+=(-n)
    printf '%s\n' 'Dry run: no data is copied. Add --execute to transfer missing files.'
fi
rsync "${options[@]}" \
    --exclude='.env*' --exclude='*.lock' --exclude='*.key' --exclude='*.pem' \
    --exclude='.ssh/' --exclude='.codex/' --exclude='*token*' --exclude='*credential*' \
    --exclude='*secret*' --exclude='*account*' \
    "${sources[@]}" "$host:/srv/hqa-research/data/"
