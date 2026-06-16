from __future__ import annotations

from src.runner.analysis_scheduler import AnalysisScheduler, BackendAutoTradeTargetClient


def test_backend_auto_trade_target_client_fetches_targets_with_internal_token(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"targets": [{"userId": "user-1"}]}

    def fake_get(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("src.runner.analysis_scheduler.requests.get", fake_get)

    client = BackendAutoTradeTargetClient(
        base_url="http://backend",
        internal_token="secret",
        timeout=3,
    )

    assert client.fetch_targets() == [{"userId": "user-1"}]
    assert captured["url"] == "http://backend/api/v1/internal/trading/auto-trade-targets"
    assert captured["headers"]["X-HQA-Internal-Token"] == "secret"
    assert captured["timeout"] == 3


def test_analysis_scheduler_runs_analysis_and_submits_signals_for_each_target():
    runner_calls = []
    submit_calls = []

    class Backend:
        def fetch_targets(self):
            return [
                {
                    "userId": "user-1",
                    "strategyProfile": "short",
                    "investorProfile": {"investment_type": "MID_AGGRESSIVE"},
                    "symbols": [{"stockCode": "005930", "stockName": "삼성전자"}],
                    "themeKeys": ["ai"],
                }
            ]

    class Runner:
        def run_all(self, **kwargs):
            runner_calls.append(kwargs)
            return {
                "global_ranked_leaders": [
                    {"stock_code": "005930"},
                    {"stock_code": "000660"},
                ]
            }

    def submitter(**kwargs):
        submit_calls.append(kwargs)
        return {"submitted": 1}

    scheduler = AnalysisScheduler(
        backend_client=Backend(),
        runner=Runner(),
        submitter=submitter,
        candidate_limit=7,
        per_theme_top_n=3,
        top_n=2,
    )

    result = scheduler.run_once()

    assert result["target_count"] == 1
    assert result["submitted"] == 1
    assert runner_calls[0]["user_id"] == "user-1"
    assert runner_calls[0]["strategy_profile"] == "short"
    assert runner_calls[0]["investor_profile"]["investment_type"] == "MID_AGGRESSIVE"
    assert runner_calls[0]["include_theme_keys"] == ["ai"]
    assert runner_calls[0]["candidate_limit"] == 7
    assert submit_calls[0]["user_id"] == "user-1"
    assert submit_calls[0]["result"]["global_ranked_leaders"] == [{"stock_code": "005930"}]
    assert submit_calls[0]["result"]["selected_count"] == 1


def test_analysis_scheduler_submits_theme_wide_results_when_symbols_are_not_specified():
    submit_calls = []

    class Backend:
        def fetch_targets(self):
            return [
                {
                    "userId": "user-1",
                    "strategyProfile": "short",
                    "investorProfile": {},
                    "symbols": [],
                    "themeKeys": ["semiconductor"],
                }
            ]

    class Runner:
        def run_all(self, **kwargs):
            return {
                "global_ranked_leaders": [
                    {"stock_code": "005930"},
                    {"stock_code": "000660"},
                ]
            }

    def submitter(**kwargs):
        submit_calls.append(kwargs)
        return {"submitted": 2}

    scheduler = AnalysisScheduler(backend_client=Backend(), runner=Runner(), submitter=submitter)

    result = scheduler.run_once()

    assert result["submitted"] == 2
    assert submit_calls[0]["result"]["global_ranked_leaders"] == [
        {"stock_code": "005930"},
        {"stock_code": "000660"},
    ]


def test_analysis_scheduler_runs_all_themes_when_theme_keys_are_empty():
    runner_calls = []

    class Backend:
        def fetch_targets(self):
            return [
                {
                    "userId": "user-1",
                    "strategyProfile": "short",
                    "investorProfile": {},
                    "symbols": [],
                    "themeKeys": [],
                }
            ]

    class Runner:
        def run_all(self, **kwargs):
            runner_calls.append(kwargs)
            return {"global_ranked_leaders": []}

    scheduler = AnalysisScheduler(
        backend_client=Backend(),
        runner=Runner(),
        submitter=lambda **_kwargs: {"submitted": 0},
    )

    scheduler.run_once()

    assert runner_calls[0]["include_theme_keys"] is None
