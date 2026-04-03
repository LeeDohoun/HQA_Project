from __future__ import annotations

# File role:
# - Convert document-level records into chunked JSONL rows for retrieval assets.
# - Ensure all canonical metadata fields are present in every chunk.

"""수집 문서를 RAG 코퍼스로 변환/저장."""

from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List, Dict
import json

from .collectors import CrawledDocument
from src.ingestion.types import generate_doc_id

# ── Canonical metadata fields that must exist on every corpus row ──
_CANONICAL_FIELDS = {
    "doc_id": "",
    "source_type": "",
    "stock_code": "",
    "stock_name": "",
    "theme_key": "",
    "published_at": "",
    "collected_at": "",
    "credibility_score": 0.0,
    "freshness_score": 0.0,
    "content_quality_score": 0.0,
}


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

            doc_dict = asdict(doc)
            # Generate a stable doc_id for this document
            base_doc_id = generate_doc_id(
                source_type=doc.source_type,
                url=doc.url,
                title=doc.title,
                published_at=doc.published_at or "",
                stock_code=doc.stock_code or "",
            )

            for idx, chunk in enumerate(chunks):
                metadata = {
                    "chunk_index": idx,
                    **doc_dict,
                }
                # Ensure all canonical fields exist
                self._ensure_canonical_fields(metadata, base_doc_id)

                record = {
                    "text": chunk,
                    "metadata": metadata,
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

    @staticmethod
    def _ensure_canonical_fields(metadata: Dict, base_doc_id: str) -> None:
        """Guarantee every canonical field is present in metadata.

        When asdict(DocumentRecord) is called, the DocumentRecord.metadata dict
        becomes a nested 'metadata' key.  We must hoist values from that nested
        dict into the top-level canonical slots so downstream consumers always
        find them at a predictable location.
        """
        # 1. Extract nested metadata dict produced by asdict()
        nested = metadata.pop("metadata", None)
        if isinstance(nested, dict):
            # Promote all nested keys that aren't already set at top level
            for k, v in nested.items():
                if k not in metadata or metadata[k] in (None, "", 0, 0.0):
                    metadata[k] = v

        # 2. Fill any missing canonical fields with defaults
        for key, default in _CANONICAL_FIELDS.items():
            if key not in metadata or metadata[key] is None:
                metadata[key] = default

        # 3. Set doc_id if still empty
        if not metadata.get("doc_id"):
            chunk_idx = metadata.get("chunk_index", 0)
            metadata["doc_id"] = f"{base_doc_id}_c{chunk_idx}"

