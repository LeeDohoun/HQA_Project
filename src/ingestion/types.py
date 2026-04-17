from __future__ import annotations

# File role:
# - Shared dataclasses for request/response payloads across the pipeline.
# - Canonical metadata schema for the unified RAG corpus.

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.config.settings import get_data_dir

@dataclass
class StockTarget:
    stock_name: str
    stock_code: str
    corp_code: str = ""


def generate_doc_id(
    source_type: str,
    url: str = "",
    title: str = "",
    published_at: str = "",
    stock_code: str = "",
) -> str:
    """Generate a stable, deterministic document ID for dedup & traceability."""
    key_parts = [
        (source_type or "").strip().lower(),
        (url or "").strip(),
        (title or "").strip(),
        (published_at or "").strip(),
        (stock_code or "").strip(),
    ]
    base = "|".join(key_parts)
    return hashlib.md5(base.encode("utf-8")).hexdigest()


@dataclass
class DocumentRecord:
    """Canonical text document record.

    Required canonical metadata fields (set explicitly or via metadata dict):
        doc_id, source_type, stock_code, stock_name, theme_key,
        published_at, collected_at,
        credibility_score, freshness_score, content_quality_score
    """

    source_type: str
    title: str
    content: str
    url: str
    doc_id: str = ""
    stock_name: Optional[str] = None
    stock_code: Optional[str] = None
    published_at: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)

    def ensure_doc_id(self) -> str:
        """Auto-generate doc_id if missing."""
        if not self.doc_id:
            self.doc_id = generate_doc_id(
                source_type=self.source_type,
                url=self.url,
                title=self.title,
                published_at=self.published_at or "",
                stock_code=self.stock_code or "",
            )
        return self.doc_id


@dataclass
class CollectRequest:
    target: StockTarget
    max_news: int
    forum_pages: int
    chart_pages: int
    from_date: str
    to_date: str
    dart_api_key: str
    theme_key: str
    enabled_sources: List[str] = field(default_factory=lambda: ["news", "dart", "forum"])
    general_news_keywords: Optional[List[str]] = None
    max_general_news: int = 20
    max_reports: int = 10
    report_source: str = "naver"
    report_days_back: int = 180
    report_pages: int = 20
    raw_output_dir: str = field(default_factory=lambda: str(get_data_dir() / "raw"))


@dataclass
class MarketRecord:
    source_type: str
    stock_name: str
    stock_code: str
    timestamp: str
    open: str
    high: str
    low: str
    close: str
    volume: str
    metadata: Dict[str, str] = field(default_factory=dict)
