from __future__ import annotations

from src.runner.signal_monitor import SignalMonitor, evaluate_condition


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
