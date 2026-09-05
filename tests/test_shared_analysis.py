from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.runner.analysis_contracts import AccountSnapshot, Predicate, TradingPlan
from src.runner.analysis_data import PRICE_WEIGHTS, content_hash, price_features, rank_price_candidates
from src.runner.analysis_scheduler import AnalysisScheduler, seconds_until_next_slot, within_analysis_session
from src.runner.shared_analysis import SharedAnalysisService, SingleFlightCache

NOW = datetime(2026, 9, 4, 5, tzinfo=timezone.utc)


def holding(code="000001"):
    return {"stockCode": code, "stockName": "Stock " + code, "quantity": 4, "sellableQuantity": 4,
            "avgPrice": 100.0, "currentPrice": 110.0, "evalAmount": 440.0, "pnlRate": 10.0}


def snapshot(user_id, holdings=()):
    return AccountSnapshot.model_validate({"userId": user_id, "accountMode": "PAPER", "success": True,
        "capturedAt": NOW.isoformat(), "source": "kis", "maxPositionPct": 20.0,
        "dailyBuyLimit": 1000000.0, "orderableCash": 10000.0, "orderableCashSource": "deposit_upper_bound",
        "reservedCash": 0.0, "equity": 20000.0, "dailyPnlPct": 0.0, "dailyPnlBaselineSource": "kis",
        "entryEligible": True, "entryBlockReason": None, "holdings": list(holdings),
        "monitorCapacity": 10, "monitorSymbolCount": len(holdings), "monitorCapacityExceeded": False})


class Data:
    def __init__(self, count=25):
        self.count = count
        self.document_version = "v1"
        self.price_version = 1
        self.loads = 0

    def load_universe(self, as_of):
        self.loads += 1
        return [{"stock_code": f"{i:06d}", "stock_name": f"Stock {i:06d}", "theme_keys": ["a", "b"],
                 "features": {key: float(i) if key != "volatility_20d" else 1 / i for key in PRICE_WEIGHTS},
                 "price_history": [{"close": i * self.price_version}], "entry_filter_errors": []}
                for i in range(1, self.count + 1)], []

    def load_evidence(self, candidate, as_of):
        return {"documents": [{"source_id": "doc:" + candidate["stock_code"], "source_type": "dart",
                              "text": self.document_version}], "data_gaps": [],
                "financial_snapshot": {"status": "ready", "source_id": "fin:" + candidate["stock_code"], "values": {"revenue": 10}}}

    def load_technical(self, candidate):
        return {"indicators": {"MA150": 100 * self.price_version, "ATR": 5}, "data_gaps": []}


class EventData(Data):
    def __init__(self, source_type="dart"):
        super().__init__(1)
        self.source_type = source_type
        dates = [NOW.date() - timedelta(days=offset) for offset in range(60, 0, -1)]
        self.bars = [self.bar(day, 100.0 + index) for index, day in enumerate(
            [day for day in dates if day.weekday() < 5][-30:])]

    @staticmethod
    def bar(day, close):
        return {"available_at": datetime(day.year, day.month, day.day, 6, 30, tzinfo=timezone.utc).isoformat(),
                "open": close - 1, "high": close + 2, "low": close - 2, "close": close, "volume": 1000.0}

    def load_universe(self, as_of):
        rows, errors = super().load_universe(as_of)
        for row in rows:
            row["price_history"] = [dict(bar) for bar in self.bars if datetime.fromisoformat(bar["available_at"]) <= as_of]
        return rows, errors

    def load_evidence(self, candidate, as_of):
        from src.runner.event_evidence import build_event_evidence

        evidence = super().load_evidence(candidate, as_of)
        text = "단일판매 공급계약 원문 " + self.document_version
        document = {"source_id": "doc:" + candidate["stock_code"], "source_type": self.source_type,
                    "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260901000001" if self.source_type == "dart"
                           else "https://news.example/article/contract-1",
                    "title": "단일판매ㆍ공급계약체결", "text": text,
                    "published_at": "2026-09-01T00:00:00+09:00", "available_at": "2026-09-01T10:00:00+09:00",
                    "source_text_hash": hashlib.sha256(text.encode()).hexdigest(), "truncated": False,
                    "original_characters": len(text), "metadata": {"published_at_precision": "date"}}
        evidence["documents"] = [document]
        evidence["events"] = build_event_evidence([document], candidate["stock_code"])
        return evidence


