"""Refresh KOSPI/KOSDAQ daily closes and theme stocks' market mappings from KIS (paper app key).

The chartist compares event reactions with the market index only when both exist. The KRX Open API key
that `scripts.data.market_context` needs still answers 401, so this reads the same indices from KIS. It
makes no LLM calls and calls KIS only when the stored indices lack the latest completed session (about
twice a day per series) or a theme stock has no market mapping yet, so it is cheap to run every loop pass.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_data_dir, load_project_env  # noqa: E402
from src.ingestion.kis_benchmarks import KisBenchmarkCollector, kis_market_mapping  # noqa: E402
from src.ingestion.krx_benchmarks import save_benchmark_records  # noqa: E402
from src.ingestion.storage import file_lock  # noqa: E402
from src.runner.trading_calendar import completed_daily_sessions  # noqa: E402

LOOKBACK_DAYS = 400  # The analysis price history spans about 400 calendar days.


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _theme_codes(data_dir: Path) -> list[str]:
    codes = set()
    for path in sorted((data_dir / "raw" / "theme_targets").glob("*.jsonl")):
        for row in _rows(path):
            code = str(row.get("stock_code") or "").strip()
            if len(code) == 6 and code.isdigit():
                codes.add(code)
    return sorted(codes)


def refresh(data_dir: Path, now: datetime, collector: KisBenchmarkCollector | None = None,
            lookback_days: int = LOOKBACK_DAYS) -> dict:
    latest = date.fromisoformat(completed_daily_sessions(now, count=1)[-1][0])
    start = latest - timedelta(days=lookback_days)
    folder = data_dir / "market_context"
    bench_path, mapping_path = folder / "benchmarks.jsonl", folder / "benchmark_mappings.jsonl"
    stored = {(row.get("series"), row.get("trade_date")) for row in _rows(bench_path)}
    summary = {"latest_session": latest.isoformat(), "saved_records": 0, "new_mappings": 0, "unmapped": []}
    for series in ("KOSPI", "KOSDAQ"):
        days = sorted(day for name, day in stored if name == series and isinstance(day, str))
        # Up to date when it holds the latest session and reaches back to the lookback start.
        if days and days[-1] == latest.isoformat() and days[0] <= (start + timedelta(days=10)).isoformat():
            continue
        collector = collector or KisBenchmarkCollector()
        records = collector.collect_index_daily(series, start, latest)
        summary["saved_records"] += save_benchmark_records(records, bench_path)
    from src.runner.market_context_data import _mapping
    mapped = {row.get("stock_code") for row in _rows(mapping_path) if row.get("kind") == "market"}
    pending = []
    for code in _theme_codes(data_dir):
        if code in mapped:
            continue
        collector = collector or KisBenchmarkCollector()
        series, observed = collector.collect_market(code)
        if series is None:
            summary["unmapped"].append(code)
            continue
        row = kis_market_mapping(code, series, observed, start)
        _mapping(row)  # The analysis loader's own check, so a bad row is never stored.
        pending.append(row)
    if pending:
        folder.mkdir(parents=True, exist_ok=True)
        with file_lock(mapping_path.with_suffix(".jsonl.lock")):
            with mapping_path.open("a", encoding="utf-8") as handle:
                handle.write("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in pending))
        summary["new_mappings"] = len(pending)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh KOSPI/KOSDAQ closes and market mappings from KIS")
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    load_project_env()
    data_dir = Path(args.data_dir) if args.data_dir else get_data_dir()
    print(json.dumps(refresh(data_dir, datetime.now(timezone.utc)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
