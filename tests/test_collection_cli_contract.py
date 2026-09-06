import json
from argparse import Namespace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from scripts.data import common as collection_common, loop as collect_themes_loop, corp_codes as download_dart_corp_codes
from scripts.data import collect as theme_pipeline
from src.ingestion.services import CollectResult, IngestionRunReport
from src.ingestion.theme_targets import ThemeTargetStore, load_corp_code_map
from src.ingestion.types import StockTarget


def args_for(tmp_path, **changes):
    return Namespace(data_dir=str(tmp_path), theme="fixture", enabled_sources="news,dart", corp_codes_csv=str(tmp_path / "corp.csv"),
                     reuse_saved_targets=True, max_news=20, forum_pages=3, chart_pages=5, from_date="20260901",
                     to_date="20260905", incremental=True, **changes)


def saved_target(tmp_path):
    store = ThemeTargetStore(str(tmp_path))
    store.save_targets("fixture", [StockTarget("Example", "005930")])
    (tmp_path / "corp.csv").write_text("stock_code,corp_code\n005930,00126380\n", encoding="utf-8")
    return store


def report_result():
    return CollectResult(report=IngestionRunReport(
        "005930", "Example", ["news", "dart"], source_success={"news": True, "dart": False},
        source_status={"news": "no_data", "dart": "error"}, failures={"dart": "DART provider error status=020"}))


def test_default_dates_roll_and_explicit_windows_do_not_enable_incremental(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 6, 1, tzinfo=tz)

    monkeypatch.setattr(collection_common, "datetime", Clock)
    args = Namespace(from_date=None, to_date=None)
    collection_common.resolve_dates(args)
    assert args.to_date == "20260905"
    assert args.from_date == (datetime(2026, 9, 5) - timedelta(days=400)).strftime("%Y%m%d")
    assert args.incremental is True
    args = Namespace(from_date="20250101", to_date="20251231")
    collection_common.resolve_dates(args)
    assert (args.from_date, args.to_date, args.incremental) == ("20250101", "20251231", False)


def test_unknown_sources_fail_instead_of_silently_disappearing():
    with pytest.raises(ValueError):
        theme_pipeline._parse_enabled_sources("news,typo")
    assert collection_common.enabled_sources("news,news,dart") == ["news", "dart"]


def test_saved_theme_targets_receive_missing_corporate_codes(tmp_path, monkeypatch):
    store = saved_target(tmp_path)
    discovery = Mock(side_effect=AssertionError("saved targets must not trigger discovery"))
    monkeypatch.setattr(theme_pipeline, "NaverThemeStockCollector", discovery)
    targets = theme_pipeline._resolve_targets(args_for(tmp_path), "fixture")
    assert targets[0].corp_code == "00126380"
    assert store.load_targets("fixture")[0].corp_code == "00126380"
    discovery.assert_not_called()


def test_required_missing_and_conflicting_corporate_mapping_fail(tmp_path):
    store = ThemeTargetStore(str(tmp_path))
    target = StockTarget("Example", "005930")
    with pytest.raises(ValueError, match="missing DART corporate code"):
        store.backfill_corp_codes("fixture", [target], {}, required=True)
    target.corp_code = "00126380"
    with pytest.raises(ValueError, match="conflicts"):
        store.backfill_corp_codes("fixture", [target], {"005930": "00126381"}, required=True)


@pytest.mark.parametrize("content", [
    "stock_code,corp_code\n005930,invalid\n",
    "stock_code,corp_code\n005930,00126380\n005930,00126381\n",
    "stock_code,wrong_column\n005930,00126380\n",
])
def test_invalid_corporate_master_fails_validation(tmp_path, content):
    path = tmp_path / "corp.csv"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_corp_code_map(str(path))


def test_collection_preserves_source_failures_and_backfills_saved_targets(tmp_path, monkeypatch):
    store = saved_target(tmp_path)
    monkeypatch.setattr(theme_pipeline, "_ensure_fresh_corp_codes_csv", lambda *args, **kwargs: False)
    service = SimpleNamespace(collect_target_documents=Mock(return_value=report_result()))
    monkeypatch.setattr(theme_pipeline, "IngestionService", lambda: service)
    monkeypatch.setattr("sys.argv", ["collect", "--theme", "fixture", "--data-dir", str(tmp_path),
                                    "--enabled-sources", "news,dart", "--corp-codes-csv", str(tmp_path / "corp.csv")])
    builder = Mock(side_effect=AssertionError("failed data must not be published"))
    monkeypatch.setattr(theme_pipeline, "EvidenceIndexBuilder", builder)
    assert theme_pipeline.main() == 1
    result = json.loads((tmp_path / "reports/fixture_ingestion_report.json").read_text())
    assert result["status"] == "partial"
    assert result["per_stock_reports"][0]["source_status"]["dart"] == "error"
    assert result["per_stock_reports"][0]["failures"]["dart"].endswith("status=020")
    request = service.collect_target_documents.call_args.args[0]
    assert request.incremental is True and request.target.corp_code == "00126380"
    assert store.load_targets("fixture")[0].corp_code == "00126380"
    builder.assert_not_called()
    assert result["build_status"] == "blocked"


