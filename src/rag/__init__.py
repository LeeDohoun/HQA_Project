# 파일: src/rag/__init__.py
"""
RAG (Retrieval-Augmented Generation) 모듈 (텍스트 전용)

구성요소:
- ocr_processor: PaddleOCR-VL-1.5 기반 문서 OCR
- document_loader: 문서 로딩 및 전처리
- text_splitter: 텍스트 청킹
- embeddings: 텍스트 임베딩 모델
- vector_store: 벡터 저장소
- bm25_index: BM25 키워드 검색 인덱스
- retriever: 검색 및 컨텍스트 구성 (Hybrid Search)

변경사항 (v0.3.0):
- BM25 Hybrid Search 추가 (벡터 + 키워드 검색 병합)
- Reciprocal Rank Fusion (RRF) 결과 병합
- BM25 인덱스 영속화 (JSON)

변경사항 (v0.2.0):
- PaddleOCR-VL-1.5로 모든 문서를 텍스트로 변환
- 멀티모달 임베딩 제거 (텍스트 임베딩만 사용)
- 이미지 저장소 제거
"""

# OCR 프로세서
from .ocr_processor import (
    PaddleOCRProcessor,
    LegacyOCRProcessor,
    get_ocr_processor,
    OCRDocument,
    OCRPage,
    check_paddleocr_availability
)

# 문서 로더
from .document_loader import PDFProcessor, DocumentLoader, ProcessedPage, ProcessedDocument

# 텍스트 분할
from .text_splitter import TextSplitter, SemanticTextSplitter, TextChunk

# 임베딩 (텍스트 전용)
from .embeddings import (
    EmbeddingManager, 
    TextEmbedding, 
    KoreanTextEmbedding,
    SnowflakeArcticEmbedding,
    SnowflakeArcticLangChainWrapper
)

# 벡터 저장소
from .vector_store import VectorStoreManager

# BM25 키워드 검색
from .bm25_index import BM25IndexManager, reciprocal_rank_fusion

# 검색기
from .retriever import RAGRetriever, RetrievalResult, ReportVectorStore

# 리랭커
from .reranker import (
    Qwen3Reranker,
    RerankResult,
    RerankerManager,
    rerank_documents
)

__all__ = [
    # OCR Processor
    "PaddleOCRProcessor",
    "LegacyOCRProcessor",
    "get_ocr_processor",
    "OCRDocument",
    "OCRPage",
    "check_paddleocr_availability",
    # Document Loader
    "PDFProcessor",
    "DocumentLoader",
    "ProcessedPage",
    "ProcessedDocument",
    # Text Splitter
    "TextSplitter",
    "SemanticTextSplitter",
    "TextChunk",
    # Embeddings
    "EmbeddingManager",
    "TextEmbedding",
    "KoreanTextEmbedding",
    "SnowflakeArcticEmbedding",
    "SnowflakeArcticLangChainWrapper",
    # Vector Store
    "VectorStoreManager",
    # BM25
    "BM25IndexManager",
    "reciprocal_rank_fusion",
    # Retriever
    "RAGRetriever",
    "RetrievalResult",
    "ReportVectorStore",
    # Reranker
    "Qwen3Reranker",
    "RerankResult",
    "RerankerManager",
    "rerank_documents",
]
