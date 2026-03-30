import importlib.util
import sys
import types
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]

_pandas_module = types.ModuleType("pandas")
_pandas_module.DataFrame = object
_pandas_module.Series = object
_pandas_module.concat = lambda *args, **kwargs: None
sys.modules.setdefault("pandas", _pandas_module)

_numpy_module = types.ModuleType("numpy")
sys.modules.setdefault("numpy", _numpy_module)

_price_loader_module = types.ModuleType("src.data_pipeline.price_loader")


class _DummyPriceLoader:
    pass


_price_loader_module.PriceLoader = _DummyPriceLoader
sys.modules.setdefault("src.data_pipeline.price_loader", _price_loader_module)


def _load_module(name: str, relative_path: str):
    module_path = _ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_charts_module = _load_module("charts_tools_for_test", "src/tools/charts_tools.py")
_web_module = _load_module("web_search_tool_for_test", "src/tools/web_search_tool.py")

TechnicalIndicators = _charts_module.TechnicalIndicators
SearchResult = _web_module.SearchResult


def test_search_result_supports_dict_style_access():
    result = SearchResult(
        title="삼성전자 실적",
        url="https://example.com/report",
        content="HBM 수요 증가",
        score=0.87,
        published_date="2026-03-30",
    )

    assert result.snippet == "HBM 수요 증가"
    assert result.get("title") == "삼성전자 실적"
    assert result.get("snippet") == "HBM 수요 증가"
    assert result.get("url") == "https://example.com/report"
    assert result.get("missing", "fallback") == "fallback"


def test_technical_indicator_dict_exposes_raw_keys_for_agents():
    indicators = TechnicalIndicators(
        stock_code="005930",
        stock_name="삼성전자",
        date="2026-03-30",
        current_price=70100.0,
        price_change=1.25,
        ma5=69000.0,
        ma20=68000.0,
        ma60=65000.0,
        ma120=62000.0,
        ma150=61000.0,
        above_ma150=True,
        golden_cross=False,
        death_cross=False,
        rsi_14=63.4,
        rsi_signal="중립",
        macd=122.5,
        macd_signal=110.2,
        macd_histogram=12.3,
        macd_trend="상승",
        bb_upper=72000.0,
        bb_middle=68000.0,
        bb_lower=64000.0,
        bb_position="밴드내",
        bb_width=11.8,
        stoch_k=70.0,
        stoch_d=65.0,
        stoch_signal="중립",
        atr_14=1800.0,
        atr_percent=2.57,
        volume=1234567,
        volume_ma20=1000000.0,
        volume_ratio=1.23,
    )

    data = indicators.to_dict()

    assert data["rsi"] == 63.4
    assert data["macd_histogram"] == 12.3
    assert data["bb_position"] == "밴드내"
    assert data["atr"] == 1800.0
    assert data["volume_ratio"] == 1.23
    assert data["현재가"] == "70,100원"
