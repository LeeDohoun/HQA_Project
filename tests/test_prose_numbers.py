import pytest

from src.runner.prose_numbers import round_percentages


@pytest.mark.parametrize("text, expected", [
    ("시가배당률은 1.56%입니다.", "시가배당률은 1.6%입니다."),
    ("코스피 대비 -13.25%p 부진했습니다.", "코스피 대비 -13.3%p 부진했습니다."),
    ("수익률 -0.42%로 손익이 작습니다.", "수익률 -0.4%로 손익이 작습니다."),
    ("상승률 2.345 ％", "상승률 2.3 ％"),
    ("0.75퍼센트 올랐습니다", "0.8퍼센트 올랐습니다"),
    ("9.96% 하락", "10.0% 하락"),
    ("1,234.56% 증가", "1,234.6% 증가"),
    ("5일 -7.25%, 20일 -9.04%p", "5일 -7.3%, 20일 -9.0%p"),
])
def test_percentages_are_rounded_to_one_decimal_half_away_from_zero(text, expected):
    assert round_percentages(text) == expected


@pytest.mark.parametrize("text", [
    "PER 31.69배, PBR 3.25배",  # multiples keep their decimals
    "현재가 208,000원, MA20 214,800.55원",  # prices are not percentages
    "영업이익률 13.1%, 부채비율 29.9%, 12%",  # already one decimal or whole
    "차이가 0.04%p뿐입니다.",  # rounding would turn a real change into 0.0
    "",
])
def test_other_numbers_and_tiny_changes_stay_as_written(text):
    assert round_percentages(text) == text


def test_non_text_is_returned_unchanged():
    assert round_percentages(None) is None
