from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.runner.signal_monitor import BackendSignalClient, BackendSnapshotProvider, SignalMonitor, evaluate_condition


def test_evaluate_condition_supports_price_comparators():
    snapshot = {"current_price": 72100, "pnl_rate": -2.5}

    assert evaluate_condition({"field": "current_price", "operator": ">=", "value": 72000}, snapshot)
    assert evaluate_condition({"field": "pnl_rate", "operator": "<=", "value": -2.0}, snapshot)
    assert not evaluate_condition({"field": "current_price", "operator": "<", "value": 70000}, snapshot)


def test_monitor_triggers_backend_only_when_waiting_entry_condition_matches():
    triggered = []

    class Backend:
        def fetch_active_signals(self):
            return [
                {
                    "signalId": "sig-1",
                    "status": "WAITING_ENTRY",
                    "stockCode": "005930",
                    "conditionPayload": {
                        "entry_conditions": [
                            {"field": "current_price", "operator": ">=", "value": 72000}
                        ]
                    },
                }
            ]

        def trigger_signal(self, signal_id, trigger):
            triggered.append((signal_id, trigger))

    monitor = SignalMonitor(
        backend_client=Backend(),
        price_provider=lambda signal: {"current_price": 72100},
    )

    assert monitor.poll_once() == 1
    assert triggered == [
        (
            "sig-1",
            {
                "triggerType": "ENTRY",
                "groupId": "legacy-entry-0",
                "matchedCondition": {"field": "current_price", "operator": ">=", "value": 72000},
                "snapshot": {"current_price": 72100},
            },
        )
    ]


def test_monitor_uses_exit_conditions_for_open_positions():
    triggered = []

    class Backend:
        def fetch_active_signals(self):
            return [
                {
                    "signalId": "sig-2",
                    "status": "OPEN",
                    "stockCode": "005930",
                    "conditionPayload": {
                        "exit_conditions": [
                            {"field": "current_price", "operator": "<=", "value": 68000}
                        ]
                    },
                }
            ]

        def trigger_signal(self, signal_id, trigger):
            triggered.append(trigger["triggerType"])

    monitor = SignalMonitor(
        backend_client=Backend(),
        price_provider=lambda signal: {"current_price": 67900},
    )

    assert monitor.poll_once() == 1
    assert triggered == ["EXIT"]


NOW = datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc)


def _group(group_id, *conditions):
    return {"id": group_id, "all": [{"field": "current_price", "operator": op, "value": value}
                                    for op, value in conditions]}


def _v2_signal(**updates):
    signal = {"signalId": "s1", "userId": "u1", "stockCode": "005930", "status": "WAITING_ENTRY",
              "planVersion": 2, "entryValidUntil": (NOW + timedelta(minutes=15)).isoformat(),
              "conditionPayload": {"schema_version": 2,
                                   "entry_conditions": [_group("range", (">=", 100), ("<=", 110))]}}
    signal.update(updates)
    return signal


class RecordingBackend:
    def __init__(self, signals):
        self.signals = signals
        self.triggers = []

    def fetch_active_signals(self):
        return self.signals

    def trigger_signal(self, signal_id, payload):
        self.triggers.append((signal_id, payload))


def _monitor(signal, price=105, age=0):
    backend = RecordingBackend([signal])
    return SignalMonitor(backend, lambda _: {"current_price": price,
                                            "snapshot_at": (NOW - timedelta(seconds=age)).isoformat()},
                         clock=lambda: NOW), backend


def test_v2_requires_all_predicates_and_sends_plan_identity():
    monitor, backend = _monitor(_v2_signal(), price=115)
    assert monitor.poll_once() == 0
    monitor, backend = _monitor(_v2_signal())
    assert monitor.poll_once() == 1
    assert backend.triggers[0][1]["groupId"] == "range"
    assert backend.triggers[0][1]["planVersion"] == 2


def test_v2_invalidation_precedes_entry():
    signal = _v2_signal()
    signal["conditionPayload"]["invalidation_conditions"] = [_group("invalid", (">", 100))]
    monitor, backend = _monitor(signal)
    assert monitor.poll_once() == 1
    assert backend.triggers[0][1]["triggerType"] == "INVALIDATION"


@pytest.mark.parametrize("age", [21, -6])
def test_stale_and_future_snapshots_do_not_trigger(age):
    monitor, backend = _monitor(_v2_signal(), age=age)
    assert monitor.poll_once() == 0
    assert not monitor.last_report["slo_met"]
    assert "snapshot" in monitor.last_report["errors"][0]["error"]


