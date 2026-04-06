from __future__ import annotations

# File role:
# - Read raw files and rebuild Layer 2 retrieval assets.
# - Produces corpora, BM25 indexes, vector stores, and market-data shards.
# - Syncs canonical RAG index after build (new Step 2 integration).

import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from src.config.settings import get_data_dir
from src.data_pipeline.rag_builder import RAGCorpusBuilder
from src.ingestion.types import DocumentRecord, generate_doc_id
from src.retrieval.bm25_index import BM25IndexManager
from src.retrieval.vector_store import SourceRAGBuilder
from src.rag.dedupe import make_market_record_id, make_record_id
from src.rag.source_registry import DEFAULT_MARKET_SOURCES, is_document_source, is_market_source


# ── Source credibility baseline ──
SOURCE_CREDIBILITY: Dict[str, float] = {
    "report": 1.0,
    "dart": 0.95,
    "news": 0.80,
    "general_news": 0.75,
    "forum": 0.35,
}


def _compute_freshness_score(published_at: str, reference_date: Optional[datetime] = None) -> float:
    """Return a 0.0-1.0 freshness score.  Recent == higher."""
    if not published_at:
        return 0.3  # unknown age → conservative default

    ref = reference_date or datetime.utcnow()
    try:
        # Try common formats
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y%m%d", "%Y.%m.%d"):
            try:
                dt = datetime.strptime(published_at.strip()[:19], fmt)
                break
            except ValueError:
                continue
        else:
            return 0.3

        delta_days = (ref - dt).days
        if delta_days < 0:
            return 1.0
        if delta_days <= 7:
            return 1.0
        if delta_days <= 30:
            return 0.85
        if delta_days <= 90:
            return 0.6
        if delta_days <= 365:
            return 0.4
        return 0.2
    except Exception:
        return 0.3


def _compute_content_quality_score(source_type: str, content: str, title: str) -> float:
    """Heuristic content quality score (0.0-1.0)."""
    if not content:
        return 0.0

    score = 0.5

    content_len = len(content.strip())
    if content_len >= 500:
        score += 0.2
    elif content_len >= 200:
        score += 0.1
    elif content_len < 50:
        score -= 0.2

    # Penalise likely low-quality
    if source_type == "forum":
        if content_len < 100:
            score -= 0.1
        # Check for spam-like patterns
        if re.search(r'(ㅋ{3,}|ㅎ{3,}|ㅠ{3,})', content):
            score -= 0.15

    # Bonus for structured content (tables, lists)
    if re.search(r'\d+[.)] ', content) or '|' in content:
        score += 0.1

    return max(0.0, min(1.0, score))


