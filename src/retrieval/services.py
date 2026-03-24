from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from src.rag import BM25IndexManager
from src.rag.dedupe import make_document_id
from src.rag.vector_store import SimpleVectorStore


class RetrievalService:
    def __init__(self, data_dir: str = "./data", theme_key: Optional[str] = None):
        self.data_dir = Path(data_dir)
        self.theme_key = theme_key

    def search(self, query: str, source_types: Optional[List[str]] = None, top_k: int = 20) -> List[Dict]:
        source_filter = {s.strip().lower() for s in (source_types or []) if s.strip()}

        vector_results = self._search_vector(query=query, source_filter=source_filter, top_k=top_k)
        bm25_results = self._search_bm25(query=query, source_filter=source_filter, top_k=top_k)

        fused = self._rrf_merge(vector_results=vector_results, bm25_results=bm25_results, top_k=top_k)
        return fused

    def _search_vector(self, query: str, source_filter: set[str], top_k: int) -> List[Dict]:
        results: List[Dict] = []
        vector_dir = self.data_dir / "vector_stores"
        if not vector_dir.exists():
            return results

        for path in vector_dir.glob("*_vector_store.json"):
            source = path.stem.replace("_vector_store", "").lower()
            if source_filter and source not in source_filter:
                continue

            store = SimpleVectorStore.load(str(path))
            for row, score in store.search(query, top_k=top_k):
                metadata = row.get("metadata", {}) or {}
                source_type = str(metadata.get("source_type", source)).lower()
                if source_filter and source_type not in source_filter:
                    continue
                doc_id = make_document_id(source_type, metadata, row.get("text", ""))
                results.append({
                    "doc_id": doc_id,
                    "text": row.get("text", ""),
                    "metadata": metadata,
                    "source_type": source_type,
                    "score": float(score),
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def _search_bm25(self, query: str, source_filter: set[str], top_k: int) -> List[Dict]:
        bm25_path = self.data_dir / "bm25" / f"{self.theme_key}_bm25.json" if self.theme_key else self.data_dir / "bm25" / "all_bm25.json"
        bm25 = BM25IndexManager(persist_path=str(bm25_path), auto_save=False)
        results: List[Dict] = []
        for doc, score in bm25.search(query, k=top_k):
            metadata = dict(doc.metadata or {})
            source_type = str(metadata.get("source_type", "")).lower()
            if source_filter and source_type not in source_filter:
                continue
            doc_id = make_document_id(source_type, metadata, doc.page_content)
            results.append({
                "doc_id": doc_id,
                "text": doc.page_content,
                "metadata": metadata,
                "source_type": source_type,
                "score": float(score),
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    @staticmethod
    def _rrf_merge(vector_results: List[Dict], bm25_results: List[Dict], top_k: int, k: int = 60) -> List[Dict]:
        fused: Dict[str, Dict] = {}

        for rank, row in enumerate(vector_results, start=1):
            doc_id = row["doc_id"]
            score = 1.0 / (k + rank)
            if doc_id not in fused:
                fused[doc_id] = {**row, "rrf_score": 0.0, "vector_rank": rank, "bm25_rank": None}
            fused[doc_id]["rrf_score"] += score

        for rank, row in enumerate(bm25_results, start=1):
            doc_id = row["doc_id"]
            score = 1.0 / (k + rank)
            if doc_id not in fused:
                fused[doc_id] = {**row, "rrf_score": 0.0, "vector_rank": None, "bm25_rank": rank}
            else:
                fused[doc_id]["bm25_rank"] = rank
            fused[doc_id]["rrf_score"] += score

        merged = sorted(fused.values(), key=lambda x: x["rrf_score"], reverse=True)
        return merged[:top_k]