class Accounts:
    def __init__(self):
        self.calls = []

    def fetch_accounts(self, ids):
        self.calls.append(ids)
        return {user: snapshot(user, [holding()]) for user in ids}

    def fetch_prices(self, user_id, codes):
        return {code: {"source_id": "quote:" + code, "current_price": 110.0,
                       "available_at": NOW.isoformat(), "source": "kis"} for code in codes}


class Model:
    model_name = "gpt-5.6-luna"
    reasoning_effort = "low"
    max_tokens = 1200

    def __init__(self, role, calls, invalid=False):
        self.role = role
        self.calls = calls
        self.invalid = invalid

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema
        assert kwargs == {"method": "json_schema", "strict": True}
        return self

    def invoke(self, messages):
        payload = json.loads(messages[-1][1])
        self.calls.append((self.role, payload))
        if self.role != "risk_manager":
            result = {"stock_code": payload["stock_code"], "role": self.role, "score": min(100.0, float(int(payload["stock_code"]))),
                      "confidence": 80, "thesis": "Evidence-grounded assessment", "risks": [],
                      "citations": [{"source_id": "invented" if self.invalid else payload["source_ids"][0], "claim": "Observed fact"}],
                      "data_gaps": []}
        else:
            holdings = {h["stockCode"]: h for h in payload["account"]["holdings"]}
            plans = []
            for row in payload["candidates"]:
                held = holdings.get(row["stock_code"])
                plans.append({"stock_code": row["stock_code"], "stock_name": row["stock_name"], "action": "HOLD",
                    "holding_quantity": held["quantity"] if held else 0, "confidence": 80, "risk_level": "MEDIUM",
                    "position_size_pct": 0.0, "entry_price": None, "stop_loss_price": 95.0 if held else None,
                    "take_profit_price": None, "entry_valid_until": (NOW + timedelta(minutes=10)).isoformat(),
                    "planned_exit_at": (NOW + timedelta(days=1)).isoformat(),
                    "condition_payload": {"schema_version": 2, "entry_conditions": [],
                        "exit_conditions": [{"id": "stop", "all": [{"field": "current_price", "operator": "<=", "value": 95.0}]}] if held else [],
                        "reduce_conditions": [], "invalidation_conditions": []},
                    "citations": [{"source_id": row["source_ids"][-1], "claim": "Current quote"}], "reasoning": "No new entry"})
            result = {"plans": plans, "reasoning": "Account-specific risk review"}
        return self.schema.model_validate(result)


def service(data=None, accounts=None, invalid_role=None):
    calls = []
    models = {role: Model(role, calls, role == invalid_role) for role in ("analyst", "quant", "chartist", "risk_manager")}
    return SharedAnalysisService(data=data or Data(), accounts=accounts or Accounts(), models=models, clock=lambda: NOW), calls


def test_held_specialists_enter_worker_queue_before_new_candidates():
    class HeldLastCodeAccounts(Accounts):
        def fetch_accounts(self, ids):
            return {user: snapshot(user, [holding("000025")]) for user in ids}

    engine, calls = service(accounts=HeldLastCodeAccounts())
    engine.max_workers = 1
    engine.run_cycle([{"userId": "a"}])
    specialist_codes = [payload["stock_code"] for role, payload in calls if role != "risk_manager"]
    assert specialist_codes[:3] == ["000025"] * 3
    assert any(code != "000025" for code in specialist_codes[3:])


def test_shared_work_deduplicates_themes_and_users_and_includes_all_holdings():
    engine, calls = service()
    targets = [{"userId": "a", "themeKeys": ["a"]}, {"userId": "b", "themeKeys": ["b"]}]
    cycle = engine.run_cycle(targets)
    assert cycle["specialist_stock_count"] == 21
    assert len([c for c in calls if c[0] != "risk_manager"]) == 63
    risk_calls = [c for c in calls if c[0] == "risk_manager"]
    assert len(risk_calls) == 2
    assert {c[1]["account"]["userId"] for c in risk_calls} == {"a", "b"}
    assert all(len(c[1]["candidates"]) == 6 for c in risk_calls)
    assert all("account" not in payload for role, payload in calls if role != "risk_manager")
    assert all(r["status"] == "completed" for r in cycle["accounts"].values())
    duplicate = engine.run_cycle(targets)
    assert duplicate["accounts"] == cycle["accounts"]
    assert len(calls) == 67
    assert len([c for c in calls if c[0] != "risk_manager"]) == 63


