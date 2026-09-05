"""Bounded offline load checks; synthetic model timings are not a real API SLA."""
from __future__ import annotations

import json
import math
import socket
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from src.runner.analysis_contracts import AccountSnapshot, TradingPlan
from src.runner.analysis_data import PRICE_WEIGHTS
from src.runner.shared_analysis import SharedAnalysisService
from src.runner.trade_signal_submitter import build_trade_signal_payloads

NOW = datetime(2026, 9, 4, 5, tzinfo=timezone.utc)
ROLES = ("analyst", "quant", "chartist", "risk_manager")
TARGETS = [{"userId": f"user-{i}", "strategyProfile": "swing", "themeKeys": ["shared"]}
           for i in range(10)]


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Offline runtime load tests must not access a network")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


class Clock:
    value = NOW

    def __call__(self):
        return self.value


class Data:
    def load_universe(self, as_of):
        return [{"stock_code": f"{i:06d}", "stock_name": f"Stock {i:06d}",
                 "theme_keys": ["shared", "duplicate-theme"], "entry_filter_errors": [],
                 "features": {key: 1.0 / i if key == "volatility_20d" else float(i)
                              for key in PRICE_WEIGHTS},
                 "price_history": [{"close": float(i)}]} for i in range(1, 101)], []

    def load_evidence(self, candidate, as_of):
        code = candidate["stock_code"]
        return {"documents": [{"source_id": "dart:" + code, "source_type": "dart",
                               "text": "Published operating results"}], "data_gaps": [],
                "financial_snapshot": {"status": "ready", "source_id": "finance:" + code,
                                       "values": {"revenue": 1000.0}}}

    def load_technical(self, candidate):
        return {"indicators": {"MA150": 100.0, "ATR": 5.0}, "data_gaps": []}


def holding(code, quantity=4):
    return {"stockCode": code, "stockName": "Stock " + code, "quantity": quantity,
            "sellableQuantity": quantity, "avgPrice": 100.0, "currentPrice": 110.0,
            "evalAmount": quantity * 110.0, "pnlRate": 10.0}


class Accounts:
    def __init__(self, clock, outside_top20=False):
        self.clock = clock
        self.holdings = {f"user-{i}": [holding(f"{i + (1 if outside_top20 else 81):06d}")]
                         for i in range(10)}
        self.late_holdings = {}
        self.reads = Counter()
        self.quote_reads = []
        self.lock = threading.Lock()

    def fetch_accounts(self, ids):
        result = {}
        for user_id in ids:
            with self.lock:
                self.reads[user_id] += 1
                held = list(self.holdings[user_id])
                if self.reads[user_id] >= 2:
                    held += self.late_holdings.get(user_id, [])
            result[user_id] = AccountSnapshot.model_validate({
                "userId": user_id, "accountMode": "PAPER", "success": True,
                "capturedAt": self.clock().isoformat(), "source": "kis", "maxPositionPct": 20.0,
                "dailyBuyLimit": 20.0, "orderableCash": 10000.0,
                "orderableCashSource": "deposit_upper_bound", "reservedCash": 0.0,
                "equity": 20000.0, "dailyPnlPct": 0.0, "dailyPnlBaselineSource": "kis",
                "entryEligible": True, "entryBlockReason": None, "holdings": held,
                "monitorCapacity": 10, "monitorSymbolCount": len(held), "monitorCapacityExceeded": False})
        return result

    def fetch_prices(self, user_id, codes):
        with self.lock:
            self.quote_reads.append((user_id, tuple(codes)))
        return {code: {"source_id": f"quote:{user_id}:{code}", "source": "kis",
                       "current_price": 110.0, "available_at": self.clock().isoformat()} for code in codes}


class Calls:
    def __init__(self, delay):
        self.delay = delay
        self.items = []
        self.durations_ms = []
        self.active = self.peak = 0
        self.lock = threading.Lock()

    def counts(self):
        return Counter(role for role, _ in self.items)


