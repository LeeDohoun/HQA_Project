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


@pytest.mark.parametrize("output,expected", [
    ("[WARN][삼성전자] dart collect failed: DART provider error status=020", True),
    ("[WARN][삼성전자] news collect failed: news_search_failed:page=1:RetryExhaustedError status=429", True),
    ("[WARN][NEWS:SEARCH:삼성전자] GET failed attempt=1/3 url=https://search.naver.com/search.naver error=HTTPError status=429", False),
    ("KRX chart request failed (HTTPError) status=429", True),
    ("429 Too Many Requests", True),
    ("saved rcept_no=20260429000123 count=429", False),
    ("[NEWS] 은행권 대출 한도 확대 기사 수집", False),
    ("status=4290 and status=0200 are not provider codes", False),
])
def test_loop_detects_only_structured_rate_limit_signals(output, expected):
    assert loop._contains_rate_limit(output) is expected


def test_discover_writes_a_catalog_and_leaves_the_analysis_universe_alone(tmp_path):
    from scripts.data import discover
    from src.ingestion.naver_theme import ThemeStock, ThemeTargets

    collected = [ThemeTargets(theme_name="2차전지", detail_url="https://finance.naver.com/sise/sise_group_detail.naver?no=1",
                              stocks=[ThemeStock(theme_name="2차전지", stock_name="에코프로", stock_code="086520")])]
    summary = discover.save_theme_catalog(collected, data_dir=str(tmp_path))
    assert summary["saved_theme_count"] == 1
    assert (tmp_path / "theme_catalog" / "2차전지.jsonl").exists()
    assert not (tmp_path / "raw" / "theme_targets").exists()


def _http_session(*statuses):
    import requests

    def response(status):
        def raise_for_status():
            if status >= 400:
                raise requests.HTTPError(f"{status} error", response=SimpleNamespace(status_code=status))
        return SimpleNamespace(status_code=status, text="", content=b"", raise_for_status=raise_for_status)

    queue = [response(status) for status in statuses]
    return SimpleNamespace(get=lambda *args, **kwargs: queue.pop(0) if len(queue) > 1 else queue[0],
                           headers={})


def test_loop_ignores_a_rate_limit_that_a_retry_recovered(capsys):
    from src.ingestion.base import BaseCollector

    collector = BaseCollector(backoff_seconds=0)
    collector.session = _http_session(429, 200)
    assert collector.get_with_retry("https://provider.invalid/list").status_code == 200
    output = capsys.readouterr().out
    assert "status=429" in output                     # the retry stays visible to operators
    assert loop._contains_rate_limit(output) is False


def test_loop_detects_rate_limits_that_exhausted_every_retry(monkeypatch):
    from src.ingestion.dart import DartDisclosureCollector
    from src.ingestion.dart_financials import DartFinancialStatementCollector
    from src.ingestion.naver_news import NaverNewsCollector

    news, dart, financials = NaverNewsCollector(), DartDisclosureCollector("fixture-key"), \
        DartFinancialStatementCollector("fixture-key")
    calls = [lambda: list(news._collect_search_candidates(keyword="삼성전자", from_date="20260901",
                                                          to_date="20260905", max_pages=1)),
             lambda: news._fetch_article_detail("https://news.invalid/1"),
             lambda: dart._collect_listing("00126380", "20260901", "20260905", 100),
             lambda: financials._fetch_rows("00126380", "2025", "11011")]
    for collector in (news, dart, financials):
        collector.backoff_seconds = 0
        collector.session = _http_session(429)
    for call in calls:
        with pytest.raises(Exception) as error:
            call()
        assert "status=429" in str(error.value) and "fixture-key" not in str(error.value)
        assert loop._contains_rate_limit(f"[WARN][삼성전자] news collect failed: {error.value}") is True


@pytest.fixture()
def fresh_settings(request):
    from src.config.settings import get_settings

    get_settings.cache_clear()  # HQA_DATA_DIR is read once and cached
    request.addfinalizer(get_settings.cache_clear)


def test_loop_maintains_saved_theme_targets_by_default(monkeypatch, tmp_path, fresh_settings):
    (tmp_path / "raw/theme_targets").mkdir(parents=True)
    for key in ("2차전지", "반도체"):
        (tmp_path / f"raw/theme_targets/{key}.jsonl").write_text('{"stock_code": "005930", "stock_name": "삼성전자"}\n',
                                                                  encoding="utf-8")
    monkeypatch.setenv("HQA_DATA_DIR", str(tmp_path))
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(loop.subprocess, "run", run)
    monkeypatch.setattr(loop.time, "sleep", Mock(side_effect=KeyboardInterrupt))
    monkeypatch.setattr(sys, "argv", ["loop"])
    with pytest.raises(KeyboardInterrupt):
        loop.main()
    commands = [call.args[0] for call in run.call_args_list]
    assert [command[command.index("--theme-key") + 1] for command in commands] == ["2차전지", "반도체"]


def test_loop_refuses_to_start_without_themes(monkeypatch, tmp_path, capsys, fresh_settings):
    monkeypatch.setenv("HQA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["loop"])
    assert loop.main() == 1
    assert "No themes configured" in capsys.readouterr().out


def test_loop_refreshes_market_indices_once_a_day_after_0800(monkeypatch, tmp_path, fresh_settings):
    from datetime import datetime as real_datetime

    (tmp_path / "raw/theme_targets").mkdir(parents=True)
    (tmp_path / "raw/theme_targets/2차전지.jsonl").write_text('{"stock_code": "006400", "stock_name": "삼성SDI"}\n',
                                                            encoding="utf-8")
    monkeypatch.setenv("HQA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KRX_OPEN_API_KEY", "fixture-key")
    clock = iter([real_datetime(2026, 10, 12, 7, 30, tzinfo=loop.KST), real_datetime(2026, 10, 12, 8, 5, tzinfo=loop.KST),
                  real_datetime(2026, 10, 12, 8, 35, tzinfo=loop.KST)])

    class Clock(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return next(clock)

    monkeypatch.setattr(loop, "datetime", Clock)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout='{"saved_records": 2}', stderr=""))
    monkeypatch.setattr(loop.subprocess, "run", run)
    sleeps = iter([None, None, KeyboardInterrupt()])

    def sleep(_):
        value = next(sleeps)
        if value is not None:
            raise value

    monkeypatch.setattr(loop.time, "sleep", sleep)
    monkeypatch.setattr(sys, "argv", ["loop", "--market-context"])
    with pytest.raises(KeyboardInterrupt):
        loop.main()
    modules = [call.args[0][2] for call in run.call_args_list]
    assert modules.count("scripts.data.market_context") == 1  # skipped at 07:30, run at 08:05, not again at 08:35
    market = next(call.args[0] for call in run.call_args_list if call.args[0][2] == "scripts.data.market_context")
    assert market[market.index("--to-date") + 1] == "20261011" and market[market.index("--from-date") + 1] == "20261001"


def test_loop_market_context_needs_a_krx_key(monkeypatch, tmp_path, fresh_settings, capsys):
    monkeypatch.setenv("HQA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("KRX_OPEN_API_KEY", raising=False)
    monkeypatch.delenv("KRX_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["loop", "--market-context"])
    assert loop.main() == 1
    assert "requires KRX_OPEN_API_KEY" in capsys.readouterr().out
