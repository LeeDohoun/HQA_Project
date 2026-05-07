from __future__ import annotations

"""Point-in-time safe RAG and market-data helpers for backtesting.

This module intentionally lives outside the production agent path.  It reads
the existing canonical corpus and market-data files, then applies an explicit
``as_of_date`` before any document or price row can reach a backtest.
"""

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.config.settings import get_data_dir
from src.rag.dedupe import make_record_id
from src.rag.source_registry import is_document_source
from src.rag.source_weighting import apply_source_weighting


DEFAULT_LOOKBACK_DAYS: Dict[str, Optional[int]] = {
    "forum": 90,
    "news": 365,
    "general_news": 365,
    "dart": None,
    "report": 730,
}


def normalize_ymd(value: Any) -> str:
    """Normalize common date formats to YYYYMMDD."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")

    text = str(value).strip()
    if not text:
        return ""

    if len(text) >= 8 and text[:8].isdigit():
        return text[:8]

    match = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", text)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}{int(month):02d}{int(day):02d}"

    return ""


def parse_ymd(value: Any) -> Optional[date]:
    ymd = normalize_ymd(value)
    if not ymd:
        return None
    try:
        return datetime.strptime(ymd, "%Y%m%d").date()
    except ValueError:
        return None


def _source_types_set(source_types: Optional[Sequence[str]]) -> set[str]:
    return {str(item).strip().lower() for item in (source_types or []) if str(item).strip()}


def _row_metadata(row: Dict[str, Any]) -> Dict[str, Any]:
    meta = row.get("metadata") or {}
    return meta if isinstance(meta, dict) else {}


def _row_source(row: Dict[str, Any]) -> str:
    meta = _row_metadata(row)
    return str(row.get("source_type") or meta.get("source_type") or "").strip().lower()


def _row_published_ymd(row: Dict[str, Any]) -> str:
    meta = _row_metadata(row)
    return normalize_ymd(
        meta.get("published_ymd")
        or meta.get("published_at")
        or row.get("published_at")
        or meta.get("timestamp")
        or row.get("timestamp")
    )


def _row_stock_code(row: Dict[str, Any]) -> str:
    meta = _row_metadata(row)
    return str(row.get("stock_code") or meta.get("stock_code") or "").strip()


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[가-힣A-Za-z0-9]+", (text or "").lower())


def _to_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class TemporalFilter:
    as_of_date: str
    source_types: Optional[Sequence[str]] = None
    stock_code: str = ""
    lookback_days: Optional[Dict[str, Optional[int]]] = None
    include_undated: bool = False


class TemporalRAG:
    """Read canonical RAG rows with point-in-time filtering."""

    def __init__(self, data_dir: Optional[str] = None, theme_key: str = "ai"):
        self.data_dir = Path(data_dir) if data_dir else get_data_dir()
        self.theme_key = theme_key
        self._records_cache: Optional[List[Dict[str, Any]]] = None

    @property
    def corpus_path(self) -> Path:
        return self.data_dir / "canonical_index" / self.theme_key / "corpus.jsonl"

    def load_records(self) -> List[Dict[str, Any]]:
        if self._records_cache is not None:
            return list(self._records_cache)
        records = list(_iter_jsonl(self.corpus_path))
        self._records_cache = records
        return list(records)

    def filter_records(
        self,
        as_of_date: str,
        *,
        source_types: Optional[Sequence[str]] = None,
        stock_code: str = "",
        lookback_days: Optional[Dict[str, Optional[int]]] = None,
        include_undated: bool = False,
    ) -> List[Dict[str, Any]]:
        as_of_ymd = normalize_ymd(as_of_date)
        if not as_of_ymd:
            raise ValueError(f"invalid as_of_date: {as_of_date}")

        allowed_sources = _source_types_set(source_types)
        lookbacks = dict(DEFAULT_LOOKBACK_DAYS)
        if lookback_days:
            lookbacks.update({str(k).lower(): v for k, v in lookback_days.items()})

        out: List[Dict[str, Any]] = []
        as_of_dt = datetime.strptime(as_of_ymd, "%Y%m%d").date()

        for row in self.load_records():
            source = _row_source(row)
            if allowed_sources and source not in allowed_sources:
                continue
            if stock_code and _row_stock_code(row) != stock_code:
                continue

            published_ymd = _row_published_ymd(row)
            if not published_ymd:
                if include_undated:
                    out.append(row)
                continue
            if published_ymd > as_of_ymd:
                continue

            days = lookbacks.get(source)
            if days is not None:
                lower = (as_of_dt - timedelta(days=int(days))).strftime("%Y%m%d")
                if published_ymd < lower:
                    continue

            out.append(row)
        return out

    def filter_period_records(
        self,
        from_date: str,
        to_date: str,
        *,
        source_types: Optional[Sequence[str]] = None,
        stock_code: str = "",
        include_undated: bool = False,
    ) -> List[Dict[str, Any]]:
        from_ymd = normalize_ymd(from_date)
        to_ymd = normalize_ymd(to_date)
        if not from_ymd or not to_ymd:
            raise ValueError(f"invalid period: {from_date}..{to_date}")

        allowed_sources = _source_types_set(source_types)
        out: List[Dict[str, Any]] = []
        for row in self.load_records():
            source = _row_source(row)
            if allowed_sources and source not in allowed_sources:
                continue
            if stock_code and _row_stock_code(row) != stock_code:
                continue
            published_ymd = _row_published_ymd(row)
            if not published_ymd:
                if include_undated:
                    out.append(row)
                continue
            if from_ymd <= published_ymd <= to_ymd:
                out.append(row)
        return dedupe_records(out)

    def search(
        self,
        query: str,
        as_of_date: str,
        *,
        top_k: int = 5,
        source_types: Optional[Sequence[str]] = None,
        stock_code: str = "",
        lookback_days: Optional[Dict[str, Optional[int]]] = None,
    ) -> List[Dict[str, Any]]:
        rows = self.filter_records(
            as_of_date=as_of_date,
            source_types=source_types,
            stock_code=stock_code,
            lookback_days=lookback_days,
        )
        if not rows:
            return []

        query_tokens = set(_tokenize(query))
        candidates: List[Dict[str, Any]] = []
        for row in rows:
            score = self._lexical_score(row, query_tokens)
            if score <= 0 and query_tokens:
                continue
            meta = dict(_row_metadata(row))
            candidates.append(
                {
                    "text": row.get("text", ""),
                    "metadata": meta,
                    "source_type": _row_source(row),
                    "score": score if score > 0 else 0.01,
                    "theme_key": self.theme_key,
                    "retrieval_method": "temporal_lexical",
                }
            )

        if not candidates:
            candidates = [
                {
                    "text": row.get("text", ""),
                    "metadata": dict(_row_metadata(row)),
                    "source_type": _row_source(row),
                    "score": 0.01,
                    "theme_key": self.theme_key,
                    "retrieval_method": "temporal_recent",
                }
                for row in sorted(rows, key=_row_published_ymd, reverse=True)[: max(top_k * 3, top_k)]
            ]

        ref = datetime.strptime(normalize_ymd(as_of_date), "%Y%m%d")
        weighted = apply_source_weighting(candidates, reference_date=ref)
        return weighted[:top_k]

    def search_for_context(
        self,
        query: str,
        as_of_date: str,
        *,
        top_k: int = 5,
        source_types: Optional[Sequence[str]] = None,
        stock_code: str = "",
        lookback_days: Optional[Dict[str, Optional[int]]] = None,
    ) -> str:
        results = self.search(
            query=query,
            as_of_date=as_of_date,
            top_k=top_k,
            source_types=source_types,
            stock_code=stock_code,
            lookback_days=lookback_days,
        )
        if not results:
            return "관련 문서를 찾을 수 없습니다."

        parts = [f"=== Temporal RAG as_of={normalize_ymd(as_of_date)} theme={self.theme_key} ==="]
        for i, result in enumerate(results, 1):
            meta = result.get("metadata", {})
            source = result.get("source_type", "unknown")
            title = str(meta.get("title", ""))[:80]
            stock_name = str(meta.get("stock_name", ""))
            published = str(meta.get("published_at", ""))
            score = float(result.get("weighted_score", result.get("score", 0.0)))
            parts.append(
                f"\n[문서 {i}] (출처: {source}, source={source}, score={score:.3f}, "
                f"title={title}, stock={stock_name}, date={published})"
            )
            parts.append(str(result.get("text", ""))[:1000])
        return "\n".join(parts)

    def source_counts(
        self,
        as_of_date: str,
        *,
        source_types: Optional[Sequence[str]] = None,
        stock_code: str = "",
        lookback_days: Optional[Dict[str, Optional[int]]] = None,
    ) -> Counter:
        rows = self.filter_records(
            as_of_date=as_of_date,
            source_types=source_types,
            stock_code=stock_code,
            lookback_days=lookback_days,
        )
        return Counter(_row_source(row) for row in rows)

    @staticmethod
    def _lexical_score(row: Dict[str, Any], query_tokens: set[str]) -> float:
        if not query_tokens:
            return 0.01
        meta = _row_metadata(row)
        haystack = " ".join(
            [
                str(row.get("text", "")),
                str(meta.get("title", "")),
                str(meta.get("stock_name", "")),
                str(meta.get("stock_code", "")),
            ]
        ).lower()
        score = 0.0
        for token in query_tokens:
            if len(token) <= 1:
                continue
            hits = haystack.count(token)
            if hits:
                score += 1.0 + math.log1p(hits)
        return score


class TemporalPriceLoader:
    """Load OHLCV rows as known on a specific backtest date."""

    def __init__(self, data_dir: Optional[str] = None, theme_key: str = "ai"):
        self.data_dir = Path(data_dir) if data_dir else get_data_dir()
        self.theme_key = theme_key

    def get_stock_data(
        self,
        stock_code: str,
        *,
        as_of_date: str,
        days: int = 300,
        filter_invalid_ohlc: bool = True,
    ):
        import pandas as pd

        as_of = parse_ymd(as_of_date)
        if as_of is None:
            raise ValueError(f"invalid as_of_date: {as_of_date}")

        records: List[Dict[str, Any]] = []
        for row in self._load_market_rows(stock_code):
            timestamp = str(row.get("timestamp", "")).strip()
            row_date = parse_ymd(timestamp)
            if row_date is None or row_date > as_of:
                continue

            open_ = _to_number(row.get("open"))
            high = _to_number(row.get("high"))
            low = _to_number(row.get("low"))
            close = _to_number(row.get("close"))
            volume = _to_number(row.get("volume"))
            if None in {open_, high, low, close}:
                continue
            if volume is None:
                volume = 0.0

            if filter_invalid_ohlc:
                if open_ <= 0 or high <= 0 or low <= 0 or close <= 0 or volume < 0:
                    continue
                if high < max(open_, close, low) or low > min(open_, close, high):
                    continue

            records.append(
                {
                    "Date": pd.to_datetime(timestamp),
                    "Open": open_,
                    "High": high,
                    "Low": low,
                    "Close": close,
                    "Volume": volume,
                }
            )

        if not records:
            raise ValueError(f"유효한 주가 레코드가 없습니다: stock_code={stock_code}, as_of={as_of_date}")

        df = pd.DataFrame.from_records(records)
        df = df.dropna(subset=["Date", "Open", "High", "Low", "Close"])
        df = df.sort_values("Date").drop_duplicates(subset=["Date"], keep="last")
        df = df.set_index("Date")
        return df.tail(days)

    def _load_market_rows(self, stock_code: str) -> List[Dict[str, Any]]:
        matched: List[Dict[str, Any]] = []
        for path in self._candidate_paths():
            for row in _iter_jsonl(path):
                if str(row.get("stock_code", "")).strip() == stock_code:
                    matched.append(row)
        return matched

    def _candidate_paths(self) -> List[Path]:
        paths = [
            self.data_dir / "market_data" / self.theme_key / "chart.jsonl",
            self.data_dir / "market_data" / self.theme_key / "combined.jsonl",
            self.data_dir / "raw" / "chart" / f"{self.theme_key}.jsonl",
        ]
        deduped: List[Path] = []
        seen = set()
        for path in paths:
            if path.exists() and path not in seen:
                seen.add(path)
                deduped.append(path)
        return deduped


def build_period_snapshot(
    *,
    data_dir: str,
    theme_key: str,
    from_date: str,
    to_date: str,
    output_name: str,
    source_types: Optional[Sequence[str]] = None,
    build_vector: bool = False,
) -> Dict[str, Any]:
    rag = TemporalRAG(data_dir=data_dir, theme_key=theme_key)
    rows = rag.filter_period_records(
        from_date=from_date,
        to_date=to_date,
        source_types=source_types,
    )

    out_dir = Path(data_dir) / "period_rag" / output_name
    out_dir.mkdir(parents=True, exist_ok=True)
    combined_count = save_jsonl(rows, out_dir / "combined.jsonl")

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        source = _row_source(row)
        if is_document_source(source):
            grouped.setdefault(source, []).append(row)

    source_counts: Dict[str, int] = {}
    for source, source_rows in grouped.items():
        source_counts[source] = save_jsonl(source_rows, out_dir / f"{source}.jsonl")

    vector_stats: Dict[str, int] = {}
    if build_vector:
        from src.retrieval.vector_store import SourceRAGBuilder

        vector_stats = SourceRAGBuilder().upsert_by_source(
            records=rows,
            output_dir=str(out_dir / "vector_stores"),
            mode="overwrite",
            theme_key="",
        )

    return {
        "output_dir": str(out_dir),
        "combined_count": combined_count,
        "source_counts": source_counts,
        "vector_stats": vector_stats,
    }


def dedupe_records(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        key = make_record_id(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def save_jsonl(rows: Iterable[Dict[str, Any]], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)
