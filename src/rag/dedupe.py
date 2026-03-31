from __future__ import annotations

import hashlib
from typing import Dict


def _hash_key(base: str) -> str:
    return hashlib.md5(base.encode("utf-8")).hexdigest()


def _extract_chunk_suffix(metadata: Dict) -> str:
    chunk_index = (metadata or {}).get("chunk_index", None)
    if chunk_index is None:
        return ""

    try:
        normalized = int(chunk_index)
    except (TypeError, ValueError):
        return ""

    return f"|chunk:{normalized}"


def make_document_id(source_type: str, metadata: Dict, text: str = "") -> str:
    source = (source_type or "").strip()
    meta = metadata or {}
    chunk_suffix = _extract_chunk_suffix(meta)

    url = str(meta.get("url", "")).strip()
    rcept_no = str(meta.get("rcept_no", "")).strip()
    stock_code = str(meta.get("stock_code", "")).strip()
    title = str(meta.get("title", "")).strip()
    published_at = str(meta.get("published_at", "")).strip()

    if source in {"news", "general_news", "report"} and url:
        return _hash_key(f"{source}|{url}{chunk_suffix}")

    if source == "dart" and rcept_no:
        return _hash_key(f"{source}|{rcept_no}{chunk_suffix}")

    if source == "forum":
        return _hash_key(f"{source}|{stock_code}|{title}|{published_at}{chunk_suffix}")

    if url:
        return _hash_key(f"{source}|{url}{chunk_suffix}")

    return _hash_key(f"{source}|{stock_code}|{title}|{published_at}|{text[:120]}{chunk_suffix}")


def make_record_id(row: Dict) -> str:
    metadata = row.get("metadata", {}) or {}
    text = row.get("text", "") or ""
    source_type = str(metadata.get("source_type", "")).strip()
    return make_document_id(source_type=source_type, metadata=metadata, text=text)


def make_market_record_id(row: Dict) -> str:
    source = str(row.get("source_type", "") or (row.get("metadata") or {}).get("source_type", "")).strip()
    stock_code = str(row.get("stock_code", "")).strip()
    timestamp = str(row.get("timestamp", "")).strip()
    frequency = str((row.get("metadata") or {}).get("frequency", "daily")).strip()
    return _hash_key(f"{source}|{stock_code}|{timestamp}|{frequency}")
