#!/usr/bin/env bash
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
    printf '%s\n' 'Run this installer with sudo.' >&2
    exit 1
fi

source /etc/os-release
if [[ "$ID" != ubuntu || ( "$VERSION_ID" != 22.04 && "$VERSION_ID" != 24.04 ) ]]; then
    printf '%s\n' 'Ubuntu 22.04 or 24.04 is required.' >&2
    exit 1
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/../.." && pwd)
apt-get update
apt-get install -y python3 python3-venv rsync ca-certificates tzdata

if ! getent group hqa >/dev/null; then
    groupadd --system hqa
fi
if ! id -u hqa >/dev/null 2>&1; then
    useradd --system --gid hqa --home-dir /var/lib/hqa --shell /usr/sbin/nologin hqa
fi
install -d -o root -g root -m 0755 /opt/hqa
install -d -o hqa -g hqa -m 0750 /var/lib/hqa /var/lib/hqa/data
install -d -o root -g hqa -m 0750 /etc/hqa

if [[ "$project_root" != /opt/hqa ]]; then
    rsync -aR --exclude='/data/' --exclude='venv/' --exclude='.env*' \
        --exclude='.agents/' --exclude='__pycache__/' \
        "$project_root/./src/" "$project_root/./scripts/data/" \
        "$project_root/./scripts/ops/" "$project_root/./scripts/__init__.py" \
        "$project_root/./deploy/collector/" "$project_root"/./requirements*.txt /opt/hqa/
fi
if [[ -e /opt/hqa/.env || -e /opt/hqa/.env-ai ]]; then
    printf '%s\n' 'Remove project .env files from /opt/hqa; only /etc/hqa/collector.env is allowed.' >&2
    exit 1
fi
if [[ ! -x /opt/hqa/venv/bin/python ]]; then
    python3 -m venv /opt/hqa/venv
fi
/opt/hqa/venv/bin/python -m pip install -r /opt/hqa/deploy/collector/requirements-collector.txt
chown -R root:root /opt/hqa
install -o root -g hqa -m 0640 /opt/hqa/deploy/collector/collector.env.example /etc/hqa/collector.env.example
for unit in /opt/hqa/deploy/collector/systemd/*; do
    install -o root -g root -m 0644 "$unit" /etc/systemd/system/
done
systemctl daemon-reload

env_check=10
if [[ -f /etc/hqa/collector.env ]]; then
    chown root:hqa /etc/hqa/collector.env
    chmod 0600 /etc/hqa/collector.env
    if /opt/hqa/venv/bin/python - /etc/hqa/collector.env <<'PY'
import sys
import re
from pathlib import Path
from dotenv import dotenv_values

lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
if any(line.strip() and not line.lstrip().startswith("#")
       and not re.fullmatch(r"(?:HQA_DATA_DIR|DART_API_KEY|KRX_OPEN_API_KEY|TZ)=[^\r\n]*", line.strip())
       for line in lines):
    print("Use one collector KEY=value assignment per line, without export or other settings.", file=sys.stderr)
    sys.exit(2)
values = dotenv_values(sys.argv[1], interpolate=False)
allowed = {"HQA_DATA_DIR", "DART_API_KEY", "KRX_OPEN_API_KEY", "TZ"}
if set(values) - allowed:
    print("collector.env may contain only HQA_DATA_DIR, DART_API_KEY, KRX_OPEN_API_KEY and TZ.", file=sys.stderr)
    sys.exit(2)
if not all(isinstance(values.get(name), str) and values[name].strip()
           for name in ("DART_API_KEY", "KRX_OPEN_API_KEY")):
    sys.exit(10)
if values.get("HQA_DATA_DIR") != "/var/lib/hqa/data":
    print("HQA_DATA_DIR must be /var/lib/hqa/data to match systemd write permissions.", file=sys.stderr)
    sys.exit(2)
sys.exit(0)
PY
    then
        env_check=0
    else
        env_check=$?
    fi
fi

if [[ "$env_check" -ne 0 ]]; then
    systemctl disable --now hqa-dart-poller.service hqa-krx-daily.timer hqa-collector-status.timer
    systemctl stop hqa-krx-daily.service hqa-collector-status.service
    cat <<'NEXT'
Collectors have not been started. On this server:
  sudo cp -n /etc/hqa/collector.env.example /etc/hqa/collector.env
  sudoedit /etc/hqa/collector.env
Set both collector keys, keep HQA_DATA_DIR=/var/lib/hqa/data, and include no KIS or LLM settings.
  sudo chown root:hqa /etc/hqa/collector.env
  sudo chmod 600 /etc/hqa/collector.env
  sudo bash /opt/hqa/deploy/collector/install.sh
NEXT
    if [[ "$env_check" -eq 10 ]]; then
        exit 0
    fi
    exit "$env_check"
fi

systemctl enable --now hqa-dart-poller.service hqa-krx-daily.timer hqa-collector-status.timer
printf '%s\n' 'Collector poller and timers enabled. Check systemctl status and /var/lib/hqa/data/ops/status.json.'
