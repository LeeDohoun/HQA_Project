from dataclasses import asdict
from datetime import datetime, timezone
import json

import pytest

from src.ingestion.services import IngestionService
from src.ingestion.types import FinancialSnapshot
from src.runner.financial_snapshot import load_financial_snapshot

UTC = timezone.utc


def snapshot(**metadata):
    return FinancialSnapshot(
        source_type="financials", stock_name="Example", stock_code="005930", corp_code="00126380",
        fiscal_year="2025", report_code="11011", report_name="annual",
        revenue=1000, operating_profit=100, net_income=50, assets=2000,
        liabilities=800, equity=1200, current_assets=400, current_liabilities=200,
        as_of="2025.12.31", metadata={"source": "dart", "rcept_no": "20260301000001", "fs_div": "CFS",
            "collected_at": "2026-03-02T00:00:00+00:00", "currency_verified": True,
            "amount_unit": "KRW", "version": "first", **metadata},
    )


def persist(root, *rows):
    path = root / "raw" / "financials" / "test.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(asdict(row)) + "\n" for row in rows), encoding="utf-8")


def test_fiscal_date_does_not_make_report_available_before_collection(tmp_path):
    persist(tmp_path, snapshot())
    result = load_financial_snapshot(tmp_path, "005930", datetime(2026, 3, 1, tzinfo=UTC))
    assert result["status"] == "blocked"
    result = load_financial_snapshot(tmp_path, "005930", datetime(2026, 3, 3, tzinfo=UTC))
    assert result["status"] == "ready"
    assert result["published_at"] is None
    assert result["ratios"]["operating_margin"] == 10
    assert result["ratios"]["current_ratio"] == 200


def test_corrected_reports_are_selected_only_after_observation(tmp_path):
    first = snapshot()
    revised = snapshot(version="second", collected_at="2026-03-05T00:00:00+00:00", rcept_no="20260305000002")
    revised.operating_profit = 80
    persist(tmp_path, first, revised)
    before = load_financial_snapshot(tmp_path, "005930", datetime(2026, 3, 4, tzinfo=UTC))
    after = load_financial_snapshot(tmp_path, "005930", datetime(2026, 3, 6, tzinfo=UTC))
    assert before["ratios"]["operating_margin"] == 10
    assert after["ratios"]["operating_margin"] == 8
    assert before["version"] != after["version"]


def test_unchanged_recollection_preserves_first_known_time(tmp_path):
    persist(tmp_path, snapshot(), snapshot(collected_at="2026-03-05T00:00:00+00:00"))
    result = load_financial_snapshot(tmp_path, "005930", datetime(2026, 3, 6, tzinfo=UTC))
    assert result["available_at"] == "2026-03-02T00:00:00+00:00"


@pytest.mark.parametrize("with_episode_ids", [False, True])
def test_return_to_earlier_value_is_a_new_observation_not_permanent_duplicate(tmp_path, with_episode_ids):
    first = snapshot(collected_at="2026-03-02T00:00:00+00:00")
    changed = snapshot(collected_at="2026-03-03T00:00:00+00:00")
    changed.operating_profit = 80
    returned = snapshot(collected_at="2026-03-04T00:00:00+00:00")
    repeated = snapshot(collected_at="2026-03-05T00:00:00+00:00")
    if with_episode_ids:
        for number, value in enumerate((first, changed, returned, repeated)):
            value.metadata["version_id"] = f"episode-{number}"
    persist(tmp_path, returned, changed, first, repeated)
    before = load_financial_snapshot(tmp_path, "005930", datetime(2026, 3, 3, 12, tzinfo=UTC))
    after = load_financial_snapshot(tmp_path, "005930", datetime(2026, 3, 6, tzinfo=UTC))
    original = load_financial_snapshot(tmp_path, "005930", datetime(2026, 3, 2, 12, tzinfo=UTC))
    assert before["values"]["operating_profit"] == 80
    assert after["values"]["operating_profit"] == 100
    assert after["available_at"] == "2026-03-04T00:00:00+00:00"
    assert original["version"] == after["version"]
    assert original["source_id"] != after["source_id"]


def test_conflicting_same_time_financial_observations_fail_closed(tmp_path):
    changed = snapshot()
    changed.operating_profit = 80
    persist(tmp_path, snapshot(), changed)
    result = load_financial_snapshot(tmp_path, "005930", datetime(2026, 3, 6, tzinfo=UTC))
    assert result["status"] == "blocked"
    assert "conflicting_financial_observations_at_same_time" in result["gaps"]


def test_consolidated_and_standalone_facts_never_mix(tmp_path):
    standalone = snapshot(fs_div="OFS", collected_at="2026-03-03T00:00:00+00:00")
    standalone.revenue = 600
    persist(tmp_path, snapshot(), standalone)
    result = load_financial_snapshot(tmp_path, "005930", datetime(2026, 3, 6, tzinfo=UTC))
    assert result["fs_div"] == "CFS"
    assert result["values"]["revenue"] == 1000


@pytest.mark.parametrize("unit,multiplier", [("KRW", 1), ("\ucc9c\uc6d0", 1000), ("\ub9cc\uc6d0", 10000),
                                            ("\ubc31\ub9cc\uc6d0", 1000000), ("\uc5b5\uc6d0", 100000000)])
def test_korean_amount_units_are_normalized_in_code(tmp_path, unit, multiplier):
    persist(tmp_path, snapshot(amount_unit=unit))
    result = load_financial_snapshot(tmp_path, "005930", datetime(2026, 3, 6, tzinfo=UTC))
    assert result["values"]["revenue"] == 1000 * multiplier
    assert result["ratios"]["operating_margin"] == 10


@pytest.mark.parametrize("metadata", [{"collected_at": None}, {"currency_verified": False},
                                      {"fs_div": None}, {"amount_unit": "unknown"}])
def test_missing_financial_provenance_blocks_new_analysis(tmp_path, metadata):
    persist(tmp_path, snapshot(**metadata))
    result = load_financial_snapshot(tmp_path, "005930", datetime(2026, 3, 6, tzinfo=UTC))
    assert result["status"] == "blocked"
    assert result["gaps"]


def test_ingestion_preserves_corrections_and_deduplicates_unchanged_versions(tmp_path):
    service = IngestionService()
    first = snapshot()
    revised = snapshot(version="second", collected_at="2026-03-05T00:00:00+00:00")
    revised.operating_profit = 80
    for row in (first, revised, revised):
        service._save_raw_financial_snapshots([row], str(tmp_path / "raw"), "test")
        service._save_market_financial_snapshots([row], str(tmp_path / "raw"), "test")
    for path in (tmp_path / "raw/financials/test.jsonl", tmp_path / "market_data/test/financials.jsonl"):
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(rows) == 2
        assert [row["operating_profit"] for row in rows] == [100, 80]


def test_legacy_finance_reader_uses_latest_revision_without_duplicate_years(tmp_path):
    from src.tools.finance_tool import QuantitativeAnalyzer

    first = snapshot()
    revised = snapshot(version="second", collected_at="2026-03-05T00:00:00+00:00")
    revised.operating_profit = 80
    previous = snapshot(version="previous")
    previous.fiscal_year = "2024"
    persist(tmp_path, first, revised, previous)
    result = QuantitativeAnalyzer(data_dir=str(tmp_path))._load_financial_snapshot("005930")
    assert result["operating_profit"] == 80
    assert result["financial_history_years"] == ["2025", "2024"]
