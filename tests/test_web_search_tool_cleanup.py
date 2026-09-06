from pathlib import Path


def test_web_search_tool_module_is_removed():
    assert not Path("src/tools/web_search_tool.py").exists()


def test_tools_public_surface_excludes_web_search():
    from src import tools

    assert "web_search" not in tools.__all__
    assert not hasattr(tools, "web_search")
    assert not hasattr(tools, "WebSearchTool")
    assert not hasattr(tools, "NewsSearchTool")
    assert not hasattr(tools, "StockNewsSearchTool")


def test_source_code_no_longer_imports_web_search_tool():
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src").rglob("*.py")
        if "__pycache__" not in path.parts
    )

    assert "web_search_tool" not in source_text
