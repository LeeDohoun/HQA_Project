from __future__ import annotations

# File role:
# - Canonical retriever that unifies pipeline-built text assets with agent consumption.
# - This is the SINGLE retrieval entry point for all agent-side text search.
# - Consumes data from data/canonical_index/<theme>/ (synced by RawLayer2Builder).
# - Falls back to src/retrieval for pipeline-level search, and to src/rag/retriever for
#   legacy ChromaDB-based search.

"""Canonical RAG retriever — unified text retrieval for agent runtime."""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from src.config.settings import get_data_dir
from src.rag.source_weighting import (
    apply_source_weighting,
    get_intent_sources,
    get_source_weight,
)
from src.retrieval.bm25_index import BM25IndexManager
from src.retrieval.vector_store import SimpleVectorStore

logger = logging.getLogger(__name__)


class CanonicalRetriever:
    """Unified text retrieval gateway for the agent runtime.

    Search flow:
        1. Load canonical vector store and BM25 index
        2. Run hybrid search (vector + BM25 + RRF)
        3. Apply source weighting / freshness decay
        4. Filter by source types (if specified)
        5. Return top-k results

    Usage:
        retriever = CanonicalRetriever(data_dir="./data")
        results = retriever.search("삼성전자 실적 전망", top_k=5)
    """

    def __init__(
        self,
        data_dir: str | None = None,
        theme_key: Optional[str] = None,
    ):
        self.data_dir = Path(data_dir) if data_dir else get_data_dir()
        self.canonical_root = self.data_dir / "canonical_index"
        self.theme_key = theme_key

        # Lazy-loaded stores per theme
        self._vector_stores: Dict[str, SimpleVectorStore] = {}
        self._bm25_indexes: Dict[str, BM25IndexManager] = {}
        self._corpus_cache: Dict[str, List[Dict]] = {}

    @property
    def available_themes(self) -> List[str]:
        """Return list of themes that have canonical indexes."""
        if not self.canonical_root.exists():
            return []
        return [
            d.name for d in self.canonical_root.iterdir()
            if d.is_dir() and (d / "corpus.jsonl").exists()
        ]

    def search(
        self,
        query: str,
        top_k: int = 10,
        source_types: Optional[List[str]] = None,
        intent: Optional[str] = None,
        theme_key: Optional[str] = None,
    ) -> List[Dict]:
        """Search the canonical text corpus.

        Args:
            query: Search query text
            top_k: Number of results to return
            source_types: Filter by specific source types (e.g., ["report", "dart"])
            intent: Query intent for automatic source filtering
            theme_key: Target theme (None = search all themes)

        Returns:
            List of result dicts with keys: text, metadata, source_type, score, weighted_score
        """
        target_theme = theme_key or self.theme_key
        themes = [target_theme] if target_theme else self.available_themes

        # Determine source filter
        effective_source_filter = set()
        if source_types:
            effective_source_filter = {s.strip().lower() for s in source_types}
        elif intent:
            effective_source_filter = set(get_intent_sources(intent))

        if not themes:
            logger.warning("Canonical index 없음 → pipeline 폴백 검색 시도")
            return self._fallback_legacy_search(query, top_k, effective_source_filter)

        # Collect results from all themes
        all_results: List[Dict] = []
        for theme in themes:
            theme_results = self._search_theme(
                query=query,
                theme_key=theme,
                top_k=top_k * 3,  # Over-fetch for post-filtering
                source_filter=effective_source_filter,
            )
            all_results.extend(theme_results)

        if not all_results:
            # Fall back to legacy retrieval
            logger.info("Canonical index 결과 없음 → 레거시 검색 시도")
            all_results = self._fallback_legacy_search(query, top_k, effective_source_filter)

        # Apply source weighting
        weighted = apply_source_weighting(all_results)

        return weighted[:top_k]

    def search_for_context(
        self,
        query: str,
        top_k: int = 5,
        source_types: Optional[List[str]] = None,
        intent: Optional[str] = None,
    ) -> str:
        """Search and return formatted context string for LLM consumption.

        Output format is designed to be parseable by both:
          - canonical: regex `source=xxx`
          - legacy: split on `출처:`
        """
        results = self.search(
            query=query, top_k=top_k,
            source_types=source_types, intent=intent,
        )

        if not results:
            status = self.describe_data_state()
            if status["raw_available"] and not status["retrieval_assets_available"]:
                return (
                    "관련 문서를 찾을 수 없습니다. "
                    "raw 데이터는 있지만 retrieval 인덱스가 없습니다. "
                    "`python3 scripts/build_rag.py --theme-key <theme> --data-dir "
                    f"{self.data_dir}` 로 인덱스를 생성하세요."
                )
            if not status["data_dir_exists"]:
                return (
                    "관련 문서를 찾을 수 없습니다. "
                    f"데이터 디렉터리가 없습니다: {self.data_dir}"
                )
            return "관련 문서를 찾을 수 없습니다."

        parts = ["=== 검색된 문서 (Canonical RAG) ==="]
        for i, result in enumerate(results, 1):
            meta = result.get("metadata", {})
            source_type = result.get("source_type", "unknown")
            score = result.get("weighted_score", result.get("score", 0.0))
            title = meta.get("title", "")[:60]
            stock = meta.get("stock_name", "")
            published = meta.get("published_at", "")

            parts.append(
                f"\n[문서 {i}] (출처: {source_type}, "
                f"source={source_type}, "
                f"score={score:.3f}, "
                f"title={title}, stock={stock}, date={published})"
            )
            parts.append(result.get("text", ""))

        return "\n".join(parts)

    def get_stats(self) -> Dict:
        """Return statistics about the canonical index."""
        stats: Dict = {"themes": {}}
        for theme in self.available_themes:
            corpus = self._load_corpus(theme)
            source_counts: Dict[str, int] = {}
            for row in corpus:
                meta = row.get("metadata", {})
                st = str(meta.get("source_type", "unknown")).lower()
                source_counts[st] = source_counts.get(st, 0) + 1
            stats["themes"][theme] = {
                "total_records": len(corpus),
                "source_counts": source_counts,
            }
        stats["total_themes"] = len(stats["themes"])
        return stats

    def describe_data_state(self) -> Dict[str, object]:
        raw_root = self.data_dir / "raw"
        bm25_root = self.data_dir / "bm25"
        vector_root = self.data_dir / "vector_stores"

        has_bm25 = bm25_root.exists() and any(bm25_root.glob("*.json"))
        has_vector = vector_root.exists() and any(vector_root.glob("*.json"))

        return {
            "data_dir": str(self.data_dir),
            "data_dir_exists": self.data_dir.exists(),
            "raw_available": raw_root.exists() and any(raw_root.rglob("*.jsonl")),
            "canonical_available": bool(self.available_themes),
            "pipeline_bm25_available": has_bm25,
            "pipeline_vector_available": has_vector,
            "retrieval_assets_available": bool(self.available_themes) or has_bm25 or has_vector,
        }

    # ──────────────────────────────────────────────
    # Internal search methods
    # ──────────────────────────────────────────────

    def _search_theme(
        self,
        query: str,
        theme_key: str,
        top_k: int,
        source_filter: set,
    ) -> List[Dict]:
        """Search within a single theme's canonical index."""
        results: List[Dict] = []

        # Vector search
        vector_results = self._vector_search(query, theme_key, top_k)
        for text, meta, score in vector_results:
            source_type = str(meta.get("source_type", "")).lower()
            if source_filter and source_type not in source_filter:
                continue
            results.append({
                "text": text,
                "metadata": meta,
                "source_type": source_type,
                "score": score,
                "theme_key": theme_key,
                "retrieval_method": "vector",
            })

        # BM25 search
        bm25_results = self._bm25_search(query, theme_key, top_k)
        existing_texts = {r["text"][:100] for r in results}
        for text, meta, score in bm25_results:
            source_type = str(meta.get("source_type", "")).lower()
            if source_filter and source_type not in source_filter:
                continue
            if text[:100] in existing_texts:
                continue
            results.append({
                "text": text,
                "metadata": meta,
                "source_type": source_type,
                "score": score * 0.8,  # BM25 scores normalised differently
                "theme_key": theme_key,
                "retrieval_method": "bm25",
            })

        # RRF fusion (simple: already merged, just sort)
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def _vector_search(
        self, query: str, theme_key: str, top_k: int
    ) -> List[tuple]:
        """Return list of (text, metadata, score)."""
        store = self._get_vector_store(theme_key)
        if store is None:
            return []

        raw_results = store.search(query, top_k=top_k)
        output = []
        for doc_dict, score in raw_results:
            output.append((
                doc_dict.get("text", ""),
                doc_dict.get("metadata", {}),
                float(score),
            ))
        return output

    def _bm25_search(
        self, query: str, theme_key: str, top_k: int
    ) -> List[tuple]:
        """Return list of (text, metadata, score)."""
        bm25 = self._get_bm25_index(theme_key)
        if bm25 is None or bm25.corpus_size == 0:
            return []

        raw_results = bm25.search(query, k=top_k)
        output = []
        for doc, score in raw_results:
            output.append((
                doc.page_content,
                dict(doc.metadata or {}),
                float(score),
            ))
        return output

    def _fallback_legacy_search(
        self, query: str, top_k: int, source_filter: set
    ) -> List[Dict]:
        """Fall back to pipeline-level retrieval stores."""
        results: List[Dict] = []
        try:
            from src.retrieval.services import RetrievalService
            svc = RetrievalService(
                data_dir=str(self.data_dir),
                theme_key=self.theme_key,
            )
            raw = svc.search(
                query=query,
                source_types=list(source_filter) if source_filter else None,
                top_k=top_k,
            )
            for item in raw:
                results.append({
                    "text": item.get("text", ""),
                    "metadata": item.get("metadata", {}),
                    "source_type": item.get("source_type", "unknown"),
                    "score": float(item.get("rrf_score", item.get("score", 0.0))),
                    "retrieval_method": "legacy_pipeline",
                })
        except Exception as e:
            logger.warning(f"Legacy retrieval fallback failed: {e}")

        return results

    # ──────────────────────────────────────────────
    # Store loaders
    # ──────────────────────────────────────────────

    def _get_vector_store(self, theme_key: str) -> Optional[SimpleVectorStore]:
        if theme_key in self._vector_stores:
            return self._vector_stores[theme_key]

        path = self.canonical_root / theme_key / "combined_vector_store.json"
        if not path.exists():
            return None

        store = SimpleVectorStore.load(str(path))
        self._vector_stores[theme_key] = store
        return store

    def _get_bm25_index(self, theme_key: str) -> Optional[BM25IndexManager]:
        if theme_key in self._bm25_indexes:
            return self._bm25_indexes[theme_key]

        path = self.canonical_root / theme_key / "bm25_index.json"
        if not path.exists():
            return None

        bm25 = BM25IndexManager(persist_path=str(path), auto_save=False)
        self._bm25_indexes[theme_key] = bm25
        return bm25

    def _load_corpus(self, theme_key: str) -> List[Dict]:
        if theme_key in self._corpus_cache:
            return self._corpus_cache[theme_key]

        path = self.canonical_root / theme_key / "corpus.jsonl"
        if not path.exists():
            return []

        corpus = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    corpus.append(json.loads(line))

        self._corpus_cache[theme_key] = corpus
        return corpus
