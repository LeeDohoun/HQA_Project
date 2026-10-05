#!/usr/bin/env bash
set -euo pipefail

execute=false
arguments=()
for argument in "$@"; do
    case "$argument" in
        --execute) execute=true ;;
        -h|--help)
            printf '%s\n' 'Usage: pull_collector_data.sh [--execute] user@host [ssh-key-path]'
            exit 0 ;;
        --*) printf 'Unknown option: %s\n' "$argument" >&2; exit 2 ;;
        *) arguments+=("$argument") ;;
    esac
done
if [[ "${#arguments[@]}" -lt 1 || "${#arguments[@]}" -gt 2 ]]; then
    printf '%s\n' 'Usage: pull_collector_data.sh [--execute] user@host [ssh-key-path]' >&2
    exit 2
fi
host=${arguments[0]}
if [[ ! "$host" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$ ]]; then
    printf '%s\n' 'Use user@host with an IPv4 address or DNS name.' >&2
    exit 2
fi
ssh_command='ssh -o BatchMode=yes'
if [[ "${#arguments[@]}" -eq 2 ]]; then
    ssh_key=$(realpath -- "${arguments[1]}")
    if [[ ! -f "$ssh_key" || ! -r "$ssh_key" ]]; then
        printf '%s\n' 'SSH key file must exist and be readable.' >&2
        exit 2
    fi
    quoted_key=${ssh_key//\"/\"\"}
    ssh_command+=" -o IdentitiesOnly=yes -i \"$quoted_key\""
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/../.." && pwd)
cd -- "$project_root"
data_dir="$project_root/data"
rsync_options=(-az --itemize-changes --rsync-path='sudo -n rsync' -e "$ssh_command")
if [[ "$execute" == false ]]; then
    rsync_options+=(-n)
    printf '%s\n' 'Dry run: no data is copied. Add --execute to transfer.'
else
    mkdir -p -- "$data_dir"
fi
rsync "${rsync_options[@]}" \
    "$host:/var/lib/hqa/data/disclosures" "$host:/var/lib/hqa/data/market" \
    "$host:/var/lib/hqa/data/ops" "$host:/var/lib/hqa/data/forward" \
    "$host:/var/lib/hqa/data/fundamentals" "$host:/var/lib/hqa/data/reference" "$data_dir/"
