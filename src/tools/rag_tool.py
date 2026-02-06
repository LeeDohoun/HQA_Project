# 파일: src/tools/rag_tool.py
"""
RAG 검색 도구 모듈
- 증권사 리포트 검색 (리랭킹 포함)
- CrewAI 에이전트에서 도구로 사용
"""

from typing import Optional
from crewai.tools import BaseTool
from pydantic import Field

from src.rag import RAGRetriever, RetrievalResult


# 전역 Retriever 인스턴스 (싱글톤)
_retriever_instance: Optional[RAGRetriever] = None


def get_retriever() -> RAGRetriever:
    """RAGRetriever 싱글톤 인스턴스 반환"""
    global _retriever_instance
    if _retriever_instance is None:
        _retriever_instance = RAGRetriever(
            persist_dir="./database/chroma_db",
            collection_name="stock_reports",
            embedding_type="default",
            # 검색 설정
            retrieval_k=20,       # 벡터 검색 후보 수
            rerank_top_k=3,       # 리랭킹 후 최종 반환 수
            use_reranker=True,    # 리랭커 사용
            # 리랭커 설정
            reranker_model="default",
            reranker_task_type="finance",
        )
    return _retriever_instance


class RAGSearchTool(BaseTool):
    """
    RAG 검색 도구 (리랭킹 포함)
    
    모든 문서(리포트, 공시, 뉴스 등)가 하나의 벡터 공간에 저장됨.
    쿼리에 따라 자동으로 관련 문서를 찾아 리랭킹 후 반환.
    """
    
    name: str = "Document Search"
    description: str = (
        "Search for relevant documents including securities reports, disclosures, and financial analysis. "
        "Returns the most relevant content with reranking applied. "
        "Input: search query (e.g., 'Samsung Electronics HBM earnings forecast')"
    )
    
    top_k: int = Field(default=3, description="Number of results to return")

    def _run(self, query: str) -> str:
        """
        문서 검색 (리랭킹 적용)
        
        Args:
            query: 검색 쿼리
            
        Returns:
            검색된 문서 컨텍스트
        """
        retriever = get_retriever()
        
        result: RetrievalResult = retriever.retrieve(
            query=query,
            k=self.top_k,
        )
        
        if not result.text_results:
            return "관련 문서를 찾을 수 없습니다."
        
        # 컨텍스트 구성
        parts = []
        status = "(리랭킹 적용)" if result.is_reranked else ""
        parts.append(f"=== 검색 결과 {status} ===\n")
        
        for i, (doc, score) in enumerate(zip(result.text_results, result.scores), 1):
            source = doc.metadata.get("source", "unknown")
            page = doc.metadata.get("page_num", "?")
            score_str = f", 관련도: {score:.3f}" if result.is_reranked else ""
            
            parts.append(f"[문서 {i}] (출처: {source}, 페이지: {page}{score_str})")
            parts.append(doc.page_content)
            parts.append("")
        
        return "\n".join(parts)


def search_documents(query: str, k: int = 3) -> str:
    """
    문서 검색 편의 함수
    
    Args:
        query: 검색 쿼리
        k: 반환할 결과 수
        
    Returns:
        검색된 컨텍스트 문자열
    """
    tool = RAGSearchTool(top_k=k)
    return tool._run(query)


# 하위 호환성
search_reports = search_documents