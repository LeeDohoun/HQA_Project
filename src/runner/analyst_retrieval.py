"""Retrieved background for the analyst: a small point-in-time BM25 over the collected documents.

The analyst's dated event packets stay exactly as they are. Retrieval adds `retrieved_context`: a few
documents the events do not already carry, from two pools,

- theme: documents collected for the *other* stocks of the stock's theme (news and filings about
  the industry the stock trades in), at most 30 days old;
- stock: the stock's own documents outside its event packets (older news, earlier filings), news
  at most 90 days and filings at most 400 days old;

each ranked by Okapi BM25 over Hangul character bigrams and ASCII words. Korean particles stay
attached to words, so whole-word matching misses most hits; bigrams match across them, and nothing
has to be installed. Only documents PAPER could have read at the decision time count (available =
max(published, collected) <= as_of, the live loader's rule), with the live loader's own quality
gates and one item per document version, and the corpus statistics are those of that moment.

The 2026-10 fitness replay tried both pools (`backtesting/fitness/analyst_rag.py`): the analyst cited
86% of the stock items and 10% of the theme items, which were mostly market wraps. PAPER therefore
uses the stock pool alone (`HQA_ANALYST_RAG=stock`, `AnalystRetrieval`); the default, "off", leaves
every request unchanged.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.runner.analysis_data import read_jsonl, source_time
from src.runner.theme_universe_loader import ThemeUniverseLoader

UTC = timezone.utc
THEME_QUERY_TERMS = "정책 규제 수요 공급 수주"
STOCK_QUERY_TERMS = "실적 수주 계약 공급 투자"
THEME_MAX_AGE = timedelta(days=30)
STOCK_MAX_AGE = {"news": timedelta(days=90), "general_news": timedelta(days=90), "dart": timedelta(days=400)}
EXCERPT_CHARS = 500
K1, B = 1.2, 0.75

ANALYST_RAG_MODES = ("off", "stock")
STOCK_RAG_VARIANT = "analyst-rag-stock-v1"
STOCK_RAG_NOTE = (" retrieved_context lists older documents about this stock found by search, outside its current"
                  " events. They are dated, may be less relevant than the events, are not this stock's current"
                  " events, and may be cited by their source_id.")

_HANGUL = re.compile(r"[가-힣]+")
_ASCII = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    """Hangul character bigrams (a lone syllable stays whole) and ASCII words."""
    text = unicodedata.normalize("NFKC", text).lower()
    out: list[str] = []
    for run in _HANGUL.findall(text):
        out.extend([run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)])
    out.extend(word for word in _ASCII.findall(text) if len(word) > 1 or word.isdigit())
    return out


def bm25_scores(documents: list[list[str]], query: list[str]) -> list[float]:
    """Okapi BM25 of each tokenized document; the statistics are those of `documents` only."""
    if not documents:
        return []
    frequencies = [Counter(doc) for doc in documents]
    lengths = [len(doc) for doc in documents]
    average = sum(lengths) / len(lengths) or 1.0
    df = Counter(term for counts in frequencies for term in counts)
    n = len(documents)
    terms = set(query)
    scores = []
    for counts, length in zip(frequencies, lengths):
        score = 0.0
        for term in terms:
            tf = counts.get(term, 0)
            if tf:
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / average))
        scores.append(score)
    return scores


@dataclass(frozen=True)
class Document:
    theme: str
    stock_code: str
    stock_name: str
    kind: str
    url: str
    title: str
    body: str
    published: datetime
    available: datetime
    text_hash: str

    @property
    def source_id(self) -> str:
        return "retrieved:" + hashlib.sha256(f"{self.url}|{self.text_hash}".encode()).hexdigest()[:24]


def admit(row: dict) -> tuple[dict | None, str | None]:
    """The live loader's per-row gates (`LocalAnalysisData.load_evidence`), without its stock and
    attention-window filters: (fields, None) for a usable document, (None, reason) otherwise."""
    from src.ingestion.dart import DartDisclosureCollector

    outer = row.get("metadata") or {}
    meta = {**(outer.get("metadata") or {}), **outer}
    kind = row.get("source_type") or meta.get("source_type")
    if kind not in {"dart", "news", "general_news"}:
        return None, "not a document source"
    try:
        published = source_time(row.get("published_at") or meta.get("published_at"))
        observed = source_time(row.get("collected_at") or meta.get("collected_at"), naive_zone=UTC)
    except (TypeError, ValueError):
        return None, "missing publication or collection time"
    title = str(row.get("title") or meta.get("title") or "")
    if kind in {"news", "general_news"}:
        if (meta.get("entity_match") or {}).get("matched") is False:
            return None, "unverified news subject"
        if meta.get("publication_time_status") in {"estimated", "missing", "invalid"}:
            return None, "unverified news publication time"
    url = row.get("url") or meta.get("url")
    full_body = row.get("content") or meta.get("content")
    if not full_body and "chunk_index" in meta:
        return None, "fragment without the full document"
    body = full_body or row.get("text")
    if not url or not body:
        return None, "evidence requires URL and content"
    if kind == "dart" and meta.get("evidence_scope") == "structured_fields":
        verified = DartDisclosureCollector.structured_fields_content(title, meta)
        if not verified or full_body != verified:
            return None, "DART structured fields do not match verified provider content"
    elif kind == "dart" and (meta.get("has_body") is False or meta.get("body_extracted") is False
                             or meta.get("wrapper_text_detected") is True or meta.get("mojibake_detected") is True
                             or meta.get("body_source") in {"title_fallback", "title_only"}):
        return None, "DART body quality is not suitable for event analysis"
    return {"kind": kind, "url": str(url), "title": title, "body": str(body), "published": published,
            "available": max(published, observed)}, None


def document(row: dict, theme: str, fields: dict) -> Document:
    return Document(theme=theme, stock_code=str(ThemeUniverseLoader._stock_code(row) or ""),
                    stock_name=str(row.get("stock_name") or ""),
                    text_hash=hashlib.sha256(fields["body"].encode("utf-8")).hexdigest(),
                    **{k: fields[k] for k in ("kind", "url", "title", "body", "published", "available")})


class RetrievalCorpus:
    """Admitted document revisions, by theme."""

    def __init__(self, documents: list[Document], members: dict[str, list[str]], rejected: Counter):
        self.documents, self.members, self.rejected = documents, members, rejected
        self.theme_of = {code: theme for theme, codes in members.items() for code in codes}

    def visible(self, as_of: datetime) -> list[Document]:
        """What PAPER could read at `as_of`: per document (source type and URL) its latest revision
        available by then, and one copy of a text published under several URLs."""
        latest: dict[tuple[str, str], Document] = {}
        for doc in self.documents:
            if doc.available > as_of:
                continue
            key = (doc.kind, doc.url)
            if key not in latest or doc.available > latest[key].available:
                latest[key] = doc
        by_text: dict[str, Document] = {}
        for doc in sorted(latest.values(), key=lambda d: (d.available, d.url)):
            by_text.setdefault(doc.text_hash, doc)
        return sorted(by_text.values(), key=lambda d: (d.available, d.url))

    def retrieve(self, stock_code: str, stock_name: str, as_of: datetime, *, exclude_urls: set[str],
                 k_theme: int = 3, k_stock: int = 2, focus_stock: bool = False) -> list[dict[str, Any]]:
        """The best theme and stock items. With `focus_stock`, a stock item's excerpt is the part of
        its text that names the stock most often (a roundup's paragraph on it), not the part that
        matches the generic query terms best."""
        theme = self.theme_of.get(stock_code)
        if theme is None:
            return []
        label = re.sub(r"[()_]+", " ", theme).strip()
        visible = [doc for doc in self.visible(as_of) if doc.url not in exclude_urls]
        theme_pool = [doc for doc in visible if doc.theme == theme and doc.stock_code != stock_code
                      and as_of - doc.published <= THEME_MAX_AGE]
        stock_pool = [doc for doc in visible if doc.stock_code == stock_code
                      and as_of - doc.published <= STOCK_MAX_AGE[doc.kind]]
        return (self._ranked(theme_pool, f"{label} {THEME_QUERY_TERMS}", k_theme, "theme")
                + self._ranked(stock_pool, f"{stock_name} {label} {STOCK_QUERY_TERMS}", k_stock, "stock",
                               focus=stock_name if focus_stock else None))

    @staticmethod
    def _ranked(pool: list[Document], query: str, limit: int, kind: str, focus: str | None = None) -> list[dict[str, Any]]:
        query_terms = tokens(query)
        scores = bm25_scores([tokens(doc.title + " " + doc.body) for doc in pool], query_terms)
        ranked = sorted(zip(scores, pool), key=lambda pair: (-pair[0], -pair[1].available.timestamp(), pair[1].url))
        return [_item(doc, score, kind, query_terms, focus) for score, doc in ranked[:limit] if score > 0]


def _excerpt(body: str, query_terms: list[str], focus: str | None = None) -> tuple[str, bool]:
    """A window of the body at most EXCERPT_CHARS long: the one that names `focus` most often when it
    is given and named, otherwise the one with the most query term hits."""
    text = " ".join(body.split())
    if len(text) <= EXCERPT_CHARS:
        return text, False
    if focus:
        starts = {max(0, min(match.start() - 50, len(text) - EXCERPT_CHARS)) for match in re.finditer(re.escape(focus), text)}
        if starts:
            start = max(sorted(starts), key=lambda at: text[at:at + EXCERPT_CHARS].count(focus))
            return text[start:start + EXCERPT_CHARS], True
    wanted = set(query_terms)
    best, best_start = -1, 0
    for start in range(0, len(text) - EXCERPT_CHARS + 1, 100):
        hits = sum(1 for term in tokens(text[start:start + EXCERPT_CHARS]) if term in wanted)
        if hits > best:
            best, best_start = hits, start
    return text[best_start:best_start + EXCERPT_CHARS], True


def _item(doc: Document, score: float, kind: str, query_terms: list[str], focus: str | None = None) -> dict[str, Any]:
    excerpt, truncated = _excerpt(doc.body, query_terms, focus)
    return {"source_id": doc.source_id, "context_kind": kind, "source_type": doc.kind,
            "stock_code": doc.stock_code, "stock_name": doc.stock_name, "title": doc.title,
            "published_at": doc.published.isoformat(), "available_at": doc.available.isoformat(),
            "url": doc.url, "excerpt": excerpt, "excerpt_truncated": truncated, "bm25": round(score, 3)}


def fit_context(payload: dict, items: list[dict], budget: int) -> dict:
    """Existing evidence keeps its room; retrieved items fill what is left, best first. Omitted
    items are counted in data_gaps (the payload is measured with that gap included)."""
    from src.runner.shared_analysis import _payload_tokens

    base_gaps = list(payload.get("data_gaps") or [])

    def build(kept: list[dict]) -> dict:
        omitted = len(items) - len(kept)
        return {**payload, "retrieved_context": kept,
                "source_ids": list(payload["source_ids"]) + [item["source_id"] for item in kept],
                "data_gaps": base_gaps + ([f"retrieved_context_omitted_for_input_budget:{omitted}"] if omitted else [])}

    kept: list[dict] = []
    for item in items:
        if _payload_tokens(build(kept + [item])) > budget:
            break
        kept.append(item)
    result = build(kept)
    if _payload_tokens(result) > budget:  # not even the empty section and its gap fit
        return {**payload, "data_gaps": base_gaps + [f"retrieved_context_omitted_for_input_budget:{len(items)}"]}
    return result


def event_urls(payload: dict) -> set[str]:
    urls = {source.get("url") for event in payload.get("events") or [] for source in event.get("sources") or []}
    urls |= {doc.get("url") for doc in payload.get("documents") or []}
    return {url for url in urls if url}


def names_stock(title: str, stock_name: str) -> bool:
    """Whether a title names the stock, ignoring case, width and spacing."""
    def flat(text: str) -> str:
        return "".join(unicodedata.normalize("NFKC", text).lower().split())
    return bool(stock_name) and flat(stock_name) in flat(title)


def analyst_rag_from_env() -> str:
    mode = os.getenv("HQA_ANALYST_RAG", "off")
    if mode not in ANALYST_RAG_MODES:
        raise ValueError(f"HQA_ANALYST_RAG must be one of {list(ANALYST_RAG_MODES)}")
    return mode


class AnalystRetrieval:
    """The live analyst's retrieved context (`HQA_ANALYST_RAG=stock`): at most `k_stock` of the stock's
    own older documents that its event packets do not carry. The documents come from the file the live
    loader reads for each of the stock's themes (`LocalAnalysisData.documents_path`), and each file is
    parsed once per version (its path, modification time and size).

    A news item counts as the stock's own only when its title names the stock. The collector tags news
    with every stock whose code it mentions, so market wraps and broker roundups would otherwise fill
    the pool: on 2026-11-03 only 50 of the 24 stocks' 420 older news items named their stock in the
    title. Titles that abbreviate a name (LG엔솔, SK이노) are missed; precision matters more for two
    background items. Filings are the company's own and always count. A roundup's excerpt is its
    paragraph on the stock (`focus_stock`)."""

    variant = STOCK_RAG_VARIANT
    note = STOCK_RAG_NOTE

    def __init__(self, data: Any, *, mode: str = "stock", k_stock: int = 2):
        if mode != "stock":
            raise ValueError(f"unknown analyst retrieval mode: {mode}")
        self.data, self.k_stock = data, k_stock
        self._lock = threading.Lock()
        self._files: dict[str, tuple[Path, tuple[int, int], list[Document]]] = {}

    def _documents(self, theme: str, path: Path) -> list[Document]:
        stat = path.stat()
        version = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            cached = self._files.get(theme)
        if cached is not None and cached[:2] == (path, version):
            return cached[2]
        documents = []
        for row in read_jsonl(path):
            fields, _ = admit(row)
            if fields is not None:
                documents.append(document(row, theme, fields))
        with self._lock:
            self._files[theme] = (path, version, documents)
        return documents

    def add_context(self, candidate: dict, payload: dict, as_of: datetime, budget: int) -> dict:
        code = candidate["stock_code"]
        themes = sorted(candidate.get("theme_keys") or [])
        documents = []
        for theme in themes:
            path = self.data.documents_path(candidate, theme)
            if path is not None:
                documents.extend(doc for doc in self._documents(theme, path) if doc.stock_code == code
                                 and (doc.kind == "dart" or names_stock(doc.title, payload["stock_name"])))
        # The query names the stock's last theme, as the replay's corpus did for a stock in several.
        corpus = RetrievalCorpus(documents, {themes[-1]: [code]} if themes else {}, Counter())
        items = corpus.retrieve(code, payload["stock_name"], as_of, exclude_urls=event_urls(payload),
                                k_theme=0, k_stock=self.k_stock, focus_stock=True)
        return fit_context(payload, items, budget)
