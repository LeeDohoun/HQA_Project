"""
Market-data loader bridging rag-data-pipeline outputs to ai-main chart analysis.

Reads JSONL files produced under:
- data/market_data/<theme>/chart.jsonl
- data/market_data/<theme>/combined.jsonl
- data/raw/chart/<theme>.jsonl
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from src.config.settings import get_data_dir


class PriceLoader:
    """Load OHLCV history for agent-side technical analysis."""

    def __init__(self, data_dir: Optional[str] = None, theme_key: Optional[str] = None):
        self.data_dir = Path(data_dir) if data_dir else get_data_dir()
        self.theme_key = theme_key

    def get_stock_data(self, stock_code: str, days: int = 300) -> pd.DataFrame:
        rows = self._load_market_rows(stock_code)
        if not rows:
            raise FileNotFoundError(
                f"주가 데이터를 찾을 수 없습니다: stock_code={stock_code} "
                f"(searched under {self.data_dir})"
            )

        records: List[Dict] = []
        for row in rows:
            timestamp = str(row.get("timestamp", "")).strip()
            if not timestamp:
                continue

            records.append(
                {
                    "Date": pd.to_datetime(timestamp),
                    "Open": self._to_number(row.get("open")),
                    "High": self._to_number(row.get("high")),
                    "Low": self._to_number(row.get("low")),
                    "Close": self._to_number(row.get("close")),
                    "Volume": self._to_number(row.get("volume")),
                }
            )

        if not records:
            raise ValueError(f"유효한 주가 레코드가 없습니다: stock_code={stock_code}")

        df = pd.DataFrame.from_records(records)
        df = df.dropna(subset=["Date", "Open", "High", "Low", "Close"])
        df = df.sort_values("Date").drop_duplicates(subset=["Date"], keep="last")
        df = df.set_index("Date")
        df["Volume"] = df["Volume"].fillna(0)

        return df.tail(days)

    def _load_market_rows(self, stock_code: str) -> List[Dict]:
        matched: List[Dict] = []
        for path in self._candidate_paths():
            for row in self._iter_jsonl(path):
                if str(row.get("stock_code", "")).strip() == stock_code:
                    matched.append(row)
        return matched

    def _candidate_paths(self) -> List[Path]:
        paths: List[Path] = []

        market_root = self.data_dir / "market_data"
        if market_root.exists():
            theme_dirs = (
                [market_root / self.theme_key]
                if self.theme_key
                else [path for path in market_root.iterdir() if path.is_dir()]
            )
            for theme_dir in theme_dirs:
                if not theme_dir.exists():
                    continue
                paths.extend(
                    [
                        theme_dir / "chart.jsonl",
                        theme_dir / "combined.jsonl",
                    ]
                )

        raw_chart_root = self.data_dir / "raw" / "chart"
        if raw_chart_root.exists():
            if self.theme_key:
                paths.append(raw_chart_root / f"{self.theme_key}.jsonl")
            else:
                paths.extend(sorted(raw_chart_root.glob("*.jsonl")))

        deduped: List[Path] = []
        seen = set()
        for path in paths:
            if path.exists() and path not in seen:
                seen.add(path)
                deduped.append(path)
        return deduped

    @staticmethod
    def _iter_jsonl(path: Path) -> Iterable[Dict]:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)

    @staticmethod
    def _to_number(value) -> float:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(",", "")
        if not text:
            return 0.0
        return float(text)
