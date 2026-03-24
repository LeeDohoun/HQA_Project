"""소스별 경량 벡터 스토어(JSON) 구축/검색 모듈."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


_DIMENSION = 256
_SUPPORTED_SOURCES = ("news", "general_news", "dart", "forum", "chart")


@dataclass
class VectorRecord:
    text: str
    metadata: Dict
    vector: List[float]


class SimpleVectorStore:
    """고정 차원 해시 임베딩 기반 벡터 스토어."""

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

    def upsert_texts(
        self,
        texts: Iterable[str],
        metadatas: Iterable[Dict],
    ) -> int:
        """
        doc_id 기준으로 중복 없이 추가한다.
        이미 존재하는 doc_id는 건너뛴다.
        """
        existing_ids = {
            _make_doc_id(record.metadata, record.text)
            for record in self.records
        }

        added = 0
        for text, metadata in zip(texts, metadatas):
            doc_id = _make_doc_id(metadata, text)
            if doc_id in existing_ids:
                continue

            vector = self._embed(text)
            self.records.append(VectorRecord(text=text, metadata=metadata, vector=vector))
            existing_ids.add(doc_id)
            added += 1

        return added

    def remove_by_theme(self, theme_key: str) -> int:
        """
        해당 테마에서 유입된 문서만 제거한다.
        """
        before = len(self.records)
        self.records = [
            r for r in self.records
            if (r.metadata or {}).get("theme_key", "") != theme_key
        ]
        return before - len(self.records)

    def search(self, query: str, top_k: int = 5) -> List[Tuple[Dict, float]]:
        query_vec = self._embed(query)

        scored: List[Tuple[Dict, float]] = []
        for record in self.records:
            score = _cosine_similarity(query_vec, record.vector)
            scored.append(
                (
                    {
                        "text": record.text,
                        "metadata": record.metadata,
                    },
                    score,
                )
            )

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def _embed(self, text: str) -> List[float]:
        tokens = _tokenize(text)
        if not tokens:
            return [0.0] * self.dimension

        vector = [0.0] * self.dimension
        for token in tokens:
            token_hash = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            idx = token_hash % self.dimension
            vector[idx] += 1.0

        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            return vector
        return [v / norm for v in vector]


class SourceRAGBuilder:
    """
    소스별 단일 vector store를 유지한다.
    - append-new-stocks: 새 문서만 dedupe 후 추가
    - overwrite: 해당 theme_key 문서만 제거 후 새 문서 추가
    """

    def __init__(self, dimension: int = _DIMENSION):
        self.dimension = dimension

    def upsert_by_source(
        self,
        records: List[Dict],
        output_dir: str,
        mode: str = "append-new-stocks",
        theme_key: str = "",
    ) -> Dict[str, int]:
        sources = {source: [] for source in _SUPPORTED_SOURCES}
        for row in records:
            metadata = row.get("metadata", {})
            source_type = metadata.get("source_type", "")
            if source_type in sources:
                sources[source_type].append(row)

        output_stats: Dict[str, int] = {}
        base = Path(output_dir)
        base.mkdir(parents=True, exist_ok=True)

        for source, rows in sources.items():
            output_file = base / f"{source}_vector_store.json"
            store = SimpleVectorStore.load(str(output_file))
            store.dimension = self.dimension

            if mode == "overwrite" and theme_key:
                removed = store.remove_by_theme(theme_key)
                print(f"[VECTOR][{source}] removed by theme='{theme_key}': {removed}")

            added = store.upsert_texts(
                texts=[r.get("text", "") for r in rows],
                metadatas=[r.get("metadata", {}) for r in rows],
            )
            total = store.save(str(output_file))

            print(f"[VECTOR][{source}] added={added}, total={total}")
            output_stats[source] = total

        return output_stats


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r"[가-힣A-Za-z0-9]+", text.lower())


def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def _make_doc_id(metadata: Dict, text: str) -> str:
    """
    소스별 문서 고유키 생성.
    """
    metadata = metadata or {}
    source_type = str(metadata.get("source_type", "")).strip()
    url = str(metadata.get("url", "")).strip()
    rcept_no = str(metadata.get("rcept_no", "")).strip()
    stock_code = str(metadata.get("stock_code", "")).strip()
    title = str(metadata.get("title", "")).strip()
    published_at = str(metadata.get("published_at", "")).strip()

    if source_type == "dart" and rcept_no:
        base = f"{source_type}|{rcept_no}"
    elif url:
        base = f"{source_type}|{url}"
    else:
        base = f"{source_type}|{stock_code}|{title}|{published_at}|{text[:120]}"

    return hashlib.md5(base.encode("utf-8")).hexdigest()
