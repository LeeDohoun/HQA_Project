from pathlib import Path


def test_evidence_package_replaces_rag_package_name():
    assert Path("src/evidence").is_dir()
    assert not Path("src/rag").exists()


def test_evidence_tool_replaces_rag_tool_name():
    assert Path("src/tools/evidence_tool.py").exists()
    assert not Path("src/tools/rag_tool.py").exists()


def test_build_evidence_index_script_replaces_build_rag_script():
    assert Path("scripts/data/build.py").exists()
    assert not Path("scripts/build_rag.py").exists()


def test_primary_evidence_imports_are_available():
    from src.evidence.retriever import EvidenceRetriever
    from src.evidence.index_builder import EvidenceIndexBuilder
    from src.tools.evidence_tool import EvidenceSearchTool, get_retriever

    assert get_retriever().__class__ is EvidenceRetriever
    assert EvidenceIndexBuilder.__name__ == "EvidenceIndexBuilder"
    assert EvidenceSearchTool(top_k=1).top_k == 1


def test_source_code_does_not_import_old_rag_package_or_tool():
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [*Path("src").rglob("*.py"), *Path("scripts").rglob("*.py")]
        if "__pycache__" not in path.parts
    )

    assert "src.rag" not in source_text
    assert "rag_tool" not in source_text
    assert "RAGSearchTool" not in source_text