def test_all_initial_account_failures_abort_paid_work_and_keep_audit_contract(tmp_path):
    from src.tracing.paper_audit import PaperAudit, summarize_runtime

    class UnavailableAccounts(Accounts):
        def fetch_accounts(self, ids):
            raise ValueError(f"account_snapshot_failed:{ids[0]}")

    data = Data(100)
    engine, calls = service(data, UnavailableAccounts())
    engine.audit = PaperAudit(tmp_path / "audit.sqlite3")
    cycle = engine.run_cycle([{"userId": "a"}, {"userId": "b"}])
    assert cycle["no_paid_work"] is True
    assert cycle["reason"] == "no_available_accounts"
    assert data.loads == 0
    assert calls == []
    assert cycle["prefilter_count"] == cycle["specialist_stock_count"] == cycle["completed_stock_count"] == 0
    assert cycle["manifest"]["role_input_hashes"] == {}
    assert set(cycle["timings_ms"]) == {"data", "specialists", "accounts", "total"}
    assert cycle["timings_ms"]["specialists"] == cycle["timings_ms"]["accounts"] == 0
    for user_id in ("a", "b"):
        account = cycle["accounts"][user_id]
        assert account["status"] == "failed"
        assert account["error"] == f"account_snapshot_failed:{user_id}"
        assert account["plans"] == []
    events = engine.audit.read()
    assert len(events) == 1 and events[0]["kind"] == "analysis"
    summary = summarize_runtime(events)
    assert summary["account_reviews"] == 2
    assert summary["valid_account_rate"] == 0
    assert summary["llm_requests"] == 0


def test_initial_account_failure_does_not_block_healthy_account_analysis():
    class PartiallyAvailableAccounts(Accounts):
        def fetch_accounts(self, ids):
            if ids == ["a"]:
                raise ValueError("account_a_unavailable")
            return super().fetch_accounts(ids)

    engine, calls = service(Data(1), PartiallyAvailableAccounts())
    cycle = engine.run_cycle([{"userId": "a"}, {"userId": "b"}])
    assert cycle["accounts"]["a"]["status"] == "failed"
    assert cycle["accounts"]["b"]["status"] == "completed"
    risk = [payload for role, payload in calls if role == "risk_manager"]
    assert len(risk) == 1 and risk[0]["account"]["userId"] == "b"
    assert len(calls) == 4


def test_role_caches_invalidate_independently():
    data = Data(1)
    engine, calls = service(data)
    engine.run_cycle([])
    data.document_version = "v2"
    engine.run_cycle([], as_of=NOW + timedelta(minutes=15))
    assert len([c for c in calls if c[0] == "analyst"]) == 2
    assert len([c for c in calls if c[0] == "quant"]) == 2
    assert len([c for c in calls if c[0] == "chartist"]) == 1
    data.price_version = 2
    engine.run_cycle([], as_of=NOW + timedelta(minutes=30))
    assert len([c for c in calls if c[0] == "analyst"]) == 2
    assert len([c for c in calls if c[0] == "quant"]) == 2
    assert len([c for c in calls if c[0] == "chartist"]) == 2


