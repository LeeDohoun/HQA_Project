from __future__ import annotations

import json
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from backtesting import __main__ as cli
from backtesting.metrics import max_drawdown


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        pytest.fail("Backtesting CLI contract tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", reject)


@pytest.mark.parametrize("argv,exit_code", [([], 2), (["unknown"], 2), (["--help"], 0)])
def test_command_required_and_help_does_not_load_implementations(monkeypatch, argv, exit_code):
    monkeypatch.setattr(cli.importlib, "import_module", lambda *args: pytest.fail("No command may run"))
    with pytest.raises(SystemExit) as error:
        cli.main(argv)
    assert error.value.code == exit_code


@pytest.mark.parametrize("command", cli.COMMANDS)
def test_dispatch_preserves_all_command_options(monkeypatch, command):
    calls = []

    def load(name):
        assert name == "backtesting." + cli.COMMANDS[command][0]
        return SimpleNamespace(main=lambda argv: calls.append(argv))

    monkeypatch.setattr(cli.importlib, "import_module", load)
    assert cli.main([command, "--input", "a path.json", "--help"]) == 0
    assert calls == [["--input", "a path.json", "--help"]]


def test_dispatch_preserves_failure_exit_status(monkeypatch):
    monkeypatch.setattr(cli.importlib, "import_module", lambda name: SimpleNamespace(main=lambda argv: 7))
    assert cli.main(["run"]) == 7


@pytest.mark.parametrize("command", cli.COMMANDS)
def test_every_command_help_is_offline_and_does_not_write_outputs(tmp_path, monkeypatch, capsys, command):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as error:
        cli.main([command, "--help"])
    assert error.value.code == 0
    assert "usage:" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_package_and_paper_metrics_do_not_load_historical_engine():
    result = subprocess.run(
        [sys.executable, "-c", (
            "import socket, sys\n"
            "def reject(*args, **kwargs): raise AssertionError('No network')\n"
            "socket.socket.connect = reject\n"
            "import backtesting\n"
            "import backtesting.__main__\n"
            "assert 'pandas' not in sys.modules\n"
            "import src.tracing.paper_performance\n"
            "assert 'backtesting.leader_backtest' not in sys.modules\n"
            "assert 'backtesting.llm_signal' not in sys.modules\n"
            "assert 'src.agents.llm_config' not in sys.modules\n"
        )],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_existing_package_imports_remain_available():
    import backtesting
    from backtesting.leader_backtest import run_leader_backtest
    from backtesting.temporal_evidence import TemporalEvidence, TemporalPriceLoader

    assert backtesting.run_leader_backtest is run_leader_backtest
    assert backtesting.TemporalEvidence is TemporalEvidence
    assert backtesting.TemporalPriceLoader is TemporalPriceLoader
    with pytest.raises(AttributeError):
        getattr(backtesting, "unknown")


@pytest.mark.parametrize("values,expected", [([], 0.0), ([100], 0.0), ([100, 80, 110], -0.2), ([100, 110], 0.0)])
def test_shared_drawdown_preserves_historical_metric(values, expected):
    from backtesting.leader_backtest import _max_drawdown

    equity = np.asarray(values, dtype=float)
    assert _max_drawdown is max_drawdown
    assert max_drawdown(equity) == pytest.approx(expected)


def test_paper_runtime_reads_audit_and_budget_without_modifying_ledgers(tmp_path, capsys):
    audit = tmp_path / "audit.sqlite3"
    with sqlite3.connect(audit) as db:
        db.execute("CREATE TABLE paper_events(id INTEGER PRIMARY KEY,kind TEXT,payload TEXT)")
        db.execute("INSERT INTO paper_events VALUES(1,?,?)", ("llm_failure", json.dumps({"error_type": "LLMBudgetExceeded"})))
    budget = tmp_path / "budget.sqlite3"
    with sqlite3.connect(budget) as db:
        db.execute("CREATE TABLE llm_spend(month TEXT,state TEXT,actual_nano INTEGER,reserved_nano INTEGER,"
                   "input_tokens INTEGER,output_tokens INTEGER,reasoning_tokens INTEGER)")
        db.execute("INSERT INTO llm_spend VALUES('2026-09','settled',1000000,2000000,100,20,5)")
    original = {path: path.read_bytes() for path in (audit, budget)}
    assert cli.main(["paper-runtime", "--audit", str(audit), "--baseline-audit", str(audit),
                     "--budget", str(budget)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["candidate"]["budget_rejections"] == 1
    assert report["baseline"] == report["candidate"]
    assert report["budget"][0]["actual_usd"] == 0.001
    assert report["budget"][0]["reasoning_tokens"] == 5
    assert {path: path.read_bytes() for path in (audit, budget)} == original


def test_missing_paper_ledger_fails_without_creating_database(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(FileNotFoundError):
        cli.main(["paper-runtime", "--audit", str(missing)])
    assert not missing.exists()
