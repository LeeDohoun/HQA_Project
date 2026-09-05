import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest

from src.ingestion.krx_chart import KrxChartCollector
from src.ingestion.krx_benchmarks import _record
from src.runner.analysis_data import price_features
from src.runner.market_context_data import load_benchmark_context


AS_OF = datetime(2026, 9, 5, 3, tzinfo=timezone.utc)
KOSPI = "\ucf54\uc2a4\ud53c"
KOSDAQ = "\ucf54\uc2a4\ub2e5"
SECTOR = "\uc804\uae30\u00b7\uc804\uc790"


def write_rows(root, filename, rows):
    path = root / "market_context" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def candidate(market=None):
    history = [{"available_at": day + "T15:30:00+09:00", "close": 100.0}
               for day in ("2026-09-03", "2026-09-04")]
    if market:
        for row in history:
            row.update(market=market, source="krx", price_basis="unadjusted",
                       source_url=KrxChartCollector.KOSPI_DAILY_URL if market == "KOSPI"
                       else KrxChartCollector.KOSDAQ_DAILY_URL)
    return {"stock_code": "005930", "price_history": history,
            "themes": [{"key": "semiconductor", "name": SECTOR}], "sector": SECTOR}


def mapping(kind="market", series="KOSPI", **overrides):
    return {"schema_version": 1, "stock_code": "005930", "kind": kind, "series": series,
            "index_name": (KOSPI if series == "KOSPI" else KOSDAQ) if kind == "market" else SECTOR,
            "source_id": "mapping:" + kind, "version": "version-1",
            "source_url": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
            "available_at": "2026-09-04T09:00:00+09:00", "effective_from": "2026-01-01",
            "effective_to": None, **overrides}


def bar(series="KOSPI", index_name=KOSPI, **overrides):
    return {**_record(series, index_name, date(2026, 9, 4), 2000.0,
                      datetime(2026, 9, 4, 8, tzinfo=timezone.utc)), **overrides}


def test_optional_context_without_files_preserves_explicit_unavailable_states(tmp_path):
    result = load_benchmark_context(tmp_path, candidate(), AS_OF)
    assert result == {kind: {"status": "unavailable", "data_gaps": [kind + "_mapping_unavailable"]}
                      for kind in ("market", "sector")}


def test_sector_is_never_inferred_from_theme_or_available_sector_bars(tmp_path):
    write_rows(tmp_path, "benchmarks.jsonl", [bar(), bar(index_name=SECTOR)])
    result = load_benchmark_context(tmp_path, candidate("KOSPI"), AS_OF)
    assert result["market"]["status"] == "ready"
    assert result["market"]["index_name"] == KOSPI
    assert result["sector"]["status"] == "unavailable"


def test_legacy_price_history_stays_unknown_through_actual_normalizer(tmp_path):
    last = datetime(2026, 9, 4, tzinfo=timezone.utc)
    rows = [{"timestamp": (last - timedelta(days=offset)).date().isoformat(),
             "open": "100", "high": "101", "low": "99", "close": "100", "volume": "1000",
             "metadata": {"source": "krx", "raw_date": "20260904"}} for offset in range(150)]
    _, history = price_features(rows, AS_OF)
    assert all("market" not in row and "price_basis" not in row for row in history)
    write_rows(tmp_path, "benchmarks.jsonl", [bar(), bar("KOSDAQ", KOSDAQ)])
    result = load_benchmark_context(tmp_path, {**candidate(), "price_history": history}, AS_OF)
    assert result["market"]["status"] == result["sector"]["status"] == "unavailable"


def test_explicit_source_backed_mapping_can_supply_market_for_legacy_prices(tmp_path):
    write_rows(tmp_path, "benchmark_mappings.jsonl", [mapping()])
    write_rows(tmp_path, "benchmarks.jsonl", [bar()])
    result = load_benchmark_context(tmp_path, candidate(), AS_OF)
    assert result["market"]["status"] == "ready"
    assert result["market"]["mapping_source_id"] == "mapping:market"
    assert result["market"]["mapping_available_at"] == "2026-09-04T00:00:00+00:00"
    assert result["market"]["mapping_source_url"] == mapping()["source_url"]