def test_event_packets_reactions_and_citations_reach_roles_without_account_leakage():
    class ScopedAccounts(Accounts):
        def fetch_accounts(self, ids):
            return {user: snapshot(user, [holding()]).model_copy(update={
                "equity": 20000.0 if user == "account-a" else 50000.0}) for user in ids}

    class EventCitingModel(Model):
        def invoke(self, messages):
            result = super().invoke(messages)
            payload = json.loads(messages[-1][1])
            event_id = payload["event_reactions"][0]["event_id"]
            values = result.model_dump()
            values["citations"].append({"source_id": event_id, "claim": "Observed event association, not causality"})
            return self.schema.model_validate(values)

    data = EventData()
    engine, calls = service(data, ScopedAccounts())
    engine.models["chartist"] = EventCitingModel("chartist", calls)
    cycle = engine.run_cycle([{"userId": "account-a", "investorProfile": {"private_marker": "profile-a"}},
                              {"userId": "account-b", "investorProfile": {"private_marker": "profile-b"}}])
    assert cycle["errors"] == []
    assert all(result["status"] == "completed" for result in cycle["accounts"].values())
    specialist_calls = {role: payload for role, payload in calls if role != "risk_manager"}
    assert len(calls) == 5
    analyst, chartist = specialist_calls["analyst"], specialist_calls["chartist"]
    assert analyst["documents"] == []
    assert len(analyst["events"]) == 1
    event = analyst["events"][0]
    assert event["source_ids"] == ["doc:000001"]
    assert set(analyst["source_ids"]) == {event["event_id"], "doc:000001"}
    assert len(specialist_calls["quant"]["disclosures"]) == 1
    reaction = chartist["event_reactions"][0]
    assert "source_ids" not in reaction
    assert reaction["benchmark_comparison"]["source_id"] in chartist["source_ids"]
    assert event["event_id"] in chartist["source_ids"]
    price_source = next(source for source in chartist["source_ids"] if source.startswith("price:000001:"))
    assert "not causation" in chartist["reaction_contract"]["interpretation"]
    assert chartist["reaction_contract"]["stock_price_basis"] == "raw_only"
    assert "market_adjusted_return_pct" not in reaction
    assert "corporate_action_adjustment_unverified" in chartist["reaction_contract"]["data_gaps"]
    assert reaction["horizons"]["3"]["status"] == "observed"
    assert reaction["horizons"]["5"]["return_pct"] is None
    assert reaction["horizons"]["5"]["status"] == "insufficient_post_event_bars"
    assert reaction["post_event_bar_count"] == 3
    assert all("account" not in payload and "investor_profile" not in payload for payload in specialist_calls.values())
    assert all("account-a" not in json.dumps(payload) and "profile-b" not in json.dumps(payload)
               for payload in specialist_calls.values())
    risk_calls = {payload["account"]["userId"]: payload for role, payload in calls if role == "risk_manager"}
    assert set(risk_calls) == {"account-a", "account-b"}
    assert risk_calls["account-a"]["account"]["equity"] == 20000.0
    assert risk_calls["account-b"]["account"]["equity"] == 50000.0
    for user, risk in risk_calls.items():
        assert risk["investor_profile"]["private_marker"] == "profile-" + user[-1]
        candidate = risk["candidates"][0]
        compact_reaction = candidate["event_reactions"][0]
        assert compact_reaction["event_id"] == reaction["event_id"]
        assert "source_ids" not in compact_reaction
        assert {event["event_id"], price_source} <= set(candidate["source_ids"])
        assert compact_reaction["horizons"]["5"] == {"status": "insufficient_post_event_bars", "return_pct": None}
        assert compact_reaction["latest_return_pct"] == round(reaction["latest_return_pct"], 4)
        assert risk["reaction_contract"] == chartist["reaction_contract"]
        assert risk["reaction_contract"]["stock_price_basis"] == "raw_only"
        assert risk["reaction_contract"]["corporate_action_adjustment"] == "unverified"
        assert not {"interpretation", "price_basis", "corporate_action_adjustment", "market_adjusted_return_pct"} & compact_reaction.keys()
        assert compact_reaction["data_gaps"] == reaction["data_gaps"]
        assert set(compact_reaction["volume_reaction"]) == {"status", "ratio"}
        assert not {"baseline_bar", "as_of_bar", "latest_post_event_bar", "post_event_bar_count"} & compact_reaction.keys()
        assert all("bar" not in horizon for horizon in compact_reaction["horizons"].values())
        assert "baseline_bar" in reaction and "bar" not in reaction["horizons"]["3"]
        assert "events" not in candidate
        assert compact_reaction["title"] == event["title"]
        assert compact_reaction["event_type"] == event["event_type"]
        assert "text" not in compact_reaction and "sources" not in compact_reaction
        assert {citation["source_id"] for citation in candidate["specialists"]["chartist"]["citations"]} == {
            event["event_id"], price_source}


@pytest.mark.parametrize("source_type", ["dart", "news"])
def test_event_role_caches_ignore_clock_ticks_but_track_relevant_bar_and_document_changes(source_type):
    data = EventData(source_type)
    engine, calls = service(data)
    first = engine.run_cycle([], as_of=NOW)
    warm = engine.run_cycle([], as_of=NOW + timedelta(minutes=15))
    assert first["errors"] == warm["errors"] == []
    assert first["manifest"]["role_input_hashes"] == warm["manifest"]["role_input_hashes"]
    assert len(calls) == 3
    data.bars.append(data.bar(NOW.date(), 135.0))
    after_bar = engine.run_cycle([], as_of=NOW + timedelta(hours=2))
    first_hashes, bar_hashes = first["manifest"]["role_input_hashes"], after_bar["manifest"]["role_input_hashes"]
    assert first_hashes["000001:analyst"] == bar_hashes["000001:analyst"]
    assert first_hashes["000001:quant"] == bar_hashes["000001:quant"]
    assert first_hashes["000001:chartist"] != bar_hashes["000001:chartist"]
    assert [role for role, _ in calls].count("analyst") == 1
    assert [role for role, _ in calls].count("quant") == 1
    assert [role for role, _ in calls].count("chartist") == 2
    chart_payloads = [payload for role, payload in calls if role == "chartist"]
    assert chart_payloads[0]["event_reactions"][0]["post_event_bar_count"] == 3
    assert chart_payloads[-1]["event_reactions"][0]["post_event_bar_count"] == 4
    assert chart_payloads[-1]["event_reactions"][0]["latest_post_event_bar"]["close"] == 135.0
    data.document_version = "v2"
    after_event = engine.run_cycle([], as_of=NOW + timedelta(hours=2, minutes=15))
    assert after_event["errors"] == []
    assert [role for role, _ in calls].count("analyst") == 2
    assert [role for role, _ in calls].count("quant") == (2 if source_type == "dart" else 1)
    assert [role for role, _ in calls].count("chartist") == 3
    final_chart = [payload for role, payload in calls if role == "chartist"][-1]
    assert final_chart["event_reactions"][0]["event_id"] != chart_payloads[-1]["event_reactions"][0]["event_id"]


