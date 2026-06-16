# 파일: src/tools/evidence_tool.py
"""Canonical evidence search tool."""

from __future__ import annotations

from typing import List, Optional

try:
    from pydantic import Field
except ImportError:  # pragma: no cover - lightweight runtime fallback
    def Field(default=None, description: str = ""):
        return default

from src.config.settings import get_data_dir

try:
    from crewai.tools import BaseTool
except ImportError:
    class BaseTool:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

from src.evidence.retriever import EvidenceRetriever

_canonical_retriever: Optional[EvidenceRetriever] = None


# ────────────────────────────────────────────────────
# Public retriever accessors
# ────────────────────────────────────────────────────

def get_canonical_retriever() -> EvidenceRetriever:
    """EvidenceRetriever 싱글톤 인스턴스. 항상 같은 타입 반환."""
    global _canonical_retriever
    if _canonical_retriever is None:
        _canonical_retriever = EvidenceRetriever(data_dir=str(get_data_dir()))
    return _canonical_retriever


def reset_retriever_cache(data_dir: Optional[str] = None) -> None:
    global _canonical_retriever
    _canonical_retriever = (
        EvidenceRetriever(data_dir=data_dir) if data_dir else None
    )


def get_retriever() -> EvidenceRetriever:
    """주 검색 인터페이스 — 항상 EvidenceRetriever를 반환합니다."""
    return get_canonical_retriever()


# ────────────────────────────────────────────────────
# EvidenceSearchTool (agent-facing tool)
# ────────────────────────────────────────────────────

class EvidenceSearchTool(BaseTool):
    """Canonical evidence 검색 도구.

    파이프라인이 빌드한 canonical text corpus를 검색합니다.
    source_types 또는 intent를 지정하여 source-aware 검색이 가능합니다.
    """

    name: str = "Document Search"
    description: str = (
        "Search for relevant documents including reports, disclosures, news, and forum posts. "
        "Returns the most relevant content with source weighting applied. "
        "Input: search query (e.g., 'Samsung Electronics HBM earnings forecast')"
    )

    top_k: int = Field(default=5, description="Number of results to return")
    source_types: Optional[List[str]] = Field(
        default=None, description="Filter by source types"
    )
    intent: Optional[str] = Field(
        default=None, description="Query intent for source filtering"
    )
    stock_code: Optional[str] = Field(
        default=None, description="Filter by stock code before retrieval"
    )

    def __init__(
        self,
        top_k: int = 5,
        source_types: Optional[List[str]] = None,
        intent: Optional[str] = None,
        stock_code: Optional[str] = None,
        **kwargs,
    ):
        # Some runtimes instantiate this class without a Pydantic-powered BaseTool,
        # leaving Field(...) descriptors as class attributes. Normalize values here.
        try:
            super().__init__(
                top_k=top_k,
                source_types=source_types,
                intent=intent,
                stock_code=stock_code,
                **kwargs,
            )
        except TypeError:
            try:
                super().__init__(**kwargs)
            except Exception:
                pass

        self.top_k = int(top_k)
        self.source_types = list(source_types) if source_types else None
        self.intent = str(intent) if intent else None
        self.stock_code = str(stock_code).strip() if stock_code else None

    def _run(self, query: str, stock_code: Optional[str] = None) -> str:
        """문서 검색 (source weighting 적용)."""
        canonical = get_canonical_retriever()
        context = canonical.search_for_context(
            query=query,
            top_k=self.top_k,
            source_types=self.source_types,
            intent=self.intent,
            stock_code=stock_code or self.stock_code,
        )
        return context or "관련 문서를 찾을 수 없습니다."


# ────────────────────────────────────────────────────
# Convenience functions
# ────────────────────────────────────────────────────

def search_documents(
    query: str,
    k: int = 3,
    source_types: Optional[List[str]] = None,
    intent: Optional[str] = None,
) -> str:
    """문서 검색 편의 함수."""
    tool = EvidenceSearchTool(top_k=k, source_types=source_types, intent=intent)
    return tool._run(query)


# 하위 호환성
search_reports = search_documents
