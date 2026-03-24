from __future__ import annotations

"""하위 호환용 collector export 모듈 (신규 구현: src.ingestion)."""

from src.ingestion.dart import DartDisclosureCollector
from src.ingestion.naver_forum import NaverStockChartCollector, NaverStockForumCollector
from src.ingestion.naver_news import NaverNewsCollector
from src.ingestion.naver_theme import NaverThemeStockCollector, ThemeStock
from src.ingestion.types import DocumentRecord

# Legacy alias
CrawledDocument = DocumentRecord

__all__ = [
    "CrawledDocument",
    "ThemeStock",
    "NaverNewsCollector",
    "DartDisclosureCollector",
    "NaverStockForumCollector",
    "NaverStockChartCollector",
    "NaverThemeStockCollector",
]
