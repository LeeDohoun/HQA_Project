"""The quant's market valuation: KIS snapshots saved by the collector, loaded for analysis, and
recomputed at the completed close the chartist also sees."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.ingestion.services import IngestionService
from src.runner.shared_analysis import _market_valuation
from src.runner.valuation_snapshot import load_valuation_snapshot, valuation_snapshot_path

OBSERVED = datetime(2026, 11, 11, 14, 12, tzinfo=timezone.utc)  # 23:12 KST, after the close


def snapshot(**changes):
    row = {"schema_version": 1, "stock_code": "005930", "source": "kis",
           "source_url": "https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/"
                         "inquire-daily-itemchartprice",
           "tr_id": "FHKST03010100", "observed_at": OBSERVED.isoformat(), "quote": 208000.0, "eps": 6564.0,
           "per": 31.69, "pbr": 3.25, "bps": 64000.0, "listed_shares": 5846278608}
    row.update(changes)
    return row


def save(tmp_path, row):
    request = SimpleNamespace(raw_output_dir=str(tmp_path / "raw"),
                              target=SimpleNamespace(stock_code=row["stock_code"], stock_name="삼성전자"))
    IngestionService._save_market_valuation(request, row)


def test_a_saved_snapshot_is_used_only_after_it_was_observed_and_while_recent(tmp_path):
    save(tmp_path, snapshot())
    assert load_valuation_snapshot(tmp_path, "005930", OBSERVED + timedelta(hours=1)) == {**snapshot(), "status": "ready"}
    assert load_valuation_snapshot(tmp_path, "005930", OBSERVED - timedelta(minutes=1))["status"] == "unavailable"
    assert load_valuation_snapshot(tmp_path, "005930", OBSERVED + timedelta(days=11))["status"] == "unavailable"
    assert load_valuation_snapshot(tmp_path, "000660", OBSERVED + timedelta(hours=1))["status"] == "unavailable"


@pytest.mark.parametrize("change", [{"eps": "6564"}, {"listed_shares": 0}, {"listed_shares": 1.5}, {"bps": -1.0},
                                    {"stock_code": "000660"}, {"schema_version": 2}, {"observed_at": "2026-11-11T23:12:00"}])
def test_a_malformed_snapshot_is_unavailable(tmp_path, change):
    path = valuation_snapshot_path(tmp_path, "005930")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(snapshot(**change)), encoding="utf-8")
    assert load_valuation_snapshot(tmp_path, "005930", OBSERVED + timedelta(hours=1))["status"] == "unavailable"


def test_a_snapshot_that_cannot_be_written_never_fails_collection(tmp_path, capsys):
    (tmp_path / "market_context").write_text("a file where the folder should be")
    save(tmp_path, snapshot())
    assert "valuation snapshot not saved" in capsys.readouterr().out


def test_multiples_are_recomputed_at_the_completed_close():
    history = [{"trade_date": "2026-11-10", "close": 200000.0}, {"trade_date": "2026-11-11", "close": 205500.0}]
    valuation = _market_valuation({**snapshot(), "status": "ready"}, history)
    assert (valuation["close_date"], valuation["close"], valuation["per"], valuation["pbr"]) == \
        ("2026-11-11", 205500.0, 31.31, 3.21)
    assert valuation["market_cap_krw"] == round(205500.0 * 5846278608)
    assert valuation["source_id"].startswith("valuation:005930:") and "data_gaps" not in valuation


def test_undefined_multiples_are_marked_and_missing_snapshots_give_nothing():
    history = [{"trade_date": "2026-11-11", "close": 1000.0}]
    valuation = _market_valuation({**snapshot(eps=-120.0, pbr=0.0, bps=None), "status": "ready"}, history)
    assert valuation["per"] is None and valuation["pbr"] is None
    assert valuation["data_gaps"] == ["per_undefined_for_nonpositive_eps", "pbr_unavailable"]
    assert _market_valuation({"status": "unavailable"}, history) is None
    assert _market_valuation(None, history) is None
    assert _market_valuation({**snapshot(), "status": "ready"}, []) is None
