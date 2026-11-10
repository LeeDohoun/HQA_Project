from __future__ import annotations

# File role:
# - Resolve a Naver theme keyword into a set of stock targets, and list every theme for the catalog.
# - Reads Naver's mobile JSON API. Since 2026-10 the finance.naver.com theme pages redirect to
#   stock.naver.com, a client-rendered site whose HTML holds no theme data.

from dataclasses import dataclass
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from .base import BaseCollector, http_status_suffix
from .base import USER_AGENT  # noqa: F401  The 2026-10-10 universe selection script imports it from here.


def _warn_alphanumeric(theme_name: str, stock_code: str) -> None:
    # KRX alphanumeric short codes (recent listings) are not supported by the pipeline yet.
    print(f"[WARN][THEME] skipped alphanumeric KRX code {stock_code} in theme {theme_name} (not supported yet)")


def _largest_market_value_first(row: Dict[str, Any]) -> Tuple[int, str]:
    # Naver lists members by the day's change rate; market value orders them without looking at returns.
    raw = str(row.get("marketValueRaw") or "").strip()
    return (-int(raw) if raw.isdigit() else 1, row["itemCode"])


@dataclass
class ThemeStock:
    theme_name: str
    stock_name: str
    stock_code: str


@dataclass
class ThemeTargets:
    theme_name: str
    detail_url: str
    stocks: List[ThemeStock]


class NaverThemeApiError(RuntimeError):
    """The theme API failed or answered in an unexpected shape; never reported as "no themes"."""