@pytest.mark.parametrize("scope", ["document", "structured_fields"])
def test_quant_disclosure_projection_bounds_text_and_preserves_distinct_provider_facts(scope):
    class StructuredData(EventData):
        def load_evidence(self, candidate, as_of):
            from src.runner.event_evidence import build_event_evidence

            evidence = super().load_evidence(candidate, as_of)
            documents = []
            for index in range(2):
                receipt = f"2026090100000{index}"
                text = "공시 원문에 실제로 기록된 내용. " * 150
                documents.append({**evidence["documents"][0], "source_id": f"doc:{index}", "text": text,
                    "source_text_hash": hashlib.sha256(text.encode()).hexdigest(), "original_characters": len(text),
                    "text_scope": scope, "metadata": {"rcept_no": receipt, "structured_rcept_no": receipt,
                        "structured_endpoint": "piicDecsn", "has_correction": True, "is_correction": False,
                        "is_withdrawal": False, "structured_row": {"rcept_no": receipt,
                            "nstk_ostk_cnt": str(10000 + index), "long_description": "RAW_PRIVATE_DETAIL_" * 1000}}})
            evidence["documents"] = documents
            evidence["events"] = build_event_evidence(documents, candidate["stock_code"])
            return evidence

    engine, calls = service(StructuredData())
    result = engine.run_cycle([])
    assert result["errors"] == []
    quant = next(payload for role, payload in calls if role == "quant")
    assert "RAW_PRIVATE_DETAIL_" not in json.dumps(quant)
    assert set(quant["source_ids"]) == {"fin:000001", "doc:0", "doc:1"}
    assert len(quant["disclosures"]) == 2
    for index, row in enumerate(quant["disclosures"]):
        assert "metadata" not in row and "structured_row" not in row
        assert len(row["text"]) <= 1200 and row["text_truncated"] is True
        assert bool(row["text"]) == (scope == "document")
        assert row["text_scope"] == scope
        assert row["has_correction"] is True and row["is_correction"] is False and row["is_withdrawal"] is False
        assert row["source_text_hash"] and row["url"] and row["available_at"] and row["published_at"]
        facts = row["structured_facts"]
        assert len(facts) == 1 and facts[0]["source_id"] == f"doc:{index}"
        assert facts[0]["fields"]["nstk_ostk_cnt"] == str(10000 + index)
        assert facts[0]["omitted_fields_count"] == 1


def test_legacy_quant_disclosure_projection_keeps_existing_text_without_requiring_events():
    engine, calls = service(Data(1))
    result = engine.run_cycle([])
    assert result["errors"] == []
    row = next(payload for role, payload in calls if role == "quant")["disclosures"][0]
    assert row["source_id"] == "doc:000001"
    assert row["text"] == "v1" and row["text_truncated"] is False
    assert row["structured_facts"] == []


def test_invalid_specialist_never_becomes_default_score_or_new_entry():
    engine, calls = service(invalid_role="analyst")
    cycle = engine.run_cycle([{"userId": "a"}])
    assert any("unknown citation" in error["error"] for error in cycle["errors"])
    risk = [payload for role, payload in calls if role == "risk_manager"][0]
    assert [r["stock_code"] for r in risk["candidates"]] == ["000001"]
    assert risk["candidates"][0]["leader_score"] is None
    assert risk["candidates"][0]["specialist_errors"]


def test_holding_missing_history_still_receives_quote_based_protection():
    engine, calls = service(Data(0))
    cycle = engine.run_cycle([{"userId": "a"}])
    assert cycle["accounts"]["a"]["status"] == "completed"
    assert cycle["accounts"]["a"]["plans"][0]["holding_quantity"] == 4
    assert len(calls) == 1


