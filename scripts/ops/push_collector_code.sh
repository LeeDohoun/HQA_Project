#!/usr/bin/env bash
set -euo pipefail

execute=false
arguments=()
for argument in "$@"; do
    case "$argument" in
        --execute) execute=true ;;
        -h|--help)
            printf '%s\n' 'Usage: push_collector_code.sh [--execute] user@host [ssh-key-path]'
            exit 0 ;;
        --*) printf 'Unknown option: %s\n' "$argument" >&2; exit 2 ;;
        *) arguments+=("$argument") ;;
    esac
done
if [[ "${#arguments[@]}" -lt 1 || "${#arguments[@]}" -gt 2 ]]; then
    printf '%s\n' 'Usage: push_collector_code.sh [--execute] user@host [ssh-key-path]' >&2
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
    # rsync parses doubled quotes inside quoted remote-shell arguments.
    quoted_key=${ssh_key//\"/\"\"}
    ssh_command+=" -o IdentitiesOnly=yes -i \"$quoted_key\""
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/../.." && pwd)
rsync_options=(-azR --itemize-changes --rsync-path='sudo -n rsync' -e "$ssh_command")
if [[ "$execute" == false ]]; then
    rsync_options+=(-n)
    printf '%s\n' 'Dry run: no code is copied. Add --execute to transfer.'
fi
# Anchor /data/ so scripts/data/ is included. Never transfer project env files.
rsync "${rsync_options[@]}" \
    --exclude='/data/' --exclude='venv/' --exclude='.env*' --exclude='.agents/' \
    --exclude='.git/' --exclude='frontend/' --exclude='backend/' --exclude='research/' \
    --exclude='tests/' --exclude='__pycache__/' \
    "$project_root/./src/" "$project_root/./scripts/data/" \
    "$project_root/./scripts/ops/" "$project_root/./scripts/__init__.py" \
    "$project_root/./deploy/collector/" "$project_root"/./requirements*.txt "$host:/opt/hqa/"