class NaverThemeStockCollector(BaseCollector):
    API_URL = "https://m.stock.naver.com/api/stocks/theme"
    PAGE_SIZE = 100  # The API refuses larger pages with HTTP 400.
    MIN_INTERVAL_SECONDS = 0.4
    MAX_MEMBER_PAGES = 10  # 1,000 members; the largest theme had 148 on 2026-10-11.
    # Both listings follow the live change rate, so rows can move between pages while the market is
    # open. A read that misses rows is repeated before it fails.
    READ_ATTEMPTS = 3

    def __init__(self, *, session: Optional[requests.Session] = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 **kwargs: Any):
        super().__init__(**kwargs)
        if session is not None:
            self.session = session
        self._sleep = sleep
        self._clock = clock
        self._last_request: Optional[float] = None

    def collect(self, theme_keyword: str, max_stocks: int = 30, max_pages: int = 10) -> List[ThemeStock]:
        """Members of the themes whose name contains the keyword, at most max_stocks in all.

        A theme named exactly like the keyword comes first, then the other matches by theme number.
        Within a theme, larger market values come first.
        """
        if max_stocks < 1:
            raise ValueError("max_stocks must be at least 1")
        keyword = theme_keyword.strip().lower()
        matches = [group for group in self.list_themes(max_pages=max_pages)
                   if keyword in group["name"].strip().lower()]
        matches.sort(key=lambda group: (group["name"].strip().lower() != keyword, group["no"]))
        print(f"[DEBUG][THEME] matched themes: {[group['name'].strip() for group in matches]}")

        stocks: List[ThemeStock] = []
        seen_codes = set()
        for group in matches:
            for stock in self._theme_stocks(group):
                if stock.stock_code in seen_codes:
                    continue
                seen_codes.add(stock.stock_code)
                stocks.append(stock)
                if len(stocks) >= max_stocks:
                    return stocks
        return stocks

    def collect_all_themes(self, max_pages: int = 10, max_stocks_per_theme: int = 200) -> List[ThemeTargets]:
        """Every theme by theme number, each with its members by market value."""
        if max_stocks_per_theme < 1:
            raise ValueError("max_stocks_per_theme must be at least 1")
        groups = sorted(self.list_themes(max_pages=max_pages), key=lambda group: group["no"])
        print(f"[DEBUG][THEME ALL] theme count={len(groups)}")
        return [
            ThemeTargets(
                theme_name=group["name"].strip(),
                detail_url=self.theme_url(group["no"]),
                stocks=self._theme_stocks(group)[:max_stocks_per_theme],
            )
            for group in groups
        ]

    def list_themes(self, max_pages: int = 10) -> List[Dict[str, Any]]:
        """Naver's theme rows ({"no", "name", "totalCount", ...}) from up to max_pages pages."""
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        groups, total = self._read_listing(None, max_pages)
        if not groups:
            raise NaverThemeApiError("Naver theme API listed no themes")
        if len(groups) < total:
            print(f"[WARN][THEME] max_pages={max_pages} read {len(groups)} of {total} themes, "
                  "taken in Naver's order (the day's change rate)")
        return groups

    def theme_members(self, theme_no: int) -> List[Dict[str, Any]]:
        """Every member row of one theme: itemCode, stockName, stockEndType, tradeStopType, marketValueRaw, ..."""
        rows, total = self._read_listing(theme_no, self.MAX_MEMBER_PAGES)
        if len(rows) < total:
            raise NaverThemeApiError(f"Naver theme {theme_no} lists {total} members, "
                                     f"more than {self.MAX_MEMBER_PAGES} pages")
        return rows

    @classmethod
    def theme_url(cls, theme_no: int) -> str:
        return f"{cls.API_URL}/{theme_no}"

    def _theme_stocks(self, group: Dict[str, Any]) -> List[ThemeStock]:
        theme_name = group["name"].strip()
        stocks: List[ThemeStock] = []
        for row in sorted(self.theme_members(group["no"]), key=_largest_market_value_first):
            stock_code = row["itemCode"]
            if not stock_code.isdigit():
                _warn_alphanumeric(theme_name, stock_code)
                continue
            stocks.append(ThemeStock(theme_name=theme_name, stock_name=row["stockName"].strip(),
                                     stock_code=stock_code))
        print(f"[DEBUG][THEME DETAIL] theme={theme_name} stock count={len(stocks)}")
        return stocks

    def _read_listing(self, theme_no: Optional[int], max_pages: int) -> Tuple[List[Dict[str, Any]], int]:
        """Rows of the theme list (theme_no None) or of one theme, and the total Naver reports.

        Reading stops at the last page the total implies; the API answers 404 past it. A read cut
        short by max_pages is returned as it is, and the caller decides what that means.
        """
        key, id_field, label = (("groups", "no", "theme list") if theme_no is None
                                else ("stocks", "itemCode", f"theme {theme_no}"))
        rows: Dict[Any, Dict[str, Any]] = {}
        total = 0
        for attempt in range(1, self.READ_ATTEMPTS + 1):
            rows = {}
            for page in range(1, max_pages + 1):
                items, total = self._read_page(theme_no, page, key, label)
                for item in items:
                    rows.setdefault(item[id_field], item)
                if not items or page * self.PAGE_SIZE >= total:
                    break
            else:
                return list(rows.values()), total
            if len(rows) >= total:
                return list(rows.values()), total
            print(f"[WARN][THEME] {label} read {len(rows)} of {total} rows; the pages follow the live "
                  f"change rate (attempt {attempt}/{self.READ_ATTEMPTS})")
        raise NaverThemeApiError(f"Naver {label} stayed incomplete after {self.READ_ATTEMPTS} reads "
                                 f"({len(rows)} of {total} rows)")

    def _read_page(self, theme_no: Optional[int], page: int, key: str, label: str
                   ) -> Tuple[List[Dict[str, Any]], int]:
        url = self.API_URL if theme_no is None else self.theme_url(theme_no)
        data = self._get_json(url, page, label)
        items, total = data.get(key), data.get("totalCount")
        if not isinstance(items, list) or type(total) is not int or total < 0:
            raise NaverThemeApiError(f"Naver {label} page {page} has no '{key}' list or totalCount")
        if theme_no is not None:
            # An unknown theme number answers 200 with no members and groupInfo.no 0.
            info = data.get("groupInfo")
            answered = info.get("no") if isinstance(info, dict) else None
            if answered != theme_no:
                raise NaverThemeApiError(f"Naver answered theme {answered} for theme {theme_no}; "
                                         "the theme may have been removed")
        check = self._check_group if theme_no is None else self._check_stock
        for item in items:
            check(item, label)
        return items, total

    def _get_json(self, url: str, page: int, label: str) -> Dict[str, Any]:
        self._pace()
        try:
            response = self.get_with_retry(url, params={"page": page, "pageSize": self.PAGE_SIZE},
                                           headers={"Accept": "application/json"}, log_prefix="THEME")
        except requests.RequestException as exc:
            raise NaverThemeApiError(f"Naver {label} page {page} request failed{http_status_suffix(exc)}") from exc
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise NaverThemeApiError(f"Naver {label} page {page} did not answer with a JSON object")
        return data

    def _pace(self) -> None:
        if self._last_request is not None:
            wait = self.MIN_INTERVAL_SECONDS - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    @staticmethod
    def _check_group(item: Any, label: str) -> None:
        if not (isinstance(item, dict) and type(item.get("no")) is int
                and isinstance(item.get("name"), str) and item["name"].strip()):
            raise NaverThemeApiError(f"Naver {label} has a theme without a number or a name")

    @staticmethod
    def _check_stock(item: Any, label: str) -> None:
        code = item.get("itemCode") if isinstance(item, dict) else None
        if not (isinstance(code, str) and re.fullmatch(r"[0-9A-Z]{6}", code)
                and isinstance(item.get("stockName"), str) and item["stockName"].strip()):
            raise NaverThemeApiError(f"Naver {label} has a member without a six-character code "
                                     f"or a name (itemCode={code!r})")
