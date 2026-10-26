from __future__ import annotations

# File role:
# - Classify source types as document sources or market-data sources.

from typing import Iterable, List, Set

DEFAULT_DOCUMENT_SOURCES: Set[str] = {"news", "general_news", "dart", "forum", "report"}
DEFAULT_MARKET_SOURCES: Set[str] = {"chart", "quote", "krx", "fdr"}


def is_market_source(source_type: str) -> bool:
    return (source_type or "").strip().lower() in DEFAULT_MARKET_SOURCES


def is_document_source(source_type: str) -> bool:
    # Allowlist: raw directories such as financials, theme_membership or quarantine
    # hold structured rows, not text, and must never be indexed as empty documents.
    return (source_type or "").strip().lower() in DEFAULT_DOCUMENT_SOURCES


def split_sources(source_types: Iterable[str]) -> tuple[List[str], List[str]]:
    document_sources: List[str] = []
    market_sources: List[str] = []
    for source_type in source_types:
        source = (source_type or "").strip().lower()
        if not source:
            continue
        if is_market_source(source):
            if source not in market_sources:
                market_sources.append(source)
        else:
            if source not in document_sources:
                document_sources.append(source)
    return document_sources, market_sources
