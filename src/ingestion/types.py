from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class StockTarget:
    stock_name: str
    stock_code: str
    corp_code: str = ""


@dataclass
class DocumentRecord:
    source_type: str
    title: str
    content: str
    url: str
    stock_name: Optional[str] = None
    stock_code: Optional[str] = None
    published_at: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)


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
    raw_output_dir: str = "./data/raw"


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
