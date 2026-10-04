import sys
from types import SimpleNamespace
from zipfile import ZipFile

import pandas as pd
import pytest

from scripts.data.corp_codes import _parse_corp_codes
from src.utils.stock_mapper import StockInfo, StockMapper


@pytest.fixture
def mapper():
    # Bypass the singleton's data download; each test owns its in-memory catalog.
    result = object.__new__(StockMapper)
    result._stocks = {code: StockInfo(code, name, "KOSDAQ")
                      for code, name in [("0015G0", "그린광학"), ("005930", "삼성전자")]}
    result._name_to_code = {info.name: code for code, info in result._stocks.items()}
    return result


@pytest.mark.parametrize("code", ["0015G0", "005930"])
def test_mapper_looks_up_krx_short_codes_without_altering_them(mapper, code):
    assert mapper.get_info(code).code == code
    assert mapper.get_name(code) == mapper._stocks[code].name


@pytest.mark.parametrize("code", ["0015g0", "15G0", "0015G0X", "0015-0"])
def test_mapper_does_not_pad_or_normalize_invalid_alphanumeric_codes(mapper, code):
    assert mapper.get_info(code) is None
    assert mapper.get_name(code) is None


def test_mapper_keeps_numeric_short_input_and_name_lookup(mapper):
    assert mapper.get_info("5930").code == "005930"
    assert mapper.get_name("5930") == "삼성전자"
    assert mapper.get_info("그린광학").code == "0015G0"


@pytest.mark.parametrize("code,valid", [("0015G0", True), ("005930", True), ("0015g0", False),
                                      ("15G0", False), ("0015G0X", False), ("0015-0", False)])
def test_fdr_catalog_validates_codes_and_only_pads_short_numeric_input(monkeypatch, mapper, code, valid):
    mapper._stocks = {}
    mapper._name_to_code = {}
    frame = pd.DataFrame([{"Code": code, "Name": "Stock"}, {"Code": "5930", "Name": "삼성전자"}])
    monkeypatch.setitem(sys.modules, "FinanceDataReader", SimpleNamespace(StockListing=lambda _: frame))
    assert mapper._load_from_fdr()
    assert (code in mapper._stocks) is valid
    assert mapper.get_info("5930").code == "005930"


@pytest.mark.parametrize("code,valid", [("0015G0", True), ("005930", True), ("0015g0", False),
                                      ("15G0", False), ("0015G0X", False), ("0015-0", False)])
def test_python_master_catalog_validates_the_padded_short_code(monkeypatch, tmp_path, mapper, code, valid):
    mapper._stocks = {}
    mapper._name_to_code = {}
    line = f'{code:<9}KR7000000000그린광학' + " " * 238 + "\n"

    def local_master(_url, destination):
        with ZipFile(destination, "w") as archive:
            archive.writestr("market.mst", line.encode("cp949"))

    monkeypatch.setattr("urllib.request.urlretrieve", local_master)
    count = mapper._download_and_parse_kis_master("https://unused.test", str(tmp_path), "KOSDAQ", "market")
    assert count == int(valid)
    assert (code in mapper._stocks) is valid
    if valid:
        assert mapper.get_name(code) == "그린광학"


@pytest.mark.parametrize("code", ["0015G0", "005930"])
def test_corporate_xml_preserves_krx_short_codes(code):
    xml = f'<result><list><corp_code>00126380</corp_code><corp_name>Stock</corp_name><stock_code>{code}</stock_code></list></result>'
    assert _parse_corp_codes(xml.encode())[0]["stock_code"] == code


@pytest.mark.parametrize("code", ["0015g0", "15G0", "0015G0X", "0015-0"])
def test_corporate_xml_rejects_invalid_stock_codes(code):
    xml = f'<result><list><corp_code>00126380</corp_code><stock_code>{code}</stock_code></list></result>'
    with pytest.raises(ValueError, match="invalid corporate or stock code"):
        _parse_corp_codes(xml.encode())
