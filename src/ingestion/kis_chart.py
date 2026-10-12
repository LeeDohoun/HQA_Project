"""KIS daily OHLCV: the explicit stand-in for KRX Open API daily prices until KRX approves the key.

KRX remains the primary price source (``chart``). This collector backs the opt-in ``kis_chart``
source and never runs as a silent fallback. It asks KIS for original prices
(``FID_ORG_ADJ_PRC=1``), the unadjusted basis KRX publishes, so a history keeps one basis whichever
source observed a day. Measured on 2026-10-10: KIS original bars equal the traded prices, and KIS
adjusted bars equal the FinanceDataReader bars used for local tests. Rows carry the exact endpoint
and basis, and ``src.runner.analysis_data.price_features`` checks both.

The paper app key is shared with the backend's trading calls, so requests are paced and the token is
issued once per collector instance (one collection run).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List

import requests

from .types import MarketRecord

KST = timezone(timedelta(hours=9))


def _now() -> datetime:
    return datetime.now(timezone.utc)


class KisChartCollector:
    """KIS 모의투자 도메인의 국내주식 기간별시세(일봉, 원주가) 수집기."""

    DOMAIN = "https://openapivts.koreainvestment.com:29443"
    DAILY_URL = DOMAIN + "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    TOKEN_URL = DOMAIN + "/oauth2/tokenP"
    TR_ID = "FHKST03010100"
    PRICE_FIELDS = ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_clpr", "acml_vol")
    PAGE_ROWS = 100  # KIS returns at most 100 daily rows per call, newest first.
    MIN_INTERVAL_SECONDS = 1.1  # About one call per second per app key, shared with the backend.
    TOKEN_RETRY_SECONDS = 61  # KIS issues one token per minute per app key (EGW00133).
    RATE_LIMIT_RETRIES = 2  # EGW00201 is the per-second limit, usually a collision with the backend.

    def __init__(self, app_key: str | None = None, app_secret: str | None = None,
                 session: requests.Session | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.app_key = (app_key or os.getenv("KIS_PAPER_APP_KEY") or os.getenv("KIS_VTS_APP_KEY") or "").strip()
        self.app_secret = (app_secret or os.getenv("KIS_PAPER_APP_SECRET")
                           or os.getenv("KIS_VTS_APP_SECRET") or "").strip()
        self.session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None
        self._token: str | None = None

    def collect_daily(self, stock_name: str, stock_code: str, from_date: str, to_date: str) -> List[MarketRecord]:
        if not self.app_key or not self.app_secret:
            raise ValueError("KIS_PAPER_APP_KEY and KIS_PAPER_APP_SECRET are required for kis_chart collection")
        if not isinstance(stock_code, str) or not re.fullmatch(r"\d{6}", stock_code):
            raise ValueError("KIS chart collection requires a six-digit stock code")
        if not all(isinstance(value, str) and re.fullmatch(r"\d{8}", value) for value in (from_date, to_date)):
            raise ValueError("KIS chart dates must be YYYYMMDD")
        start = datetime.strptime(from_date, "%Y%m%d").date()
        end = datetime.strptime(to_date, "%Y%m%d").date()
        if start > end or end >= _now().astimezone(KST).date():
            # Today's bar is still forming; only completed earlier sessions are collected.
            raise ValueError("KIS chart range must be ordered and exclude current and future KST dates")
        bars: Dict[str, tuple[Dict[str, str], datetime]] = {}
        name = stock_name
        cursor = end
        # Each page holds up to 100 sessions, which span more than 100 calendar days.
        for _ in range((end - start).days // self.PAGE_ROWS + 2):
            output1, page, observed = self._fetch_page(stock_code, start, cursor)
            name = name or str(output1.get("hts_kor_isnm") or "").strip()
            if not page:
                break
            for day, content in page:
                if not start.strftime("%Y%m%d") <= day <= cursor.strftime("%Y%m%d"):
                    raise ValueError("KIS chart row falls outside the requested dates")
                if day in bars and bars[day][0] != content:
                    raise ValueError("conflicting duplicate KIS chart date")
                bars.setdefault(day, (content, observed))
            oldest = datetime.strptime(min(day for day, _ in page), "%Y%m%d").date()
            if len(page) < self.PAGE_ROWS or oldest <= start:
                break
            cursor = oldest - timedelta(days=1)
        else:
            raise ValueError("KIS chart pagination did not reach the requested start date")
        return [self._to_market_record(name, stock_code, day, *bars[day]) for day in sorted(bars)]

    def _fetch_page(self, stock_code: str, start: date, end: date
                    ) -> tuple[Dict[str, Any], List[tuple[str, Dict[str, str]]], datetime]:
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code,
                  "FID_INPUT_DATE_1": start.strftime("%Y%m%d"), "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                  "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "1"}
        for attempt in range(self.RATE_LIMIT_RETRIES + 1):
            headers = {"content-type": "application/json; charset=utf-8",
                       "authorization": f"Bearer {self._access_token()}", "appkey": self.app_key,
                       "appsecret": self.app_secret, "tr_id": self.TR_ID, "custtype": "P"}
            status, payload = self._call("GET", self.DAILY_URL, "daily chart", params=params, headers=headers)
            if payload.get("rt_cd") == "0" and status is not None and 200 <= status < 300:
                break
            code = self._provider_code(payload.get("msg_cd"))
            if code != "EGW00201" or attempt == self.RATE_LIMIT_RETRIES:
                # "HTTP 429" rather than "status=429": the collection loop reads the latter as a
                # daily quota and pauses until tomorrow, but KIS limits are per second.
                raise ValueError(f"KIS daily chart refused the request (HTTP {status or 'invalid'}, msg_cd={code})")
            self._sleep(self.MIN_INTERVAL_SECONDS)
        observed = _now()
        output1, output2 = payload.get("output1") or {}, payload.get("output2")
        if not isinstance(output1, dict) or not isinstance(output2, list):
            raise ValueError("KIS daily chart response requires an output1 object and an output2 array")
        if str(output1.get("stck_shrn_iscd") or stock_code).strip() != stock_code:
            raise ValueError("KIS daily chart answered for a different stock")
        page = []
        for row in output2:
            if not isinstance(row, dict):
                raise ValueError("KIS daily chart rows must be objects")
            day = str(row.get("stck_bsop_date") or "").strip()
            if not day:
                continue  # KIS pads an empty window with an all-blank row.
            if not re.fullmatch(r"\d{8}", day):
                raise ValueError("KIS daily chart date must be YYYYMMDD")
            page.append((day, {field: self._clean_number(row.get(field)) for field in self.PRICE_FIELDS}))
        return output1, page, observed

    def _access_token(self) -> str:
        if not self._token:
            body = {"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret}
            _, payload = self._call("POST", self.TOKEN_URL, "token", json_body=body)
            if self._provider_code(payload.get("error_code")) == "EGW00133":
                # Another process (the backend shares the key) took this minute's token.
                self._sleep(self.TOKEN_RETRY_SECONDS)
                _, payload = self._call("POST", self.TOKEN_URL, "token", json_body=body)
            token = payload.get("access_token")
            if not isinstance(token, str) or not token.strip():
                code = self._provider_code(payload.get("error_code"))
                raise ValueError(f"KIS token request was refused (error_code={code})")
            self._token = token.strip()
        return self._token

    def _call(self, method: str, url: str, label: str, *, params: Dict[str, str] | None = None,
              headers: Dict[str, str] | None = None, json_body: Dict[str, str] | None = None
              ) -> tuple[int | None, Dict[str, Any]]:
        self._pace()
        try:
            if method == "GET":
                response = self.session.get(url, params=params, headers=headers, timeout=20, allow_redirects=False)
            else:
                response = self.session.post(url, json=json_body, headers={"content-type": "application/json"},
                                             timeout=20, allow_redirects=False)
        except requests.RequestException as exc:
            # Exception text can echo request headers or URLs; keep only the exception type.
            raise requests.RequestException(f"KIS {label} request failed ({type(exc).__name__})") from None
        status = response.status_code if type(response.status_code) is int else None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise ValueError(f"KIS {label} response is not a JSON object (HTTP {status or 'invalid'})")
        # KIS explains refusals in the body (rt_cd/msg_cd or error_code), sometimes under HTTP 403/500.
        return status, payload

    def _pace(self) -> None:
        if self._last_call is not None:
            wait = self.MIN_INTERVAL_SECONDS - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()

    @staticmethod
    def _provider_code(value: Any) -> str:
        text = str(value or "").strip()
        return text if re.fullmatch(r"[A-Z0-9_]{1,16}", text) else "unknown"

    def _to_market_record(self, stock_name: str, stock_code: str, raw_date: str, content: Dict[str, str],
                          observed: datetime) -> MarketRecord:
        day = datetime.strptime(raw_date, "%Y%m%d").date()
        from src.runner.trading_calendar import CALENDAR_VERSION, SPECIAL_CLOSES, daily_session_close
        bar_at = daily_session_close(day.isoformat()).astimezone(KST)
        if observed.tzinfo is None or observed < bar_at:
            raise ValueError("KIS chart observation must follow the completed market close and include timezone")
        record = MarketRecord(
            source_type="chart",
            stock_name=stock_name,
            stock_code=stock_code,
            timestamp=day.isoformat() + "T00:00:00",
            open=content["stck_oprc"],
            high=content["stck_hgpr"],
            low=content["stck_lwpr"],
            close=content["stck_clpr"],
            volume=content["acml_vol"],
            metadata={"source": "kis", "raw_date": raw_date, "price_basis": "unadjusted",
                      "calendar_version": CALENDAR_VERSION, "source_url": self.DAILY_URL, "tr_id": self.TR_ID},
        )
        if day.isoformat() in SPECIAL_CLOSES:
            record.metadata["calendar_notice"] = SPECIAL_CLOSES[day.isoformat()]
        payload = {"stock_code": record.stock_code, "timestamp": record.timestamp,
                   **{field: getattr(record, field) for field in ("open", "high", "low", "close", "volume")},
                   "metadata": record.metadata}
        version = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        record.metadata.update(trade_date=day.isoformat(), bar_at=bar_at.isoformat(),
                               collected_at=observed.isoformat(), available_at=observed.isoformat(),
                               version=version, source_id="kis-chart:" + version)
        return record

    @staticmethod
    def _clean_number(value: Any) -> str:
        text = str(value if value is not None else "").strip()
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text) or not math.isfinite(float(text)):
            raise ValueError("KIS chart prices and volume must be nonnegative finite numbers")
        return text