def test_expired_entry_is_not_submitted_but_open_protection_remains():
    signal = _v2_signal(entryValidUntil=(NOW - timedelta(days=1)).isoformat())
    monitor, _ = _monitor(signal)
    assert monitor.poll_once() == 0
    signal["status"] = "OPEN"
    signal["conditionPayload"]["exit_conditions"] = [_group("stop", ("<=", 110))]
    monitor, backend = _monitor(signal)
    assert monitor.poll_once() == 1
    assert backend.triggers[0][1]["triggerType"] == "EXIT"


def test_partial_fills_are_protected_and_planned_exit_has_stable_group():
    signal = _v2_signal(status="PARTIALLY_FILLED", plannedExitAt=(NOW - timedelta(seconds=1)).isoformat())
    monitor, backend = _monitor(signal)
    assert monitor.poll_once() == 1
    assert backend.triggers[0][1]["groupId"] == "planned-exit"


def test_invalid_group_and_missing_inputs_are_reported_not_holds():
    signal = _v2_signal()
    signal["conditionPayload"]["entry_conditions"] = [{"id": "bad", "all": []}]
    monitor, _ = _monitor(signal)
    assert monitor.poll_once() == 0
    assert monitor.last_report["errors"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, None, "not-a-price"])
def test_numeric_condition_rejects_invalid_values(value):
    assert not evaluate_condition({"field": "current_price", "operator": "!=", "value": 10},
                                  {"current_price": value})


def test_market_time_uses_time_comparison_not_lexicographic_numbers():
    assert evaluate_condition({"field": "market_time", "operator": ">=", "value": "15:20"},
                              {"market_time": "15:20:01"})
    assert not evaluate_condition({"field": "market_time", "operator": ">", "value": "99:00"},
                                  {"market_time": "15:20"})


def test_active_signals_pagination(monkeypatch):
    calls = []

    class Response:
        def __init__(self, page):
            self.page = page

        def raise_for_status(self):
            pass

        def json(self):
            return {"signals": [{"signalId": str(self.page)}], "hasMore": self.page < 2,
                    "nextPage": self.page + 1 if self.page < 2 else None}

    def get(url, **kwargs):
        calls.append(kwargs["params"]["page"])
        return Response(calls[-1])

    monkeypatch.setattr("src.runner.signal_monitor.requests.get", get)
    client = BackendSignalClient(internal_token="test-token")
    assert len(client.fetch_active_signals()) == 3
    assert calls == [0, 1, 2]


def test_batch_provider_reuses_accounts_and_does_not_mix_positions():
    class Backend:
        def __init__(self):
            self.accounts = []
            self.prices = []

        def fetch_account_snapshot(self, user_id):
            self.accounts.append(user_id)
            return {"capturedAt": datetime.now(timezone.utc).isoformat(),
                    "holdings": [{"stockCode": "005930", "quantity": 2 if user_id == "u1" else 3,
                                  "avgPrice": 100}]}

        def fetch_price_snapshots(self, user_id, codes):
            self.prices.append((user_id, codes))
            return [{"stockCode": code, "success": True, "currentPrice": 110, "source": "kis",
                     "snapshotAt": datetime.now(timezone.utc).isoformat()} for code in codes]

    backend = Backend()
    provider = BackendSnapshotProvider(backend)
    snapshots = provider.prepare([_v2_signal(), _v2_signal(), _v2_signal(userId="u2")])
    assert sorted(backend.accounts) == ["u1", "u2"]
    assert len(backend.prices) == 2
    assert snapshots[("u1", "005930")]["holding_quantity"] == 2
    assert snapshots[("u2", "005930")]["holding_quantity"] == 3


def test_monitor_requires_internal_authentication():
    with pytest.raises(ValueError, match="HQA_INTERNAL_TOKEN"):
        BackendSignalClient(internal_token="")


def test_nonfinite_v2_snapshot_is_failure_not_a_nonmatching_condition():
    monitor, _ = _monitor(_v2_signal(), price=float("nan"))
    assert monitor.poll_once() == 0
    assert monitor.last_report["checked"] == 0
    assert not monitor.last_report["slo_met"]


