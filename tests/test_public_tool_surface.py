from src import tools


def test_tools_public_api_is_limited_to_primary_facades():
    assert tools.__all__ == [
        "rag",
        "web_search",
        "finance",
        "chart",
        "realtime",
    ]


def test_legacy_search_tool_is_not_part_of_public_api():
    public_names = set(tools.__all__)

    assert "StockReportSearchTool" not in public_names
    assert "MultimodalReportSearchTool" not in public_names
    assert "ReportImageSearchTool" not in public_names
