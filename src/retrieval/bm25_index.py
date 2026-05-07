from __future__ import annotations

# File role:
# - Lightweight BM25 index for pipeline-built corpora.
# - Avoids depending on the ai-main RAG stack during data-pipeline flows.

import json
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src.config.settings import get_data_dir

logger = logging.getLogger(__name__)

try:
    from rank_bm25 import BM25Okapi

    _BM25_AVAILABLE = True
except ImportError:
    BM25Okapi = None
    _BM25_AVAILABLE = False
    logger.warning("rank_bm25 미설치 → pipeline BM25 검색 비활성화")


def _tokenize_korean(text: str) -> List[str]:
    if not text:
        return []

    text = text.lower()
    tokens = []
    tokens.extend(
        pattern.strip()
        for pattern in re.findall(
            r"[\-+]?\d[\d,]*\.?\d*\s*(?:배|%|억|조|만|원|주|달러|위안|엔|점|bp|bps)",
            text,
        )
    )
    tokens.extend(re.findall(r"[a-z][a-z/&]+[a-z]", text))
    tokens.extend(re.findall(r"[가-힣]{2,}", text))
    tokens.extend(re.findall(r"\b\d{4,}\b", text))
    return tokens


@dataclass
class Document:
    page_content: str
    metadata: Dict = field(default_factory=dict)


@dataclass
class BM25Document:
    doc_id: str
    page_content: str
    metadata: Dict
    tokens: List[str] = field(default_factory=list)


class BM25IndexManager:
    def __init__(
        self,
        persist_path: Optional[str] = None,
        auto_save: bool = True,
        save_interval: int = 50,
    ):
        self.persist_path = persist_path or str(get_data_dir() / "bm25" / "index.json")
        self.auto_save = auto_save
        self.save_interval = save_interval
        self._corpus: List[BM25Document] = []
        self._tokenized_corpus: List[List[str]] = []
        self._bm25: Optional[BM25Okapi] = None
        self._indexed_ids: set[str] = set()
        self._changes_since_save = 0
        self._load_index()

    @property
    def is_available(self) -> bool:
        return _BM25_AVAILABLE

    @property
    def corpus_size(self) -> int:
        return len(self._corpus)

    def clear(self) -> None:
        self._corpus = []
        self._tokenized_corpus = []
        self._bm25 = None
        self._indexed_ids.clear()
        self._changes_since_save = 0
        if self.auto_save:
            self.save_index()

    def add_documents(self, documents: List[Document]) -> int:
        if not self.is_available:
            return 0

        added = 0
        for doc in documents:
            doc_id = self._generate_id(doc)
            if doc_id in self._indexed_ids:
                continue

            tokens = _tokenize_korean(doc.page_content)
            if not tokens:
                continue

            self._corpus.append(
                BM25Document(
                    doc_id=doc_id,
                    page_content=doc.page_content,
                    metadata=dict(doc.metadata or {}),
                    tokens=tokens,
                )
            )
            self._tokenized_corpus.append(tokens)
            self._indexed_ids.add(doc_id)
            added += 1

        if added > 0:
            self._rebuild_bm25()
            self._changes_since_save += added
            if self.auto_save and self._changes_since_save >= self.save_interval:
                self.save_index()

        return added

    def add_texts(self, texts: List[str], metadatas: Optional[List[Dict]] = None) -> int:
        docs = []
        for index, text in enumerate(texts):
            metadata = metadatas[index] if metadatas and index < len(metadatas) else {}
            docs.append(Document(page_content=text, metadata=metadata))
        return self.add_documents(docs)

    def search(self, query: str, k: int = 20) -> List[Tuple[Document, float]]:
        if not self.is_available or self._bm25 is None or self.corpus_size == 0:
            return []

        query_tokens = _tokenize_korean(query)
        if not query_tokens:
            return []

        scores = self._bm25.get_scores(query_tokens)
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]

        results: List[Tuple[Document, float]] = []
        for idx in top_indices:
            score = float(scores[idx])
            if score <= 0:
                break
            row = self._corpus[idx]
            results.append((Document(page_content=row.page_content, metadata=row.metadata), score))
        return results

    def save_index(self) -> bool:
        try:
            directory = os.path.dirname(self.persist_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.persist_path, "w", encoding="utf-8") as f:
                payload = {
                    "documents": [
                        {
                            "doc_id": row.doc_id,
                            "page_content": row.page_content,
                            "metadata": row.metadata,
                            "tokens": row.tokens,
                        }
                        for row in self._corpus
                    ]
                }
                json.dump(payload, f, ensure_ascii=False)
            self._changes_since_save = 0
            return True
        except Exception as exc:
            logger.warning(f"BM25 저장 실패: {exc}")
            return False

    def _load_index(self) -> None:
        if not os.path.exists(self.persist_path):
            return

        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as exc:
            logger.warning(f"BM25 로드 실패: {exc}")
            return

        self._corpus = []
        self._tokenized_corpus = []
        self._indexed_ids = set()

        for row in payload.get("documents", []):
            doc = BM25Document(
                doc_id=row.get("doc_id", ""),
                page_content=row.get("page_content", ""),
                metadata=row.get("metadata", {}),
                tokens=row.get("tokens", []),
            )
            if not doc.doc_id:
                continue
            self._corpus.append(doc)
            self._tokenized_corpus.append(doc.tokens)
            self._indexed_ids.add(doc.doc_id)

        self._rebuild_bm25()

    def _rebuild_bm25(self) -> None:
        if not self.is_available or not self._tokenized_corpus:
            self._bm25 = None
            return
        self._bm25 = BM25Okapi(self._tokenized_corpus)

    @staticmethod
    def _generate_id(doc: Document) -> str:
        metadata = doc.metadata or {}
        base = "|".join(
            [
                str(metadata.get("source_type", "")),
                str(metadata.get("stock_code", "")),
                str(metadata.get("title", "")),
                str(metadata.get("published_at", "")),
                doc.page_content[:120],
            ]
        )
        return hashlib.md5(base.encode("utf-8")).hexdigest()


def reciprocal_rank_fusion(
    ranked_lists: List[List[Tuple[Document, float]]],
    k: int = 60,
) -> List[Tuple[Document, float]]:
    fused_scores: Dict[str, Dict] = {}

    for ranked in ranked_lists:
        for rank, (doc, _) in enumerate(ranked, start=1):
            metadata = doc.metadata or {}
            doc_id = "|".join(
                [
                    str(metadata.get("source_type", "")),
                    str(metadata.get("stock_code", "")),
                    str(metadata.get("title", "")),
                    str(metadata.get("published_at", "")),
                    doc.page_content[:120],
                ]
            )
            if doc_id not in fused_scores:
                fused_scores[doc_id] = {"doc": doc, "score": 0.0}
            fused_scores[doc_id]["score"] += 1.0 / (k + rank)

    merged = sorted(fused_scores.values(), key=lambda row: row["score"], reverse=True)
    return [(row["doc"], row["score"]) for row in merged]
