# 파일: src/rag/__init__.py
"""
RAG package surface with optional imports.

Heavy OCR/vector dependencies are loaded progressively so lighter modules
such as dedupe/source_registry/retrieval helpers remain importable even
when the full agent RAG stack is not installed.
"""

__all__ = []


def _export(names, namespace):
    globals().update({name: namespace[name] for name in names})
    __all__.extend(names)


from .dedupe import make_document_id, make_market_record_id, make_record_id
from .source_registry import (
    DEFAULT_DOCUMENT_SOURCES,
    DEFAULT_MARKET_SOURCES,
    is_document_source,
    is_market_source,
    split_sources,
)
_export(
    [
        "make_document_id",
        "make_market_record_id",
        "make_record_id",
        "DEFAULT_DOCUMENT_SOURCES",
        "DEFAULT_MARKET_SOURCES",
        "is_document_source",
        "is_market_source",
        "split_sources",
    ],
    locals(),
)

try:
    from .bm25_index import BM25IndexManager, reciprocal_rank_fusion

    _export(
        [
            "BM25IndexManager",
            "reciprocal_rank_fusion",
        ],
        locals(),
    )
except ImportError:
    pass

try:
    from .vector_store import VectorStoreManager, SimpleVectorStore, SourceRAGBuilder

    _export(
        [
            "VectorStoreManager",
            "SimpleVectorStore",
            "SourceRAGBuilder",
        ],
        locals(),
    )
except ImportError:
    pass

try:
    from .ocr_processor import (
        PaddleOCRProcessor,
        LegacyOCRProcessor,
        get_ocr_processor,
        OCRDocument,
        OCRPage,
        check_paddleocr_availability,
    )

    _export(
        [
            "PaddleOCRProcessor",
            "LegacyOCRProcessor",
            "get_ocr_processor",
            "OCRDocument",
            "OCRPage",
            "check_paddleocr_availability",
        ],
        locals(),
    )
except ImportError:
    pass

try:
    from .document_loader import PDFProcessor, DocumentLoader, ProcessedPage, ProcessedDocument

    _export(
        [
            "PDFProcessor",
            "DocumentLoader",
            "ProcessedPage",
            "ProcessedDocument",
        ],
        locals(),
    )
except ImportError:
    pass

try:
    from .text_splitter import TextSplitter, SemanticTextSplitter, TextChunk

    _export(
        [
            "TextSplitter",
            "SemanticTextSplitter",
            "TextChunk",
        ],
        locals(),
    )
except ImportError:
    pass

try:
    from .embeddings import (
        EmbeddingManager,
        TextEmbedding,
        KoreanTextEmbedding,
        SnowflakeArcticEmbedding,
    )

    _export(
        [
            "EmbeddingManager",
            "TextEmbedding",
            "KoreanTextEmbedding",
            "SnowflakeArcticEmbedding",
        ],
        locals(),
    )
except ImportError:
    pass

try:
    from .retriever import RAGRetriever, RetrievalResult, ReportVectorStore

    _export(
        [
            "RAGRetriever",
            "RetrievalResult",
            "ReportVectorStore",
        ],
        locals(),
    )
except ImportError:
    pass

try:
    from .raw_layer2_builder import RawLayer2Builder

    _export(["RawLayer2Builder"], locals())
except ImportError:
    pass

try:
    from .reranker import (
        Qwen3Reranker,
        RerankResult,
        RerankerManager,
        rerank_documents,
    )

    _export(
        [
            "Qwen3Reranker",
            "RerankResult",
            "RerankerManager",
            "rerank_documents",
        ],
        locals(),
    )
except ImportError:
    pass
