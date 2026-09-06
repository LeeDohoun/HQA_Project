from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import main as cli


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("argv", [[], ["--help"], ["--help-full"]])
def test_help_does_not_load_models_or_network_clients(argv):
    result = subprocess.run(
        [sys.executable, "-c", (
            "import sys; import main; "
            f"sys.argv = ['main.py', *{argv!r}]; "
            "\ntry: main.main()\nexcept SystemExit as exc: assert exc.code == 0\n"
            "assert 'src.agents' not in sys.modules\n"
            "assert 'src.runner' not in sys.modules\n"
            "assert 'requests' not in sys.modules\n"
            "assert 'openai' not in sys.modules\n"
        )],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--theme-trade" in result.stdout
    assert "--stock" not in result.stdout
    assert "--auto" not in result.stdout


@pytest.mark.parametrize("argv", [
    ["--stock", "005930"], ["--quick", "005930"], ["--theme", "AI"],
    ["--auto"], ["--loop"], ["--paper"], ["--dry-run"],
    ["--collect-command", "collect"], ["--trade-interval-minutes", "15"],
])
def test_retired_modes_and_unused_options_fail_explicitly(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_direct_order_flag_is_rejected_before_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_theme_trading_mode", lambda **_: pytest.fail("must not dispatch"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["--theme-trade", "AI", "--execute"])
    assert exc.value.code == 2
    assert "Python direct order execution has been removed" in capsys.readouterr().err


@pytest.mark.parametrize("fn, kwargs", [
    (cli.run_theme_trading_mode, {"theme": "AI"}),
    (cli.run_theme_report_trading_mode, {"report_path": "unused.json"}),
    (cli.run_multi_theme_trading_mode, {}),
])
def test_direct_order_helpers_reject_execution(fn, kwargs):
    with pytest.raises(ValueError, match="Python direct order execution has been removed"):
        fn(execute=True, **kwargs)


def test_only_one_mode_can_run(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--price", "005930", "--multi-theme-trade"])
    assert exc.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_price_mode_dispatches_without_analysis(monkeypatch):
    prices = []
    monkeypatch.setattr(cli, "show_realtime_price", prices.append)
    assert cli.main(["--price", "005930"]) == 0
    assert prices == ["005930"]


@pytest.mark.parametrize("argv, expected", [
    (["--theme-trade", "AI", "--theme-key", "ai", "--top-n", "4"], {
        "config_path": "config/watchlist.yaml", "include_theme_keys": ["ai"],
        "candidate_limit": 5, "top_n": 4, "min_leader_score": None,
        "strategy_profile": "default",
    }),
    (["--multi-theme-trade", "--execute-top-n", "2", "--top-n", "4",
      "--candidate-limit", "20", "--min-confidence", "70", "--preview"], {
        "config_path": "config/watchlist.yaml", "candidate_limit": 20,
        "per_theme_top_n": 4, "top_n": 2, "min_leader_score": None,
        "min_confidence": 70, "max_risk_level": None,
        "strategy_profile": "default", "buy_only": True,
    }),
])
def test_analysis_modes_submit_to_shared_server(monkeypatch, argv, expected):
    submitted = []

    class Client:
        def submit(self, path, payload):
            submitted.append((path, payload))
            return {"status": "queued", "task_id": "test-task"}

    monkeypatch.setitem(sys.modules, "src.runner.analysis_scheduler", SimpleNamespace(RemoteAnalysisClient=Client))
    assert cli.main(argv) == 0
    assert submitted == [("/runtime/multi-theme-trade", expected)]


def test_report_mode_only_previews_existing_report(monkeypatch):
    calls = []

    class Runner:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def run_from_report(self, **kwargs):
            calls.append(kwargs)
            return {"status": "success", "mode": "preview"}

    monkeypatch.setitem(sys.modules, "src.runner", SimpleNamespace(ThemeLeaderTradingRunner=Runner))
    assert cli.main(["--theme-trade-report", "saved.json", "--execute-top-n", "2"]) == 0
    assert calls == [
        {"config_path": "config/watchlist.yaml"},
        {"report_path": "saved.json", "execute_top_n": 2, "execute": False},
    ]


def test_retired_analysis_functions_are_not_exposed():
    for name in ("run_interactive_mode", "run_stock_analysis", "_run_full_analysis",
                 "_run_quick_analysis", "run_theme_orchestration"):
        assert not hasattr(cli, name)


def test_only_canonical_ai_dockerfile_remains():
    assert not (ROOT / "Dockerfile").exists()
    dockerfile = (ROOT / "ai_server" / "Dockerfile").read_text(encoding="utf-8")
    assert '"--workers", "1"' in dockerfile
    assert "COPY src/ ./src/" in dockerfile


def test_unused_postgres_raw_store_is_removed():
    assert not (ROOT / "src/database/__init__.py").exists()
    assert not (ROOT / "src/database/raw_data_store.py").exists()
