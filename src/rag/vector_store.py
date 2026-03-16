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


@dataclass
class VectorRecord:
    text: str
    metadata: Dict
    vector: List[float]


class SimpleVectorStore:
    """고정 차원 해시 임베딩 기반 벡터 스토어.

    외부 모델 의존성을 줄이기 위해 텍스트를 토큰화한 뒤
    해시 버킷에 누적하여 고정 크기 벡터를 만듭니다.
    """

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
    """소스(news/dart/forum)별 벡터 스토어 파일을 생성."""

    def __init__(self, dimension: int = _DIMENSION):
        self.dimension = dimension

    def build_and_save(self, records: List[Dict], output_dir: str) -> Dict[str, int]:
        sources = {"news": [], "dart": [], "forum": []}
        for row in records:
            metadata = row.get("metadata", {})
            source_type = metadata.get("source_type", "")
            if source_type in sources:
                sources[source_type].append(row)

        output_stats: Dict[str, int] = {}
        base = Path(output_dir)
        base.mkdir(parents=True, exist_ok=True)

        for source, rows in sources.items():
            store = SimpleVectorStore(dimension=self.dimension)
            store.add_texts(
                texts=[r.get("text", "") for r in rows],
                metadatas=[r.get("metadata", {}) for r in rows],
            )
            output_file = base / f"{source}_vector_store.json"
            output_stats[source] = store.save(str(output_file))

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