class FakeLuna:
    model_name = "gpt-5.6-luna"
    reasoning_effort = "low"
    max_tokens = 1200

    def __init__(self, role, calls, buy=False):
        self.role, self.calls, self.buy = role, calls, buy

    def with_structured_output(self, schema, **kwargs):
        assert kwargs == {"method": "json_schema", "strict": True}
        self.schema = schema
        return self

    def invoke(self, messages):
        payload = json.loads(messages[-1][1])
        started = time.perf_counter()
        with self.calls.lock:
            self.calls.items.append((self.role, payload))
            self.calls.active += 1
            self.calls.peak = max(self.calls.peak, self.calls.active)
        try:
            time.sleep(self.calls.delay)
            if self.role != "risk_manager":
                result = {"stock_code": payload["stock_code"], "role": self.role,
                          "score": float(int(payload["stock_code"])), "confidence": 80,
                          "thesis": "Observed financial evidence", "risks": [], "data_gaps": [],
                          "citations": [{"source_id": payload["source_ids"][0], "claim": "Observed fact"}]}
            else:
                held = {row["stockCode"]: row for row in payload["account"]["holdings"]}
                at = datetime.fromisoformat(payload["decision_as_of"])
                plans = []
                for row in payload["candidates"]:
                    quantity = held.get(row["stock_code"], {}).get("quantity", 0)
                    buy = self.buy and not quantity
                    plans.append({"stock_code": row["stock_code"], "stock_name": row["stock_name"],
                        "action": "BUY" if buy else "HOLD", "holding_quantity": quantity,
                        "confidence": 80, "risk_level": "MEDIUM", "position_size_pct": 10.0 if buy else 0.0,
                        "entry_price": 110.0 if buy else None, "stop_loss_price": 95.0 if buy or quantity else None,
                        "take_profit_price": 125.0 if buy else None,
                        "entry_valid_until": (at + timedelta(minutes=10)).isoformat(),
                        "planned_exit_at": (at + timedelta(days=2)).isoformat(),
                        "condition_payload": {"schema_version": 2,
                            "entry_conditions": [group("entry", ">=", 110.0)] if buy else [],
                            "exit_conditions": [group("stop", "<=", 95.0)] if buy or quantity else [],
                            "reduce_conditions": [],
                            "invalidation_conditions": [group("invalid", ">", 120.0)] if buy else []},
                        "citations": [{"source_id": row["quote"]["source_id"], "claim": "Account-specific quote"}],
                        "reasoning": "Explicit account-specific decision"})
                result = {"plans": plans, "reasoning": "Account-specific risk review"}
            return self.schema.model_validate(result)
        finally:
            with self.calls.lock:
                self.calls.active -= 1
                self.calls.durations_ms.append((time.perf_counter() - started) * 1000)


def group(identifier, operator, value):
    return {"id": identifier, "all": [{"field": "current_price", "operator": operator, "value": value}]}


def engine(*, outside_top20=False, buy=False, delay=0.002):
    clock = Clock()
    accounts = Accounts(clock, outside_top20)
    calls = Calls(delay)
    models = {role: FakeLuna(role, calls, buy) for role in ROLES}
    return SharedAnalysisService(data=Data(), accounts=accounts, models=models, max_workers=6, clock=clock), calls


def test_100_candidates_10_accounts_share_60_specialist_calls_not_600():
    service, calls = engine()
    cycle = service.run_cycle(TARGETS)
    assert cycle["prefilter_count"] == 100
    assert cycle["specialist_stock_count"] == cycle["completed_stock_count"] == 20
    assert calls.counts() == {"analyst": 20, "quant": 20, "chartist": 20, "risk_manager": 10}
    assert not cycle["errors"]
    assert 1 < calls.peak <= service.max_workers <= 8
    assert len(calls.items) == 70 < 20 * 3 * 10 + 10
    specialist_pairs = [(role, payload["stock_code"]) for role, payload in calls.items if role != "risk_manager"]
    assert len(set(specialist_pairs)) == 60
    assert all("account" not in payload for role, payload in calls.items if role != "risk_manager")
    for user_id, result in cycle["accounts"].items():
        assert result["status"] == "completed"
        assert result["selected_count"] == 6
        assert {plan["stock_code"] for plan in result["plans"] if plan["holding_quantity"]} == {
            row["stockCode"] for row in service.accounts.holdings[user_id]}
    assert service.accounts.reads == {target["userId"]: 2 for target in TARGETS}


def test_next_15_minute_cycle_reuses_specialists_but_refreshes_each_account_risk():
    service, calls = engine()
    first = service.run_cycle(TARGETS)
    service.clock.value += timedelta(minutes=15)
    service.accounts.holdings["user-0"] = [holding("000081", quantity=9)]
    second = service.run_cycle(TARGETS)
    assert calls.counts() == {"analyst": 20, "quant": 20, "chartist": 20, "risk_manager": 20}
    assert len(calls.items) == 80
    risk = [payload for role, payload in calls.items if role == "risk_manager"]
    assert Counter(payload["account"]["userId"] for payload in risk) == {target["userId"]: 2 for target in TARGETS}
    assert len(service.accounts.quote_reads) == 20
    for user_id, result in second["accounts"].items():
        assert result["status"] == "completed"
        assert result["analysis_id"] != first["accounts"][user_id]["analysis_id"]
        for plan in result["plans"]:
            assert all(citation["source_id"].startswith(f"quote:{user_id}:") for citation in plan["citations"])
            if plan["holding_quantity"]:
                assert plan["holding_quantity"] == (9 if user_id == "user-0" else 4)


