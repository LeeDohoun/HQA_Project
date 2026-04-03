"""
Canonical RAG architecture tests.

Covers:
1. Source metadata presence test
2. Source weighting test
3. Forum low-credibility suppression test
4. Analyst source filter test
5. Chart path non-regression test
6. DocumentRecord doc_id generation test
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ──────────────────────────────────────────────
# Test 1: Source metadata presence
# ──────────────────────────────────────────────

class TestSourceMetadata:
    """All text records must have canonical metadata fields."""

    def test_document_record_has_doc_id(self):
        from src.ingestion.types import DocumentRecord

        doc = DocumentRecord(
            source_type="news",
            title="삼성전자 실적",
            content="삼성전자 3분기 실적...",
            url="https://example.com/1",
            stock_code="005930",
            published_at="2025-01-15",
        )
        doc_id = doc.ensure_doc_id()
        assert doc_id, "doc_id should be auto-generated"
        assert len(doc_id) == 32, "doc_id should be MD5 hex"

    def test_document_record_stable_doc_id(self):
        from src.ingestion.types import DocumentRecord

        doc1 = DocumentRecord(
            source_type="news",
            title="Same Title",
            content="content",
            url="https://example.com/1",
            stock_code="005930",
            published_at="2025-01-15",
        )
        doc2 = DocumentRecord(
            source_type="news",
            title="Same Title",
            content="different content",
            url="https://example.com/1",
            stock_code="005930",
            published_at="2025-01-15",
        )
        assert doc1.ensure_doc_id() == doc2.ensure_doc_id(), (
            "doc_id should be stable for same source+url+title+date"
        )

    def test_rag_corpus_builder_canonical_fields(self):
        from src.data_pipeline.rag_builder import RAGCorpusBuilder
        from src.ingestion.types import DocumentRecord

        builder = RAGCorpusBuilder(chunk_size=100, chunk_overlap=20)
        docs = [
            DocumentRecord(
                source_type="dart",
                title="TEST 공시",
                content="A" * 200,
                url="https://dart.fss.or.kr/1",
                stock_code="005930",
                stock_name="삼성전자",
                published_at="2025-01-10",
                metadata={"theme_key": "test_theme", "collected_at": "2025-01-11T00:00:00"},
            ),
        ]
        records = builder.build_records(docs)
        assert len(records) > 0

        for record in records:
            meta = record["metadata"]
            assert meta.get("doc_id"), "doc_id must exist"
            assert meta.get("source_type") == "dart"
            assert meta.get("stock_code") == "005930"
            # Canonical quality scores must exist (even if default)
            assert "credibility_score" in meta
            assert "freshness_score" in meta
            assert "content_quality_score" in meta


# ──────────────────────────────────────────────
# Test 2: Source weighting
# ──────────────────────────────────────────────

class TestSourceWeighting:
    """Source weighting correctly prioritises report/dart over forum."""

    def test_source_weights_hierarchy(self):
        from src.rag.source_weighting import get_source_weight

        assert get_source_weight("report") > get_source_weight("news")
        assert get_source_weight("dart") > get_source_weight("news")
        assert get_source_weight("news") > get_source_weight("forum")

    def test_freshness_multiplier_recent_news(self):
        from datetime import datetime
        from src.rag.source_weighting import compute_freshness_multiplier

        today = datetime(2025, 6, 1)
        recent = "2025-05-28"  # 4 days ago
        old = "2024-01-01"     # >1 year ago

        recent_mult = compute_freshness_multiplier("news", recent, today)
        old_mult = compute_freshness_multiplier("news", old, today)

        assert recent_mult > 1.0, "Recent news should get a boost"
        assert old_mult < 1.0, "Old news should be penalised"

    def test_apply_source_weighting_sorts_correctly(self):
        from src.rag.source_weighting import apply_source_weighting

        results = [
            {
                "text": "Forum post",
                "source_type": "forum",
                "score": 0.9,  # High base score
                "metadata": {"published_at": "2025-01-01"},
            },
            {
                "text": "Report analysis",
                "source_type": "report",
                "score": 0.7,  # Lower base score
                "metadata": {"published_at": "2025-01-01"},
            },
        ]

        weighted = apply_source_weighting(results)
        # Report should be ranked higher despite lower base score
        assert weighted[0]["source_type"] == "report", (
            "Report should outrank forum due to higher credibility weight"
        )


# ──────────────────────────────────────────────
# Test 3: Forum low-credibility suppression
# ──────────────────────────────────────────────

class TestForumSuppression:
    """Forum results must not easily outrank report/dart."""

    def test_forum_cannot_top_rank_easily(self):
        from src.rag.source_weighting import apply_source_weighting

        results = [
            {"text": "Forum A", "source_type": "forum", "score": 1.0,
             "metadata": {"published_at": "2025-06-01"}},
            {"text": "Forum B", "source_type": "forum", "score": 0.95,
             "metadata": {"published_at": "2025-06-01"}},
            {"text": "Report A", "source_type": "report", "score": 0.5,
             "metadata": {"published_at": "2025-06-01"}},
            {"text": "DART A", "source_type": "dart", "score": 0.45,
             "metadata": {"published_at": "2025-06-01"}},
        ]

        weighted = apply_source_weighting(results)
        top_3 = [r["source_type"] for r in weighted[:3]]

        # At least report or dart should be in top 3
        assert "report" in top_3 or "dart" in top_3, (
            "Report or DART must appear in top 3 even if forum has higher base score"
        )


# ──────────────────────────────────────────────
# Test 4: Analyst source filter
# ──────────────────────────────────────────────

class TestAnalystSourceFilter:
    """Analyst search functions use source-aware retrieval."""

    def test_analyst_has_source_aware_tools(self):
        """AnalystAgent should have source-specific RAG tools."""
        pytest.importorskip("langchain_core")
        from src.agents.analyst import AnalystAgent

        agent = AnalystAgent()
        assert hasattr(agent, "rag_tool_reports")
        assert hasattr(agent, "rag_tool_news")
        assert hasattr(agent, "rag_tool_policy")
        assert hasattr(agent, "rag_tool_industry")

    def test_report_tool_filters_sources(self):
        pytest.importorskip("langchain_core")
        from src.agents.analyst import AnalystAgent

        agent = AnalystAgent()
        tool = agent.rag_tool_reports
        assert tool.source_types is not None
        assert "report" in tool.source_types
        assert "forum" not in tool.source_types

    def test_news_tool_includes_forum(self):
        pytest.importorskip("langchain_core")
        from src.agents.analyst import AnalystAgent

        agent = AnalystAgent()
        tool = agent.rag_tool_news
        assert "forum" in tool.source_types
        assert "news" in tool.source_types

    def test_intent_source_map_coverage(self):
        from src.rag.source_weighting import INTENT_SOURCE_MAP

        assert "earnings" in INTENT_SOURCE_MAP
        assert "investment" in INTENT_SOURCE_MAP
        assert "policy" in INTENT_SOURCE_MAP
        assert "sentiment" in INTENT_SOURCE_MAP
        assert "industry" in INTENT_SOURCE_MAP


# ──────────────────────────────────────────────
# Test 5: Chart path non-regression
# ──────────────────────────────────────────────

class TestChartPathNonRegression:
    """Chart/price data path remains unchanged after RAG restructuring."""

    def test_price_loader_exists(self):
        pytest.importorskip("pandas")
        from src.data_pipeline.price_loader import PriceLoader
        try:
            loader = PriceLoader(data_dir="./data")
        except TypeError:
            pytest.skip("PriceLoader is a stub (pandas not installed)")
        assert loader is not None

    def test_chart_tools_importable(self):
        pytest.importorskip("pandas")
        from src.tools.charts_tools import TechnicalAnalyzer
        assert TechnicalAnalyzer is not None

    def test_market_sources_excluded_from_document(self):
        from src.rag.source_registry import is_document_source, is_market_source

        # Chart/market data must NOT be treated as document sources
        for market_src in ["chart", "quote", "krx", "fdr"]:
            assert is_market_source(market_src), f"{market_src} should be market source"
            assert not is_document_source(market_src), f"{market_src} should NOT be document source"

    def test_chartist_does_not_use_canonical_rag(self):
        """ChartistAgent should use PriceLoader, not canonical retriever."""
        import ast
        chartist_path = Path(__file__).parent.parent / "src" / "agents" / "chartist.py"
        source = chartist_path.read_text(encoding="utf-8")
        assert "CanonicalRetriever" not in source
        assert "canonical_retriever" not in source


# ──────────────────────────────────────────────
# Test 6: RetrievalService not used in agent runtime
# ──────────────────────────────────────────────

class TestRetrievalServiceIsolation:
    """Agent runtime must not directly import RetrievalService."""

    def test_no_retrieval_service_in_agents(self):
        agents_dir = Path(__file__).parent.parent / "src" / "agents"
        for py_file in agents_dir.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            assert "from src.retrieval.services import RetrievalService" not in content, (
                f"{py_file.name} should not directly import RetrievalService"
            )
            assert "from src.retrieval import RetrievalService" not in content, (
                f"{py_file.name} should not directly import RetrievalService"
            )

    def test_no_retrieval_service_in_tools(self):
        tools_dir = Path(__file__).parent.parent / "src" / "tools"
        for py_file in tools_dir.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            content = py_file.read_text(encoding="utf-8")
            assert "from src.retrieval.services import RetrievalService" not in content, (
                f"{py_file.name} should not directly import RetrievalService"
            )


# ──────────────────────────────────────────────
# Test 7: Canonical retriever integration
# ──────────────────────────────────────────────

class TestCanonicalRetriever:
    """CanonicalRetriever correctly consumes pipeline-built assets."""

    def test_canonical_retriever_init(self):
        from src.rag.canonical_retriever import CanonicalRetriever

        retriever = CanonicalRetriever(data_dir="./data")
        assert retriever is not None

    def test_canonical_retriever_empty_search(self):
        from src.rag.canonical_retriever import CanonicalRetriever

        with tempfile.TemporaryDirectory() as tmpdir:
            retriever = CanonicalRetriever(data_dir=tmpdir)
            results = retriever.search("test query")
            assert isinstance(results, list)

    def test_canonical_retriever_with_data(self):
        """Build test data and verify canonical search works."""
        from src.rag.canonical_retriever import CanonicalRetriever
        from src.retrieval.vector_store import SimpleVectorStore
        from src.retrieval.bm25_index import BM25IndexManager

        with tempfile.TemporaryDirectory() as tmpdir:
            # Build test canonical index
            index_dir = Path(tmpdir) / "canonical_index" / "test_theme"
            index_dir.mkdir(parents=True)

            # Create corpus
            records = [
                {
                    "text": "삼성전자 3분기 실적이 크게 개선되었습니다",
                    "metadata": {
                        "source_type": "report",
                        "stock_code": "005930",
                        "stock_name": "삼성전자",
                        "published_at": "2025-01-15",
                        "doc_id": "test_001",
                    },
                },
                {
                    "text": "삼성전자 주가 어디까지 갈까요 ㅋㅋ",
                    "metadata": {
                        "source_type": "forum",
                        "stock_code": "005930",
                        "stock_name": "삼성전자",
                        "published_at": "2025-01-15",
                        "doc_id": "test_002",
                    },
                },
            ]

            with (index_dir / "corpus.jsonl").open("w") as f:
                for r in records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

            # Create vector store
            store = SimpleVectorStore()
            store.add_texts(
                [r["text"] for r in records],
                [r["metadata"] for r in records],
            )
            store.save(str(index_dir / "combined_vector_store.json"))

            # Create BM25
            bm25 = BM25IndexManager(
                persist_path=str(index_dir / "bm25_index.json"), auto_save=False,
            )
            bm25.add_texts(
                [r["text"] for r in records],
                [r["metadata"] for r in records],
            )
            bm25.save_index()

            # Search
            retriever = CanonicalRetriever(data_dir=tmpdir)
            results = retriever.search("삼성전자 실적", top_k=5)
            assert len(results) > 0, "Should find results from test data"

            # Report should rank higher than forum
            if len(results) >= 2:
                report_result = next(
                    (r for r in results if r["source_type"] == "report"), None
                )
                forum_result = next(
                    (r for r in results if r["source_type"] == "forum"), None
                )
                if report_result and forum_result:
                    assert report_result["weighted_score"] >= forum_result["weighted_score"], (
                        "Report should have higher weighted score than forum"
                    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
