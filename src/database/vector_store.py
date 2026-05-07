# 파일: src/database/vector_store.py
"""
하위 호환성을 위한 래퍼 모듈
새로운 RAG 모듈(src/rag/)로 마이그레이션됨

사용법:
    # 기존 방식 (호환)
    from src.database.vector_store import ReportVectorStore
    store = ReportVectorStore()
    
    # 새로운 방식 (권장)
    from src.rag import RAGRetriever
    retriever = RAGRetriever()
"""

# 새로운 RAG 모듈에서 임포트
from src.rag import (
    PDFProcessor,
    DocumentLoader,
    TextSplitter,
    EmbeddingManager,
    VectorStoreManager,
    RAGRetriever
)
from src.rag.retriever import ReportVectorStore

# 기존 코드와의 호환성을 위해 re-export
__all__ = [
    "PDFProcessor",
    "DocumentLoader", 
    "TextSplitter",
    "EmbeddingManager",
    "VectorStoreManager",
    "RAGRetriever",
    "ReportVectorStore"
]
