#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
DEFAULT_THEMES = ["ai", "battery", "bio", "defense", "robot", "semiconductor"]
RATE_LIMIT_PATTERNS = [
    "429",
    "rate limit",
    "too many requests",
    "quota exceeded",
    "한도",
]


def _contains_rate_limit(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in RATE_LIMIT_PATTERNS)


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


def _run_once(theme: str, enabled_sources: str) -> tuple[int, str]:
    cmd = [
        sys.executable,
        "scripts/run_pipeline.py",
        "--theme",
        theme,
        "--collect-and-build",
        "--enabled-sources",
        enabled_sources,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = f"{proc.stdout}\n{proc.stderr}".strip()
    return proc.returncode, output


def main() -> int:
    parser = argparse.ArgumentParser(description="Theme collection loop with next-day pause on API limit")
    parser.add_argument("--themes", type=str, default=",".join(DEFAULT_THEMES), help="Comma-separated theme keys")
    parser.add_argument("--interval-minutes", type=int, default=30, help="Normal loop interval")
    parser.add_argument("--resume-hour", type=int, default=0, help="Next-day resume hour (KST)")
    parser.add_argument("--resume-minute", type=int, default=5, help="Next-day resume minute (KST)")
    parser.add_argument(
        "--enabled-sources",
        type=str,
        default="news,dart,forum,chart",
        help="Comma-separated sources passed to run_pipeline.py",
    )
    args = parser.parse_args()

    themes = [t.strip() for t in args.themes.split(",") if t.strip()]
    if not themes:
        print("No themes configured.")
        return 1

    print(
        f"📡 수집 루프 시작: themes={themes}, interval={args.interval_minutes}분, "
        f"enabled_sources={args.enabled_sources}"
    )
    while True:
        for theme in themes:
            print(f"\n[COLLECT] {theme} 시작")
            code, output = _run_once(theme, args.enabled_sources)
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
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    raise SystemExit(main())