def test_all_off_top20_and_newly_observed_holdings_remain_in_risk_review():
    service, calls = engine(outside_top20=True)
    service.accounts.late_holdings["user-0"] = [holding("999999", quantity=2)]
    cycle = service.run_cycle(TARGETS)
    assert cycle["specialist_stock_count"] == cycle["completed_stock_count"] == 30
    assert calls.counts() == {"analyst": 30, "quant": 30, "chartist": 30, "risk_manager": 10}
    for user_id, result in cycle["accounts"].items():
        expected = service.accounts.holdings[user_id] + service.accounts.late_holdings.get(user_id, [])
        assert result["status"] == "completed"
        assert {plan["stock_code"] for plan in result["plans"] if plan["holding_quantity"]} == {
            row["stockCode"] for row in expected}
    risk = next(payload for role, payload in calls.items if role == "risk_manager" and payload["account"]["userId"] == "user-0")
    late = next(row for row in risk["candidates"] if row["stock_code"] == "999999")
    assert late["leader_score"] is None
    assert late["specialist_errors"] == ["new_holding_since_shared_snapshot"]
    assert late["quote"]["source_id"] == "quote:user-0:999999"


def test_strict_engine_results_build_executable_account_bound_submission_payloads():
    service, _ = engine(buy=True)
    cycle = service.run_cycle(TARGETS)
    published = []
    for user_id, result in cycle["accounts"].items():
        assert result["status"] == "completed"
        held_code = service.accounts.holdings[user_id][0]["stockCode"]
        active = [{"userId": user_id, "stockCode": held_code, "planVersion": 4}]
        payloads = build_trade_signal_payloads(user_id=user_id, result=result, now=service.clock(), active_plans=active)
        assert len(payloads) == 6
        assert Counter(row["action"] for row in payloads) == {"BUY": 5, "HOLD": 1}
        for payload in payloads:
            assert payload["userId"] == user_id
            assert payload["accountMode"] == "PAPER"
            assert payload["analysisId"] == result["analysis_id"]
            assert payload["analysisAsOf"] == result["as_of"]
            assert payload["conditionPayload"]["schema_version"] == 2
            assert payload["planVersion"] == (5 if payload["stockCode"] == held_code else 1)
            assert TradingPlan.model_validate(payload["tradePlanJson"]).stock_code == payload["stockCode"]
        retry = build_trade_signal_payloads(user_id=user_id, result=result, now=service.clock() + timedelta(minutes=1), active_plans=active)
        assert [row["idempotencyKey"] for row in retry] == [row["idempotencyKey"] for row in payloads]
        with pytest.raises(ValueError, match="account"):
            build_trade_signal_payloads(user_id="wrong-account", result=result, now=service.clock())
        published.extend(payloads)
    assert len(published) == len({row["idempotencyKey"] for row in published}) == 60


def test_synthetic_cold_and_warm_p95_report_is_not_a_real_llm_sla(record_property):
    cold_ms, warm_ms, call_ms, peaks = [], [], [], []
    for _ in range(20):
        service, calls = engine()
        for durations in (cold_ms, warm_ms):
            started = time.perf_counter()
            cycle = service.run_cycle(TARGETS)
            durations.append((time.perf_counter() - started) * 1000)
            assert all(result["status"] == "completed" for result in cycle["accounts"].values())
            service.clock.value += timedelta(minutes=15)
        assert calls.counts() == {"analyst": 20, "quant": 20, "chartist": 20, "risk_manager": 20}
        call_ms.extend(calls.durations_ms)
        peaks.append(calls.peak)

    def p95(values):
        return round(sorted(values)[math.ceil(len(values) * 0.95) - 1], 3)

    report = {"measurement": "offline synthetic fake-Luna; not a real model/network SLA",
              "samples_per_cycle_type": 20, "candidates": 100, "accounts": 10,
              "fake_model_delay_ms": 2, "cold_calls": 70, "warm_calls": 10,
              "cold_cycle_p95_ms": p95(cold_ms), "warm_cycle_p95_ms": p95(warm_ms),
              "fake_call_p95_ms": p95(call_ms), "peak_parallel_calls": max(peaks)}
    assert max(peaks) <= 6
    record_property("synthetic_load_report", json.dumps(report, sort_keys=True))
    print("\nSYNTHETIC_LOAD_REPORT " + json.dumps(report, sort_keys=True))
