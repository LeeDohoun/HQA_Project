from __future__ import annotations

import json

import pytest

from src.ingestion.theme_targets import ThemeTargetStore, load_corp_code_map, make_theme_key
from src.ingestion.types import StockTarget


def test_make_theme_key_normalizes_text():
    assert make_theme_key("  2차 전지 ") == "2차_전지"
    assert make_theme_key("AI/로봇") == "ai_로봇"


def test_theme_target_store_save_and_load(tmp_path):
    store = ThemeTargetStore(data_dir=str(tmp_path))
    saved = store.save_targets(
        theme_key="2차전지",
        theme_name="2차전지",
        targets=[
            StockTarget(stock_name="LG에너지솔루션", stock_code="373220", corp_code="01515323"),
            StockTarget(stock_name="삼성SDI", stock_code="006400", corp_code="00126362"),
        ],
    )

    assert len(saved) == 2
    loaded = store.load_targets("2차전지")
    assert [target.stock_code for target in loaded] == ["373220", "006400"]

    meta_path = store.get_meta_path("2차전지")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["theme_key"] == "2차전지"
    assert meta["target_count"] == 2


def test_theme_target_store_append_dedupes_by_stock_code(tmp_path):
    store = ThemeTargetStore(data_dir=str(tmp_path))
    store.save_targets(
        theme_key="반도체",
        targets=[
            StockTarget(stock_name="삼성전자", stock_code="005930", corp_code="00126380"),
        ],
    )
    saved = store.save_targets(
        theme_key="반도체",
        mode="append",
        targets=[
            StockTarget(stock_name="삼성전자", stock_code="005930", corp_code="00126380"),
            StockTarget(stock_name="SK하이닉스", stock_code="000660", corp_code="00164779"),
        ],
    )

    assert [target.stock_code for target in saved] == ["005930", "000660"]


@pytest.mark.parametrize("code", ["0015G0", "005930"])
def test_corporate_csv_and_saved_targets_preserve_krx_short_codes(tmp_path, code):
    path = tmp_path / "corp_codes.csv"
    path.write_text(f"stock_code,corp_code\n{code},00126380\n", encoding="utf-8-sig")
    assert load_corp_code_map(str(path)) == {code: "00126380"}
    store = ThemeTargetStore(data_dir=str(tmp_path))
    targets = [StockTarget("Stock", code, "00126380")]
    assert store.save_targets("theme", targets) == targets
    assert store.load_targets("theme") == targets


@pytest.mark.parametrize("code", ["0015g0", "15G0", "0015G0X", "0015-0"])
def test_corporate_csv_and_targets_reject_invalid_stock_codes(tmp_path, code):
    path = tmp_path / "corp_codes.csv"
    path.write_text(f"stock_code,corp_code\n{code},00126380\n", encoding="utf-8-sig")
    with pytest.raises(ValueError, match="invalid stock"):
        load_corp_code_map(str(path))
    store = ThemeTargetStore(data_dir=str(tmp_path))
    with pytest.raises(ValueError, match="target requires"):
        store.save_targets("theme", [StockTarget("Stock", code, "00126380")])