def test_series_and_index_name_must_both_match_exactly(tmp_path):
    rows = [bar(), bar("KOSDAQ", KOSPI), bar(index_name=KOSPI + " 200"),
            bar(index_name=KOSPI + " "), bar(index_name=SECTOR)]
    write_rows(tmp_path, "benchmarks.jsonl", rows)
    write_rows(tmp_path, "benchmark_mappings.jsonl", [mapping("sector")])
    result = load_benchmark_context(tmp_path, candidate("KOSPI"), AS_OF)
    assert result["market"]["bars"] == [rows[0]]
    assert result["sector"]["bars"] == [rows[4]]


@pytest.mark.parametrize("create_history", [False, True])
def test_missing_exact_index_never_uses_another_series_or_zero_data(tmp_path, create_history):
    if create_history:
        write_rows(tmp_path, "benchmarks.jsonl", [bar(index_name=SECTOR)])
    result = load_benchmark_context(tmp_path, candidate("KOSPI"), AS_OF)
    assert result["market"]["status"] == "unavailable"
    expected = "benchmark_index_not_found" if create_history else "benchmark_history_unavailable"
    assert result["market"]["data_gaps"] == [expected]
    assert not result["market"].get("bars")


def test_future_mapping_is_excluded_before_unavailable_fields_are_validated(tmp_path):
    future = mapping("sector", available_at=(AS_OF + timedelta(seconds=1)).isoformat())
    del future["source_url"]
    write_rows(tmp_path, "benchmark_mappings.jsonl", [future])
    write_rows(tmp_path, "benchmarks.jsonl", [bar(index_name=SECTOR)])
    result = load_benchmark_context(tmp_path, candidate(), AS_OF)
    assert result["sector"]["status"] == "unavailable"


def test_future_effective_mapping_does_not_replace_current_mapping(tmp_path):
    original = mapping("sector")
    future = mapping("sector", source_id="future-effective", version="version-2",
                     effective_from="2026-10-01", available_at="2026-09-05T09:00:00+09:00")
    write_rows(tmp_path, "benchmark_mappings.jsonl", [future, original])
    write_rows(tmp_path, "benchmarks.jsonl", [bar(index_name=SECTOR)])
    result = load_benchmark_context(tmp_path, candidate("KOSPI"), AS_OF)
    assert result["sector"]["mapping_source_id"] == "mapping:sector"


@pytest.mark.parametrize("hour,ready", [(14, False), (16, True)])
def test_mapping_effectiveness_uses_korean_date_boundary(tmp_path, hour, ready):
    write_rows(tmp_path, "benchmark_mappings.jsonl", [mapping(effective_from="2026-09-05")])
    write_rows(tmp_path, "benchmarks.jsonl", [bar()])
    result = load_benchmark_context(tmp_path, candidate(), datetime(2026, 9, 4, hour, tzinfo=timezone.utc))
    assert (result["market"]["status"] == "ready") is ready


def test_finite_mapping_interval_is_preserved_for_historical_window_validation(tmp_path):
    write_rows(tmp_path, "benchmark_mappings.jsonl", [mapping(effective_to="2026-09-03")])
    write_rows(tmp_path, "benchmarks.jsonl", [bar()])
    result = load_benchmark_context(tmp_path, candidate(), AS_OF)
    assert result["market"]["effective_from"] == "2026-01-01"
    assert result["market"]["effective_to"] == "2026-09-03"


@pytest.mark.parametrize("reverse", [False, True])
def test_latest_known_mapping_selection_is_independent_of_file_order(tmp_path, reverse):
    rows = [mapping("sector"), mapping("sector", source_id="revised", version="version-2",
                                      available_at="2026-09-05T09:00:00+09:00")]
    write_rows(tmp_path, "benchmark_mappings.jsonl", rows[::-1] if reverse else rows)
    write_rows(tmp_path, "benchmarks.jsonl", [bar(index_name=SECTOR)])
    result = load_benchmark_context(tmp_path, candidate(), AS_OF)
    assert result["sector"]["mapping_source_id"] == "revised"


def test_same_availability_conflict_fails_but_exact_duplicate_is_harmless(tmp_path):
    original = mapping("sector")
    write_rows(tmp_path, "benchmarks.jsonl", [bar(index_name=SECTOR)])
    write_rows(tmp_path, "benchmark_mappings.jsonl", [original, original])
    assert load_benchmark_context(tmp_path, candidate(), AS_OF)["sector"]["status"] == "ready"
    changed = {**original, "version": "conflicting", "index_name": SECTOR + "2"}
    write_rows(tmp_path, "benchmark_mappings.jsonl", [original, changed])
    with pytest.raises(ValueError, match="conflict"):
        load_benchmark_context(tmp_path, candidate(), AS_OF)