def test_expired_account_snapshot_cannot_trigger_with_fresh_quote():
    monitor, _ = _monitor(_v2_signal())
    monitor.price_provider = lambda _: {"current_price": 105, "snapshot_at": NOW.isoformat(),
                                        "account_snapshot_at": (NOW - timedelta(seconds=31)).isoformat()}
    assert monitor.poll_once() == 0
    assert "Account snapshot" in monitor.last_report["errors"][0]["error"]


def test_http_success_does_not_mean_trigger_was_accepted(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"accepted": False, "rejectReason": "STALE_PLAN_VERSION", "status": "OPEN"}

    monkeypatch.setattr("src.runner.signal_monitor.requests.post", lambda *a, **kw: Response())
    with pytest.raises(ValueError, match="STALE_PLAN_VERSION"):
        BackendSignalClient(internal_token="test").trigger_signal("s1", {})


def test_deduplicated_trigger_is_not_counted_as_new_submission():
    monitor, backend = _monitor(_v2_signal())
    backend.trigger_signal = lambda *args: {"accepted": True, "deduplicated": True}
    assert monitor.poll_once() == 0
    assert monitor.last_report["deduplicated"] == 1


@pytest.mark.parametrize("stop_list", ["exit_conditions", "invalidation_conditions"])
def test_missing_optional_pnl_does_not_block_an_independent_hard_stop(stop_list):
    payload = {"schema_version": 2,
               "exit_conditions": [{"id": "pnl-exit", "all": [{"field": "pnl_rate", "operator": "<=", "value": -10}]}]}
    payload.setdefault(stop_list, []).append(_group("hard-stop", ("<=", 90)))
    signal = _v2_signal(status="OPEN", conditionPayload=payload)
    monitor, backend = _monitor(signal, price=85)
    assert monitor.poll_once() == 1
    assert backend.triggers[0][1]["groupId"] == "hard-stop"
    assert backend.triggers[0][1]["triggerType"] == ("EXIT" if stop_list == "exit_conditions" else "INVALIDATION")


def test_missing_inputs_remain_an_error_when_no_other_protection_is_true():
    signal = _v2_signal(status="OPEN", conditionPayload={"schema_version": 2,
        "exit_conditions": [{"id": "pnl-exit", "all": [{"field": "pnl_rate", "operator": "<=", "value": -10}]}],
        "invalidation_conditions": [_group("hard-stop", ("<=", 90))]})
    monitor, backend = _monitor(signal, price=105)
    assert monitor.poll_once() == 0
    assert not backend.triggers
    assert "pnl_rate" in monitor.last_report["errors"][0]["error"]


def test_unknown_invalidation_still_blocks_entry_even_when_entry_is_true():
    signal = _v2_signal()
    signal["conditionPayload"]["invalidation_conditions"] = [{"id": "pnl-invalid", "all": [
        {"field": "pnl_rate", "operator": "<=", "value": -10}]}]
    monitor, backend = _monitor(signal)
    assert monitor.poll_once() == 0
    assert not backend.triggers
    assert "pnl_rate" in monitor.last_report["errors"][0]["error"]


def test_legacy_trigger_group_id_preserves_original_index_and_actual_plan_version():
    conditions = [{"field": "current_price", "operator": ">", "value": 150},
                  {"field": "current_price", "operator": "<=", "value": 90}]
    signal = {"signalId": "legacy", "status": "OPEN", "planVersion": 3,
              "conditionPayload": {"exit_conditions": conditions}}
    monitor, backend = _monitor(signal, price=85)
    assert monitor.poll_once() == 1
    payload = backend.triggers[0][1]
    assert payload["groupId"] == "legacy-exit-1"
    assert payload["planVersion"] == 3
    assert payload["matchedCondition"] == conditions[1]


class CoverageBackend:
    def __init__(self, *, signals=(), targets=(), holdings=None):
        self.signals = list(signals)
        self.targets = list(targets)
        self.holdings = holdings or {}
        self.account_calls = []
        self.quote_calls = []
        self.triggers = []

    def fetch_active_signals(self):
        return self.signals

    def fetch_auto_trade_targets(self):
        return [{"userId": user_id} for user_id in self.targets]

    def fetch_account_snapshot(self, user_id):
        self.account_calls.append(user_id)
        return {"capturedAt": datetime.now(timezone.utc).isoformat(), "holdings": [
            {"stockCode": code, "quantity": 3, "avgPrice": 100} for code in self.holdings.get(user_id, [])]}

    def fetch_price_snapshots(self, user_id, codes):
        self.quote_calls.append((user_id, codes))
        return [{"stockCode": code, "success": True, "currentPrice": 85, "source": "kis",
                 "snapshotAt": datetime.now(timezone.utc).isoformat()} for code in codes]

    def trigger_signal(self, signal_id, trigger):
        self.triggers.append((signal_id, trigger))
        return {"accepted": True}


