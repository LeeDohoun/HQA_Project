from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.runner.signal_monitor import (BackendSignalClient, BackendSnapshotProvider, SignalMonitor, TriggerRejected,
                                      evaluate_condition, market_session)


@pytest.fixture(autouse=True)
def market_open(monkeypatch):
    """Monitor tests run at any hour; the KRX session gate has its own tests below."""
    monkeypatch.setattr("src.runner.signal_monitor.market_session", lambda at: "open")


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


def test_run_forever_keeps_polling_after_transient_backend_errors(monkeypatch):
    import requests
    from src.runner import signal_monitor as module

    class FlakyBackend:
        calls = 0

        def fetch_active_signals(self):
            FlakyBackend.calls += 1
            if FlakyBackend.calls == 1:
                raise requests.ConnectionError("backend restarting")
            return []

    class Provider:
        def prepare(self, signals):
            return {}

    class Audit:
        events = []

        def append(self, kind, payload):
            self.events.append((kind, payload))
            return len(self.events)

    class Stop(Exception):
        pass

    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise Stop()

    monkeypatch.setattr(module.time, "sleep", fake_sleep)
    audit = Audit()
    monitor = module.SignalMonitor(FlakyBackend(), snapshot_batch_provider=Provider(), audit=audit)
    with pytest.raises(Stop):
        monitor.run_forever()
    assert FlakyBackend.calls == 3
    failed = [payload for kind, payload in audit.events if kind == "monitor" and payload.get("status") == "failed"]
    assert len(failed) == 1
    assert failed[0]["slo_met"] is False and failed[0]["errors"][0]["error_type"] == "ConnectionError"
    assert monitor.last_report.get("status") != "failed"


