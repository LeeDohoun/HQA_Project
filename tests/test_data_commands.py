"""The reorganized commands preserve collection defaults without starting models."""

import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.data import batch, collect, loop


@pytest.mark.parametrize("name", ["collect", "build", "batch", "loop", "discover", "corp_codes", "market_context"])
def test_command_help_is_offline_and_does_not_create_jobs(name, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HQA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", [name, "--help"])
    module = importlib.import_module(f"scripts.data.{name}")
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 0
    assert "usage:" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_batch_uses_single_collector_and_rolling_source_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["batch", "--themes", "Example:fixture", "--data-dir", str(tmp_path)])
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(batch.subprocess, "run", run)
    with pytest.raises(SystemExit) as error:
        batch.main()
    assert error.value.code == 0
    command = run.call_args.args[0]
    assert command[:3] == [sys.executable, "-m", "scripts.data.collect"]
    assert command[command.index("--enabled-sources") + 1] == "news,dart,financials,chart"
    assert command[command.index("--data-dir") + 1] == str(tmp_path)
    assert command[command.index("--theme-key") + 1] == "fixture"
    assert "--from-date" not in command and "--to-date" not in command
    assert "--refresh-targets" not in command
    assert run.call_args.kwargs["cwd"] == Path(__file__).resolve().parents[1]


def test_batch_preserves_explicit_backfill_and_refresh_options(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["batch", "--themes", "Example", "--from-date", "20250101",
                                     "--to-date", "20251231", "--refresh-targets"])
    run = Mock(return_value=SimpleNamespace(returncode=1))
    monkeypatch.setattr(batch.subprocess, "run", run)
    with pytest.raises(SystemExit) as error:
        batch.main()
    assert error.value.code == 1
    command = run.call_args.args[0]
    assert command[command.index("--from-date") + 1] == "20250101"
    assert command[command.index("--to-date") + 1] == "20251231"
    assert "--refresh-targets" in command


def test_batch_dry_run_never_launches_collector(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["batch", "--themes", "Example", "--dry-run"])
    run = Mock(side_effect=AssertionError("dry-run launched a job"))
    monkeypatch.setattr(batch.subprocess, "run", run)
    with pytest.raises(SystemExit) as error:
        batch.main()
    assert error.value.code == 0
    run.assert_not_called()


def test_loop_calls_same_collection_entrypoint_without_analysis(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=1, stdout="partial", stderr=""))
    monkeypatch.setattr(loop.subprocess, "run", run)
    assert loop._run_once("Example", "news,dart") == (1, "partial")
    command = run.call_args.args[0]
    assert command[:3] == [sys.executable, "-m", "scripts.data.collect"]
    assert "--full" not in command and "--collect-and-build" not in command


def test_collection_module_has_no_model_analysis_entrypoint():
    import ast

    tree = ast.parse(Path(collect.__file__).read_text(encoding="utf-8"))
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(name and name.startswith(("src.agents", "src.runner", "openai")) for name in imports)
    assert not hasattr(collect, "_step_analyze")