def test_prefilter_cap_is_100_unique_price_only_then_20_specialists():
    engine, _ = service(Data(110))
    cycle = engine.run_cycle([])
    assert cycle["prefilter_count"] == 100
    assert cycle["specialist_stock_count"] == 20
    assert cycle["completed_stock_count"] == 20
    assert math.isclose(sum(PRICE_WEIGHTS.values()), 1)
    assert "document_count" not in PRICE_WEIGHTS


def test_singleflight_shares_inflight_work_and_does_not_cache_errors():
    cache = SingleFlightCache(2)
    count = 0
    barrier = threading.Barrier(4)

    def work():
        nonlocal count
        count += 1
        time.sleep(.04)
        return 12

    def get():
        barrier.wait()
        return cache.get_or_compute("same", work)

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: get(), range(4))) == [12] * 4
    assert count == 1
    with pytest.raises(ValueError):
        cache.get_or_compute("failed", lambda: (_ for _ in ()).throw(ValueError("failure")))
    assert cache.get_or_compute("failed", lambda: 42) == 42


def test_price_features_use_backtest_ma150_annualized_vol_and_pit_close():
    rows = []
    for i in range(160):
        day = NOW.date() - timedelta(days=160 - i)
        rows.append({"timestamp": day.isoformat(), "open": 100 + i, "high": 102 + i,
                     "low": 99 + i, "close": 101 + i, "volume": 1000 + i})
    rows.append({"timestamp": NOW.date().isoformat(), "open": 10000, "high": 10000, "low": 10000, "close": 10000, "volume": 1})
    features, known = price_features(rows, NOW)
    assert len(known) == 160
    assert features["current_price"] == 260
    assert features["trend_150d"] == pytest.approx(260 / ((111 + 260) / 2) - 1)
    assert features["volume_ratio_20d"] == pytest.approx(1159 / ((1140 + 1159) / 2))
    with pytest.raises(ValueError, match="stale"):
        price_features(rows[:-1], NOW + timedelta(days=7))


def test_missing_factor_is_not_a_neutral_percentile():
    candidate = {"stock_code": "000001", "features": {key: 1 for key in PRICE_WEIGHTS}}
    candidate["features"]["return_60d"] = float("nan")
    with pytest.raises(ValueError, match="nonfinite"):
        rank_price_candidates([candidate])


@pytest.mark.parametrize("field,value", [("current_price", -1), ("current_price", "100"),
                                         ("current_price", True), ("holding_quantity", False),
                                         ("holding_quantity", 1.5), ("market_time", "09:10")])
def test_condition_contract_rejects_unexecutable_values(field, value):
    with pytest.raises(ValidationError):
        Predicate(field=field, operator=">", value=value)


@pytest.mark.parametrize("field", ["position_size_pct", "entry_price", "stop_loss_price", "take_profit_price",
                                   "holding_quantity", "confidence"])
@pytest.mark.parametrize("value", [True, False, "10"])
def test_plan_contract_does_not_coerce_executable_numbers(field, value):
    engine, _ = service(Data(0))
    plan = engine.run_cycle([{"userId": "a"}])["accounts"]["a"]["plans"][0]
    plan[field] = value
    with pytest.raises(ValidationError):
        TradingPlan.model_validate(plan)


def buy_plan():
    return {"stock_code": "000001", "stock_name": "Stock 000001", "action": "BUY", "holding_quantity": 0,
            "confidence": 80, "risk_level": "MEDIUM", "position_size_pct": 10.0,
            "entry_price": 100.0, "stop_loss_price": 90.0, "take_profit_price": 120.0,
            "entry_valid_until": (NOW + timedelta(minutes=15)).isoformat(),
            "planned_exit_at": (NOW + timedelta(days=1)).isoformat(),
            "condition_payload": {"schema_version": 2,
                "entry_conditions": [{"id": "entry", "all": [{"field": "current_price", "operator": "<=", "value": 100.0}]}],
                "exit_conditions": [{"id": "stop", "all": [{"field": "current_price", "operator": "<=", "value": 90.0}]}],
                "reduce_conditions": [],
                "invalidation_conditions": [{"id": "invalid", "all": [{"field": "current_price", "operator": ">", "value": 120.0}]}]},
            "citations": [{"source_id": "source", "claim": "Observed source"}], "reasoning": "Explicit numerical plan"}


