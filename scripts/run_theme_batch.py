#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List


@dataclass(frozen=True)
class ThemeSpec:
    theme: str
    theme_key: str = ""


def _parse_theme_item(raw: str) -> ThemeSpec:
    item = raw.strip()
    if not item:
        raise ValueError("empty theme item")

    if ":" in item:
        theme, theme_key = item.split(":", 1)
        return ThemeSpec(theme=theme.strip(), theme_key=theme_key.strip())
    return ThemeSpec(theme=item)


def _load_theme_file(path: Path) -> List[ThemeSpec]:
    if not path.exists():
        raise FileNotFoundError(path)

    suffix = path.suffix.lower()
    if suffix == ".json":
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload.get("themes", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("JSON theme file must contain a list or {'themes': [...]}")

        specs: List[ThemeSpec] = []
        for row in rows:
            if isinstance(row, str):
                specs.append(_parse_theme_item(row))
            elif isinstance(row, dict):
                theme = str(row.get("theme") or row.get("name") or "").strip()
                theme_key = str(row.get("theme_key") or row.get("key") or "").strip()
                if theme:
                    specs.append(ThemeSpec(theme=theme, theme_key=theme_key))
        return specs

    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            return [
                ThemeSpec(
                    theme=str(row.get("theme") or row.get("name") or "").strip(),
                    theme_key=str(row.get("theme_key") or row.get("key") or "").strip(),
                )
                for row in reader
                if str(row.get("theme") or row.get("name") or "").strip()
            ]

    specs = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            specs.append(_parse_theme_item(line))
    return specs


def _merge_specs(inline: str, theme_file: str) -> List[ThemeSpec]:
    specs: List[ThemeSpec] = []
    if inline:
        specs.extend(_parse_theme_item(item) for item in inline.split(",") if item.strip())
    if theme_file:
        specs.extend(_load_theme_file(Path(theme_file)))

    deduped = {}
    for spec in specs:
        key = spec.theme_key or spec.theme
        deduped[key] = spec
    return list(deduped.values())


def _append_common_args(command: List[str], args: argparse.Namespace) -> None:
    common_options = [
        ("--data-dir", args.data_dir),
        ("--theme-max-stocks", args.theme_max_stocks),
        ("--theme-max-pages", args.theme_max_pages),
        ("--target-mode", args.target_mode),
        ("--corp-codes-csv", args.corp_codes_csv),
        ("--from-date", args.from_date),
        ("--to-date", args.to_date),
        ("--max-news", args.max_news),
        ("--max-general-news", args.max_general_news),
        ("--forum-pages", args.forum_pages),
        ("--chart-pages", args.chart_pages),
        ("--enabled-sources", args.enabled_sources),
        ("--general-news-keywords", args.general_news_keywords),
        ("--update-mode", args.update_mode),
    ]
    for option, value in common_options:
        if value not in (None, ""):
            command.extend([option, str(value)])

    if args.reuse_saved_targets:
        command.append("--reuse-saved-targets")
    if args.save_only:
        command.append("--save-only")


def _build_command(spec: ThemeSpec, args: argparse.Namespace) -> List[str]:
    command = [
        sys.executable,
        "scripts/theme_pipeline.py",
        "--theme",
        spec.theme,
    ]
    if spec.theme_key:
        command.extend(["--theme-key", spec.theme_key])
    _append_common_args(command, args)
    return command


def _format_spec(spec: ThemeSpec) -> str:
    return f"{spec.theme}:{spec.theme_key}" if spec.theme_key else spec.theme


def run_batch(specs: Iterable[ThemeSpec], args: argparse.Namespace) -> int:
    specs = list(specs)
    print(f"[BATCH] themes={len(specs)} continue_on_error={args.continue_on_error}")

    failures = []
    for index, spec in enumerate(specs, start=1):
        print(f"\n[BATCH] ({index}/{len(specs)}) {_format_spec(spec)}")
        command = _build_command(spec, args)
        print("[BATCH] command=" + " ".join(command))
        if args.dry_run:
            continue

        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            failures.append((spec, result.returncode))
            print(f"[BATCH][ERROR] {_format_spec(spec)} failed rc={result.returncode}")
            if not args.continue_on_error:
                break

    if failures:
        print("\n[BATCH] failed themes:")
        for spec, returncode in failures:
            print(f"  - {_format_spec(spec)} rc={returncode}")
        return 1

    print("\n[BATCH] done")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="여러 테마를 순차적으로 theme_pipeline.py에 전달해 수집/빌드합니다.",
    )
    parser.add_argument(
        "--themes",
        default="",
        help="쉼표 구분 테마 목록. 예: 'AI:ai,반도체:semiconductor,2차전지:battery'",
    )
    parser.add_argument(
        "--theme-file",
        default="",
        help="테마 목록 파일(txt/csv/json). txt는 '테마:theme_key' 한 줄씩.",
    )
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true")

    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--theme-max-stocks", type=int, default=30)
    parser.add_argument("--theme-max-pages", type=int, default=10)
    parser.add_argument("--target-mode", choices=["overwrite", "append"], default="overwrite")
    parser.add_argument("--reuse-saved-targets", action="store_true")
    parser.add_argument("--save-only", action="store_true")
    parser.add_argument("--corp-codes-csv", default="./corp_codes.csv")
    parser.add_argument("--from-date", default="20250101")
    parser.add_argument("--to-date", default="20251231")
    parser.add_argument("--max-news", type=int, default=20)
    parser.add_argument("--max-general-news", type=int, default=20)
    parser.add_argument("--forum-pages", type=int, default=3)
    parser.add_argument("--chart-pages", type=int, default=5)
    parser.add_argument("--enabled-sources", default="news,dart,forum")
    parser.add_argument("--general-news-keywords", default="")
    parser.add_argument(
        "--update-mode",
        choices=["append-new-stocks", "overwrite"],
        default="append-new-stocks",
    )
    args = parser.parse_args()

    specs = _merge_specs(args.themes, args.theme_file)
    if not specs:
        parser.error("--themes 또는 --theme-file 중 하나는 필요합니다.")

    raise SystemExit(run_batch(specs, args))


if __name__ == "__main__":
    main()
