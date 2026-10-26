"""Retrieved background for the analyst: a small point-in-time BM25 over the replay corpus.

The analyst's dated event packets stay exactly as they are. The variant adds `retrieved_context`:
a few documents the events do not already carry, in two pools,

- theme: documents collected for the *other* stocks of the stock's theme (news and filings about
  the industry the stock trades in), at most 30 days old;
- stock: the stock's own documents outside its event packets (older news, earlier filings), news
  at most 90 days and filings at most 400 days old;

each ranked by Okapi BM25 over Hangul character bigrams and ASCII words. Korean particles stay
attached to words, so whole-word matching misses most hits; bigrams match across them, and nothing
has to be installed. Only documents PAPER could have read at the decision time count (available =
max(published, collected) <= as_of, the live loader's rule), with the live loader's own quality
gates and one item per document version, and the corpus statistics are those of that moment.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.runner.analysis_data import read_jsonl, source_time
from src.runner.theme_universe_loader import ThemeUniverseLoader

UTC = timezone.utc
VARIANT = "analyst-rag-v1"
RAG_PROMPT_VERSION = "hqa-fixed-dag-v8-backend-plan-rules+" + VARIANT
ANALYST_RAG_NOTE = (" retrieved_context lists background documents found by search: theme items about other stocks of"
                    " the same theme and older items about this stock. They are dated, may be less relevant than the"
                    " events, are not this stock's current events, and may be cited by their source_id.")
THEME_QUERY_TERMS = "정책 규제 수요 공급 수주"
STOCK_QUERY_TERMS = "실적 수주 계약 공급 투자"
THEME_MAX_AGE = timedelta(days=30)
STOCK_MAX_AGE = {"news": timedelta(days=90), "general_news": timedelta(days=90), "dart": timedelta(days=400)}
EXCERPT_CHARS = 500
K1, B = 1.2, 0.75

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


class RetrievalCorpus:
    """Every admitted document revision of the replay copy, by theme."""

    def __init__(self, documents: list[Document], members: dict[str, list[str]], rejected: Counter):
        self.documents, self.members, self.rejected = documents, members, rejected
        self.theme_of = {code: theme for theme, codes in members.items() for code in codes}

    @classmethod
    def from_pit(cls, pit_dir: Path) -> "RetrievalCorpus":
        members: dict[str, list[str]] = {}
        for path in sorted((Path(pit_dir) / "raw" / "theme_targets").glob("*.jsonl")):
            members[path.stem] = [str(row["stock_code"]) for row in read_jsonl(path)]
        documents, rejected = [], Counter()
        for theme in sorted(members):
            path = Path(pit_dir) / "canonical_index" / theme / "documents.jsonl"
            if not path.exists():
                continue
            for row in read_jsonl(path):
                fields, reason = admit(row)
                if fields is None:
                    rejected[reason] += 1
                    continue
                documents.append(Document(theme=theme, stock_code=str(ThemeUniverseLoader._stock_code(row) or ""),
                                          stock_name=str(row.get("stock_name") or ""),
                                          text_hash=hashlib.sha256(fields["body"].encode("utf-8")).hexdigest(),
                                          **{k: fields[k] for k in ("kind", "url", "title", "body", "published",
                                                                    "available")}))
        return cls(documents, members, rejected)

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
                 k_theme: int = 3, k_stock: int = 2) -> list[dict[str, Any]]:
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
                + self._ranked(stock_pool, f"{stock_name} {label} {STOCK_QUERY_TERMS}", k_stock, "stock"))

    @staticmethod
    def _ranked(pool: list[Document], query: str, limit: int, kind: str) -> list[dict[str, Any]]:
        query_terms = tokens(query)
        scores = bm25_scores([tokens(doc.title + " " + doc.body) for doc in pool], query_terms)
        ranked = sorted(zip(scores, pool), key=lambda pair: (-pair[0], -pair[1].available.timestamp(), pair[1].url))
        return [_item(doc, score, kind, query_terms) for score, doc in ranked[:limit] if score > 0]


def _excerpt(body: str, query_terms: list[str]) -> tuple[str, bool]:
    """The best-matching window of the body (by query term hits), at most EXCERPT_CHARS long."""
    text = " ".join(body.split())
    if len(text) <= EXCERPT_CHARS:
        return text, False
    wanted = set(query_terms)
    best, best_start = -1, 0
    for start in range(0, len(text) - EXCERPT_CHARS + 1, 100):
        hits = sum(1 for term in tokens(text[start:start + EXCERPT_CHARS]) if term in wanted)
        if hits > best:
            best, best_start = hits, start
    return text[best_start:best_start + EXCERPT_CHARS], True


def _item(doc: Document, score: float, kind: str, query_terms: list[str]) -> dict[str, Any]:
    excerpt, truncated = _excerpt(doc.body, query_terms)
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


def make_service_class():
    """The live SharedAnalysisService with the analyst variant: retrieved context added to the
    analyst's payload inside its leftover budget, and the variant note in the analyst prompt.
    Every other role's request is built exactly as the live service builds it."""
    from src.runner import shared_analysis
    from src.runner.analysis_data import content_hash
    from src.utils.llm_queue import LLMTaskPriority, llm_task_priority

    class RagAnalystService(shared_analysis.SharedAnalysisService):
        corpus: RetrievalCorpus

        def _specialist(self, role, payload, critical, priority=LLMTaskPriority.SCHEDULED):
            if role == "analyst":
                items = self.corpus.retrieve(payload["stock_code"], payload["stock_name"], self.clock(),
                                             exclude_urls=event_urls(payload))
                payload = fit_context(payload, items, self._input_budget("analyst"))
            return super()._specialist(role, payload, critical, priority)

        def _invoke(self, role, schema, payload, *, critical=False, priority=LLMTaskPriority.SCHEDULED):
            if role != "analyst":
                return super()._invoke(role, schema, payload, critical=critical, priority=priority)
            # The live _invoke, with the variant's analyst note and prompt version.
            prompt = shared_analysis.ROLE_INSTRUCTIONS[role] + ANALYST_RAG_NOTE
            prompt += (f" You are the {role} specialist: set role to \"{role}\" and stock_code to the supplied stock_code."
                       " Cite only IDs listed in source_ids."
                       " score is a number from 0 (strongly unfavorable) to 100 (strongly favorable) for a new long"
                       " position based only on this role's evidence; confidence is an integer from 0 to 100 for how"
                       " well the supplied evidence supports that score.")
            messages = [("system", prompt + shared_analysis.PROMPT_SUFFIX),
                        ("human", json.dumps(payload, ensure_ascii=False, allow_nan=False))]
            request_id = self.audit.append("llm_request", {
                "role": role, "model": self._model_names().get(role, shared_analysis.MODEL_VERSION),
                "prompt_version": RAG_PROMPT_VERSION, "input_hash": content_hash(payload), "holding_priority": critical,
                "instructions": messages[0][1], "payload": payload, "schema": schema.model_json_schema()}) if self.audit else None
            try:
                with llm_task_priority(LLMTaskPriority.RUNTIME if critical else priority):
                    output = self.models[role].with_structured_output(schema, method="json_schema", strict=True).invoke(messages)
                result = output if isinstance(output, schema) else schema.model_validate(output)
            except Exception as exc:
                if self.audit:
                    self.audit.append("llm_failure", {"request_id": request_id, "role": role,
                                                      "error_type": type(exc).__name__, "error": str(exc)})
                raise
            if self.audit:
                self.audit.append("llm_response", {"request_id": request_id, "role": role,
                                                  "validation": "schema_only", "output": result.model_dump(mode="json")})
            return result

    return RagAnalystService
