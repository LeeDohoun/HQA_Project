"""
Canonical RAG architecture tests — behavioral verification.

Covers:
1. Source metadata presence & promotion
2. Source weighting behavioral correctness
3. Forum low-credibility suppression
4. Analyst source filter verification
5. Chart path non-regression
6. Source extraction compatibility (canonical + legacy format)
7. Metadata promotion from nested dict
8. Theme target reuse
9. Canonical fallback behavior
10. RetrievalService isolation
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ──────────────────────────────────────────────
# Test 1: Source metadata presence & promotion
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
            assert "credibility_score" in meta
            assert "freshness_score" in meta
            assert "content_quality_score" in meta


# ──────────────────────────────────────────────
# Test 2: Source weighting behavioral correctness
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
                "score": 0.9,
                "metadata": {"published_at": "2025-01-01"},
            },
            {
                "text": "Report analysis",
                "source_type": "report",
                "score": 0.7,
                "metadata": {"published_at": "2025-01-01"},
            },
        ]

        weighted = apply_source_weighting(results)
        assert weighted[0]["source_type"] == "report", (
            "Report should outrank forum due to higher credibility weight"
        )

    def test_weighted_score_formula(self):
        """weighted_score = base_score * credibility * freshness_mult."""
        from datetime import datetime
        from src.rag.source_weighting import apply_source_weighting

        results = [
            {
                "text": "test",
                "source_type": "dart",
                "score": 1.0,
                "metadata": {"published_at": "2025-06-01"},
            },
        ]
        ref = datetime(2025, 6, 5)  # 4 days later, within boost range
        weighted = apply_source_weighting(results, reference_date=ref)
        r = weighted[0]

        # Must have all three component fields
        assert "weighted_score" in r
        assert "credibility_weight" in r
        assert "freshness_multiplier" in r

        expected = r["score"] * r["credibility_weight"] * r["freshness_multiplier"]
        assert abs(r["weighted_score"] - expected) < 1e-6


# ──────────────────────────────────────────────
# Test 3: Forum low-credibility suppression
# ──────────────────────────────────────────────

class TestForumSuppression:
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
        assert "report" in top_3 or "dart" in top_3


# ──────────────────────────────────────────────
# Test 4: Analyst source filter verification
# ──────────────────────────────────────────────

class TestAnalystSourceFilter:
    def test_analyst_has_source_aware_tools(self):
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

        for market_src in ["chart", "quote", "krx", "fdr"]:
            assert is_market_source(market_src), f"{market_src} should be market source"
            assert not is_document_source(market_src)

    def test_chartist_does_not_use_canonical_rag(self):
        chartist_path = Path(__file__).parent.parent / "src" / "agents" / "chartist.py"
        source = chartist_path.read_text(encoding="utf-8")
        assert "CanonicalRetriever" not in source
        assert "canonical_retriever" not in source


# ──────────────────────────────────────────────
# Test 6: Source extraction (canonical + legacy)
# ──────────────────────────────────────────────

class TestSourceExtraction:
    """_extract_sources must handle both canonical (source=xxx) and legacy (출처:) formats."""

    def _get_extractor(self):
        """Create a standalone extraction function matching analyst.py logic."""
        def extract(context: str) -> list:
            sources = []
            for line in context.split("\n"):
                m = re.search(r'source=([a-z_]+)', line)
                if m:
                    src = m.group(1).strip()
                    if src and src not in sources:
                        sources.append(src)
                    continue
                if "출처:" in line:
                    try:
                        src = line.split("출처:")[1].split(",")[0].strip().rstrip(")")
                        if src and src not in sources:
                            sources.append(src)
                    except Exception:
                        pass
            return sources
        return extract

    def test_canonical_format(self):
        extract = self._get_extractor()
        context = "[문서 1] (출처: report, source=report, score=0.9)"
        sources = extract(context)
        assert "report" in sources

    def test_legacy_format(self):
        extract = self._get_extractor()
        context = "[1] (출처: unknown, 페이지: 3)"
        sources = extract(context)
        assert "unknown" in sources

    def test_mixed_format(self):
        extract = self._get_extractor()
        context = (
            "[문서 1] (출처: report, source=report, score=0.9)\n"
            "content...\n"
            "[2] (출처: web_search, 페이지: ?)\n"
        )
        sources = extract(context)
        assert "report" in sources
        assert "web_search" in sources

    def test_no_sources(self):
        extract = self._get_extractor()
        assert extract("no source info here") == []

    def test_canonical_retriever_output_format(self):
        """search_for_context must emit both source= and 출처: tokens."""
        from src.rag.canonical_retriever import CanonicalRetriever
        from src.retrieval.vector_store import SimpleVectorStore
        from src.retrieval.bm25_index import BM25IndexManager

        with tempfile.TemporaryDirectory() as tmpdir:
            index_dir = Path(tmpdir) / "canonical_index" / "test"
            index_dir.mkdir(parents=True)

            records = [
                {
                    "text": "테스트 문서입니다",
                    "metadata": {
                        "source_type": "report",
                        "stock_code": "005930",
                        "stock_name": "삼성전자",
                        "published_at": "2025-01-15",
                        "doc_id": "t1",
                    },
                },
            ]

            with (index_dir / "corpus.jsonl").open("w") as f:
                for r in records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

            store = SimpleVectorStore()
            store.add_texts(
                [r["text"] for r in records],
                [r["metadata"] for r in records],
            )
            store.save(str(index_dir / "combined_vector_store.json"))

            bm25 = BM25IndexManager(
                persist_path=str(index_dir / "bm25_index.json"), auto_save=False,
            )
            bm25.add_texts(
                [r["text"] for r in records],
                [r["metadata"] for r in records],
            )
            bm25.save_index()

            retriever = CanonicalRetriever(data_dir=tmpdir)
            context = retriever.search_for_context("테스트", top_k=5)

            # Must contain both formats
            assert "source=" in context, "Output must contain canonical source= token"
            assert "출처:" in context, "Output must contain legacy 출처: token"


# ──────────────────────────────────────────────
# Test 7: Metadata promotion from nested dict
# ──────────────────────────────────────────────

class TestMetadataPromotion:
    """Canonical fields from nested metadata dict must be promoted."""

    def test_theme_key_preserved(self):
        from src.data_pipeline.rag_builder import RAGCorpusBuilder
        from src.ingestion.types import DocumentRecord

        builder = RAGCorpusBuilder(chunk_size=500, chunk_overlap=50)
        docs = [
            DocumentRecord(
                source_type="news",
                title="Test",
                content="Content " * 50,
                url="https://example.com/1",
                stock_code="005930",
                stock_name="삼성전자",
                published_at="2025-01-10",
                metadata={
                    "theme_key": "반도체",
                    "collected_at": "2025-01-11T12:00:00",
                    "credibility_score": 0.8,
                    "freshness_score": 0.9,
                    "content_quality_score": 0.7,
                },
            ),
        ]
        records = builder.build_records(docs)
        assert len(records) > 0

        meta = records[0]["metadata"]
        assert meta.get("theme_key") == "반도체", (
            "theme_key must be promoted from nested metadata"
        )
        assert meta.get("collected_at") == "2025-01-11T12:00:00", (
            "collected_at must be promoted"
        )
        assert meta.get("credibility_score") == 0.8, (
            "credibility_score must be promoted"
        )
        assert meta.get("freshness_score") == 0.9, (
            "freshness_score must be promoted"
        )
        assert meta.get("content_quality_score") == 0.7, (
            "content_quality_score must be promoted"
        )

    def test_no_nested_metadata_key(self):
        """After promotion, there should be no nested 'metadata' key."""
        from src.data_pipeline.rag_builder import RAGCorpusBuilder
        from src.ingestion.types import DocumentRecord

        builder = RAGCorpusBuilder(chunk_size=500, chunk_overlap=50)
        docs = [
            DocumentRecord(
                source_type="dart",
                title="Test",
                content="Content " * 50,
                url="https://example.com/2",
                metadata={"theme_key": "test", "extra_field": "value"},
            ),
        ]
        records = builder.build_records(docs)
        meta = records[0]["metadata"]
        assert "metadata" not in meta, (
            "Nested 'metadata' key must be removed after promotion"
        )


# ──────────────────────────────────────────────
# Test 8: Theme target reuse
# ──────────────────────────────────────────────

class TestThemeTargetReuse:
    """theme_targets store must support save + load + reuse."""

    def test_save_and_load_roundtrip(self):
        from src.ingestion.theme_targets import ThemeTargetStore
        from src.ingestion.types import StockTarget

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ThemeTargetStore(data_dir=tmpdir)
            targets = [
                StockTarget(stock_name="삼성전자", stock_code="005930"),
                StockTarget(stock_name="SK하이닉스", stock_code="000660"),
            ]
            saved = store.save_targets("test_theme", targets, theme_name="테스트")
            assert len(saved) == 2

            loaded = store.load_targets("test_theme")
            assert len(loaded) == 2
            codes = {t.stock_code for t in loaded}
            assert "005930" in codes
            assert "000660" in codes

    def test_meta_file_created(self):
        from src.ingestion.theme_targets import ThemeTargetStore
        from src.ingestion.types import StockTarget

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ThemeTargetStore(data_dir=tmpdir)
            store.save_targets(
                "meta_test",
                [StockTarget(stock_name="테스트", stock_code="999999")],
            )
            meta_path = store.get_meta_path("meta_test")
            assert meta_path.exists()

            with meta_path.open("r") as f:
                meta = json.load(f)
            assert meta["theme_key"] == "meta_test"
            assert meta["target_count"] == 1

    def test_append_mode_deduplicates(self):
        from src.ingestion.theme_targets import ThemeTargetStore
        from src.ingestion.types import StockTarget

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ThemeTargetStore(data_dir=tmpdir)
            store.save_targets(
                "dedup_test",
                [StockTarget(stock_name="삼성전자", stock_code="005930")],
                mode="overwrite",
            )
            store.save_targets(
                "dedup_test",
                [
                    StockTarget(stock_name="삼성전자", stock_code="005930"),
                    StockTarget(stock_name="SK하이닉스", stock_code="000660"),
                ],
                mode="append",
            )
            loaded = store.load_targets("dedup_test")
            assert len(loaded) == 2  # Not 3


# ──────────────────────────────────────────────
# Test 9: Canonical fallback behavior
# ──────────────────────────────────────────────

class TestCanonicalFallback:
    """CanonicalRetriever must gracefully handle missing data."""

    def test_empty_data_dir_returns_empty(self):
        from src.rag.canonical_retriever import CanonicalRetriever

        with tempfile.TemporaryDirectory() as tmpdir:
            retriever = CanonicalRetriever(data_dir=tmpdir)
            results = retriever.search("test query")
            assert isinstance(results, list)
            assert len(results) == 0

    def test_context_returns_not_found_message(self):
        from src.rag.canonical_retriever import CanonicalRetriever

        with tempfile.TemporaryDirectory() as tmpdir:
            retriever = CanonicalRetriever(data_dir=tmpdir)
            context = retriever.search_for_context("test")
            assert "찾을 수 없습니다" in context

    def test_available_themes_empty(self):
        from src.rag.canonical_retriever import CanonicalRetriever

        with tempfile.TemporaryDirectory() as tmpdir:
            retriever = CanonicalRetriever(data_dir=tmpdir)
            assert retriever.available_themes == []

    def test_search_with_source_filter(self):
        """Source filter must correctly narrow results."""
        from src.rag.canonical_retriever import CanonicalRetriever
        from src.retrieval.vector_store import SimpleVectorStore
        from src.retrieval.bm25_index import BM25IndexManager

        with tempfile.TemporaryDirectory() as tmpdir:
            index_dir = Path(tmpdir) / "canonical_index" / "filter_test"
            index_dir.mkdir(parents=True)

            records = [
                {"text": "Report content",
                 "metadata": {"source_type": "report", "doc_id": "r1",
                              "published_at": "2025-01-15"}},
                {"text": "Forum content",
                 "metadata": {"source_type": "forum", "doc_id": "f1",
                              "published_at": "2025-01-15"}},
                {"text": "News content",
                 "metadata": {"source_type": "news", "doc_id": "n1",
                              "published_at": "2025-01-15"}},
            ]

            with (index_dir / "corpus.jsonl").open("w") as f:
                for r in records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

            store = SimpleVectorStore()
            store.add_texts(
                [r["text"] for r in records],
                [r["metadata"] for r in records],
            )
            store.save(str(index_dir / "combined_vector_store.json"))

            bm25 = BM25IndexManager(
                persist_path=str(index_dir / "bm25_index.json"), auto_save=False,
            )
            bm25.add_texts(
                [r["text"] for r in records],
                [r["metadata"] for r in records],
            )
            bm25.save_index()

            retriever = CanonicalRetriever(data_dir=tmpdir)

            # Filter to report only
            results = retriever.search("content", source_types=["report"], top_k=10)
            source_types = {r["source_type"] for r in results}
            assert "forum" not in source_types, "Forum must be filtered out"
            assert "report" in source_types, "Report must remain"


# ──────────────────────────────────────────────
# Test 10: RetrievalService isolation
# ──────────────────────────────────────────────

class TestRetrievalServiceIsolation:
    def test_no_retrieval_service_in_agents(self):
        agents_dir = Path(__file__).parent.parent / "src" / "agents"
        for py_file in agents_dir.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            assert "from src.retrieval.services import RetrievalService" not in content
            assert "from src.retrieval import RetrievalService" not in content

    def test_no_retrieval_service_in_tools(self):
        tools_dir = Path(__file__).parent.parent / "src" / "tools"
        for py_file in tools_dir.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            content = py_file.read_text(encoding="utf-8")
            assert "from src.retrieval.services import RetrievalService" not in content


# ──────────────────────────────────────────────
# Test 11: get_retriever() type contract
# ──────────────────────────────────────────────

class TestRetrieverTypeContract:
    """get_retriever() must always return CanonicalRetriever."""

    def test_get_retriever_returns_canonical(self):
        from src.tools.rag_tool import get_retriever
        from src.rag.canonical_retriever import CanonicalRetriever

        retriever = get_retriever()
        assert isinstance(retriever, CanonicalRetriever), (
            "get_retriever() must always return CanonicalRetriever"
        )

    def test_get_canonical_retriever_singleton(self):
        from src.tools.rag_tool import get_canonical_retriever

        r1 = get_canonical_retriever()
        r2 = get_canonical_retriever()
        assert r1 is r2, "Must return the same singleton instance"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
