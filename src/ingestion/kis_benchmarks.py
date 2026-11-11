"""KOSPI/KOSDAQ daily closes and each stock's listed market from KIS, for market-relative event reactions.

The benchmark pipeline was built for the KRX Open API, whose key still answers 401, so the chartist had
no market comparison at all. KIS serves the same price indices and each stock's representative market
to the paper app key. Index records keep the KRX record schema (``validate_benchmark_record`` checks
them) with a KIS source URL and a ``kis-benchmark:`` ID; market mappings follow
``src.runner.market_context_data``'s mapping schema with the KIS quote endpoint as their source.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict

from .kis_chart import KisChartCollector, _now
from .krx_benchmarks import KIS_INDEX_CODES, KIS_INDEX_NAMES, KIS_INDEX_URL, _close, kis_benchmark_record

PRICE_URL = KisChartCollector.DOMAIN + "/uapi/domestic-stock/v1/quotations/inquire-price"


def kis_market_mapping(stock_code: str, series: str, observed: datetime, effective_from: date) -> dict:
    """A stock-to-market benchmark mapping whose provenance is the KIS quote that named the market."""
    if not isinstance(stock_code, str) or not re.fullmatch(r"\d{6}", stock_code):
        raise ValueError("market mapping requires a six-digit stock code")
    if series not in KIS_INDEX_NAMES:
        raise ValueError("unsupported benchmark series")
    content = {"schema_version": 1, "stock_code": stock_code, "kind": "market", "series": series,
               "index_name": KIS_INDEX_NAMES[series],
               "source_url": f"{PRICE_URL}?FID_COND_MRKT_DIV_CODE=J&FID_INPUT_ISCD={stock_code}",
               "effective_from": effective_from.isoformat(), "effective_to": None}
    version = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {**content, "version": version, "source_id": "kis-market:" + version,
            "available_at": observed.isoformat()}


def market_from_representative(name: Any) -> str | None:
    """KIS names a stock's representative market e.g. KOSPI200, KOSPI, KSQ150 (KOSDAQ 150) or KOSDAQ.

    Anything else, such as a cross-market index name, stays unmapped rather than guessed.
    """
    text = str(name or "").strip().upper()
    if text.startswith("KOSPI"):
        return "KOSPI"
    if text.startswith(("KOSDAQ", "KSQ")):
        return "KOSDAQ"
    return None


class KisBenchmarkCollector(KisChartCollector):
    INDEX_TR_ID = "FHKUP03500100"
    PRICE_TR_ID = "FHKST01010100"
    INDEX_PAGE_ROWS = 50  # Measured 2026-11-11: the index chart returns at most 50 daily rows per call.

    def collect_index_daily(self, series: str, start: date, end: date) -> list[dict]:
        if series not in KIS_INDEX_CODES:
            raise ValueError("unsupported benchmark series")
        if start > end:
            raise ValueError("benchmark range must be ordered")
        records: Dict[date, dict] = {}
        cursor = end
        # Each page holds up to 50 sessions, which span more than 50 calendar days.
        for _ in range((end - start).days // self.INDEX_PAGE_ROWS + 2):
            payload, observed = self._get(KIS_INDEX_URL, self.INDEX_TR_ID, "index chart", {
                "FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": KIS_INDEX_CODES[series],
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"), "FID_INPUT_DATE_2": cursor.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D"})
            rows = payload.get("output2")
            if not isinstance(rows, list):
                raise ValueError("KIS index chart response requires an output2 array")
            days = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("KIS index chart rows must be objects")
                text = str(row.get("stck_bsop_date") or "").strip()
                if not text:
                    continue  # KIS pads an empty window with an all-blank row.
                if not re.fullmatch(r"\d{8}", text):
                    raise ValueError("KIS index chart date must be YYYYMMDD")
                day = datetime.strptime(text, "%Y%m%d").date()
                if not start <= day <= cursor:
                    raise ValueError("KIS index chart row falls outside the requested dates")
                record = kis_benchmark_record(series, day, _close(str(row.get("bstp_nmix_prpr") or "").strip()),
                                              observed)
                previous = records.setdefault(day, record)
                if previous["close"] != record["close"]:
                    raise ValueError("conflicting duplicate KIS index date")
                days.append(day)
            if not days:
                break
            oldest = min(days)
            if len(days) < self.INDEX_PAGE_ROWS or oldest <= start:
                break
            cursor = oldest - timedelta(days=1)
        else:
            raise ValueError("KIS index pagination did not reach the requested start date")
        return [records[day] for day in sorted(records)]

    def collect_market(self, stock_code: str) -> tuple[str | None, datetime]:
        payload, observed = self._get(PRICE_URL, self.PRICE_TR_ID, "quote",
                                      {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code})
        output = payload.get("output")
        if not isinstance(output, dict):
            raise ValueError("KIS quote response requires an output object")
        return market_from_representative(output.get("rprs_mrkt_kor_name")), observed

    def _get(self, url: str, tr_id: str, label: str, params: Dict[str, str]) -> tuple[Dict[str, Any], datetime]:
        for attempt in range(self.RATE_LIMIT_RETRIES + 1):
            headers = {"content-type": "application/json; charset=utf-8",
                       "authorization": f"Bearer {self._access_token()}", "appkey": self.app_key,
                       "appsecret": self.app_secret, "tr_id": tr_id, "custtype": "P"}
            status, payload = self._call("GET", url, label, params=params, headers=headers)
            if payload.get("rt_cd") == "0" and status is not None and 200 <= status < 300:
                return payload, _now()
            code = self._provider_code(payload.get("msg_cd"))
            if code != "EGW00201" or attempt == self.RATE_LIMIT_RETRIES:
                raise ValueError(f"KIS {label} refused the request (HTTP {status or 'invalid'}, msg_cd={code})")
            self._sleep(self.MIN_INTERVAL_SECONDS)
        raise AssertionError("unreachable")
