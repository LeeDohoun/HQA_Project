from __future__ import annotations

# File role:
# - Convert document-level records into chunked JSONL rows for retrieval assets.

"""수집 문서를 RAG 코퍼스로 변환/저장."""

from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List, Dict
import json

from .collectors import CrawledDocument


class RAGCorpusBuilder:
    """크롤링 결과를 청크 단위 JSONL로 저장."""

    def __init__(self, chunk_size: int = 700, chunk_overlap: int = 100):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def build_records(self, docs: Iterable[CrawledDocument]) -> List[Dict]:
        records: List[Dict] = []
        for doc in docs:
            chunks = self._split_text(doc.content)
            if not chunks:
                chunks = [doc.content]

            for idx, chunk in enumerate(chunks):
                record = {
                    "text": chunk,
                    "metadata": {
                        "chunk_index": idx,
                        **asdict(doc),
                    },
                }
                records.append(record)
        return records

    def save_jsonl(self, records: Iterable[Dict], output_path: str) -> int:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        count = 0
        with output.open("w", encoding="utf-8") as f:
            for row in records:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                count += 1
        return count

    def _split_text(self, text: str) -> List[str]:
        if not text:
            return []

        stripped = text.strip()
        if len(stripped) <= self.chunk_size:
            return [stripped]

        chunks: List[str] = []
        start = 0
        while start < len(stripped):
            end = min(len(stripped), start + self.chunk_size)
            chunks.append(stripped[start:end])
            if end == len(stripped):
                break
            start = max(0, end - self.chunk_overlap)
        return chunks