@pytest.mark.parametrize("reverse", [False, True])
def test_market_sector_series_mismatch_is_rejected_regardless_of_row_order(tmp_path, reverse):
    rows = [mapping("sector", "KOSPI"), mapping("market", "KOSDAQ")]
    write_rows(tmp_path, "benchmark_mappings.jsonl", rows[::-1] if reverse else rows)
    with pytest.raises(ValueError, match="conflict"):
        load_benchmark_context(tmp_path, candidate(), AS_OF)


@pytest.mark.parametrize("kind", ["market", "sector"])
def test_explicit_mapping_cannot_override_verified_stock_market(tmp_path, kind):
    write_rows(tmp_path, "benchmark_mappings.jsonl", [mapping(kind, "KOSDAQ")])
    with pytest.raises(ValueError, match="conflict"):
        load_benchmark_context(tmp_path, candidate("KOSPI"), AS_OF)


@pytest.mark.parametrize("kind", ["market", "sector"])
@pytest.mark.parametrize("reverse", [False, True])
def test_mapping_checks_each_verified_bar_when_market_history_is_mixed(tmp_path, kind, reverse):
    stock = candidate("KOSPI")
    stock["price_history"][1].update(market="KOSDAQ", source_url=KrxChartCollector.KOSDAQ_DAILY_URL)
    if reverse:
        stock["price_history"].reverse()
    write_rows(tmp_path, "benchmark_mappings.jsonl", [mapping(kind)])
    with pytest.raises(ValueError, match="effective interval"):
        load_benchmark_context(tmp_path, stock, AS_OF)


@pytest.mark.parametrize("kind", ["market", "sector"])
@pytest.mark.parametrize("series,start,end", [("KOSPI", "2026-09-03", "2026-09-03"),
                                             ("KOSDAQ", "2026-09-04", None)])
def test_other_market_outside_mapping_effective_interval_is_not_a_conflict(tmp_path, kind, series, start, end):
    stock = candidate("KOSPI")
    stock["price_history"][1].update(market="KOSDAQ", source_url=KrxChartCollector.KOSDAQ_DAILY_URL)
    explicit = mapping(kind, series, effective_from=start, effective_to=end)
    write_rows(tmp_path, "benchmark_mappings.jsonl", [explicit])
    write_rows(tmp_path, "benchmarks.jsonl", [bar(series, explicit["index_name"])])
    result = load_benchmark_context(tmp_path, stock, AS_OF)
    assert result[kind]["status"] == "ready"
    assert result[kind]["series"] == series
    assert result[kind]["effective_from"] == start
    assert result[kind]["effective_to"] == end


@pytest.mark.parametrize("separator", ["?", "#"])
@pytest.mark.parametrize("credential_key", ["access_token", "AUTH_KEY", "crtfc_key", "api_key", "authorization"])
def test_source_url_credentials_are_rejected_in_query_and_fragment(tmp_path, separator, credential_key):
    url = "https://data.krx.co.kr/source" + separator + credential_key + "=test-placeholder"
    write_rows(tmp_path, "benchmark_mappings.jsonl", [mapping(source_url=url)])
    with pytest.raises(ValueError, match="credential-free") as error:
        load_benchmark_context(tmp_path, candidate(), AS_OF)
    assert "test-placeholder" not in str(error.value)


def test_source_url_public_query_and_fragment_anchor_are_preserved(tmp_path):
    url = "https://data.krx.co.kr/source?index=industry#constituents"
    write_rows(tmp_path, "benchmark_mappings.jsonl", [mapping(source_url=url)])
    write_rows(tmp_path, "benchmarks.jsonl", [bar()])
    result = load_benchmark_context(tmp_path, candidate(), AS_OF)
    assert result["market"]["mapping_source_url"] == url


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"schema_version": 2}, {"kind": "theme"}, {"series": "kospi"},
    {"index_name": " "}, {"source_id": ""}, {"version": ""}, {"source_url": "http://data.krx.co.kr/source"},
    {"source_url": "https://username:password@example.test/source"}, {"source_url": "not-a-url"},
    {"effective_from": "2026-09-06", "effective_to": "2026-09-05"}, {"effective_from": "2026-02-30"},
    {"effective_from": None}, {"effective_to": []},
    {"available_at": "2026-09-04T09:00:00"}, {"available_at": None}, {"available_at": 20260904},
    {"index_name": SECTOR},
])
def test_malformed_matching_mapping_fails_clearly(tmp_path, changes):
    write_rows(tmp_path, "benchmark_mappings.jsonl", [mapping(**changes)])
    with pytest.raises(ValueError):
        load_benchmark_context(tmp_path, candidate(), AS_OF)


