from pathlib import Path


def test_removed_ocr_and_reranker_modules_are_absent():
    evidence_dir = Path("src/evidence")

    assert not (evidence_dir / "reranker.py").exists()
    assert not (evidence_dir / "ocr_provider.py").exists()
    assert not (evidence_dir / "ocr_processor.py").exists()


def test_evidence_public_surface_does_not_export_removed_modules():
    import src.evidence as evidence

    removed_exports = {
        "Qwen3Reranker",
        "RerankResult",
        "RerankerManager",
        "rerank_documents",
        "PaddleOCRProcessor",
        "LegacyOCRProcessor",
        "get_ocr_processor",
        "OCRDocument",
        "OCRPage",
        "check_paddleocr_availability",
    }

    assert removed_exports.isdisjoint(set(evidence.__all__))
    for name in removed_exports:
        assert not hasattr(evidence, name)


def test_canonical_evidence_tool_imports_without_removed_legacy_modules():
    from src.tools.evidence_tool import EvidenceSearchTool, get_retriever

    assert EvidenceSearchTool(top_k=1).top_k == 1
    assert get_retriever().__class__.__name__ == "EvidenceRetriever"


def test_source_code_no_longer_imports_removed_modules():
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src").rglob("*.py")
        if "__pycache__" not in path.parts
    )

    assert "from .ocr_processor import" not in source_text
    assert "from src.evidence.ocr_processor import" not in source_text
    assert "from .reranker import" not in source_text
    assert "from src.evidence.reranker import" not in source_text


def test_legacy_chroma_modules_are_absent():
    legacy_paths = [
        Path("src/evidence/bm25_index.py"),
        Path("src/evidence/document_loader.py"),
        Path("src/evidence/embeddings.py"),
        Path("src/evidence/retriever_legacy.py"),
        Path("src/evidence/text_splitter.py"),
        Path("src/evidence/vector_store.py"),
        Path("src/tools/search_tool.py"),
        Path("src/database/vector_store.py"),
    ]

    for path in legacy_paths:
        assert not path.exists(), f"{path} should be removed"


def test_evidence_public_surface_keeps_only_canonical_and_pipeline_exports():
    import src.evidence as evidence

    removed_exports = {
        "BM25IndexManager",
        "DocumentLoader",
        "EmbeddingManager",
        "PDFProcessor",
        "ProcessedDocument",
        "ProcessedPage",
        "RAGRetriever",
        "ReportVectorStore",
        "RetrievalResult",
        "SemanticTextSplitter",
        "TextChunk",
        "TextSplitter",
        "VectorStoreManager",
    }

    assert removed_exports.isdisjoint(set(evidence.__all__))
    assert {"EvidenceRetriever", "EvidenceIndexBuilder"}.issubset(set(evidence.__all__))


def test_evidence_tool_has_no_legacy_retriever_fallback():
    source = Path("src/tools/evidence_tool.py").read_text(encoding="utf-8")

    assert "get_legacy_retriever" not in source
    assert "RAGRetriever" not in source
    assert "Legacy RAG" not in source
