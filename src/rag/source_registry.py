from __future__ import annotations

from typing import Iterable, List, Set

DEFAULT_DOCUMENT_SOURCES: Set[str] = {"news", "general_news", "dart", "forum", "report"}
DEFAULT_MARKET_SOURCES: Set[str] = {"chart", "quote", "krx", "fdr"}


def is_market_source(source_type: str) -> bool:
    return (source_type or "").strip().lower() in DEFAULT_MARKET_SOURCES


def is_document_source(source_type: str) -> bool:
    source = (source_type or "").strip().lower()
    if not source:
        return False
    if source in DEFAULT_MARKET_SOURCES:
        return False
    return True


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
