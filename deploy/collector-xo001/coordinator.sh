#!/bin/bash
set -euo pipefail
# systemd, as root, loads the EnvironmentFile. This script never opens it.
test "$(id -u)" = 0
registry_properties=()
if [[ "${1:-}" == evaluate ]]; then
  registry_root=""
  args=("$@")
  for ((index=1; index<${#args[@]}; index++)); do
    if [[ "${args[index]}" == --registry-root ]]; then
      registry_root="$(realpath -e -- "${args[index+1]:?--registry-root requires a path}")"
    fi
  done
  # The authoritative local Git checkout must remain visible under ProtectHome.
  [[ -n "$registry_root" && -e "$registry_root/.git" && -d "$registry_root/research/experiments" ]]
  case "$registry_root/" in /home/*|/root/*|/run/user/*) exit 2 ;; esac
  registry_properties=("--property=ReadOnlyPaths=$registry_root"
    "--property=ReadWritePaths=$registry_root/research/experiments")
fi
exec systemd-run --wait --pipe --collect --service-type=exec \
  "${registry_properties[@]}" \
  --unit="hqa-xo001-coordinator-$(date +%s)-$$" \
  --property=User=hqa --property=Group=hqa \
  --property=WorkingDirectory=/opt/hqa-xo001 \
  --property=EnvironmentFile=/etc/hqa/xo001.env \
  --property=UMask=0077 --property=NoNewPrivileges=yes \
  --property=ProtectSystem=strict --property=ProtectHome=yes \
  --property=ReadWritePaths=/var/lib/hqa-xo001 \
  --property=ReadOnlyPaths=/var/lib/hqa-xo001/activation-control \
  --property=InaccessiblePaths=-/opt/hqa \
  --property=MemoryMax=384M --property=MemorySwapMax=0 \
  --property=Nice=19 --property=CPUWeight=1 --property=IOWeight=1 \
  --setenv=PYTHONDONTWRITEBYTECODE=1 --setenv=PYTHON_DOTENV_DISABLED=1 \
  --setenv=OPENBLAS_NUM_THREADS=1 --setenv=OMP_NUM_THREADS=1 \
  /opt/hqa-xo001/venv/bin/python -B -m scripts.xo001 "$@"
