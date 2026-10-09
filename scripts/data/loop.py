#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

KST = timezone(timedelta(hours=9))
# Only structured provider signals count. Bare numbers such as "429" and words such
# as "한도" also appear in receipt numbers, counts and news titles.
RATE_LIMIT_PATTERNS = [
    re.compile(r"\bstatus=020\b"),  # OpenDART: request limit exceeded
    re.compile(r"\bstatus=429\b"),  # HTTP 429 reported by the collectors
    re.compile(r"\btoo many requests\b", re.IGNORECASE),
    re.compile(r"\bquota exceeded\b", re.IGNORECASE),
]
# src/ingestion/base.py warns on every failed attempt, and a later attempt may still
# succeed; only final errors (which keep the status) mean the provider refused the run.
RETRY_ATTEMPT_PATTERN = re.compile(r"\bGET failed attempt=\d+/\d+")


def _contains_rate_limit(text: str) -> bool:
    return any(pattern.search(line) for line in text.splitlines()
               if not RETRY_ATTEMPT_PATTERN.search(line) for pattern in RATE_LIMIT_PATTERNS)


def _sleep_until_next_day(resume_hour: int, resume_minute: int) -> None:
    now = datetime.now(KST)
    tomorrow = now.date() + timedelta(days=1)
    wakeup = datetime(
        tomorrow.year,
        tomorrow.month,
        tomorrow.day,
        resume_hour,
        resume_minute,
        tzinfo=KST,
    )
    wait_seconds = max(1, int((wakeup - now).total_seconds()))
    print(
        f"⛔ API 한도 초과 감지. 다음 날 {wakeup.strftime('%Y-%m-%d %H:%M KST')}까지 대기합니다 "
        f"({wait_seconds // 60}분)."
    )
    time.sleep(wait_seconds)


def _saved_themes() -> list[str]:
    """Theme keys whose curated target lists exist in the data directory. The loop keeps
    these current by default instead of discovering new themes from fixed keywords."""
    from src.config.settings import get_data_dir

    return sorted(path.stem for path in (Path(get_data_dir()) / "raw" / "theme_targets").glob("*.jsonl"))


def _run_once(theme: str, enabled_sources: str, theme_key: str = "") -> tuple[int, str]:
    cmd = [
        sys.executable,
        "-m",
        "scripts.data.collect",
        "--theme",
        theme,
        "--enabled-sources",
        enabled_sources,
    ]
    if theme_key:
        cmd += ["--theme-key", theme_key]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    output = f"{proc.stdout}\n{proc.stderr}".strip()
    return proc.returncode, output


MARKET_CONTEXT_LOOKBACK_DAYS = 10


def _run_market_context(today) -> tuple[int, str]:
    """Collect KOSPI/KOSDAQ indices through yesterday. Stored observations are
    deduplicated, so re-reading a short window repairs gaps without duplicating."""
    to_date = today - timedelta(days=1)
    from_date = to_date - timedelta(days=MARKET_CONTEXT_LOOKBACK_DAYS)
    cmd = [sys.executable, "-m", "scripts.data.market_context",
           "--from-date", from_date.strftime("%Y%m%d"), "--to-date", to_date.strftime("%Y%m%d")]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    return proc.returncode, f"{proc.stdout}\n{proc.stderr}".strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Theme collection loop with next-day pause on API limit")
    parser.add_argument("--themes", type=str, default="",
                        help="Comma-separated theme names (default: every saved raw/theme_targets/<key>.jsonl)")
    parser.add_argument("--interval-minutes", type=int, default=30, help="Normal loop interval")
    parser.add_argument("--resume-hour", type=int, default=0, help="Next-day resume hour (KST)")
    parser.add_argument("--resume-minute", type=int, default=5, help="Next-day resume minute (KST)")
    parser.add_argument(
        "--enabled-sources",
        type=str,
        default="news,dart,financials,chart",
        help="Sources passed to scripts.data.collect. chart=KRX OHLCV, financials=DART statements.",
    )
    parser.add_argument("--market-context", action="store_true",
                        help="Also refresh KOSPI/KOSDAQ indices once per KST day after 08:00 (needs KRX_OPEN_API_KEY)")
    args = parser.parse_args()
    # The loop runs for days, usually with stdout sent to a file; without line buffering
    # its progress stays in the buffer for hours (one line per theme every 30 minutes).
    sys.stdout.reconfigure(line_buffering=True)
    if args.market_context and not (os.getenv("KRX_OPEN_API_KEY") or os.getenv("KRX_API_KEY") or "").strip():
        print("--market-context requires KRX_OPEN_API_KEY (or KRX_API_KEY) with the index service approval.")
        return 1

    themes = [t.strip() for t in args.themes.split(",") if t.strip()]
    saved = not themes
    if saved:
        themes = _saved_themes()
    if not themes:
        print("No themes configured: pass --themes or save targets under raw/theme_targets/ first.")
        return 1

    print(
        f"📡 수집 루프 시작: themes={themes}, interval={args.interval_minutes}분, "
        f"enabled_sources={args.enabled_sources}"
    )
    market_context_day = None
    while True:
        now = datetime.now(KST)
        if args.market_context and market_context_day != now.date() and now.hour >= 8:
            code, output = _run_market_context(now.date())
            if code == 0:
                market_context_day = now.date()
                print(f"[MARKET] 지수 갱신 완료: {output.splitlines()[-1] if output else ''}")
            else:
                print(f"[MARKET] 지수 갱신 실패(code={code}); 다음 주기에 다시 시도")
                tail = "\n".join(output.splitlines()[-5:])
                if tail:
                    print(tail)
        for theme in themes:
            print(f"\n[COLLECT] {theme} 시작")
            code, output = _run_once(theme, args.enabled_sources, theme_key=theme if saved else "")
            if code == 0:
                print(f"[COLLECT] {theme} 완료")
                continue

            print(f"[COLLECT] {theme} 실패(code={code})")
            if _contains_rate_limit(output):
                _sleep_until_next_day(args.resume_hour, args.resume_minute)
            else:
                # 일반 오류는 다음 테마로 진행
                tail = "\n".join(output.splitlines()[-10:])
                if tail:
                    print(tail)

        print(f"\n⏳ 루프 대기 {args.interval_minutes}분")
        time.sleep(max(1, args.interval_minutes) * 60)


if __name__ == "__main__":
    project_root = ROOT
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    raise SystemExit(main())