@pytest.mark.parametrize("field", ["available_at", "effective_from", "effective_to"])
def test_missing_required_mapping_time_is_a_validation_error(tmp_path, field):
    row = mapping()
    del row[field]
    write_rows(tmp_path, "benchmark_mappings.jsonl", [row])
    with pytest.raises(ValueError):
        load_benchmark_context(tmp_path, candidate(), AS_OF)


@pytest.mark.parametrize("changes", [{"source": None}, {"source_url": "https://example.test/prices"},
                                      {"price_basis": "adjusted"}])
def test_explicit_price_market_requires_verified_raw_krx_provenance(tmp_path, changes):
    stock = candidate("KOSPI")
    stock["price_history"][0].update(changes)
    with pytest.raises(ValueError, match="provenance|verified"):
        load_benchmark_context(tmp_path, stock, AS_OF)


def test_future_bad_price_market_bar_is_not_used_or_validated(tmp_path):
    stock = candidate("KOSPI")
    stock["price_history"].append({"available_at": (AS_OF + timedelta(days=1)).isoformat(),
                                   "market": "KOSDAQ", "source": "unknown"})
    write_rows(tmp_path, "benchmarks.jsonl", [bar()])
    result = load_benchmark_context(tmp_path, stock, AS_OF)
    assert result["market"]["series"] == "KOSPI"
    assert result["market"]["status"] == "ready"


def test_auto_mapping_interval_uses_kst_min_max_not_input_order_or_utc_day(tmp_path):
    stock = candidate("KOSPI")
    stock["price_history"][0]["available_at"] = "2026-09-04T00:30:00+09:00"
    stock["price_history"][1]["available_at"] = "2026-09-03T00:30:00+09:00"
    before = deepcopy(stock)
    write_rows(tmp_path, "benchmarks.jsonl", [bar()])
    result = load_benchmark_context(tmp_path, stock, AS_OF)
    assert result["market"]["effective_from"] == "2026-09-03"
    assert result["market"]["effective_to"] == "2026-09-04"
    assert stock == before


def test_context_requires_aware_analysis_timestamp(tmp_path):
    with pytest.raises(ValueError, match="timezone"):
        load_benchmark_context(tmp_path, candidate(), AS_OF.replace(tzinfo=None))


@pytest.mark.parametrize("changes", [
    {"close": 2100.0}, {"close": "2000.0"}, {"version": "tampered"}, {"source_id": "tampered"},
    {"source_url": "https://example.test/not-krx"}, {"schema_version": True},
    {"source_url": "https://data-dbg.krx.co.kr/svc/apis/idx/kospi_dd_trd?basDd=20260903"},
    {"extra_unknown_field": "unexpected"},
])
def test_known_matching_benchmark_must_match_collector_archive_contract(tmp_path, changes):
    write_rows(tmp_path, "benchmarks.jsonl", [bar(**changes)])
    with pytest.raises(ValueError):
        load_benchmark_context(tmp_path, candidate("KOSPI"), AS_OF)


def test_future_tampered_benchmark_is_excluded_before_archive_validation(tmp_path):
    known = bar()
    future = bar(available_at=(AS_OF + timedelta(seconds=1)).isoformat(), close="invalid-future-close",
                 version="tampered", source_url="invalid-future-source")
    write_rows(tmp_path, "benchmarks.jsonl", [future, known])
    result = load_benchmark_context(tmp_path, candidate("KOSPI"), AS_OF)
    assert result["market"]["bars"] == [known]


def test_future_only_benchmark_is_not_a_ready_or_zero_filled_history(tmp_path):
    write_rows(tmp_path, "benchmarks.jsonl", [bar(available_at=(AS_OF + timedelta(seconds=1)).isoformat())])
    result = load_benchmark_context(tmp_path, candidate("KOSPI"), AS_OF)
    assert result["market"]["status"] == "unavailable"
    assert not result["market"].get("bars")
