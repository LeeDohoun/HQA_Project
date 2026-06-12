from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

import requests

from .types import MarketRecord


class KrxChartCollector:
    """KRX Open API 일별매매정보 기반 OHLCV 수집기."""

    KOSPI_DAILY_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
    KOSDAQ_DAILY_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        self.api_key = (api_key or os.getenv("KRX_OPEN_API_KEY") or os.getenv("KRX_API_KEY") or "").strip()
        self.session = session or requests.Session()
        self._daily_cache: Dict[str, List[Dict[str, Any]]] = {}

    def collect_recent_daily(self, stock_name: str, stock_code: str, days: int = 300) -> List[MarketRecord]:
        to_date = date.today()
        # 거래일 기준 days개를 얻기 위해 캘린더 일수는 넉넉히 잡는다.
        from_date = to_date - timedelta(days=max(30, int(days * 1.8)))
        rows = self.collect_daily(stock_name, stock_code, from_date.strftime("%Y%m%d"), to_date.strftime("%Y%m%d"))
        return rows[-days:]

    def collect_daily(self, stock_name: str, stock_code: str, from_date: str, to_date: str) -> List[MarketRecord]:
        if not self.api_key:
            return []

        start = datetime.strptime(from_date, "%Y%m%d").date()
        end = datetime.strptime(to_date, "%Y%m%d").date()
        records: List[MarketRecord] = []
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5:
                row = self._find_stock_row(stock_code, cursor.strftime("%Y%m%d"))
                if row:
                    records.append(self._to_market_record(stock_name, stock_code, row))
            cursor += timedelta(days=1)
        return records

    def _find_stock_row(self, stock_code: str, bas_dd: str) -> Dict[str, Any]:
        for url in (self.KOSPI_DAILY_URL, self.KOSDAQ_DAILY_URL):
            for row in self._fetch_market_rows(url, bas_dd):
                if str(row.get("ISU_CD", "")).strip() == stock_code:
                    return row
        return {}

    def _fetch_market_rows(self, url: str, bas_dd: str) -> List[Dict[str, Any]]:
        cache_key = f"{url}:{bas_dd}"
        if cache_key in self._daily_cache:
            return self._daily_cache[cache_key]

        response = self.session.get(
            url,
            params={"basDd": bas_dd},
            headers={"AUTH_KEY": self.api_key},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("OutBlock_1") or payload.get("output") or []
        self._daily_cache[cache_key] = rows if isinstance(rows, list) else []
        return self._daily_cache[cache_key]

    def _to_market_record(self, stock_name: str, stock_code: str, row: Dict[str, Any]) -> MarketRecord:
        bas_dd = str(row.get("BAS_DD", "")).strip()
        timestamp = f"{bas_dd[:4]}-{bas_dd[4:6]}-{bas_dd[6:8]}T00:00:00" if len(bas_dd) == 8 else ""
        return MarketRecord(
            source_type="chart",
            stock_name=stock_name or str(row.get("ISU_NM", "")).strip(),
            stock_code=stock_code,
            timestamp=timestamp,
            open=self._clean_number(row.get("TDD_OPNPRC")),
            high=self._clean_number(row.get("TDD_HGPRC")),
            low=self._clean_number(row.get("TDD_LWPRC")),
            close=self._clean_number(row.get("TDD_CLSPRC")),
            volume=self._clean_number(row.get("ACC_TRDVOL")),
            metadata={"source": "krx", "raw_date": bas_dd},
        )

    @staticmethod
    def _clean_number(value: Any) -> str:
        text = "" if value is None else str(value).strip()
        return text.replace(",", "")
