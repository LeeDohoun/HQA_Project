from __future__ import annotations

# File role:
# - Lightweight JSON vector store for data-pipeline artifacts.
# - Keeps pipeline retrieval separate from ai-main's Chroma-based RAG stack.

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from src.rag.dedupe import make_document_id
from src.rag.source_registry import is_document_source


_DIMENSION = 256


@dataclass
class VectorRecord:
    text: str
    metadata: Dict
    vector: List[float]


class SimpleVectorStore:
    """Fixed-dimension hash embedding vector store persisted as JSON."""

    def __init__(self, dimension: int = _DIMENSION):
        self.dimension = dimension
        self.records: List[VectorRecord] = []

    def add_texts(self, texts: Iterable[str], metadatas: Iterable[Dict]) -> int:
        count = 0
        for text, metadata in zip(texts, metadatas):
            vector = self._embed(text)
            self.records.append(VectorRecord(text=text, metadata=metadata, vector=vector))
            count += 1
        return count

    def save(self, output_path: str) -> int:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "dimension": self.dimension,
            "size": len(self.records),
            "records": [
                {
                    "text": record.text,
                    "metadata": record.metadata,
                    "vector": record.vector,
                }
                for record in self.records
            ],
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return len(self.records)

    @classmethod
    def load(cls, input_path: str) -> "SimpleVectorStore":
        path = Path(input_path)
        store = cls()

        if not path.exists():
            return store

        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)

        store.dimension = int(payload.get("dimension", _DIMENSION))
        store.records = [
            VectorRecord(
                text=row.get("text", ""),
                metadata=row.get("metadata", {}),
                vector=row.get("vector", []),
            )
            for row in payload.get("records", [])
        ]
        return store

    def upsert_texts(self, texts: Iterable[str], metadatas: Iterable[Dict]) -> int:
        existing_ids = {
            self._make_doc_id(record.metadata, record.text)
            for record in self.records
        }

        added = 0
        for text, metadata in zip(texts, metadatas):
            doc_id = self._make_doc_id(metadata, text)
            if doc_id in existing_ids:
                continue

            vector = self._embed(text)
            self.records.append(VectorRecord(text=text, metadata=metadata, vector=vector))
            existing_ids.add(doc_id)
            added += 1

        return added

    def remove_by_theme(self, theme_key: str) -> int:
        before = len(self.records)
        self.records = [
            record
            for record in self.records
            if (record.metadata or {}).get("theme_key", "") != theme_key
        ]
        return before - len(self.records)

    def search(self, query: str, top_k: int = 5) -> List[Tuple[Dict, float]]:
        query_vec = self._embed(query)

        scored: List[Tuple[Dict, float]] = []
        for record in self.records:
            score = self._cosine_similarity(query_vec, record.vector)
            scored.append(({"text": record.text, "metadata": record.metadata}, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def _embed(self, text: str) -> List[float]:
        tokens = re.findall(r"[가-힣A-Za-z0-9]+", (text or "").lower())
        if not tokens:
            return [0.0] * self.dimension

        vector = [0.0] * self.dimension
        for token in tokens:
            token_hash = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            idx = token_hash % self.dimension
            vector[idx] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    @staticmethod
    def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
        dot = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    @staticmethod
    def _make_doc_id(metadata: Dict, text: str) -> str:
        source_type = str((metadata or {}).get("source_type", "")).strip().lower()
        return make_document_id(source_type=source_type, metadata=metadata or {}, text=text or "")


class SourceRAGBuilder:
    """Builds per-source JSON vector stores from layer-2 records."""

    def __init__(self, dimension: int = _DIMENSION):
        self.dimension = dimension

    def upsert_by_source(
        self,
        records: List[Dict],
        output_dir: str,
        mode: str = "append-new-stocks",
        theme_key: str = "",
    ) -> Dict[str, int]:
        sources: Dict[str, List[Dict]] = {}
        for row in records:
            metadata = row.get("metadata", {}) or {}
            source_type = str(metadata.get("source_type", "")).strip().lower()
            if not is_document_source(source_type):
                continue
            sources.setdefault(source_type, []).append(row)

        output_stats: Dict[str, int] = {}
        base = Path(output_dir)
        base.mkdir(parents=True, exist_ok=True)

        for source, rows in sources.items():
            output_file = base / f"{source}_vector_store.json"
            store = SimpleVectorStore.load(str(output_file))
            store.dimension = self.dimension

            if mode == "overwrite" and theme_key:
                store.remove_by_theme(theme_key)

            store.upsert_texts(
                texts=[row.get("text", "") for row in rows],
                metadatas=[row.get("metadata", {}) for row in rows],
            )
            output_stats[source] = store.save(str(output_file))

        return output_stats
