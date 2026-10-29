#!/usr/bin/env bash
# One-screen status of the stack started by `HQA_PAPER=1 ./scripts/dev.sh`.
# Usage: ./scripts/status.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a; . ./.env; set +a
PY="$ROOT/venv/bin/python"
LOG="$ROOT/logs/dev"
ok() { printf '\033[32m✓\033[0m %s\n' "$*"; }
bad() { printf '\033[31m✗ %s\033[0m\n' "$*"; }
tail_clean() { [[ -f "$1" ]] && sed -E 's/\x1b\[[0-9;]*m//g' "$1" | grep -vE '^\s*$' | tail -"$2" | cut -c1-220; }

echo "== $(date '+%Y-%m-%d %a %H:%M KST')"
for svc in "frontend 3000 /" "backend 8000 /actuator/health" "ai 8001 /health"; do
  set -- $svc
  [[ "$(curl -s -o /dev/null -w '%{http_code}' "localhost:$2$3")" == 200 ]] && ok "$1 :$2" || bad "$1 :$2 not answering"
done
for w in "수집 루프:scripts.data.loop" "주문 모니터:src.runner.signal_monitor" "분석 스케줄러:src.runner.analysis_scheduler"; do
  pgrep -f "${w#*:}" >/dev/null && ok "${w%%:*}" || bad "${w%%:*} not running"
done
pmset -g | grep -q "SleepDisabled.*1" && ok "잠자기 꺼짐 (덮개 닫아도 동작)" || echo "! 잠자기 켜짐: 덮개를 닫으면 멈춤"

echo "== 모의투자 계좌"
curl -s -X POST -H "X-HQA-Internal-Token: $HQA_INTERNAL_TOKEN" -H "Content-Type: application/json" \
  -d '{"userIds":["mangotree0867test"]}' localhost:8000/api/v1/internal/trading/account-snapshots | "$PY" -c "
import json, sys
s = json.load(sys.stdin)['snapshots'][0]
print(f\"  연결={s['success']} 평가금액={s['equity']:,} 주문가능={s['orderableCash']:,} 일손익={s['dailyPnlPct']}% 진입가능={s['entryEligible']}\")
for h in s.get('holdings') or []:
    print('  보유', h.get('stockName') or h.get('stockCode'), h.get('quantity'), '주')" 2>/dev/null || bad "계좌 조회 실패"
unknown=$("$PY" -m scripts.paper_orders unknown 2>/dev/null | "$PY" -c 'import json,sys; print(len(json.load(sys.stdin)["executions"]))' 2>/dev/null)
[[ "$unknown" == 0 ]] && ok "결과 불명 주문 없음" || bad "결과 불명 주문: ${unknown:-조회 실패} (docs/paper-preflight-checklist.md 7장)"

echo "== 분석 스케줄러 (15분마다, 장중만)"; tail_clean "$LOG/sched.log" 3 || echo "  아직 기록 없음"
echo "== 주문 모니터"; tail_clean "$LOG/mon.log" 2
echo "== 수집 루프"; tail_clean "$LOG/loop.log" 2
