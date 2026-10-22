from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.settings import get_data_dir
from src.ingestion.naver_theme import NaverThemeStockCollector, ThemeTargets
from src.ingestion.theme_targets import ThemeTargetStore, make_theme_key
from src.ingestion.types import StockTarget


def save_theme_catalog(collected: Iterable[ThemeTargets], *, data_dir: str) -> Dict[str, Any]:
    """Store discovered themes in data/theme_catalog without touching raw/theme_targets.

    raw/theme_targets is the live analysis universe; copying every Naver theme there
    would silently replace curated theme lists and multiply the universe.
    """
    from datetime import datetime, timezone

    root = Path(data_dir) / "theme_catalog"
    root.mkdir(parents=True, exist_ok=True)
    observed_at = datetime.now(timezone.utc).isoformat()
    saved: List[Dict[str, Any]] = []
    for theme in collected:
        theme_key = make_theme_key(theme.theme_name, theme.theme_name)
        rows = [{"theme_name": theme.theme_name, "stock_name": stock.stock_name, "stock_code": stock.stock_code,
                 "detail_url": theme.detail_url, "observed_at": observed_at} for stock in theme.stocks]
        path = root / f"{theme_key}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        saved.append({"theme": theme.theme_name, "themeKey": theme_key, "targetCount": len(rows),
                      "detailUrl": theme.detail_url, "path": str(path)})
    return {"saved_theme_count": len(saved), "saved_stock_count": sum(item["targetCount"] for item in saved),
            "themes": saved, "catalog_dir": str(root)}


def save_collected_themes(
    collected: Iterable[ThemeTargets],
    *,
    data_dir: str,
    overwrite: bool = True,
) -> Dict[str, Any]:
    store = ThemeTargetStore(data_dir=data_dir)
    saved: List[Dict[str, Any]] = []
    mode = "overwrite" if overwrite else "append"

    for theme in collected:
        theme_key = make_theme_key(theme.theme_name, theme.theme_name)
        targets = [
            StockTarget(stock_name=stock.stock_name, stock_code=stock.stock_code, corp_code="")
            for stock in theme.stocks
        ]
        rows = store.save_targets(
            theme_key=theme_key,
            targets=targets,
            theme_name=theme.theme_name,
            mode=mode,
        )
        saved.append(
            {
                "theme": theme.theme_name,
                "themeKey": theme_key,
                "targetCount": len(rows),
                "detailUrl": theme.detail_url,
                "path": str(store.get_path(theme_key)),
            }
        )

    return {
        "saved_theme_count": len(saved),
        "saved_stock_count": sum(int(item["targetCount"]) for item in saved),
        "themes": saved,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="네이버 증권 전체 테마와 테마별 종목을 data/theme_catalog에 저장합니다. 분석 대상 목록(theme_targets)은 바꾸지 않습니다.")
    parser.add_argument("--data-dir", default=str(get_data_dir()))
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--max-stocks-per-theme", type=int, default=200)
    parser.add_argument("--append", action="store_true", help="--as-targets와 함께: 기존 theme_targets에 append/dedupe로 저장합니다.")
    parser.add_argument("--as-targets", action="store_true",
                        help="수집한 모든 테마를 raw/theme_targets(실제 분석 대상)에 저장합니다. 기본은 data/theme_catalog 카탈로그입니다.")
    parser.add_argument("--summary-path", default="", help="수집 요약 JSON 저장 경로")
    args = parser.parse_args()

    collector = NaverThemeStockCollector()
    collected = collector.collect_all_themes(
        max_pages=args.max_pages,
        max_stocks_per_theme=args.max_stocks_per_theme,
    )
    if args.as_targets:
        print("[WARN][DISCOVER] writing every discovered theme into raw/theme_targets (the live analysis universe)")
        summary = save_collected_themes(collected, data_dir=args.data_dir, overwrite=not args.append)
    else:
        summary = save_theme_catalog(collected, data_dir=args.data_dir)

    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.summary_path:
        path = Path(args.summary_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