@pytest.mark.parametrize("predicates", [
    [{"field": "current_price", "operator": ">=", "value": 120.0}],
    [{"field": "current_price", "operator": "<", "value": 90.0}],
    [{"field": "current_price", "operator": "<=", "value": 80.0}],
    [{"field": "current_price", "operator": "<=", "value": 95.0}],
    [{"field": "pnl_rate", "operator": "<=", "value": -10.0}],
    [{"field": "current_price", "operator": "<=", "value": 90.0},
     {"field": "market_time", "operator": ">=", "value": "14:00:00"}],
    [{"field": "current_price", "operator": "<=", "value": 90.0},
     {"field": "holding_quantity", "operator": ">", "value": 10.0}],
])
def test_buy_stop_must_be_exact_unconditional_executable_price_stop(predicates):
    plan = buy_plan()
    plan["condition_payload"]["exit_conditions"][0]["all"] = predicates
    with pytest.raises(ValidationError, match="unconditional"):
        TradingPlan.model_validate(plan)


def test_buy_stop_in_invalidation_is_valid_but_reduction_is_not_full_protection():
    plan = buy_plan()
    conditions = plan["condition_payload"]
    conditions["exit_conditions"], conditions["invalidation_conditions"] = conditions["invalidation_conditions"], conditions["exit_conditions"]
    assert TradingPlan.model_validate(plan).stop_loss_price == 90.0
    stop = conditions["invalidation_conditions"].pop()
    conditions["invalidation_conditions"].append({"id": "other", "all": [{"field": "current_price", "operator": ">", "value": 130.0}]})
    conditions["reduce_conditions"].append({**stop, "reduce_fraction": 0.5})
    with pytest.raises(ValidationError, match="unconditional"):
        TradingPlan.model_validate(plan)


@pytest.mark.parametrize("length", [100, 3500, 3501])
def test_canonical_evidence_records_full_source_hash_and_explicit_truncation(tmp_path, length):
    import hashlib
    from src.runner.analysis_data import LocalAnalysisData
    path = tmp_path / "canonical_index" / "a" / "corpus.jsonl"
    path.parent.mkdir(parents=True)
    body = "a" * length
    document = {"text": body, "metadata": {"stock_code": "000001", "source_type": "dart", "doc_id": "doc-1", "title": "Quarterly report",
        "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260904000001",
        "published_at": (NOW - timedelta(hours=1)).isoformat(), "collected_at": NOW.isoformat()}}
    path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    data = LocalAnalysisData(data_dir=str(tmp_path))
    evidence = data.load_evidence({"stock_code": "000001", "theme_keys": ["a"]}, NOW)["documents"][0]
    assert len(evidence["text"]) == min(length, 3500)
    assert evidence["original_characters"] == length
    assert evidence["truncated"] is (length > 3500)
    assert evidence["source_text_hash"] == hashlib.sha256(body.encode()).hexdigest()
    if length > 3500:
        document["text"] = body[:-1] + "b"
        path.write_text(json.dumps(document) + "\n", encoding="utf-8")
        revised = data.load_evidence({"stock_code": "000001", "theme_keys": ["a"]}, NOW)["documents"][0]
        assert revised["text"] == evidence["text"]
        assert revised["source_text_hash"] != evidence["source_text_hash"]


def test_scheduler_is_aligned_and_failed_account_is_not_submitted():
    assert seconds_until_next_slot(901) == 899
    assert seconds_until_next_slot(900) == 900

    class Backend:
        def fetch_targets(self):
            return [{"userId": "a"}]

    class Engine:
        def run_cycle(self, targets):
            return {"accounts": {"a": {"status": "failed", "error": "snapshot_failed", "plans": []}}}

    scheduler = AnalysisScheduler(backend_client=Backend(), analysis_service=Engine(),
                                  submitter=lambda **kwargs: pytest.fail("must not submit failed analysis"))
    assert scheduler.run_once()["failed"] == 1


@pytest.mark.parametrize("at,expected", [("2026-09-04T00:00:00+00:00", True),
                                        ("2026-09-04T06:29:59+00:00", True),
                                        ("2026-09-04T06:30:00+00:00", False),
                                        ("2026-09-03T23:59:59+00:00", False),
                                        ("2026-09-05T03:00:00+00:00", False)])
def test_scheduler_korean_weekday_session_boundaries(at, expected):
    assert within_analysis_session(datetime.fromisoformat(at)) is expected


def test_audit_records_real_inputs_without_exposing_them_in_public_rankings(tmp_path):
    from src.tracing.paper_audit import PaperAudit
    engine, _ = service(Data(1))
    engine.audit = PaperAudit(tmp_path / "audit.sqlite3")
    cycle = engine.run_cycle([{"userId": "a"}])
    events = engine.audit.read()
    requests = [event["payload"] for event in events if event["kind"] == "llm_request"]
    assert len(requests) == 4
    assert len([event for event in events if event["kind"] == "analysis"]) == 1
    assert next(row for row in requests if row["role"] == "risk_manager")["payload"]["account"]["userId"] == "a"
    assert all("account" not in row for row in cycle["global_ranked_leaders"])