def protected_signal(user_id, code):
    return _v2_signal(signalId=user_id + code, userId=user_id, stockCode=code, status="OPEN",
                      conditionPayload={"schema_version": 2, "exit_conditions": [_group("stop", ("<=", 90))]})


def test_monitor_covers_enabled_users_and_all_holdings_even_without_plans():
    backend = CoverageBackend(signals=[protected_signal("planned", "000001"), protected_signal("active-only", "000004")],
                              targets=["planned", "no-plan"],
                              holdings={"planned": ["000001", "000002"], "no-plan": ["000003"], "active-only": ["000004"]})
    monitor = SignalMonitor(backend, snapshot_batch_provider=BackendSnapshotProvider(backend))
    assert monitor.poll_once() == 2
    assert set(backend.account_calls) == {"planned", "no-plan", "active-only"}
    assert dict(backend.quote_calls) == {"planned": ["000001", "000002"], "no-plan": ["000003"], "active-only": ["000004"]}
    assert {(row["user_id"], row["stock_code"]) for row in monitor.last_report["uncovered_holdings"]} == {
        ("planned", "000002"), ("no-plan", "000003")}
    assert all(row["quote_available"] for row in monitor.last_report["uncovered_holdings"])
    assert len([row for row in monitor.last_report["errors"] if row["error"] == "missing_protection"]) == 2
    assert not monitor.last_report["slo_met"]


def test_target_lookup_failure_does_not_stop_known_held_position_protection():
    backend = CoverageBackend(signals=[protected_signal("known", "000001")], holdings={"known": ["000001"]})

    def unavailable():
        raise ValueError("target endpoint unavailable")
    backend.fetch_auto_trade_targets = unavailable
    monitor = SignalMonitor(backend, snapshot_batch_provider=BackendSnapshotProvider(backend))
    assert monitor.poll_once() == 1
    assert any("auto_trade_targets_unavailable" in row["error"] for row in monitor.last_report["errors"])
    assert not monitor.last_report["slo_met"]


def test_unplanned_account_snapshot_failure_is_reported_with_no_active_signals():
    backend = CoverageBackend(targets=["no-plan"])

    def unavailable(user_id):
        raise ValueError("account unavailable")
    backend.fetch_account_snapshot = unavailable
    monitor = SignalMonitor(backend, snapshot_batch_provider=BackendSnapshotProvider(backend))
    assert monitor.poll_once() == 0
    assert monitor.last_report["errors"][0]["user_id"] == "no-plan"
    assert "holding_coverage_unavailable" in monitor.last_report["errors"][0]["error"]
    assert not monitor.last_report["slo_met"]


def test_quote_failure_preserves_uncovered_inventory_without_quota_slicing():
    codes = [f"{number:06d}" for number in range(1, 12)]
    backend = CoverageBackend(targets=["no-plan"], holdings={"no-plan": codes})

    def unavailable(user_id, requested_codes):
        backend.quote_calls.append((user_id, requested_codes))
        raise ValueError("quota exhausted")
    backend.fetch_price_snapshots = unavailable
    monitor = SignalMonitor(backend, snapshot_batch_provider=BackendSnapshotProvider(backend))
    assert monitor.poll_once() == 0
    assert backend.quote_calls == [("no-plan", codes)]
    assert len(monitor.last_report["uncovered_holdings"]) == 11
    assert all(not row["quote_available"] and "quota exhausted" in row["quote_error"]
               for row in monitor.last_report["uncovered_holdings"])
    assert not backend.triggers
    assert not monitor.last_report["slo_met"]


def test_enabled_empty_account_requires_no_price_request_or_invented_plan():
    backend = CoverageBackend(targets=["empty"])
    monitor = SignalMonitor(backend, snapshot_batch_provider=BackendSnapshotProvider(backend))
    assert monitor.poll_once() == 0
    assert backend.account_calls == ["empty"]
    assert not backend.quote_calls
    assert not monitor.last_report["errors"]
    assert not monitor.last_report["uncovered_holdings"]
