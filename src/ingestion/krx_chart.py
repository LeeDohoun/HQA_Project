from __future__ import annotations

import os
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import requests

from .types import MarketRecord

KST = timezone(timedelta(hours=9))


def _now() -> datetime:
    return datetime.now(timezone.utc)


class KrxChartCollector:
    """KRX Open API 일별매매정보 기반 OHLCV 수집기."""

    KOSPI_DAILY_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
    KOSDAQ_DAILY_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        self.api_key = (api_key or os.getenv("KRX_OPEN_API_KEY") or os.getenv("KRX_API_KEY") or "").strip()
        self.session = session or requests.Session()
        self._daily_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._cache_times: Dict[str, datetime] = {}

    def collect_recent_daily(self, stock_name: str, stock_code: str, days: int = 300) -> List[MarketRecord]:
        if type(days) is not int or days < 1:
            raise ValueError("days must be a positive integer")
        to_date = _now().astimezone(KST).date() - timedelta(days=1)
        # 거래일 기준 days개를 얻기 위해 캘린더 일수는 넉넉히 잡는다.
        from_date = to_date - timedelta(days=max(30, int(days * 1.8)))
        rows = self.collect_daily(stock_name, stock_code, from_date.strftime("%Y%m%d"), to_date.strftime("%Y%m%d"))
        return rows[-days:]

    def collect_daily(self, stock_name: str, stock_code: str, from_date: str, to_date: str) -> List[MarketRecord]:
        if not self.api_key:
            raise ValueError("KRX_OPEN_API_KEY or KRX_API_KEY is required for chart collection")

        if not re.fullmatch(r"\d{6}", stock_code):
            raise ValueError("KRX chart collection requires a six-digit stock code")
        if not all(isinstance(value, str) and re.fullmatch(r"\d{8}", value) for value in (from_date, to_date)):
            raise ValueError("KRX chart dates must be YYYYMMDD")
        start = datetime.strptime(from_date, "%Y%m%d").date()
        end = datetime.strptime(to_date, "%Y%m%d").date()
        if start > end or end >= _now().astimezone(KST).date():
            raise ValueError("KRX chart range must be ordered and exclude current and future KST dates")
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
        for market, url in (("KOSPI", self.KOSPI_DAILY_URL), ("KOSDAQ", self.KOSDAQ_DAILY_URL)):
            for row in self._fetch_market_rows(url, bas_dd):
                if str(row.get("ISU_CD", "")).strip() == stock_code:
                    return {**row, "_source_market": market, "_source_url": url}
        return {}

    def _fetch_market_rows(self, url: str, bas_dd: str) -> List[Dict[str, Any]]:
        if url not in {self.KOSPI_DAILY_URL, self.KOSDAQ_DAILY_URL}:
            raise ValueError("unsupported KRX chart endpoint")
        cache_key = f"{url}:{bas_dd}"
        if cache_key in self._daily_cache and _now() - self._cache_times[cache_key] < timedelta(minutes=15):
            return self._daily_cache[cache_key]

        try:
            response = self.session.get(url, params={"basDd": bas_dd}, headers={"AUTH_KEY": self.api_key},
                                        timeout=20, allow_redirects=False)
            if type(response.status_code) is not int or not 200 <= response.status_code < 300:
                raise requests.HTTPError("KRX chart response requires a successful HTTP status")
        except requests.RequestException as exc:
            # Provider messages and request URLs can echo authentication material.
            raise requests.RequestException(f"KRX chart request failed ({type(exc).__name__})") from None
        try:
            payload = response.json()
        except ValueError:
            raise ValueError("KRX chart response is not valid JSON") from None
        if (not isinstance(payload, dict) or set(payload) != {"OutBlock_1"}
                or not isinstance(payload["OutBlock_1"], list)):
            raise ValueError("KRX chart response requires only an OutBlock_1 array")
        observed = _now()
        daily = {}
        fields = {"ISU_CD", "BAS_DD", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC", "ACC_TRDVOL"}
        for row in payload["OutBlock_1"]:
            if not isinstance(row, dict) or not fields <= row.keys():
                raise ValueError("KRX chart row is missing required fields")
            if row["BAS_DD"] != bas_dd or not isinstance(row["ISU_CD"], str) or not row["ISU_CD"].strip():
                raise ValueError("KRX chart row has an invalid stock identity or requested date")
            if any(self.api_key in str(row.get(field, "")) for field in ("ISU_CD", "ISU_NM")):
                raise ValueError("KRX chart identity contains request credentials")
            for field in fields - {"ISU_CD", "BAS_DD"}:
                self._clean_number(row[field])
            if row["ISU_CD"] in daily and daily[row["ISU_CD"]] != row:
                raise ValueError("conflicting duplicate KRX chart stock/date")
            daily[row["ISU_CD"]] = row
        rows = [{**row, "_collected_at": observed.isoformat()} for row in daily.values()]
        # Empty responses may be unpublished sessions; never retain them as a cached holiday.
        self._daily_cache.pop(cache_key, None)
        self._cache_times.pop(cache_key, None)
        if rows:
            self._daily_cache[cache_key] = rows
            self._cache_times[cache_key] = observed
        return rows

    def _to_market_record(self, stock_name: str, stock_code: str, row: Dict[str, Any]) -> MarketRecord:
        bas_dd = str(row.get("BAS_DD", "")).strip()
        day = datetime.strptime(bas_dd, "%Y%m%d").date()
        from src.runner.trading_calendar import CALENDAR_VERSION, SPECIAL_CLOSES, daily_session_close
        bar_at = daily_session_close(day.isoformat()).astimezone(KST)
        observed = datetime.fromisoformat(row.get("_collected_at") or _now().isoformat())
        if observed.tzinfo is None or observed < bar_at:
            raise ValueError("KRX chart observation must follow the completed market close and include timezone")
        record = MarketRecord(
            source_type="chart",
            stock_name=stock_name or str(row.get("ISU_NM", "")).strip(),
            stock_code=stock_code,
            timestamp=day.isoformat() + "T00:00:00",
            open=self._clean_number(row.get("TDD_OPNPRC")),
            high=self._clean_number(row.get("TDD_HGPRC")),
            low=self._clean_number(row.get("TDD_LWPRC")),
            close=self._clean_number(row.get("TDD_CLSPRC")),
            volume=self._clean_number(row.get("ACC_TRDVOL")),
            metadata={"source": "krx", "raw_date": bas_dd, "price_basis": "unadjusted",
                      "calendar_version": CALENDAR_VERSION,
                      **({"market": row["_source_market"], "source_url": row["_source_url"]}
                         if "_source_market" in row else {})},
        )
        if day.isoformat() in SPECIAL_CLOSES:
            record.metadata["calendar_notice"] = SPECIAL_CLOSES[day.isoformat()]
        content = {"stock_code": record.stock_code, "timestamp": record.timestamp,
                   **{field: getattr(record, field) for field in ("open", "high", "low", "close", "volume")},
                   "metadata": record.metadata}
        version = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        record.metadata.update(trade_date=day.isoformat(), bar_at=bar_at.isoformat(),
                               collected_at=observed.isoformat(), available_at=observed.isoformat(),
                               version=version, source_id="krx-chart:" + version)
        return record

    @staticmethod
    def _clean_number(value: Any) -> str:
        text = str(value).strip()
        if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", text):
            raise ValueError("KRX chart prices and volume must be nonnegative finite numbers")
        cleaned = text.replace(",", "")
        if not math.isfinite(float(cleaned)):
            raise ValueError("KRX chart prices and volume must be finite")
        return cleaned
