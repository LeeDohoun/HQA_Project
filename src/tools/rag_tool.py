# 파일: src/tools/rag_tool.py
"""
RAG 검색 도구 모듈 — Canonical Retriever 통합 버전

경로 분리:
  - **주 경로** (canonical): CanonicalRetriever — pipeline 빌드 자산을 직접 소비
  - **레거시 경로** (deprecated): RAGRetriever — ChromaDB 기반, 하위 호환용

get_retriever()는 항상 CanonicalRetriever를 반환합니다.
레거시가 필요한 경우 get_legacy_retriever()를 명시적으로 호출하세요.
"""

from __future__ import annotations

import warnings
from typing import List, Optional

from pydantic import Field

try:
    from crewai.tools import BaseTool
except ImportError:
    BaseTool = object

# ── Canonical Retriever (primary — always available) ──
from src.rag.canonical_retriever import CanonicalRetriever

# ── Legacy RAG Retriever (deprecated, optional) ──
try:
    from src.rag.retriever import RAGRetriever, RetrievalResult

    _LEGACY_RAG_AVAILABLE = True
except ImportError:
    _LEGACY_RAG_AVAILABLE = False

# ── Singletons ──
_canonical_retriever: Optional[CanonicalRetriever] = None
_legacy_retriever = None


# ────────────────────────────────────────────────────
# Public retriever accessors
# ────────────────────────────────────────────────────

def get_canonical_retriever() -> CanonicalRetriever:
    """CanonicalRetriever 싱글톤 인스턴스. 항상 같은 타입 반환."""
    global _canonical_retriever
    if _canonical_retriever is None:
        _canonical_retriever = CanonicalRetriever(data_dir="./data")
    return _canonical_retriever


def get_retriever() -> CanonicalRetriever:
    """주 검색 인터페이스 — 항상 CanonicalRetriever를 반환합니다.

    이전 코드 호환: 반환 타입이 이제 CanonicalRetriever로 고정됩니다.
    legacy ChromaDB 접근이 필요하면 get_legacy_retriever()를 사용하세요.
    """
    return get_canonical_retriever()


def get_legacy_retriever():
    """Deprecated: ChromaDB 기반 레거시 RAGRetriever 인스턴스.

    반환 타입이 CanonicalRetriever와 다르므로 주의하세요.
    canonical index가 운영 중이면 이 함수를 호출할 필요가 없습니다.
    """
    warnings.warn(
        "get_legacy_retriever() is deprecated. "
        "Use get_canonical_retriever() or get_retriever() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    global _legacy_retriever
    if _legacy_retriever is None and _LEGACY_RAG_AVAILABLE:
        _legacy_retriever = RAGRetriever(
            persist_dir="./database/chroma_db",
            collection_name="stock_reports",
            embedding_type="default",
            retrieval_k=20,
            rerank_top_k=3,
            use_reranker=True,
            reranker_model="default",
            reranker_task_type="finance",
        )
    return _legacy_retriever


# ────────────────────────────────────────────────────
# RAGSearchTool (agent-facing tool)
# ────────────────────────────────────────────────────

class RAGSearchTool(BaseTool):
    """Canonical RAG 검색 도구.

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

    def _run(self, query: str) -> str:
        """문서 검색 (source weighting 적용)."""
        canonical = get_canonical_retriever()

        if canonical.available_themes:
            context = canonical.search_for_context(
                query=query,
                top_k=self.top_k,
                source_types=self.source_types,
                intent=self.intent,
            )
            if context and "찾을 수 없습니다" not in context:
                return context

        # Fallback: legacy RAGRetriever (deprecated path)
        if _LEGACY_RAG_AVAILABLE:
            try:
                retriever = get_legacy_retriever()
                if retriever and hasattr(retriever, "retrieve"):
                    result = retriever.retrieve(query=query, k=self.top_k)
                    if result.text_results:
                        parts = ["=== 검색 결과 (Legacy RAG — deprecated) ===\n"]
                        for i, (doc, score) in enumerate(
                            zip(result.text_results, result.scores), 1
                        ):
                            source = doc.metadata.get("source", "unknown")
                            parts.append(
                                f"[문서 {i}] (출처: {source}, "
                                f"source={source}, "
                                f"score={score:.3f})"
                            )
                            parts.append(doc.page_content)
                            parts.append("")
                        return "\n".join(parts)
            except Exception:
                pass

        return "관련 문서를 찾을 수 없습니다."


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
    tool = RAGSearchTool(top_k=k, source_types=source_types, intent=intent)
    return tool._run(query)


# 하위 호환성
search_reports = search_documents
