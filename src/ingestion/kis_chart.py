from __future__ import annotations

from typing import List

from .kis_client import KISClient
from .types import MarketRecord


class KISChartCollector:
    """KIS 일봉 차트 수집기 (기본: 주식 일자별 시세)."""

    def __init__(self, client: KISClient | None = None):
        self.client = client or KISClient()

    def collect_daily(self, stock_name: str, stock_code: str, from_date: str, to_date: str) -> List[MarketRecord]:
        payload = self.client.request(
            path="/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
                "FID_INPUT_DATE_1": from_date,
                "FID_INPUT_DATE_2": to_date,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
            tr_id="FHKST03010100",
        )

        rows = payload.get("output2", []) or []
        records: List[MarketRecord] = []
        for row in rows:
            ts = str(row.get("stck_bsop_date", ""))
            if not ts:
                continue
            records.append(
                MarketRecord(
                    source_type="chart",
                    stock_name=stock_name,
                    stock_code=stock_code,
                    timestamp=f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}T00:00:00",
                    open=str(row.get("stck_oprc", "")),
                    high=str(row.get("stck_hgpr", "")),
                    low=str(row.get("stck_lwpr", "")),
                    close=str(row.get("stck_clpr", "")),
                    volume=str(row.get("acml_vol", "")),
                    metadata={"raw_date": ts},
                )
            )
        return records
