from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Iterable, List

from src.data_pipeline.rag_builder import RAGCorpusBuilder
from src.ingestion.types import DocumentRecord
from src.rag.bm25_index import BM25IndexManager
from src.rag.dedupe import make_market_record_id, make_record_id
from src.rag.source_registry import DEFAULT_MARKET_SOURCES, is_document_source, is_market_source
from src.rag.vector_store import SourceRAGBuilder


class RawLayer2Builder:
    DART_WRAPPER_TOKENS = (
        "잠시만 기다려주세요",
        "현재목차",
        "본문선택",
        "첨부선택",
        "문서목차",
        "인쇄 닫기",
    )

    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir)
        self.raw_dir = self.data_dir / "raw"
        self.corpora_root = self.data_dir / "corpora"
        self.market_root = self.data_dir / "market_data"
        self.vector_root = self.data_dir / "vector_stores"
        self.bm25_root = self.data_dir / "bm25"

    def rebuild_theme(self, theme_key: str, update_mode: str = "append-new-stocks") -> Dict:
        docs, doc_quality_stats = self._load_raw_documents(theme_key)
        builder = RAGCorpusBuilder(chunk_size=700, chunk_overlap=100)
        records = builder.build_records(docs)
        deduped_records = self._dedupe_records(records)

        corpora_dir = self.corpora_root / theme_key
        corpora_dir.mkdir(parents=True, exist_ok=True)
        combined_path = corpora_dir / "combined.jsonl"
        combined_count = builder.save_jsonl(deduped_records, str(combined_path))

        per_source_doc_counts: Dict[str, int] = {}
        grouped_records = self._group_by_source(deduped_records)
        for source, rows in grouped_records.items():
            per_source_doc_counts[source] = builder.save_jsonl(rows, str(corpora_dir / f"{source}.jsonl"))

        vector_builder = SourceRAGBuilder()
        vector_stats = vector_builder.upsert_by_source(
            records=deduped_records,
            output_dir=str(self.vector_root),
            mode=update_mode,
            theme_key=theme_key,
        )

        bm25_path = self.bm25_root / f"{theme_key}_bm25.json"
        bm25 = BM25IndexManager(persist_path=str(bm25_path), auto_save=False)
        bm25.clear()
        bm25.add_texts(
            texts=[row.get("text", "") for row in deduped_records],
            metadatas=[row.get("metadata", {}) for row in deduped_records],
        )
        bm25.save_index()

        market_records = self._load_raw_market_records(theme_key)
        deduped_market = self._dedupe_market_records(market_records)
        market_stats = self._save_market_data(theme_key, deduped_market)

        return {
            "combined_count": combined_count,
            "document_source_counts": per_source_doc_counts,
            "vector_stats": vector_stats,
            "bm25_path": str(bm25_path),
            "market_stats": market_stats,
            "records": deduped_records,
            "raw_docs_count": doc_quality_stats["raw_docs_count"],
            "skipped_invalid_count_by_source": doc_quality_stats["skipped_invalid_count_by_source"],
            "built_records_count": len(records),
            "final_records_count": len(deduped_records),
        }

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

                docs.append(
                    DocumentRecord(
                        source_type=source_type,
                        title=row.get("title", ""),
                        content=row.get("content", ""),
                        url=row.get("url", ""),
                        stock_name=row.get("stock_name"),
                        stock_code=row.get("stock_code"),
                        published_at=row.get("published_at"),
                        metadata=merged_metadata,
                    )
                )

        for source_name, skipped_count in skipped_invalid_count_by_source.items():
            print(f"[LAYER2][{source_name}] skipped_invalid={skipped_count}")

        return docs, {
            "raw_docs_count": raw_docs_count,
            "skipped_invalid_count_by_source": skipped_invalid_count_by_source,
        }

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
