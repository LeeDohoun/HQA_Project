#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf '%s\n' 'Usage: setup_research.sh [--execute] <mounted-volume>'
}
fail() { printf '%s\n' "$*" >&2; exit 1; }
execute=false
volume=
for argument in "$@"; do
    case "$argument" in
        --execute) execute=true ;;
        -h|--help) usage; exit 0 ;;
        --*) usage >&2; exit 2 ;;
        *) [[ -z "$volume" ]] || { usage >&2; exit 2; }; volume=$argument ;;
    esac
done
[[ -n "$volume" ]] || { usage >&2; exit 2; }
[[ $(id -u) == 0 ]] || fail 'Run as root (sudo).'
[[ "$volume" == /* && -d "$volume" ]] || fail 'Volume must be an existing absolute directory.'
volume=$(realpath -e -- "$volume")
checkout=/srv/hqa-research
repository=https://github.com/LeeDohoun/HQA_Project.git
branch=claude/strategy-research-system
case "$volume" in
    /opt|/opt/hqa|/opt/hqa/*|/var|/var/lib|/var/lib/hqa|/var/lib/hqa/*|/etc|/etc/hqa|/etc/hqa/*|"$checkout"|"$checkout"/*)
        fail 'Use a separate research volume outside collector and checkout paths.' ;;
esac
mountpoint -q -- "$volume" || fail "Not a separate mount: $volume"
device=$(findmnt -n -o MAJ:MIN --mountpoint "$volume")
root_device=$(findmnt -n -o MAJ:MIN --target /)
[[ -n "$device" && "$device" != "$root_device" ]] || fail 'Research volume must use a filesystem separate from /.'
free_bytes=$(df -B1 --output=avail -- "$volume" | awk 'NR == 2 {print $1}')
[[ "$free_bytes" =~ ^[0-9]+$ ]] || fail 'Could not measure volume free space.'
(( free_bytes >= 20 * 1000 * 1000 * 1000 )) || fail 'Research volume needs at least 20 GB free.'
data_directory="$volume/hqa-research-data"
[[ ! -L "$checkout" && ! -L "$data_directory" ]] || fail 'Checkout and volume data directory must not be symlinks.'

# Never change an existing account into a privileged or login-enabled account.
if id -u hqares >/dev/null 2>&1; then
    IFS=: read -r name password uid gid gecos account_home account_shell < <(getent passwd hqares)
    [[ "$uid" != 0 && "$account_home" == "$checkout" && "$account_shell" == /usr/sbin/nologin ]] ||
        fail 'Existing hqares account has an unexpected home, UID or shell.'
    [[ $(id -Gn hqares) == hqares ]] || fail 'hqares must belong only to its own group (no sudo).'
fi
git_read() {
    runuser -u hqares -- env -i HOME="$checkout" PATH=/usr/bin:/bin GIT_TERMINAL_PROMPT=0 \
        git -C "$checkout" "$@"
}
if [[ -d "$checkout/.git" && ! -L "$checkout/.git" ]]; then
    [[ -z $(git_read status --porcelain=v1 --untracked-files=all) ]] || fail 'Research checkout is dirty; refusing to update.'
    [[ $(git_read config --get remote.origin.url) == "$repository" ]] || fail 'Unexpected research origin.'
    [[ $(git_read branch --show-current) == "$branch" ]] || fail 'Unexpected research branch.'
elif [[ -e "$checkout" ]]; then
    [[ -d "$checkout" && -z $(ls -A -- "$checkout") ]] || fail 'Checkout exists but is not an empty directory or a Git checkout.'
fi
if [[ -L "$checkout/data" ]]; then
    [[ $(readlink -f -- "$checkout/data") == "$data_directory" ]] || fail 'Existing data link points to another volume.'
elif [[ -e "$checkout/data" ]]; then
    fail 'Existing checkout data is not the research volume link; refusing to replace it.'
fi
[[ ! -e "$checkout/.env" && ! -e "$checkout/.env-ai" ]] || fail 'Remove private project environment files from the research checkout.'

run() {
    printf '  '; printf '%q ' "$@"; printf '\n'
    if [[ "$execute" == true ]]; then "$@"; fi
}
as_research() {
    run runuser -u hqares -- env -i HOME="$checkout" USER=hqares LOGNAME=hqares \
        PATH=/usr/bin:/bin GIT_TERMINAL_PROMPT=0 "$@"
}
if [[ "$execute" == false ]]; then
    printf '%s\n' 'Dry run: only local preflight checks; no clone, install or changes. Add --execute.'
fi
run apt-get update
run apt-get install -y git python3 python3-venv rsync ca-certificates
if ! getent group hqares >/dev/null; then run groupadd --system hqares; fi
if ! id -u hqares >/dev/null 2>&1; then
    run useradd --system --gid hqares --home-dir "$checkout" --shell /usr/sbin/nologin hqares
fi
run install -d -o hqares -g hqares -m 0750 "$checkout" "$data_directory"
if [[ ! -d "$checkout/.git" ]]; then
    # Full commit history is required. Exclude only tracked legacy data before
    # checkout so the volume symlink does not appear as tracked-file deletions.
    as_research git clone --single-branch --branch "$branch" --no-checkout "$repository" "$checkout"
    as_research git -C "$checkout" sparse-checkout set --no-cone '/*' '!/data/'
    as_research git -C "$checkout" checkout "$branch"
else
    as_research git -C "$checkout" fetch origin
    as_research git -C "$checkout" merge --ff-only "origin/$branch"
fi
as_research git -C "$checkout" remote set-url --push origin DISABLED
if [[ "$execute" == true ]]; then
    # Local runtime state belongs to the account home, never to the repository.
    for pattern in /data /runs/ /.codex/ /.npm/ /.cache/; do
        if ! grep -Fxq -- "$pattern" "$checkout/.git/info/exclude"; then
            printf '%s\n' "$pattern" >> "$checkout/.git/info/exclude"
        fi
    done
    cat > "$checkout/.git/hooks/pre-push" <<'HOOK'
#!/usr/bin/env bash
printf '%s\n' 'Research server pushes are forbidden.' >&2
exit 1
HOOK
    chown hqares:hqares "$checkout/.git/hooks/pre-push" "$checkout/.git/info/exclude"
    chmod 0750 "$checkout/.git/hooks/pre-push"
fi
if [[ ! -L "$checkout/data" ]]; then as_research ln -s -- "$data_directory" "$checkout/data"; fi
run install -d -o hqares -g hqares -m 0750 "$checkout/runs" "$data_directory/research"
if [[ ! -x "$checkout/venv/bin/python" ]]; then as_research python3 -m venv "$checkout/venv"; fi
as_research "$checkout/venv/bin/python" -m pip install -r "$checkout/deploy/research/requirements-research.txt"
as_research env OPENAI_API_KEY=offline-disabled OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
    LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false PYTHONDONTWRITEBYTECODE=1 \
    "$checkout/venv/bin/python" -c \
    'import importlib; [importlib.import_module("backtesting.experiments." + name) for name in ("hc002", "hc003", "hi001", "r001", "hf001", "bb001", "lh001.__main__", "lh002.__main__")]'
cat <<'NEXT'
Next steps (operator):
  On the PC, preview then execute scripts/ops/sync_research_data.sh user@host [ssh-key].
  It transfers KRX daily history, fundamentals, reference, DART lists, market_context
  and data/research/lh001, lh002 caches to /srv/hqa-research/data.
  Existing buyback archives and business_text inputs are included for BB001/LH text.
  Check existing registry and holdout ledger on the PC before any server experiment.
  See docs/research-server.md for moving their history without losing local rows.

Optional, only for LH001/LH002: install Node.js/npm first as the administrator.
Then open an interactive shell as hqares (the account itself has no sudo):
  sudo -u hqares -H /bin/bash
  npm install --global --prefix "$HOME/.local" @openai/codex@0.160.0
  export PATH="$HOME/.local/bin:$PATH"
  codex --version
  codex login --device-auth
  exit
The fixed runner requires codex-cli 0.160.0. Login is interactive; never copy credentials.
Run experiments with sudo bash /srv/hqa-research/scripts/ops/research_run.sh hc002
NEXT