@pytest.mark.parametrize("flag", ["--full", "--build-and-analyze", "--analyze-only"])
def test_collection_rejects_retired_analysis_modes_before_running(tmp_path, monkeypatch, flag):
    monkeypatch.setattr("sys.argv", ["collect", "--theme", "fixture", "--data-dir", str(tmp_path), flag])
    targets = Mock(side_effect=AssertionError("retired flags must not start a job"))
    monkeypatch.setattr(theme_pipeline, "_resolve_targets", targets)
    with pytest.raises(SystemExit) as error:
        theme_pipeline.main()
    assert error.value.code == 2
    targets.assert_not_called()


def test_default_pipeline_is_collection_only_even_with_model_key_present(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture-key")
    saved_target(tmp_path)
    monkeypatch.setattr("sys.argv", ["collect", "--theme", "fixture", "--data-dir", str(tmp_path),
                                    "--corp-codes-csv", str(tmp_path / "corp.csv")])
    monkeypatch.setattr(theme_pipeline, "_ensure_fresh_corp_codes_csv", lambda *args, **kwargs: False)
    sources = ["news", "dart", "financials", "chart"]
    result = CollectResult(report=IngestionRunReport(
        "005930", "Example", sources, source_success={source: True for source in sources},
        source_status={source: "no_data" for source in sources}))
    collect = Mock(return_value=result)
    monkeypatch.setattr(theme_pipeline, "IngestionService", lambda: SimpleNamespace(collect_target_documents=collect))
    monkeypatch.setattr(theme_pipeline, "EvidenceIndexBuilder", lambda **kwargs: SimpleNamespace(rebuild_theme=lambda **kw: {}))
    assert theme_pipeline.main() == 0
    request = collect.call_args.args[0]
    assert request.enabled_sources == sources and request.incremental is True
    report = json.loads((tmp_path / "reports/fixture_ingestion_report.json").read_text())
    assert report["build_status"] == "done"


def test_theme_partial_failure_keeps_raw_history_and_does_not_publish_index(tmp_path, monkeypatch):
    saved_target(tmp_path)
    raw = tmp_path / "raw/news/fixture.jsonl"
    raw.parent.mkdir(parents=True)
    raw.write_text('{"content":"old observation"}\n')
    monkeypatch.setattr("sys.argv", ["theme_pipeline", "--theme", "fixture", "--data-dir", str(tmp_path),
                                    "--enabled-sources", "news,dart", "--corp-codes-csv", str(tmp_path / "corp.csv"),
                                    "--reuse-saved-targets", "--update-mode", "overwrite"])
    monkeypatch.setattr(theme_pipeline, "_ensure_fresh_corp_codes_csv", lambda *args, **kwargs: False)
    monkeypatch.setattr(theme_pipeline, "IngestionService", lambda: SimpleNamespace(
        collect_target_documents=Mock(return_value=report_result())))
    builder = Mock(side_effect=AssertionError("failed data must not be published"))
    monkeypatch.setattr(theme_pipeline, "EvidenceIndexBuilder", builder)
    assert theme_pipeline.main() == 1
    assert "old observation" in raw.read_text()
    builder.assert_not_called()
    report = json.loads((tmp_path / "reports/fixture_ingestion_report.json").read_text())
    assert report["status"] == "partial" and report["build_status"] == "blocked"
    assert report["per_stock_reports"][0]["failures"]["dart"].endswith("status=020")


@pytest.mark.parametrize("kind", ["transport", "business", "http"])
def test_corporate_code_download_errors_do_not_expose_key_or_request_url(monkeypatch, kind):
    secret = "fixture-secret-key"
    if kind == "transport":
        response = Mock(side_effect=requests.RequestException("https://provider.invalid?crtfc_key=" + secret))
    else:
        response = Mock(return_value=SimpleNamespace(status_code=401 if kind == "http" else 200,
                        content=f"<result><status>020</status><message>{secret}</message></result>".encode()))
    monkeypatch.setattr(download_dart_corp_codes.requests, "get", response)
    with pytest.raises(ValueError) as error:
        download_dart_corp_codes.download_corp_codes(secret)
    assert secret not in str(error.value) and "https://" not in str(error.value)
    assert response.call_args.kwargs["allow_redirects"] is False


def test_loop_recognizes_dart_quota_status():
    assert collect_themes_loop._contains_rate_limit("DART provider error status=020")
