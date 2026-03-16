# 파일: src/data_pipeline/__init__.py
"""
데이터 파이프라인 모듈

구성요소:
- price_loader: 주가 데이터 로더 (FinanceDataReader)
- dart_collector: DART 공시 수집기
- news_crawler: 뉴스 크롤러
- crawler: 증권사 리포트 크롤러
- data_ingestion: 통합 수집 → 저장 → RAG 파이프라인
- scheduler: 자동화 스케줄러 (주기적 수집)
"""

from .price_loader import PriceLoader
from .dart_collector import DARTCollector, Disclosure
from .news_crawler import NewsCrawler, NewsArticle
from .crawler import ReportCrawler, Report
from .data_ingestion import DataIngestionPipeline
from .scheduler import PipelineScheduler, Watchlist, ScheduleConfig, run_once

__all__ = [
    # 주가 데이터
    "PriceLoader",
    # DART 공시
    "DARTCollector",
    "Disclosure",
    # 뉴스
    "NewsCrawler",
    "NewsArticle",
    # 증권사 리포트
    "ReportCrawler",
    "Report",
    # 통합 파이프라인
    "DataIngestionPipeline",
    # 스케줄러
    "PipelineScheduler",
    "Watchlist",
    "ScheduleConfig",
    "run_once",
]
