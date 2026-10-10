#!/usr/bin/env bash
# Runs the fear/greed A/B grid over one theme and prints a comparison table.
#
# Usage: scripts/compare_fear_greed.sh <theme> <theme_key> <from_ymd> <to_ymd> [data_dir]
# Example: scripts/compare_fear_greed.sh AI ai 20250101 20251231
#
# Every run uses identical selection; only position sizing differs, so a
# difference in return is attributable to the sizing feature alone.
set -euo pipefail

THEME="${1:?theme name required}"
THEME_KEY="${2:?theme key required}"
FROM="${3:?from date YYYYMMDD required}"
TO="${4:?to date YYYYMMDD required}"
DATA_DIR="${5:-data}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
OUT="$DATA_DIR/backtest_results/fear_greed/$THEME_KEY"
mkdir -p "$OUT"

# label:fear_greed_sensitivity:market_regime_sensitivity:extra_flags
RUNS=(
  "baseline:0.0:0.0:"
  "fg:0.3:0.0:"
  "fg-price-only:0.3:0.0:--no-fear-greed-social --no-fear-greed-short-interest"
  "fg-vk:0.3:0.2:"
  "vk-only:0.0:0.2:"
)

for run in "${RUNS[@]}"; do
  IFS=':' read -r label fg mr extra <<< "$run"
  echo "[RUN] $label (fg=$fg mr=$mr) ${extra:-}"
  # shellcheck disable=SC2086
  "$PY" -m backtesting run \
    --data-dir "$DATA_DIR" --theme "$THEME" --theme-key "$THEME_KEY" \
    --from-date "$FROM" --to-date "$TO" \
    --rebalance M --top-n 3 --hold-days 20 \
    --fear-greed-sensitivity "$fg" --market-regime-sensitivity "$mr" $extra \
    --task-id "fg-$label" --output-dir "$OUT" >/dev/null
done

"$PY" - "$OUT" <<'PYEOF'
import json, sys
from pathlib import Path

out = Path(sys.argv[1])
labels = ["baseline", "fg", "fg-price-only", "fg-vk", "vk-only"]
rows, baseline_curve = [], None
for label in labels:
    path = out / f"fg-{label}.json"
    if not path.exists():
        continue
    result = json.loads(path.read_text(encoding="utf-8"))
    metrics = result["metrics"]
    curve = [(p["date"], p["equity"]) for p in result["equity_curve"]]
    if label == "baseline":
        baseline_curve = curve
    rows.append({
        "label": label,
        "return": metrics.get("total_return_pct"),
        "excess": metrics.get("excess_return_pct"),
        "mdd": metrics.get("mdd_pct"),
        "sharpe": metrics.get("sharpe"),
        "identical": curve == baseline_curve,
        "warnings": result["strategy"]["fear_greed_sizing"]["warnings"],
    })

width = max(len(r["label"]) for r in rows) if rows else 10
print(f"\n{'run'.ljust(width)}  {'return':>8} {'excess':>8} {'mdd':>8} {'sharpe':>7}  vs-baseline")
print("-" * (width + 48))
for row in rows:
    same = "identical" if row["identical"] else "different"
    print(f"{row['label'].ljust(width)}  {row['return']:>7}% {row['excess']:>7}% "
          f"{row['mdd']:>7}% {row['sharpe']:>7}  {same}")

warnings = sorted({w for row in rows for w in row["warnings"]})
if warnings:
    print("\nmissing inputs (components were reweighted, not zero-filled):")
    for warning in warnings:
        print(f"  - {warning}")
print(f"\nresults: {out}")
PYEOF
