from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import TypeAdapter, ValidationError

from src.ingestion import dart_poller, minute_bars
from src.tracing.paper_performance import StockCode
from src.utils.stock_codes import KRX_SHORT_CODE_PATTERN, KRX_SHORT_CODE_REGEX, is_stock_code


@pytest.mark.parametrize("code", ["0015G0", "005930", "0041B0", "0001A0", "00088K", "ABCDEF"])
def test_accepts_ascii_krx_short_codes(code):
    assert is_stock_code(code)
    assert KRX_SHORT_CODE_PATTERN.fullmatch(code)
    assert TypeAdapter(StockCode).validate_python(code) == code


@pytest.mark.parametrize("code", ["0015g0", "15G0", "0015G0X", "0015-0", "", None, 5930,
                                 " 005930", "005930\n", "００５９３０", "0015Ｇ0"])
def test_rejects_invalid_codes_without_normalizing_them(code):
    assert not is_stock_code(code)
    with pytest.raises(ValidationError):
        TypeAdapter(StockCode).validate_python(code)


def test_ingestion_public_patterns_alias_the_shared_definition():
    assert dart_poller.KRX_SHORT_CODE_PATTERN is KRX_SHORT_CODE_PATTERN
    assert minute_bars.KRX_SHORT_CODE_PATTERN is KRX_SHORT_CODE_PATTERN
    assert KRX_SHORT_CODE_PATTERN.pattern == KRX_SHORT_CODE_REGEX


def test_stock_code_import_stays_pure_and_preserves_lazy_package_exports():
    code = """
import sys
from src.utils.stock_codes import is_stock_code
assert is_stock_code('0015G0')
assert 'requests' not in sys.modules
assert 'src.utils.kis_auth' not in sys.modules
assert 'src.runner' not in sys.modules
from src.utils import StockMapper
from src.utils.stock_mapper import StockMapper as DirectStockMapper
assert StockMapper is DirectStockMapper
assert 'src.utils.kis_auth' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