class RawLayer2Builder:
    DART_WRAPPER_TOKENS = (
        "잠시만 기다려주세요",
        "현재목차",
        "본문선택",
        "첨부선택",
        "문서목차",
        "인쇄 닫기",
    )

    def __init__(self, data_dir: str | None = None):
        self.data_dir = Path(data_dir) if data_dir else get_data_dir()
        self.raw_dir = self.data_dir / "raw"
        self.corpora_root = self.data_dir / "corpora"
        self.market_root = self.data_dir / "market_data"
        self.vector_root = self.data_dir / "vector_stores"
        self.bm25_root = self.data_dir / "bm25"
        # Canonical index directory for agent-side consumption
        self.canonical_index_root = self.data_dir / "canonical_index"

    def rebuild_theme(self, theme_key: str, update_mode: str = "append-new-stocks") -> Dict:
        """Main flow: raw → corpus → canonical RAG sync.

        Returns a detailed stats dict.
        """
        # 1) validate raw documents
        docs, doc_quality_stats = self._load_raw_documents(theme_key)

        # 2) build chunked corpus rows
        builder = RAGCorpusBuilder(chunk_size=700, chunk_overlap=100)
        records = builder.build_records(docs)
        deduped_records = self._dedupe_records(records)

        # 3) write text corpus
        corpora_dir = self.corpora_root / theme_key
        corpora_dir.mkdir(parents=True, exist_ok=True)
        combined_path = corpora_dir / "combined.jsonl"
        combined_count = builder.save_jsonl(deduped_records, str(combined_path))

        per_source_doc_counts: Dict[str, int] = {}
        grouped_records = self._group_by_source(deduped_records)
        for source, rows in grouped_records.items():
            per_source_doc_counts[source] = builder.save_jsonl(rows, str(corpora_dir / f"{source}.jsonl"))

        # 4) pipeline vector stores (lightweight JSON stores)
        vector_builder = SourceRAGBuilder()
        vector_stats = vector_builder.upsert_by_source(
            records=deduped_records,
            output_dir=str(self.vector_root),
            mode=update_mode,
            theme_key=theme_key,
        )

        # 5) pipeline BM25
        bm25_path = self.bm25_root / f"{theme_key}_bm25.json"
        bm25 = BM25IndexManager(persist_path=str(bm25_path), auto_save=False)
        bm25.clear()
        bm25.add_texts(
            texts=[row.get("text", "") for row in deduped_records],
            metadatas=[row.get("metadata", {}) for row in deduped_records],
        )
        bm25.save_index()

        # 6) market data
        market_records = self._load_raw_market_records(theme_key)
        deduped_market = self._dedupe_market_records(market_records)
        market_stats = self._save_market_data(theme_key, deduped_market)

        # 7) ★ Canonical RAG sync — write combined index for agent consumption
        canonical_stats = self._sync_canonical_index(theme_key, deduped_records)

        return {
            "combined_count": combined_count,
            "document_source_counts": per_source_doc_counts,
            "vector_stats": vector_stats,
            "bm25_path": str(bm25_path),
            "market_stats": market_stats,
            "canonical_stats": canonical_stats,
            "records": deduped_records,
            "raw_docs_count": doc_quality_stats["raw_docs_count"],
            "skipped_invalid_count_by_source": doc_quality_stats["skipped_invalid_count_by_source"],
            "built_records_count": len(records),
            "final_records_count": len(deduped_records),
        }

    # ──────────────────────────────────────────────
    # Canonical RAG sync
    # ──────────────────────────────────────────────

    def _sync_canonical_index(self, theme_key: str, records: List[Dict]) -> Dict[str, int]:
        """Write a unified canonical index that agent-side retriever consumes.

        Output:
            data/canonical_index/<theme_key>/corpus.jsonl  — all records with metadata
            data/canonical_index/<theme_key>/bm25_index.json — BM25 for hybrid search
        """
        index_dir = self.canonical_index_root / theme_key
        index_dir.mkdir(parents=True, exist_ok=True)

        # 1) Corpus JSONL
        corpus_path = index_dir / "corpus.jsonl"
        with corpus_path.open("w", encoding="utf-8") as f:
            for row in records:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

        # 2) BM25 Index
        bm25_path = index_dir / "bm25_index.json"
        bm25 = BM25IndexManager(persist_path=str(bm25_path), auto_save=False)
        bm25.clear()
        bm25.add_texts(
            texts=[row.get("text", "") for row in records],
            metadatas=[row.get("metadata", {}) for row in records],
        )
        bm25.save_index()

        # 3) Vector store (combined, unified)
        vector_store_builder = SourceRAGBuilder()
        # Build a single combined vector store (not split by source)
        combined_store_path = index_dir / "combined_vector_store.json"
        from src.retrieval.vector_store import SimpleVectorStore
        store = SimpleVectorStore()
        store.upsert_texts(
            texts=[row.get("text", "") for row in records],
            metadatas=[row.get("metadata", {}) for row in records],
        )
        store.save(str(combined_store_path))

        return {
            "corpus_count": len(records),
            "bm25_path": str(bm25_path),
            "vector_store_path": str(combined_store_path),
        }

    # ──────────────────────────────────────────────
    # Raw document loading
    # ──────────────────────────────────────────────

    def _load_raw_documents(self, theme_key: str) -> tuple[List[DocumentRecord], Dict]:
        docs: List[DocumentRecord] = []
        raw_docs_count = 0
        skipped_invalid_count_by_source: Dict[str, int] = {"news": 0, "forum": 0, "dart": 0}
        if not self.raw_dir.exists():
            return docs, {
                "raw_docs_count": 0,
                "skipped_invalid_count_by_source": skipped_invalid_count_by_source,
            }

        for source_dir in self.raw_dir.iterdir():
            if not source_dir.is_dir():
                continue

            source = source_dir.name
            if source == "theme_targets" or source in DEFAULT_MARKET_SOURCES:
                continue

            file_path = source_dir / f"{theme_key}.jsonl"
            if not file_path.exists():
                continue

            for row in self._iter_jsonl(file_path):
                raw_docs_count += 1
                source_type = str(row.get("source_type", source)).strip().lower()
                if not is_document_source(source_type):
                    continue

                merged_metadata = self._normalize_document_metadata(row, source_type)

                if not self._is_valid_document_for_layer2(source_type=source_type, row=row, metadata=merged_metadata):
                    if source_type in skipped_invalid_count_by_source:
                        skipped_invalid_count_by_source[source_type] += 1
                    continue

                # DART는 본문이 있는 문서만 corpus/BM25/vector 대상으로 사용
                if source_type == "dart" and not merged_metadata.get("has_body", False):
                    continue

                # ★ Compute canonical quality scores
                title = row.get("title", "")
                content = row.get("content", "")
                published_at = row.get("published_at", "") or merged_metadata.get("published_at", "")

                doc_id = generate_doc_id(
                    source_type=source_type,
                    url=row.get("url", ""),
                    title=title,
                    published_at=published_at,
                    stock_code=row.get("stock_code", ""),
                )
                merged_metadata["doc_id"] = doc_id
                merged_metadata["credibility_score"] = SOURCE_CREDIBILITY.get(source_type, 0.5)
                merged_metadata["freshness_score"] = _compute_freshness_score(published_at)
                merged_metadata["content_quality_score"] = _compute_content_quality_score(
                    source_type, content, title,
                )

                docs.append(
                    DocumentRecord(
                        source_type=source_type,
                        title=title,
                        content=content,
                        url=row.get("url", ""),
                        doc_id=doc_id,
                        stock_name=row.get("stock_name"),
                        stock_code=row.get("stock_code"),
                        published_at=published_at or None,
                        metadata=merged_metadata,
                    )
                )

        for source_name, skipped_count in skipped_invalid_count_by_source.items():
            print(f"[LAYER2][{source_name}] skipped_invalid={skipped_count}")

        return docs, {
            "raw_docs_count": raw_docs_count,
            "skipped_invalid_count_by_source": skipped_invalid_count_by_source,
        }

    # ──────────────────────────────────────────────
    # Document validation
    # ──────────────────────────────────────────────

    def _is_valid_document_for_layer2(self, source_type: str, row: Dict, metadata: Dict) -> bool:
        if source_type == "news":
            return self._is_valid_news_doc(row)
        if source_type == "forum":
            return self._is_valid_forum_doc(row, metadata)
        if source_type == "dart":
            return self._is_valid_dart_doc(row, metadata)
        return True

    @staticmethod
    def _is_valid_news_doc(row: Dict) -> bool:
        title = str(row.get("title", "") or "").strip()
        content = str(row.get("content", "") or "").strip()
        if not title or not content:
            return False
        if title == "네이버뉴스" or content == "네이버뉴스":
            return False
        return len(content) >= 30

    @staticmethod
    def _as_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "y"}
        if isinstance(value, (int, float)):
            return value != 0
        return False

    def _is_valid_forum_doc(self, row: Dict, metadata: Dict) -> bool:
        title = str(row.get("title", "") or "").strip()
        content = str(row.get("content", "") or "").strip()
        if not title or not content:
            return False
        if content == title:
            return False
        if str(metadata.get("content_source", "")).strip().lower() == "title_only":
            return False
        body_extracted = self._as_bool(metadata.get("body_extracted"))
        if not body_extracted:
            return False
        return len(content) >= 20

    def _is_valid_dart_doc(self, row: Dict, metadata: Dict) -> bool:
        title = str(row.get("title", "") or "").strip()
        content = str(row.get("content", "") or "").strip()
        if not title or not content:
            return False
        if not self._as_bool(metadata.get("has_body")):
            return False
        if self._as_bool(metadata.get("wrapper_text_detected")):
            return False
        normalized_content = re.sub(r"\s+", " ", content)
        if any(token in normalized_content for token in self.DART_WRAPPER_TOKENS):
            return False
        return len(normalized_content) >= 200

    # ──────────────────────────────────────────────
    # Market data
    # ──────────────────────────────────────────────

    def _load_raw_market_records(self, theme_key: str) -> List[Dict]:
        rows: List[Dict] = []
        if not self.raw_dir.exists():
            return rows

        for source in DEFAULT_MARKET_SOURCES:
            source_dir = self.raw_dir / source
            file_path = source_dir / f"{theme_key}.jsonl"
            if not file_path.exists():
                continue

            for row in self._iter_jsonl(file_path):
                source_type = str(row.get("source_type", source)).strip().lower()
                if not is_market_source(source_type):
                    continue
                row["source_type"] = source_type
                row["metadata"] = row.get("metadata") or {}
                rows.append(row)

        return rows

    def _save_market_data(self, theme_key: str, rows: List[Dict]) -> Dict[str, int]:
        theme_dir = self.market_root / theme_key
        theme_dir.mkdir(parents=True, exist_ok=True)

        grouped = self._group_by_source(rows)
        stats: Dict[str, int] = {}
        for source, source_rows in grouped.items():
            output = theme_dir / f"{source}.jsonl"
            stats[source] = self._save_jsonl(source_rows, output)

        stats["combined"] = self._save_jsonl(rows, theme_dir / "combined.jsonl")
        return stats

    # ──────────────────────────────────────────────
    # Dedup & helpers
    # ──────────────────────────────────────────────

    def _dedupe_records(self, rows: List[Dict]) -> List[Dict]:
        seen = set()
        deduped: List[Dict] = []
        for row in rows:
            record_id = make_record_id(row)
            if record_id in seen:
                continue
            seen.add(record_id)
            deduped.append(row)
        return deduped

    def _dedupe_market_records(self, rows: List[Dict]) -> List[Dict]:
        seen = set()
        deduped: List[Dict] = []
        for row in rows:
            record_id = make_market_record_id(row)
            if record_id in seen:
                continue
            seen.add(record_id)
            deduped.append(row)
        return deduped

    @staticmethod
    def _normalize_document_metadata(row: Dict, source_type: str) -> Dict:
        raw_meta = row.get("metadata") or {}
        nested_meta = raw_meta.get("metadata") if isinstance(raw_meta.get("metadata"), dict) else {}

        merged = {}
        merged.update(nested_meta)
        merged.update(raw_meta)

        merged.pop("metadata", None)

        merged["source_type"] = source_type
        merged["title"] = row.get("title", "") or merged.get("title", "")
        merged["url"] = row.get("url", "") or merged.get("url", "")
        merged["stock_name"] = row.get("stock_name", "") or merged.get("stock_name", "")
        merged["stock_code"] = row.get("stock_code", "") or merged.get("stock_code", "")
        merged["published_at"] = row.get("published_at", "") or merged.get("published_at", "")

        return merged

    @staticmethod
    def _group_by_source(rows: List[Dict]) -> Dict[str, List[Dict]]:
        grouped: Dict[str, List[Dict]] = {}
        for row in rows:
            metadata = row.get("metadata", {}) if isinstance(row, dict) else {}
            source = str(row.get("source_type") or metadata.get("source_type") or "unknown").strip().lower()
            grouped.setdefault(source, []).append(row)
        return grouped

    @staticmethod
    def _iter_jsonl(path: Path) -> Iterable[Dict]:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)

    @staticmethod
    def _save_jsonl(rows: List[Dict], path: Path) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                if hasattr(row, "__dataclass_fields__"):
                    payload = asdict(row)
                else:
                    payload = row
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return len(rows)
