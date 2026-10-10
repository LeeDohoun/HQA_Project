from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from src.runner.event_evidence import build_event_evidence, select_event_evidence
from src.runner.shared_analysis import (DEFAULT_SPECIALIST_INPUT_TOKENS, SPECIALIST_PROMPT_RESERVE_TOKENS,
                                        estimate_tokens, fit_specialist_payload)
from tests.test_shared_analysis import NOW, EventData, service

BUDGET = DEFAULT_SPECIALIST_INPUT_TOKENS - SPECIALIST_PROMPT_RESERVE_TOKENS
BODY = "에코프로비엠은 양극재 공급계약을 체결했다고 공시했다. 계약 규모와 기간, 상대방 정보가 원문에 기재되어 있다. " * 40


def news(index, *, hours_ago):
    text = f"[{index}] " + BODY
    published = (NOW - timedelta(hours=hours_ago)).isoformat()
    return {"source_id": f"doc:{index:03d}", "source_type": "news", "url": f"https://news.example/article/{index}",
            "title": f"에코프로비엠 {index}차 공급계약 체결 발표", "text": text[:3500], "published_at": published,
            "available_at": published, "source_text_hash": hashlib.sha256(text.encode()).hexdigest(),
            "truncated": len(text) > 3500, "original_characters": len(text),
            "metadata": {"published_at_precision": "datetime"}}


def analyst_payload(documents):
    events = select_event_evidence(build_event_evidence(documents, "247540"), as_of=NOW)
    return {"stock_code": "247540", "stock_name": "에코프로비엠", "documents": [], "events": events,
            "source_ids": [row["source_id"] for row in documents] + [event["event_id"] for event in events],
            "data_gaps": []}, events


def test_small_analyst_payload_is_kept_and_only_visible_sources_are_citable():
    payload, events = analyst_payload([news(1, hours_ago=2)])
    fitted = fit_specialist_payload("analyst", payload, BUDGET)
    assert fitted["events"] == events and fitted["data_gaps"] == []
    assert set(fitted["source_ids"]) == {events[0]["event_id"], "doc:001"}


def test_large_analyst_payload_keeps_priority_prefix_within_budget():
    payload, events = analyst_payload([news(index, hours_ago=index) for index in range(1, 9)])
    assert len(events) == 8 and estimate_tokens(json.dumps(payload, ensure_ascii=False)) > BUDGET
    fitted = fit_specialist_payload("analyst", payload, BUDGET)
    assert estimate_tokens(json.dumps(fitted, ensure_ascii=False)) <= BUDGET
    kept_ids = [event["event_id"] for event in fitted["events"]]
    assert kept_ids == [event["event_id"] for event in events][:len(kept_ids)]  # priority order preserved
    assert 1 <= len(kept_ids) < 8
    assert any(gap.startswith("analyst_events_omitted_for_input_budget:") for gap in fitted["data_gaps"])
    visible = {source for event in fitted["events"] for source in [event["event_id"], *event["source_ids"]]}
    assert set(fitted["source_ids"]) == visible


def test_quant_disclosures_keep_flagged_then_newest_in_chronological_order():
    disclosures = [{"source_id": f"dart:{index:03d}", "source_type": "dart", "title": f"공시 {index}",
                    "available_at": (NOW - timedelta(days=60 - index)).isoformat(), "text": BODY[:1200],
                    "risk_flags": ["correction"] if index == 3 else [], "is_correction": index == 3}
                   for index in range(40)]
    payload = {"stock_code": "247540", "stock_name": "에코프로비엠",
               "financial_snapshot": {"status": "ready", "source_id": "fin:247540"},
               "disclosures": disclosures, "source_ids": ["fin:247540"] + [row["source_id"] for row in disclosures]}
    fitted = fit_specialist_payload("quant", payload, BUDGET)
    kept = [row["source_id"] for row in fitted["disclosures"]]
    assert estimate_tokens(json.dumps(fitted, ensure_ascii=False)) <= BUDGET
    assert "dart:003" in kept and "dart:039" in kept and "dart:004" not in kept
    assert kept == sorted(kept)  # chronological, as collected
    assert fitted["source_ids"] == ["fin:247540"] + kept
    assert fitted["data_gaps"] == [f"quant_disclosures_omitted_for_input_budget:{40 - len(kept)}"]


def test_quant_payload_within_budget_is_unchanged():
    payload = {"stock_code": "247540", "financial_snapshot": {"status": "ready", "source_id": "fin:1"},
               "disclosures": [], "source_ids": ["fin:1"]}
    assert fit_specialist_payload("quant", payload, BUDGET) is payload


def test_news_heavy_stock_still_gets_an_analyst_result_in_the_cycle():
    class NewsHeavy(EventData):
        def load_evidence(self, candidate, as_of):
            evidence = super().load_evidence(candidate, as_of)
            documents = [news(index, hours_ago=index) for index in range(1, 9)]
            for document in documents:
                document["source_id"] += ":" + candidate["stock_code"]
            evidence["documents"] = documents
            evidence["events"] = select_event_evidence(build_event_evidence(documents, candidate["stock_code"]), as_of=as_of)
            return evidence

    engine, calls = service(data=NewsHeavy())
    result = engine.preview_stock("000001")
    analyst = [payload for role, payload in calls if role == "analyst"]
    assert analyst and estimate_tokens(json.dumps(analyst[0], ensure_ascii=False)) <= BUDGET
    assert any(gap.startswith("analyst_events_omitted_for_input_budget:") for gap in analyst[0]["data_gaps"])
    assert set(result["specialists"]) == {"analyst", "quant", "chartist"}
    assert not [gap for gap in result["data_gaps"] if gap.startswith("analyst_input:")]


def test_chartist_payload_is_left_to_its_design_bound():
    payload = {"stock_code": "247540", "event_reactions": [{"event_id": f"event:{i}", "title": BODY[:900]} for i in range(8)],
               "source_ids": ["price:247540:abc"]}
    assert fit_specialist_payload("chartist", payload, 1_000) is payload


def test_analyst_fit_counts_its_own_gap_strings_instead_of_dropping_the_analyst():
    # At the production budget the first six events fit only without the omission note;
    # the fitter must fall back to five events plus a truncated sixth, not fail the stock.
    lengths = [3118, 1050, 2042, 596, 1401, 368, 2898, 662]
    documents = [news(index, hours_ago=index) for index in range(1, 9)]
    for document, length in zip(documents, lengths):
        document["text"] = document["text"][:length]
    payload, events = analyst_payload(documents)
    fitted = fit_specialist_payload("analyst", payload, BUDGET)
    assert estimate_tokens(json.dumps(fitted, ensure_ascii=False)) <= BUDGET
    assert fitted["events"] and any(gap.startswith("analyst_events_omitted_for_input_budget:")
                                    for gap in fitted["data_gaps"])


def test_truncated_analyst_text_is_measured_even_when_characters_cost_more_than_hangul():
    documents = [news(index, hours_ago=index) for index in range(1, 9)]
    for document in documents:
        text = "\u2605\"\\" * 1100  # symbols and JSON escapes cost more than 1.05 tokens per char
        document.update(text=text, original_characters=len(text), truncated=False,
                        source_text_hash=hashlib.sha256(text.encode()).hexdigest())
    payload, _ = analyst_payload(documents)
    fitted = fit_specialist_payload("analyst", payload, BUDGET)
    assert estimate_tokens(json.dumps(fitted, ensure_ascii=False)) <= BUDGET