def test_signal_monitor_import_does_not_load_llm_stack():
    import subprocess
    import sys
    from pathlib import Path

    program = ("import sys\nimport src.runner.signal_monitor\n"
               "loaded = [m for m in sys.modules if m.startswith(('src.agents', 'src.tools', 'src.utils.llm_queue', 'src.utils.kis_auth'))]\n"
               "assert not loaded, loaded\n")
    result = subprocess.run([sys.executable, "-c", program], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


KST = timezone(timedelta(hours=9))


@pytest.mark.parametrize("local,expected", [
    (datetime(2026, 10, 12, 10, 0, tzinfo=KST), "open"),
    (datetime(2026, 10, 12, 8, 59, tzinfo=KST), "closed"),
    (datetime(2026, 10, 12, 15, 30, tzinfo=KST), "closed"),     # the close is exclusive
    (datetime(2026, 10, 9, 10, 0, tzinfo=KST), "closed"),       # Hangul Day
    (datetime(2026, 10, 10, 10, 0, tzinfo=KST), "closed"),      # Saturday
    (datetime(2025, 11, 13, 9, 30, tzinfo=KST), "closed"),      # CSAT day with its notice: opens 10:00
    (datetime(2025, 11, 13, 16, 0, tzinfo=KST), "open"),        # ... and closes 16:30
    (datetime(2026, 11, 19, 9, 30, tzinfo=KST), "protect"),     # CSAT day, notice still pending
    (datetime(2026, 11, 19, 16, 0, tzinfo=KST), "protect"),
    (datetime(2026, 11, 19, 16, 30, tzinfo=KST), "closed"),
])
def test_market_session_follows_the_verified_krx_calendar(local, expected):
    assert market_session(local.astimezone(timezone.utc)) == expected


def _two_plan_monitor(session):
    entry = _v2_signal(signalId="entry")
    held = protected_signal("u1", "000660")
    held["signalId"] = "held"
    backend = RecordingBackend([entry, held])
    monitor = SignalMonitor(backend, lambda signal: {"current_price": 105 if signal["signalId"] == "entry" else 85,
                                                     "snapshot_at": NOW.isoformat()},
                            clock=lambda: NOW, session=lambda at: session)
    return monitor, backend


def test_protective_triggers_go_out_before_entries():
    monitor, backend = _two_plan_monitor("open")
    assert monitor.poll_once() == 2
    assert [(signal_id, trigger["triggerType"]) for signal_id, trigger in backend.triggers] == [
        ("held", "EXIT"), ("entry", "ENTRY")]


def test_unverified_session_hours_send_protection_but_hold_entries():
    monitor, backend = _two_plan_monitor("protect")
    assert monitor.poll_once() == 1
    assert [trigger["triggerType"] for _, trigger in backend.triggers] == ["EXIT"]
    assert monitor.last_report["deferred"] == 1 and monitor.last_report["session"] == "protect"


def test_a_closed_session_evaluates_without_sending_anything():
    monitor, backend = _two_plan_monitor("closed")
    assert monitor.poll_once() == 0
    assert not backend.triggers
    assert monitor.last_report["deferred"] == 2 and monitor.last_report["checked"] == 2


def test_run_forever_makes_no_backend_calls_while_the_market_is_closed(monkeypatch):
    from src.runner import signal_monitor as module

    class Backend:
        def fetch_active_signals(self):
            raise AssertionError("polled while closed")

    class Stop(Exception):
        pass

    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise Stop()

    monkeypatch.setattr(module.time, "sleep", fake_sleep)
    monitor = SignalMonitor(Backend(), lambda _: {}, session=lambda at: "closed")
    with pytest.raises(Stop):
        monitor.run_forever()
    assert monitor.last_report == {}


class ScriptedBackend(RecordingBackend):
    def __init__(self, signals, outcomes):
        super().__init__(signals)
        self.outcomes = list(outcomes)

    def trigger_signal(self, signal_id, payload):
        self.triggers.append((signal_id, payload))
        outcome = self.outcomes.pop(0) if self.outcomes else {"accepted": True}
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_a_settled_rejection_is_not_resent_until_the_plan_changes():
    signal = _v2_signal()
    backend = ScriptedBackend([signal], [TriggerRejected("TRIGGER_ALREADY_CONSUMED")])
    monitor = SignalMonitor(backend, lambda _: {"current_price": 105, "snapshot_at": NOW.isoformat()}, clock=lambda: NOW)
    assert monitor.poll_once() == 0
    assert monitor.last_report["rejections"][0]["reason"] == "TRIGGER_ALREADY_CONSUMED"
    assert monitor.last_report["slo_met"]                 # a refused entry is a decision, not a monitoring failure
    assert monitor.poll_once() == 0
    assert len(backend.triggers) == 1
    assert monitor.last_report["settled"] == [{"signal_id": "s1", "trigger_type": "ENTRY", "group_id": "range",
                                               "reason": "TRIGGER_ALREADY_CONSUMED"}]
    signal["planVersion"] = 3
    assert monitor.poll_once() == 1
    assert len(backend.triggers) == 2


def test_a_refused_sell_is_an_error_and_is_retried_on_the_next_poll():
    signal = _v2_signal(status="OPEN", conditionPayload={"schema_version": 2, "exit_conditions": [_group("stop", ("<=", 90))]})
    backend = ScriptedBackend([signal], [TriggerRejected("KIS_RATE_LIMITED")])
    monitor = SignalMonitor(backend, lambda _: {"current_price": 85, "snapshot_at": NOW.isoformat()}, clock=lambda: NOW)
    assert monitor.poll_once() == 0
    assert "KIS_RATE_LIMITED" in monitor.last_report["errors"][0]["error"]
    assert not monitor.last_report["slo_met"]
    assert monitor.poll_once() == 1
    assert len(backend.triggers) == 2


def test_an_exit_refused_in_one_plan_state_is_retried_once_the_state_changes():
    signal = _v2_signal(conditionPayload={"schema_version": 2, "entry_conditions": [_group("range", (">=", 100))],
                                          "exit_conditions": [_group("stop", ("<=", 90))]})
    backend = ScriptedBackend([signal], [TriggerRejected("EXIT_STATE_INVALID")])
    monitor = SignalMonitor(backend, lambda _: {"current_price": 85, "holding_quantity": 3,
                                                "snapshot_at": NOW.isoformat()}, clock=lambda: NOW)
    assert monitor.poll_once() == 0
    assert monitor.poll_once() == 0 and len(backend.triggers) == 1   # still WAITING_ENTRY: not re-sent
    signal["status"] = "OPEN"                                          # the fill was recorded
    assert monitor.poll_once() == 1
    assert [trigger["triggerType"] for _, trigger in backend.triggers] == ["EXIT", "EXIT"]


def test_an_accepted_trigger_rests_for_a_quiet_period_before_it_is_sent_again():
    clock = [NOW]
    signal = _v2_signal(status="OPEN", conditionPayload={"schema_version": 2, "exit_conditions": [_group("stop", ("<=", 90))]})
    backend = ScriptedBackend([signal], [])
    monitor = SignalMonitor(backend, lambda _: {"current_price": 85, "snapshot_at": clock[0].isoformat()},
                            clock=lambda: clock[0])
    assert monitor.poll_once() == 1
    clock[0] = NOW + timedelta(seconds=20)
    assert monitor.poll_once() == 0
    assert monitor.last_report["quiet"] == 1
    clock[0] = NOW + timedelta(seconds=61)
    assert monitor.poll_once() == 1
    assert len(backend.triggers) == 2


def test_held_shares_under_an_entry_plan_get_their_stop_and_never_a_second_entry():
    signal = _v2_signal(conditionPayload={"schema_version": 2, "entry_conditions": [_group("range", (">=", 80))],
                                          "exit_conditions": [_group("stop", ("<=", 90))]})
    monitor, backend = _monitor(signal, price=85)
    monitor.price_provider = lambda _: {"current_price": 85, "holding_quantity": 3, "snapshot_at": NOW.isoformat()}
    assert monitor.poll_once() == 1
    assert backend.triggers[0][1]["triggerType"] == "EXIT"
    monitor.price_provider = lambda _: {"current_price": 95, "holding_quantity": 3, "snapshot_at": NOW.isoformat()}
    assert monitor.poll_once() == 0                     # entry is true at 95, but the shares are already held


def test_backend_error_bodies_survive_in_the_raised_error(monkeypatch):
    import requests

    class Response:
        status_code = 400
        text = '{"code":"BAD_REQUEST","message":"UNKNOWN_CONDITION_GROUP"}'

        def raise_for_status(self):
            raise requests.HTTPError("400 Client Error", response=self)

    monkeypatch.setattr("src.runner.signal_monitor.requests.post", lambda *a, **kw: Response())
    with pytest.raises(requests.HTTPError, match="UNKNOWN_CONDITION_GROUP"):
        BackendSignalClient(internal_token="test").trigger_signal("s1", {})


def test_internal_token_is_stripped_and_blank_tokens_are_refused():
    assert BackendSignalClient(internal_token=" token\n").internal_token == "token"
    with pytest.raises(ValueError, match="HQA_INTERNAL_TOKEN"):
        BackendSignalClient(internal_token=" \n")


def test_an_audit_failure_does_not_turn_a_completed_poll_into_a_failed_one():
    class BrokenAudit:
        def append(self, kind, payload):
            raise OSError("disk full")

    signal = _v2_signal(status="OPEN", conditionPayload={"schema_version": 2, "exit_conditions": [_group("stop", ("<=", 90))]})
    backend = RecordingBackend([signal])
    monitor = SignalMonitor(backend, lambda _: {"current_price": 85, "snapshot_at": NOW.isoformat()},
                            clock=lambda: NOW, audit=BrokenAudit())
    assert monitor.poll_once() == 1
    assert monitor.last_report["triggered"] == 1 and "disk full" in monitor.last_report["audit_error"]
    assert monitor.last_report.get("status") != "failed"


def _held_plan(**updates):
    plan = protected_signal("u1", "000001")
    plan.update(updates)
    return plan


@pytest.mark.parametrize("plan,reason", [
    (_held_plan(conditionPayload=None, rejectReason="INVALID_STORED_CONDITIONS"), "plan_conditions_unreadable"),
    (_held_plan(rejectReason="BROKER_ORDER_ID_NOT_UNIQUE_OR_MISSING"), "protection_blocked:BROKER_ORDER_ID_NOT_UNIQUE_OR_MISSING"),
    (_held_plan(rejectReason="ORDER_RECONCILIATION_REQUIRED",
                unresolvedOrders=[{"status": "UNKNOWN", "orderSide": "SELL", "brokerOrderKnown": False}]),
     "protection_blocked:order_without_broker_id"),
    (_held_plan(conditionPayload={"schema_version": 2}), "plan_without_exit"),
    (_held_plan(managedQuantity=1), "partially_managed:1/3"),
])
def test_only_a_plan_the_backend_can_sell_counts_as_protection(plan, reason):
    backend = CoverageBackend(signals=[plan], holdings={"u1": ["000001"]})
    backend.trigger_signal = lambda *args: {"accepted": True}
    monitor = SignalMonitor(backend, snapshot_batch_provider=BackendSnapshotProvider(backend))
    monitor.poll_once()
    assert [row["reason"] for row in monitor.last_report["uncovered_holdings"]] == [reason]
    assert {"user_id": "u1", "stock_code": "000001", "error": "missing_protection", "reason": reason} in \
        monitor.last_report["errors"]


def test_a_working_order_with_a_broker_id_still_counts_as_protection():
    plan = _held_plan(unresolvedOrders=[{"status": "ORDER_SUBMITTED", "orderSide": "SELL", "brokerOrderKnown": True}])
    backend = CoverageBackend(signals=[plan], holdings={"u1": ["000001"]})
    monitor = SignalMonitor(backend, snapshot_batch_provider=BackendSnapshotProvider(backend))
    monitor.poll_once()
    assert not monitor.last_report["uncovered_holdings"]


def test_an_unrecorded_entry_fill_is_reported_only_when_it_outlasts_the_grace_period():
    plan = _v2_signal(signalId="entry", userId="u1", stockCode="000001")
    backend = CoverageBackend(signals=[plan], holdings={"u1": ["000001"]})
    provider = BackendSnapshotProvider(backend)
    monitor = SignalMonitor(backend, snapshot_batch_provider=provider)
    monitor.poll_once()
    assert monitor.last_report["uncovered_holdings"][0]["grace"] is True
    assert not monitor.last_report["errors"] and not backend.triggers
    provider._unrecorded_since[("u1", "000001")] = datetime.now(timezone.utc) - timedelta(seconds=61)
    monitor.poll_once()
    assert monitor.last_report["errors"] == [{"user_id": "u1", "stock_code": "000001", "error": "missing_protection",
                                              "reason": "entry_fill_unrecorded"}]


def test_capacity_overload_is_reported_while_every_holding_is_still_quoted():
    backend = CoverageBackend(signals=[protected_signal("u1", code) for code in ("000001", "000002", "000003")],
                              holdings={"u1": ["000001", "000002", "000003"]})
    snapshot = backend.fetch_account_snapshot
    backend.fetch_account_snapshot = lambda user_id: {**snapshot(user_id), "monitorCapacity": 2,
                                                      "monitorCapacityExceeded": True}
    monitor = SignalMonitor(backend, snapshot_batch_provider=BackendSnapshotProvider(backend))
    assert monitor.poll_once() == 3
    assert backend.quote_calls[-1] == ("u1", ["000001", "000002", "000003"])
    assert {"user_id": "u1", "error": "monitor_capacity_exceeded:3/2"} in monitor.last_report["errors"]


@pytest.mark.parametrize("session,is_error", [("open", True), ("protect", False)])
def test_market_closed_refusals_are_errors_only_when_the_hours_were_verified(session, is_error):
    signal = _v2_signal(status="OPEN", conditionPayload={"schema_version": 2, "exit_conditions": [_group("stop", ("<=", 90))]})
    backend = ScriptedBackend([signal], [TriggerRejected("MARKET_CLOSED")])
    monitor = SignalMonitor(backend, lambda _: {"current_price": 85, "snapshot_at": NOW.isoformat()},
                            clock=lambda: NOW, session=lambda at: session)
    monitor.poll_once()
    assert monitor.last_report["rejections"][0]["reason"] == "MARKET_CLOSED"
    assert bool(monitor.last_report["errors"]) is is_error


def _tiered_plan(**updates):
    plan = _v2_signal(status="OPEN", plannedExitAt=(NOW + timedelta(days=3)).isoformat(), conditionPayload={
        "schema_version": 2, "entry_conditions": [], "invalidation_conditions": [],
        "exit_conditions": [_group("stop", ("<=", 90))],
        "reduce_conditions": [
            {"id": "take-profit-1", "reduce_fraction": 0.5, "all": [{"field": "pnl_rate", "operator": ">=", "value": 5.0}]},
            {"id": "take-profit-2", "reduce_fraction": 0.5, "all": [{"field": "pnl_rate", "operator": ">=", "value": 10.0}]}]})
    plan.update(updates)
    return plan


def _priced(price):
    return lambda _: {"current_price": price, "pnl_rate": (price / 100 - 1) * 100, "holding_quantity": 10,
                      "snapshot_at": NOW.isoformat()}


def test_a_consumed_take_profit_tier_lets_the_next_tier_go_out():
    backend = ScriptedBackend([_tiered_plan()], [TriggerRejected("TRIGGER_ALREADY_CONSUMED")])
    monitor = SignalMonitor(backend, _priced(112), clock=lambda: NOW)
    monitor.poll_once()
    assert not monitor.last_report["errors"]          # an already-run reduction is not a failure
    monitor.poll_once()
    assert [trigger["groupId"] for _, trigger in backend.triggers] == ["take-profit-1", "take-profit-2"]
    assert monitor.last_report["settled"][0]["group_id"] == "take-profit-1"


def test_a_due_planned_exit_never_stands_in_front_of_a_crossed_stop():
    plan = _tiered_plan(plannedExitAt=(NOW - timedelta(seconds=1)).isoformat())
    backend = ScriptedBackend([plan], [])
    monitor = SignalMonitor(backend, _priced(85), clock=lambda: NOW)
    assert monitor.poll_once() == 1
    assert backend.triggers[0][1]["groupId"] == "stop"


def test_a_refused_planned_exit_rests_with_the_plans_reductions_then_retries():
    clock = [NOW]
    plan = _tiered_plan(plannedExitAt=(NOW - timedelta(seconds=1)).isoformat())
    backend = ScriptedBackend([plan], [TriggerRejected("ORDER_RECONCILIATION_REQUIRED")])
    monitor = SignalMonitor(backend, lambda _: {"current_price": 112, "pnl_rate": 12.0, "holding_quantity": 10,
                                                "snapshot_at": clock[0].isoformat()}, clock=lambda: clock[0])
    monitor.poll_once()                                 # refused while the backend cancels a working reduction
    clock[0] = NOW + timedelta(seconds=20)
    monitor.poll_once()                                 # it rests, and the take-profit tiers wait with it
    clock[0] = NOW + timedelta(seconds=61)
    monitor.poll_once()                                 # retried after the rest
    assert [trigger["groupId"] for _, trigger in backend.triggers] == ["planned-exit", "planned-exit"]
    assert not monitor.last_report["settled"]


def test_a_crossed_stop_goes_out_while_a_refused_planned_exit_rests():
    clock, price = [NOW], [112.0]
    plan = _tiered_plan(plannedExitAt=(NOW - timedelta(seconds=1)).isoformat())
    backend = ScriptedBackend([plan], [TriggerRejected("UNKNOWN_CONDITION_GROUP")])
    monitor = SignalMonitor(backend, lambda _: {"current_price": price[0], "pnl_rate": price[0] - 100, "holding_quantity": 10,
                                                "snapshot_at": clock[0].isoformat()}, clock=lambda: clock[0])
    monitor.poll_once()                                 # planned exit refused (backend clock behind)
    clock[0], price[0] = NOW + timedelta(seconds=20), 85.0
    monitor.poll_once()
    assert [trigger["groupId"] for _, trigger in backend.triggers] == ["planned-exit", "stop"]


def test_an_order_working_for_one_exit_group_is_not_crowded_by_another_group():
    clock = [NOW]
    plan = _v2_signal(status="OPEN", conditionPayload={"schema_version": 2, "exit_conditions": [
        _group("stop", ("<=", 90)), _group("hard-floor", ("<=", 88))]})
    backend = ScriptedBackend([plan], [])
    monitor = SignalMonitor(backend, lambda _: {"current_price": 85, "holding_quantity": 10,
                                                "snapshot_at": clock[0].isoformat()}, clock=lambda: clock[0])
    monitor.poll_once()
    clock[0] = NOW + timedelta(seconds=20)
    monitor.poll_once()                                 # "stop" rests; "hard-floor" must not cancel its order
    assert [trigger["groupId"] for _, trigger in backend.triggers] == ["stop"]
    assert monitor.last_report["quiet"] == 1
