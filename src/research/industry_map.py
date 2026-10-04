"""Deterministic investment groups from current KSIC 10th revision codes.

This mapping is a research grouping, not an official industry index taxonomy.
Missing reference archives fail explicitly; unknown stocks/codes remain 미분류.
Current OpenDART classifications do not reconstruct historical industry changes.
"""
from __future__ import annotations

from pathlib import Path
import re

import pandas as pd

from src.ingestion.storage import read_rows
from src.utils.stock_codes import is_stock_code


DEFAULT_COMPANIES_PATH = Path(__file__).resolve().parents[2] / "data/reference/dart_company/companies.jsonl"
UNCLASSIFIED = "미분류"

# Source: 통계청, 한국표준산업분류 제10차 개정 (2017), division/subdivision
# names (통계분류포털 https://kssc.kostat.go.kr). These are numeric KSIC
# prefixes, with the C manufacturing section letter omitted by OpenDART.
# 26 = 전자 부품, 컴퓨터, 영상, 음향 및 통신장비 제조업:
# C261 = 반도체; C262 = 전자 부품 (C2621 = 표시장치); C263 = 컴퓨터 및
# 주변장치; C264 = 통신 및 방송 장비; C265/C266 = 영상·음향/기록매체.
# Investment judgments: computers/audio join display/electronic components;
# all 28 전기장비 joins batteries (cells/accumulators are under 282, not 20);
# 20423 화장품 overrides 20 화학 물질 및 화학제품; 271 의료용 기기 overrides
# 27 의료, 정밀, 광학 기기 및 시계. Non-ship 31 기타 운송장비 joins machinery.
# 582 소프트웨어 overrides 58 출판업, 6391 뉴스 제공업 overrides 63 정보서비스업,
# and 64992 지주회사 overrides 64 금융업. 37~39 environmental services join
# utilities. 기타 covers the remaining valid divisions, rather than unknown codes.
KSIC_PREFIXES = {
    "261": "반도체",
    "262": "디스플레이·전자부품", "263": "디스플레이·전자부품",
    "265": "디스플레이·전자부품", "266": "디스플레이·전자부품",
    "264": "통신장비",
    "28": "2차전지·전기장비",
    "30": "자동차·부품",
    "311": "조선",
    "27": "기계·장비", "29": "기계·장비", "31": "기계·장비", "34": "기계·장비",
    "06": "철강·금속", "24": "철강·금속", "25": "철강·금속",
    "20": "화학", "22": "화학",
    "05": "정유·에너지", "19": "정유·에너지",
    "21": "제약·바이오",
    "271": "의료기기",
    "41": "건설", "42": "건설",
    "49": "운송", "50": "운송", "51": "운송", "52": "운송",
    "45": "유통·소매", "46": "유통·소매", "47": "유통·소매",
    "10": "음식료·담배", "11": "음식료·담배", "12": "음식료·담배",
    "13": "섬유·의복·화장품", "14": "섬유·의복·화장품", "15": "섬유·의복·화장품",
    "20423": "섬유·의복·화장품",
    "58": "미디어·엔터", "59": "미디어·엔터", "60": "미디어·엔터",
    "90": "미디어·엔터", "6391": "미디어·엔터",
    "582": "게임·소프트웨어", "62": "게임·소프트웨어", "63": "게임·소프트웨어",
    "61": "통신서비스",
    "64": "금융(은행·증권·보험)", "65": "금융(은행·증권·보험)", "66": "금융(은행·증권·보험)",
    "64992": "지주회사",
    "35": "유틸리티", "36": "유틸리티", "37": "유틸리티", "38": "유틸리티", "39": "유틸리티",
    **dict.fromkeys(("01", "02", "03", "07", "08", "16", "17", "18", "23", "32", "33",
                     "55", "56", "68", "70", "71", "72", "73", "74", "75", "76", "84",
                     "85", "86", "87", "91", "94", "95", "96", "97", "98", "99"), "기타"),
}
# Division 26 alone cannot distinguish the investment subgroups.
_PREFIX_ORDER = tuple(sorted(KSIC_PREFIXES, key=lambda prefix: (-len(prefix), prefix)))


def industry_from_ksic(induty_code: object) -> str:
    if not isinstance(induty_code, str):
        return UNCLASSIFIED
    code = induty_code.strip()
    if not re.fullmatch(r"[0-9]{2,5}", code):
        return UNCLASSIFIED
    return next((KSIC_PREFIXES[prefix] for prefix in _PREFIX_ORDER if code.startswith(prefix)), UNCLASSIFIED)


def load_industry_map(companies_path: str | Path = DEFAULT_COMPANIES_PATH) -> dict[str, str]:
    path = Path(companies_path)
    if not path.is_file():
        raise FileNotFoundError("DART company reference archive is required")
    mapping = {}
    for row in read_rows(path):
        industry = industry_from_ksic(row.get("induty_code"))
        # Explicit aliases retain delisted companies without inventing a current
        # OpenDART stock_code. The collector validates the two codes agree if both exist.
        for stock in {row.get("stock_code", ""), row.get("reference_stock_code", "")}:
            if stock == "":
                continue
            if not is_stock_code(stock):
                raise ValueError("DART company reference contains an invalid stock code")
            if stock in mapping:
                raise ValueError("DART company reference contains duplicate stock identities")
            mapping[stock] = industry
    return mapping


def industry_of(stock_code: str, *, companies_path: str | Path = DEFAULT_COMPANIES_PATH) -> str:
    return load_industry_map(companies_path).get(stock_code, UNCLASSIFIED)


def attach_industry(prices_frame: pd.DataFrame, *, companies_path: str | Path = DEFAULT_COMPANIES_PATH) -> pd.DataFrame:
    """Preserve the price index; count unmapped distinct stocks and price rows in attrs."""
    mapping = load_industry_map(companies_path)
    if "stock_code" in prices_frame.index.names:
        codes = prices_frame.index.get_level_values("stock_code")
    elif "stock_code" in prices_frame:
        codes = pd.Index(prices_frame["stock_code"])
    else:
        raise ValueError("prices require stock_code in the index or columns")
    frame = prices_frame.copy()
    frame["industry"] = [mapping.get(code, UNCLASSIFIED) for code in codes]
    unknown = frame["industry"].eq(UNCLASSIFIED).to_numpy()
    frame.attrs.update(unmapped_stock_count=len(set(codes[unknown])),
                       unmapped_row_count=int(unknown.sum()), unmapped_stock_codes=sorted(set(codes[unknown])))
    return frame
