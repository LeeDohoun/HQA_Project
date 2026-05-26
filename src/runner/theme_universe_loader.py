from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.config.settings import get_data_dir

logger = logging.getLogger(__name__)


class ThemeUniverseLoader:
    """Load raw theme candidates, price rows, and document rows without judging them."""

    def __init__(self, *, data_dir: Optional[str] = None):
        self._data_dir = Path(data_dir) if data_dir else get_data_dir()

    def load_theme(self, theme_config: Dict[str, Any]) -> Dict[str, Any]:
        theme = str(theme_config.get("theme") or theme_config.get("theme_name") or "").strip()
        theme_key = str(theme_config.get("theme_key") or "").strip()
        max_candidates = self._to_int(theme_config.get("max_candidates"), default=0)

        target_path = self._data_dir / "raw" / "theme_targets" / f"{theme_key}.jsonl"
        if not theme_key or not target_path.exists():
            return {
                "status": "skipped",
                "reason": "missing_theme_targets",
                "theme": theme,
                "theme_key": theme_key,
                "target_path": str(target_path),
                "candidates": [],
            }

        target_rows = self._read_jsonl(target_path)
        price_by_code = self._load_price_rows(theme_key)
        docs_by_code = self._load_documents(theme_key)

        candidates: List[Dict[str, Any]] = []
        seen = set()
        for row in target_rows:
            stock_code = self._stock_code(row)
            if not stock_code or stock_code in seen:
                continue
            seen.add(stock_code)
            stock_name = self._stock_name(row)
            documents = docs_by_code.get(stock_code, [])
            price_history = price_by_code.get(stock_code, [])
            source_counts = self._count_sources(documents)
            candidates.append(
                {
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "corp_code": str(row.get("corp_code") or "").strip(),
                    "price_history": price_history,
                    "documents": documents,
                    "source_counts": source_counts,
                    "data_flags": {
                        "has_price_history": bool(price_history),
                        "has_documents": bool(documents),
                        "has_recent_documents": self._has_recent_documents(documents),
                    },
                    "raw_target": row,
                }
            )

        if max_candidates > 0:
            candidates = candidates[:max_candidates]

        return {
            "status": "loaded",
            "theme": theme,
            "theme_key": theme_key,
            "target_path": str(target_path),
            "candidate_count": len(candidates),
            "candidates": candidates,
        }

    def _load_price_rows(self, theme_key: str) -> Dict[str, List[Dict[str, Any]]]:
        path = self._data_dir / "market_data" / theme_key / "chart.jsonl"
        rows_by_code: Dict[str, List[Dict[str, Any]]] = {}
        if not path.exists():
            return rows_by_code

        for row in self._read_jsonl(path):
            stock_code = self._stock_code(row)
            if not stock_code:
                continue
            rows_by_code.setdefault(stock_code, []).append(row)
        return rows_by_code

    def _load_documents(self, theme_key: str) -> Dict[str, List[Dict[str, Any]]]:
        path = self._data_dir / "canonical_index" / theme_key / "corpus.jsonl"
        docs_by_code: Dict[str, List[Dict[str, Any]]] = {}
        if not path.exists():
            return docs_by_code

        for row in self._read_jsonl(path):
            stock_code = self._stock_code(row)
            if not stock_code:
                continue
            docs_by_code.setdefault(stock_code, []).append(row)
        return docs_by_code

    @staticmethod
    def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("Invalid JSONL row skipped: %s:%s", path, line_no)
                        continue
                    if isinstance(payload, dict):
                        rows.append(payload)
        except Exception as exc:
            logger.warning("Failed to read JSONL path=%s error=%s", path, exc)
        return rows

    @classmethod
    def _stock_code(cls, row: Dict[str, Any]) -> str:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        for key in ("stock_code", "code", "ticker", "pdno"):
            value = row.get(key)
            if value:
                return cls._normalize_stock_code(value)
            value = metadata.get(key)
            if value:
                return cls._normalize_stock_code(value)
        return ""

    @staticmethod
    def _normalize_stock_code(value: Any) -> str:
        text = str(value or "").strip()
        if text.endswith(".KS") or text.endswith(".KQ"):
            text = text.split(".", 1)[0]
        return text.zfill(6) if text.isdigit() and len(text) < 6 else text

    @staticmethod
    def _stock_name(row: Dict[str, Any]) -> str:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        for key in ("stock_name", "name", "corp_name", "prdt_name"):
            value = row.get(key) or metadata.get(key)
            if value:
                return str(value).strip()
        return ""

    @classmethod
    def _count_sources(cls, documents: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        counts = {"news": 0, "dart": 0, "forum": 0}
        for doc in documents:
            source_type = cls._source_type(doc)
            if "dart" in source_type or "disclosure" in source_type:
                counts["dart"] += 1
            elif "forum" in source_type or "community" in source_type or "board" in source_type:
                counts["forum"] += 1
            elif "news" in source_type or "article" in source_type:
                counts["news"] += 1
        return counts

    @classmethod
    def _has_recent_documents(cls, documents: Iterable[Dict[str, Any]]) -> bool:
        for doc in documents:
            source_type = cls._source_type(doc)
            if source_type == "theme_membership":
                continue
            text = cls._document_text(doc)
            if text:
                return True
        return False

    @staticmethod
    def _source_type(doc: Dict[str, Any]) -> str:
        metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        return str(doc.get("source_type") or metadata.get("source_type") or metadata.get("source") or "").lower()

    @staticmethod
    def _document_text(doc: Dict[str, Any]) -> str:
        metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        for key in ("title", "summary", "content", "text"):
            value = doc.get(key) or metadata.get(key)
            if value and str(value).strip():
                return str(value).strip()
        return ""

    @staticmethod
    def _to_int(value: Any, *, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default