def test_chartist_input_failure_is_explicit_and_does_not_discard_other_roles():
    class BrokenChartData(Data):
        def load_technical(self, candidate):
            raise ValueError("invalid_chart_input")
    engine, calls = service(BrokenChartData(1))
    result = engine.run_cycle([{"userId": "a"}])
    assert {role for role, _ in calls} == {"analyst", "quant", "risk_manager"}
    assert result["accounts"]["a"]["status"] == "completed"
    assert any("invalid_chart_input" in row["error"] for row in result["errors"])


def test_runtime_route_composes_explicit_shared_service(monkeypatch):
    from ai_server.app import MultiThemeTradeRequest, _run_multi_theme_trade
    captured = {}

    class RuntimeService:
        def run_all(self, **kwargs):
            captured.update(kwargs)
            return {"schema_version": 2, "status": "preview", "plans": []}
    monkeypatch.setattr("src.runner.shared_analysis.get_runtime_analysis_service", lambda *args: RuntimeService())
    result = _run_multi_theme_trade(MultiThemeTradeRequest(include_theme_keys=["a"], save_report=False))
    assert result["status"] == "preview"
    assert captured["include_theme_keys"] == ["a"]


def test_runtime_keeps_failed_analysis_reason_without_publishing(monkeypatch):
    from ai_server.app import MultiThemeTradeRequest, _run_multi_theme_trade

    class FailedService:
        def run_all(self, **kwargs):
            return {"schema_version": 2, "status": "failed", "error": "account_snapshot_stale", "plans": []}

    monkeypatch.setattr("src.runner.shared_analysis.get_runtime_analysis_service", lambda *args: FailedService())
    monkeypatch.setattr("src.runner.trade_signal_submitter.submit_trade_signals",
                        lambda **kwargs: pytest.fail("failed analysis must not publish plans"))
    result = _run_multi_theme_trade(MultiThemeTradeRequest(user_id="user-a"))
    assert result["error"] == "account_snapshot_stale"
    assert result["signal_submission"] == {"submitted": 0, "failed": 1, "error": "account_snapshot_stale"}


@pytest.mark.parametrize("path,method", [("/runtime/multi-theme-trade", "post"),
                                         ("/internal/runtime/analysis-cycle", "post"),
                                         ("/runtime/tasks/unknown", "get"),
                                         ("/chat", "post"), ("/suggest", "post")])
def test_privileged_runtime_routes_require_internal_auth(monkeypatch, path, method):
    from fastapi.testclient import TestClient
    from ai_server.app import app
    monkeypatch.setenv("HQA_INTERNAL_TOKEN", "test-runtime-token")
    client = TestClient(app)
    request = getattr(client, method)
    kwargs = {"json": {}} if method == "post" else {}
    assert request(path, **kwargs).status_code == 401
    assert request(path, headers={"X-HQA-Internal-Token": "wrong"}, **kwargs).status_code == 403


def test_internal_analysis_cycle_submits_existing_async_task(monkeypatch):
    from fastapi.testclient import TestClient
    import ai_server.app as module
    monkeypatch.setenv("HQA_INTERNAL_TOKEN", "test-runtime-token")
    monkeypatch.setattr(module, "_submit_runtime_task", lambda operation, fn: {"task_id": "id", "operation": operation})
    response = TestClient(module.app).post("/internal/runtime/analysis-cycle", headers={"X-HQA-Internal-Token": "test-runtime-token"})
    assert response.status_code == 202
    assert response.json()["operation"] == "analysis_cycle"


def test_remote_scheduler_only_posts_and_polls_authorized_ai_task(monkeypatch):
    from src.runner.analysis_scheduler import RemoteAnalysisClient
    calls = []
    task_id = "7f1d009e-18ed-4631-99a1-71e75864144c"

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response({"task_id": task_id})

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response({"status": "completed", "result": {"submitted": 0}})

    monkeypatch.setattr("src.runner.analysis_scheduler.requests.post", post)
    monkeypatch.setattr("src.runner.analysis_scheduler.requests.get", get)
    result = RemoteAnalysisClient(base_url="http://ai", internal_token="token").run_once()
    assert result == {"submitted": 0}
    assert calls[0][0] == "http://ai/internal/runtime/analysis-cycle"
    assert calls[1][0] == f"http://ai/runtime/tasks/{task_id}"
    assert all(call[1]["headers"] == {"X-HQA-Internal-Token": "token"} for call in calls)
