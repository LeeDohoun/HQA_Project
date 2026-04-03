# 파일: src/tools/rag_tool.py
"""
RAG 검색 도구 모듈 — Canonical Retriever 통합 버전

- 에이전트가 사용하는 유일한 텍스트 검색 진입점
- CanonicalRetriever → 파이프라인 빌드 자산을 직접 소비
- 레거시 ChromaDB RAGRetriever도 폴백으로 지원
"""

from typing import List, Optional
from pydantic import Field

try:
    from crewai.tools import BaseTool
except ImportError:
    BaseTool = object

# ── Canonical Retriever (primary) ──
from src.rag.canonical_retriever import CanonicalRetriever

# ── Legacy RAG Retriever (fallback) ──
try:
    from src.rag.retriever import RAGRetriever, RetrievalResult
    _LEGACY_RAG_AVAILABLE = True
except ImportError:
    _LEGACY_RAG_AVAILABLE = False

# ── Singletons ──
_canonical_retriever: Optional[CanonicalRetriever] = None
_legacy_retriever = None


def get_canonical_retriever() -> CanonicalRetriever:
    """CanonicalRetriever 싱글톤 인스턴스."""
    global _canonical_retriever
    if _canonical_retriever is None:
        _canonical_retriever = CanonicalRetriever(data_dir="./data")
    return _canonical_retriever


def get_retriever():
    """하위 호환: 기존 코드가 get_retriever()를 호출하면 canonical 우선, legacy 폴백."""
    canonical = get_canonical_retriever()
    if canonical.available_themes:
        return canonical

    # Fallback to legacy RAGRetriever
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
    source_types: Optional[List[str]] = Field(default=None, description="Filter by source types")
    intent: Optional[str] = Field(default=None, description="Query intent for source filtering")

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

        # Fallback: legacy RAGRetriever
        if _LEGACY_RAG_AVAILABLE:
            try:
                retriever = get_retriever()
                if retriever and hasattr(retriever, 'retrieve'):
                    result = retriever.retrieve(query=query, k=self.top_k)
                    if result.text_results:
                        parts = ["=== 검색 결과 (Legacy RAG) ===\n"]
                        for i, (doc, score) in enumerate(
                            zip(result.text_results, result.scores), 1
                        ):
                            source = doc.metadata.get("source", "unknown")
                            parts.append(f"[문서 {i}] (출처: {source}, score: {score:.3f})")
                            parts.append(doc.page_content)
                            parts.append("")
                        return "\n".join(parts)
            except Exception:
                pass

        return "관련 문서를 찾을 수 없습니다."


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
