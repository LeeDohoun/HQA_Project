#!/usr/bin/env bash
set -euo pipefail

execute=false
if [[ "${1:-}" == --execute ]]; then execute=true; shift; fi
if [[ "$#" -lt 2 || "$#" -gt 3 ]]; then
    printf '%s\n' 'Usage: push_fw001_supplement.sh [--execute] PC_DATA_DIR user@host [ssh-key-path]' >&2
    exit 2
fi
source_dir=$(realpath -- "$1")/disclosures/dart_full/list_supplement
host=$2
if [[ ! "$host" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$ || ! -f "$source_dir/_state.json" ]]; then
    printf '%s\n' 'Require user@host and the completed supplement directory with _state.json.' >&2
    exit 2
fi
ssh_command='ssh -o BatchMode=yes'
if [[ "$#" -eq 3 ]]; then
    key=$(realpath -- "$3")
    if [[ ! -f "$key" || ! -r "$key" ]]; then exit 2; fi
    quoted_key=${key//\"/\"\"}
    ssh_command+=" -o IdentitiesOnly=yes -i \"$quoted_key\""
fi
options=(-az --itemize-changes --rsync-path='sudo -n rsync' -e "$ssh_command" --chown=hqa:hqa)
if [[ "$execute" == false ]]; then options+=(-n); fi
rsync "${options[@]}" --exclude='*.lock' --exclude='.backfill.lock' \
    "$source_dir/" "$host:/var/lib/hqa/data/disclosures/dart_full/list_supplement/"
