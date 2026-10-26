"""Analyst RAG variant: point-in-time retrieval, dedupe, the live loader's gates, and a variant
service that changes nothing but the analyst's request."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from backtesting.fitness.analyst_rag import (ANALYST_RAG_NOTE, RetrievalCorpus, admit, bm25_scores, event_urls,
                                             fit_context, make_service_class, tokens)
from src.runner import shared_analysis
from src.runner.analysis_contracts import SpecialistResult

KST = timezone(timedelta(hours=9))
AS_OF = datetime(2026, 9, 22, 9, 15, tzinfo=KST)
MEMBERS = {"2차전지": ["000001", "000002"], "5g": ["000003"]}


def news(code, url, body, published="2026-09-20T10:00:00+09:00", collected="2026-09-21T00:30:00+09:00", **meta):
    return {"source_type": "news", "stock_code": code, "stock_name": f"종목{code}", "url": url, "title": body[:20],
            "content": body, "published_at": published,
            "metadata": {"collected_at": collected, "entity_match": {"matched": True},
                         "publication_time_status": "confirmed", **meta}}


def corpus(rows):
    from backtesting.fitness.analyst_rag import Document, UTC
    import hashlib

    docs, rejected = [], Counter()
    theme_of = {code: theme for theme, codes in MEMBERS.items() for code in codes}
    for row in rows:
        fields, reason = admit(row)
        if fields is None:
            rejected[reason] += 1
            continue
        docs.append(Document(theme=theme_of[row["stock_code"]], stock_code=row["stock_code"],
                             stock_name=row["stock_name"], text_hash=hashlib.sha256(fields["body"].encode()).hexdigest(),
                             **{k: fields[k] for k in ("kind", "url", "title", "body", "published", "available")}))
    return RetrievalCorpus(docs, MEMBERS, rejected)


def test_korean_bigrams_match_across_particles():
    assert "수주" in tokens("대규모 수주를 따냈다") and "수주" in tokens("수주가")
    scores = bm25_scores([tokens("배터리 공급계약 수주를 체결"), tokens("날씨가 맑다")], tokens("수주 공급"))
    assert scores[0] > 0 == scores[1]


def test_no_document_from_after_the_decision_can_be_retrieved():
    c = corpus([news("000002", "u-past", "2차전지 수주 공급 정책"),
                news("000002", "u-future", "2차전지 수주 공급 정책 규제", published="2026-09-22T08:00:00+09:00",
                     collected="2026-09-23T00:30:00+09:00")])
    urls = [item["url"] for item in c.retrieve("000001", "종목000001", AS_OF, exclude_urls=set())]
    assert urls == ["u-past"]
    later = [item["url"] for item in c.retrieve("000001", "종목000001", AS_OF + timedelta(days=2), exclude_urls=set())]
    assert set(later) == {"u-past", "u-future"}


def test_a_revision_counts_only_from_its_own_collection_time():
    c = corpus([news("000002", "u", "2차전지 수주 첫 판", collected="2026-09-21T00:30:00+09:00"),
                news("000002", "u", "2차전지 수주 고친 판", collected="2026-09-23T09:00:00+09:00")])
    assert [item["excerpt"] for item in c.retrieve("000001", "x", AS_OF, exclude_urls=set())] == ["2차전지 수주 첫 판"]
    assert [item["excerpt"] for item in c.retrieve("000001", "x", AS_OF + timedelta(days=2), exclude_urls=set())] == [
        "2차전지 수주 고친 판"]


def test_one_item_per_document_version_and_per_text():
    same = "2차전지 공급 수주 소식 본문"
    c = corpus([news("000002", "u1", same), news("000002", "u1", same), news("000002", "u2", same)])
    items = c.retrieve("000001", "x", AS_OF, exclude_urls=set())
    assert len(items) == 1 and len({item["source_id"] for item in items}) == 1


def test_the_live_loaders_quality_gates_apply():
    title_only = {"source_type": "dart", "stock_code": "000002", "stock_name": "x", "url": "d", "title": "단일판매ㆍ공급계약체결",
                  "content": "x 공시: 단일판매ㆍ공급계약체결", "published_at": "2026-09-20",
                  "metadata": {"collected_at": "2026-09-21T00:30:00+09:00", "body_source": "title_fallback",
                               "has_body": False}}
    rows = [title_only, news("000002", "a", "수주", entity_match={"matched": False}),
            news("000002", "b", "수주", publication_time_status="estimated"),
            {**news("000002", "c", "수주"), "content": None}]
    c = corpus(rows)
    assert c.documents == [] and sum(c.rejected.values()) == 4
    assert c.rejected["DART body quality is not suitable for event analysis"] == 1


def test_pools_exclude_the_stocks_events_and_keep_theme_and_stock_apart():
    c = corpus([news("000001", "own-old", "종목000001 2차전지 수주 실적", published="2026-08-01T10:00:00+09:00",
                     collected="2026-08-02T00:30:00+09:00"),
                news("000001", "own-event", "종목000001 2차전지 수주 실적 계약"),
                news("000002", "peer", "2차전지 정책 수요 공급"),
                news("000003", "other-theme", "2차전지 정책 수요 공급 수주")])
    items = c.retrieve("000001", "종목000001", AS_OF, exclude_urls={"own-event"})
    assert {(item["context_kind"], item["url"]) for item in items} == {("theme", "peer"), ("stock", "own-old")}


def test_retrieved_context_takes_only_the_room_left_after_existing_evidence():
    payload = {"stock_code": "000001", "stock_name": "x", "events": [{"text": "가" * 400}], "documents": [],
               "source_ids": ["event:1"], "data_gaps": []}
    items = [{"source_id": f"retrieved:{i}", "excerpt": "나" * 300} for i in range(5)]
    base = shared_analysis._payload_tokens(payload)
    fitted = fit_context(payload, items, base + 700)
    assert fitted["events"] == payload["events"] and 0 < len(fitted["retrieved_context"]) < 5
    assert fitted["source_ids"][:1] == ["event:1"]
    assert fitted["data_gaps"] == [f"retrieved_context_omitted_for_input_budget:{5 - len(fitted['retrieved_context'])}"]
    assert shared_analysis._payload_tokens(fitted) <= base + 700
    starved = fit_context(payload, items, base)
    assert "retrieved_context" not in starved and starved["data_gaps"] == ["retrieved_context_omitted_for_input_budget:5"]


def test_event_urls_cover_event_sources_and_bare_documents():
    payload = {"events": [{"sources": [{"url": "e1"}, {"url": "e2"}]}], "documents": [{"url": "d1"}]}
    assert event_urls(payload) == {"e1", "e2", "d1"}


class Capture:
    model_name, reasoning_effort, max_tokens, hqa_input_limit = "claude-opus-5-5", "low", 4000, 12000

    def __init__(self, cite=None):
        self.messages, self.cite = [], cite

    def with_structured_output(self, schema, method=None, strict=None):
        model = self

        class Runnable:
            def invoke(self, messages):
                model.messages.append(messages)
                payload = json.loads(messages[-1][1])
                return SpecialistResult(stock_code=payload["stock_code"], role=payload.get("_role", "analyst"),
                                        score=55.0, confidence=50, thesis="t", risks=[], data_gaps=[],
                                        citations=[{"source_id": model.cite or payload["source_ids"][0], "claim": "c"}])

        return Runnable()


def service(cls, model, clock=AS_OF):
    return cls(data=None, accounts=None, models={"analyst": model, "quant": model, "chartist": model,
                                                 "risk_manager": model}, clock=lambda: clock)


def test_only_the_analyst_request_changes():
    live, variant = Capture(), Capture()
    Rag = make_service_class()
    payload = {"stock_code": "000001", "stock_name": "x", "source_ids": ["s"], "_role": "chartist"}
    shared_analysis.SharedAnalysisService._invoke(service(shared_analysis.SharedAnalysisService, live), "chartist",
                                                  SpecialistResult, payload)
    Rag._invoke(service(Rag, variant), "chartist", SpecialistResult, payload)
    assert live.messages == variant.messages
    analyst = {**payload, "_role": "analyst"}
    shared_analysis.SharedAnalysisService._invoke(service(shared_analysis.SharedAnalysisService, live), "analyst",
                                                  SpecialistResult, analyst)
    Rag._invoke(service(Rag, variant), "analyst", SpecialistResult, analyst)
    live_system, variant_system = live.messages[-1][0][1], variant.messages[-1][0][1]
    instructions = shared_analysis.ROLE_INSTRUCTIONS["analyst"]
    assert variant_system == live_system.replace(instructions, instructions + ANALYST_RAG_NOTE, 1)
    assert live.messages[-1][1] == variant.messages[-1][1]


def test_the_analyst_may_cite_what_it_retrieved():
    rows = [news("000002", "peer", "2차전지 정책 수요 공급 수주")]
    Rag = make_service_class()
    c = corpus(rows)
    retrieved_id = c.retrieve("000001", "x", AS_OF, exclude_urls=set())[0]["source_id"]
    model = Capture(cite=retrieved_id)
    svc = service(Rag, model)
    svc.corpus = c
    payload = {"stock_code": "000001", "stock_name": "x", "events": [], "documents": [], "source_ids": ["event:1"],
               "data_gaps": []}
    result = svc._specialist("analyst", payload, False)
    assert result.citations[0].source_id == retrieved_id
    sent = json.loads(model.messages[-1][1][1])
    assert sent["retrieved_context"][0]["source_id"] == retrieved_id and retrieved_id in sent["source_ids"]
